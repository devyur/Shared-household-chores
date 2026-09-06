import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def generate_invite_code():
    return secrets.token_hex(4).upper()


class Household(models.Model):
    """A single household. The system only ever needs one, but modeling it
    explicitly keeps membership/ownership relations clean."""

    name = models.CharField(max_length=100)
    invite_code = models.CharField(
        max_length=20, unique=True, default=generate_invite_code
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("household", "user")

    def __str__(self):
        return f"{self.user} ({self.role}) in {self.household}"

    @property
    def is_active(self):
        return self.removed_at is None


class RecurrenceRule(models.Model):
    class RuleType(models.TextChoices):
        CALENDAR = "calendar", "Calendar-based"
        COMPLETION = "completion", "Completion-based interval"

    chore = models.OneToOneField(
        "Chore", on_delete=models.CASCADE, related_name="recurrence_rule"
    )
    rule_type = models.CharField(max_length=10, choices=RuleType.choices)
    interval_days = models.PositiveIntegerField(
        help_text=(
            "Calendar-based: fixed number of days between due dates. "
            "Completion-based: number of days after completion until the next due date."
        )
    )

    def __str__(self):
        return f"{self.get_rule_type_display()} every {self.interval_days}d"


class ChoreQuerySet(models.QuerySet):
    def due_for_failure(self):
        """ASSIGNED chores in this queryset whose due date has passed."""
        return self.filter(
            status=Chore.Status.ASSIGNED, due_date__lt=timezone.localdate()
        )

    def fail_overdue(self):
        """Lazily fail chores that are still ASSIGNED past their due date (#4,
        §8, §17.7, §17.8).

        Evaluated on read, same convention as `auto_assign_due()` below.
        Called from `chore_list` *before* `auto_assign_due()` in the same
        request: failure must be resolved for chores that were ASSIGNED
        under a *previous* lazy-eval pass before this pass potentially
        assigns other, unrelated OPEN chores. Running it in the other order
        would let a chore that `auto_assign_due()` *just* assigned this same
        pass (whose due date, by definition, has already arrived) be
        immediately failed by this method in the same request, before its
        new assignee ever had a chance.

        Only `assigned` chores past due are touched (§17.7) — an `open`
        chore past due is #3's "stays available" case and is left alone,
        and a `completed` chore is never matched by this queryset's
        `status=ASSIGNED` filter, so a chore actually completed (even late,
        even in the gap before this method next runs) is never touched.

        For each matching chore: it is reopened (`status=open`,
        `assigned_to=None`), its prior claims are deleted (see module-level
        note below on why), and a `PointEvent` (kind=`failure_penalty`,
        points=-round-half-up(chore.points * 0.5)) is recorded against the
        former assignee — the running total may go negative (§8, §17.8).

        Idempotent: once reopened, a chore's status is `open`, so it no
        longer matches this queryset and re-evaluating it is a no-op — no
        double penalty, no duplicate log entry.
        """
        overdue_chores = self.due_for_failure()
        for chore in overdue_chores:
            assignee = chore.assigned_to
            chore.assigned_to = None
            chore.status = Chore.Status.OPEN
            chore.save(update_fields=["assigned_to", "status"])

            # Prior claims are deleted, not kept, on failure. Rationale: the
            # chore's `due_date` doesn't change on reopening, so if claims
            # survived, the very next lazy-eval call in this same request
            # (`auto_assign_due()`, which runs right after this method) would
            # see an OPEN, due, claimed chore and immediately reassign it —
            # right back into `assigned` status with the same past due date,
            # ready to be failed again on the *next* page load. That would
            # turn one missed due date into a runaway per-request penalty
            # loop. Deleting claims here means `auto_assign_due()` sees zero
            # claims and correctly leaves the chore open/unassigned (its
            # documented behaviour), matching §17.5 ("claims last until the
            # due date") — the due date has now passed and the chore has
            # failed, so those claims have already served their purpose.
            # Members who still want it re-claim it fresh.
            chore.claims.all().delete()

            penalty = failure_penalty_points(chore.points)
            PointEvent.objects.create(
                member=assignee,
                chore=chore,
                kind=PointEvent.Kind.FAILURE_PENALTY,
                points=-penalty,
            )
            ActivityLog.objects.create(
                household=chore.household,
                actor=None,
                action=ActivityLog.Action.CHORE_FAILED,
                chore=chore,
                member=assignee,
                description=(
                    f"'{chore.name}' was not completed by its due date; it "
                    f"reopened for claiming and {assignee} lost {penalty} "
                    f"points (50% of {chore.points}, rounded half-up)."
                ),
            )

    def due_for_auto_assignment(self):
        """OPEN chores in this queryset whose due date has arrived."""
        return self.filter(
            status=Chore.Status.OPEN, due_date__lte=timezone.localdate()
        )

    def auto_assign_due(self):
        """Lazily auto-assign chores whose due date has arrived (§5, §17.4).

        This is evaluated on read (called from wherever chores are listed,
        e.g. `chore_list`) rather than by a scheduler/cron/Celery job, per
        the project's lazy-eval convention.

        For every OPEN chore in this queryset whose `due_date` has arrived
        and which has one or more claims, assigns it to the claimant with
        the highest current point total (`current_point_total`) and sets
        its status to ASSIGNED. Ties are broken deterministically by
        earliest claim (`claimed_at`). A due OPEN chore with zero claims is
        left untouched — it stays open/unassigned (§17.15). Chores that
        are already ASSIGNED or COMPLETED never reach this method (this
        queryset only considers OPEN chores), so re-evaluating them is a
        no-op: no re-assignment, no duplicate log entries. Existing claims
        are never deleted — only `assigned_to`/`status` change.
        """
        due_chores = self.due_for_auto_assignment().prefetch_related("claims__member")
        for chore in due_chores:
            claims = list(chore.claims.all())
            if not claims:
                continue

            winner = claims[0]
            winner_points = current_point_total(winner.member)
            for claim in claims[1:]:
                points = current_point_total(claim.member)
                if points > winner_points or (
                    points == winner_points and claim.claimed_at < winner.claimed_at
                ):
                    winner = claim
                    winner_points = points

            chore.assigned_to = winner.member
            chore.status = Chore.Status.ASSIGNED
            chore.save(update_fields=["assigned_to", "status"])
            ActivityLog.objects.create(
                household=chore.household,
                actor=None,
                action=ActivityLog.Action.CHORE_ASSIGNED,
                chore=chore,
                member=winner.member,
                description=(
                    f"'{chore.name}' automatically assigned to {winner.member} "
                    "at its due date (highest current points; ties go to the "
                    "earliest claim)."
                ),
            )


class Chore(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ASSIGNED = "assigned", "Assigned"
        COMPLETED = "completed", "Completed"

    objects = ChoreQuerySet.as_manager()

    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="chores"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    points = models.PositiveIntegerField()
    due_date = models.DateField()
    is_recurring = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_chores",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_chores",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return self.name

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def create_next_occurrence(self):
        """Spawn the next occurrence of a recurring chore.

        Called by the completion flow (#4) right after this chore is saved
        as completed — this method doesn't decide *when* a chore is
        completed, only what happens next if it was. Returns the new
        `Chore`, or `None` for a non-recurring chore or one with no saved
        `RecurrenceRule`.
        """
        if not self.is_recurring:
            return None
        try:
            rule = self.recurrence_rule
        except RecurrenceRule.DoesNotExist:
            return None

        if rule.rule_type == RecurrenceRule.RuleType.CALENDAR:
            next_due = self.due_date + timedelta(days=rule.interval_days)
        else:
            base = (self.completed_at.date() if self.completed_at else self.due_date)
            next_due = base + timedelta(days=rule.interval_days)

        next_chore = Chore.objects.create(
            household=self.household,
            name=self.name,
            description=self.description,
            points=self.points,
            due_date=next_due,
            is_recurring=True,
            created_by=self.created_by,
        )
        RecurrenceRule.objects.create(
            chore=next_chore,
            rule_type=rule.rule_type,
            interval_days=rule.interval_days,
        )
        ActivityLog.objects.create(
            household=self.household,
            actor=None,
            action=ActivityLog.Action.CHORE_CREATED,
            chore=next_chore,
            description=(
                f"Next occurrence of '{self.name}' created automatically "
                "after completion."
            ),
        )
        return next_chore


class Claim(models.Model):
    chore = models.ForeignKey(Chore, on_delete=models.CASCADE, related_name="claims")
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chore_claims"
    )
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("chore", "member")

    def __str__(self):
        return f"{self.member} claims {self.chore}"


class PointEvent(models.Model):
    class Kind(models.TextChoices):
        COMPLETION = "completion", "Completion award"
        REVIEW_ADJUSTMENT = "review_adjustment", "Owner review adjustment"
        FAILURE_PENALTY = "failure_penalty", "Failure penalty"
        MANUAL_ADJUSTMENT = "manual_adjustment", "Direct owner adjustment"

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="point_events"
    )
    chore = models.ForeignKey(
        Chore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="point_events",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    points = models.IntegerField(help_text="Signed delta applied to the member's total.")
    reason = models.TextField(
        blank=True, help_text="Required for review and manual adjustments."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="point_events_created",
        help_text="Who triggered this event (self for completions, owner for adjustments).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    review_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set on completion events; review adjustments are locked after this.",
    )

    def __str__(self):
        return f"{self.member} {self.points:+d} ({self.kind})"


def failure_penalty_points(points):
    """The failure penalty for a chore worth `points`: 50% of its point
    value, rounded half-up (#4, §8, §17.7).

    Round-half-up (rather than Python's default `round()`, which is
    round-half-to-even/banker's rounding) since an odd chore point value
    doesn't halve evenly and the issue calls for round-half-up explicitly.
    Implemented as integer arithmetic (`(points + 1) // 2`) rather than
    float math to sidestep floating-point rounding entirely: for a
    non-negative integer `points`, `(points + 1) // 2` is exactly
    `points / 2` rounded half-up (e.g. 20 -> 10, 15 -> 8, 21 -> 11).
    """
    return (points + 1) // 2


def current_point_total(member):
    """A member's lifetime point total: the sum of all their `PointEvent`
    deltas (completions, review adjustments, failure penalties, manual
    adjustments), computed on read rather than stored redundantly (#7).

    This is the single source of truth for "a member's points" — #3's
    auto-assignment tie-break and #7's leaderboard both call this rather
    than each re-summing `PointEvent` independently. A removed member's
    `PointEvent` rows are never deleted (§2), so this keeps returning their
    full historical total even after `Membership.removed_at` is set; it's
    up to callers (e.g. the leaderboard view) to filter out inactive
    members if they shouldn't be displayed.
    """
    total = PointEvent.objects.filter(member=member).aggregate(
        total=models.Sum("points")
    )["total"]
    return total or 0


def week_bounds(day=None):
    """The (Monday, Sunday) calendar-date bounds, inclusive, of the week
    containing `day` (default: today).

    Decision (#7 explicitly asks this be documented since plan.md doesn't
    specify a week boundary): weeks run Monday-Sunday, using Python's/
    Django's ISO convention where `date.weekday()` is 0 for Monday. "Today"
    is resolved via `timezone.localdate()`, i.e. server-local time per
    `settings.TIME_ZONE` — the same basis the rest of the app already uses
    for due-date/lazy-eval date comparisons (see `ChoreQuerySet` above), and
    the same basis `chores_completed_on` below uses for "completed today".
    """
    day = day or timezone.localdate()
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    return start, end


def weekly_point_total(member, day=None):
    """Sum of `member`'s `PointEvent.points` where `created_at` falls in
    the current week (Monday-Sunday, server-local — see `week_bounds`).

    Built alongside `current_point_total` as the second point-aggregation
    primitive #7 asks for, using the same on-read `Sum` approach rather
    than a stored/cached total.
    """
    start, end = week_bounds(day)
    total = PointEvent.objects.filter(
        member=member, created_at__date__gte=start, created_at__date__lte=end
    ).aggregate(total=models.Sum("points"))["total"]
    return total or 0


def monthly_point_total(member, day=None):
    """Sum of `member`'s `PointEvent.points` where `created_at` falls in
    the current calendar month (server-local, per `timezone.localdate()`).
    """
    day = day or timezone.localdate()
    total = PointEvent.objects.filter(
        member=member, created_at__year=day.year, created_at__month=day.month
    ).aggregate(total=models.Sum("points"))["total"]
    return total or 0


def chores_completed_on(member, day=None):
    """Count of `member`'s chores with `status=completed` and
    `completed_at` falling on `day` (default: today, server-local per
    `timezone.localdate()` — same basis as the rest of this module's
    date-based lazy-eval checks). Backs the leaderboard's "completed
    today" column (#7, §9).
    """
    day = day or timezone.localdate()
    return Chore.objects.filter(
        assigned_to=member, status=Chore.Status.COMPLETED, completed_at__date=day
    ).count()


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CHORE_CREATED = "chore_created", "Chore created"
        CHORE_EDITED = "chore_edited", "Chore edited"
        CHORE_DELETED = "chore_deleted", "Chore deleted"
        CHORE_CLAIMED = "chore_claimed", "Chore claimed"
        CHORE_ASSIGNED = "chore_assigned", "Chore assigned"
        CHORE_COMPLETED = "chore_completed", "Chore completed"
        CHORE_FAILED = "chore_failed", "Chore failed"
        POINTS_ADJUSTED = "points_adjusted", "Points adjusted"
        MEMBER_INVITED = "member_invited", "Member invited"
        MEMBER_JOINED = "member_joined", "Member joined"
        MEMBER_REMOVED = "member_removed", "Member removed"

    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="activity_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
        help_text="Null for system-triggered events (e.g. lazy-eval auto-assignment).",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    chore = models.ForeignKey(
        Chore,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs_about",
        help_text="The member the action concerns, if different from the actor.",
    )
    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


class Achievement(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class MemberAchievement(models.Model):
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="achievements"
    )
    achievement = models.ForeignKey(
        Achievement, on_delete=models.CASCADE, related_name="awarded_to"
    )
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("member", "achievement")

    def __str__(self):
        return f"{self.member} earned {self.achievement}"


# Achievement rules (#8, §10/§18 — plan.md deliberately leaves the exact
# rules as an implementation detail; these are the 3 concrete ones chosen,
# each with a specific number per the issue's explicit ask):
#
# - "First Chore": the member's first-ever chore completion.
# - "On a Roll": ON_A_ROLL_COUNT (5) chores completed within a rolling
#   ON_A_ROLL_WINDOW_DAYS (7)-day window ending now.
# - "Point Milestone": lifetime points (`current_point_total`) reach
#   POINT_MILESTONE_THRESHOLD (100).
#
# `Achievement` rows for these names are seeded by a data migration (see
# chores/migrations/0003_seed_achievements.py) so they exist without manual
# admin setup.
FIRST_CHORE = "First Chore"
ON_A_ROLL = "On a Roll"
POINT_MILESTONE = "Point Milestone"

ON_A_ROLL_COUNT = 5
ON_A_ROLL_WINDOW_DAYS = 7
POINT_MILESTONE_THRESHOLD = 100

ACHIEVEMENT_DEFINITIONS = [
    (FIRST_CHORE, "Complete your first chore."),
    (
        ON_A_ROLL,
        f"Complete {ON_A_ROLL_COUNT} chores within a rolling "
        f"{ON_A_ROLL_WINDOW_DAYS}-day window.",
    ),
    (POINT_MILESTONE, f"Reach {POINT_MILESTONE_THRESHOLD} lifetime points."),
]


def _award_achievement(member, name):
    """Award the named achievement to `member` if it isn't already earned.

    A no-op (not an error) if the `Achievement` row doesn't exist (seeding
    failed/hasn't run) or the member already has it — `MemberAchievement`'s
    `unique_together(member, achievement)` makes `get_or_create` here
    naturally idempotent, matching the issue's "re-triggering the same
    condition later is a no-op" requirement.
    """
    try:
        achievement = Achievement.objects.get(name=name)
    except Achievement.DoesNotExist:
        return
    MemberAchievement.objects.get_or_create(member=member, achievement=achievement)


def evaluate_achievements(member):
    """Check all achievement rules for `member` and award any newly met
    ones (#8).

    Called right after the events that could trigger an achievement —
    chore completion (`chore_complete`, #4) and point changes
    (`chore_review_adjust` #5, `adjust_points` #6) — not via a poll or
    scheduler, consistent with this project's event-driven convention
    (`_docs/AGENTS.md`). Awarding never changes any point total; this only
    ever creates `MemberAchievement` badge rows.
    """
    completed_count = Chore.objects.filter(
        assigned_to=member, status=Chore.Status.COMPLETED
    ).count()
    if completed_count >= 1:
        _award_achievement(member, FIRST_CHORE)

    window_start = timezone.now() - timedelta(days=ON_A_ROLL_WINDOW_DAYS)
    recent_completions = Chore.objects.filter(
        assigned_to=member,
        status=Chore.Status.COMPLETED,
        completed_at__gte=window_start,
    ).count()
    if recent_completions >= ON_A_ROLL_COUNT:
        _award_achievement(member, ON_A_ROLL)

    if current_point_total(member) >= POINT_MILESTONE_THRESHOLD:
        _award_achievement(member, POINT_MILESTONE)
