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


def current_point_total(member):
    """A member's current point total: the sum of all their `PointEvent`
    deltas (completions, review adjustments, failure penalties, manual
    adjustments).

    This is the initial, minimal version of the "current points" primitive
    needed by #3 (auto-assignment tie-break). The full lifetime point-total
    calculation — including any leaderboard/summary presentation — is owned
    by #7; that work should reuse or extend this function rather than
    forking a second, separate implementation.
    """
    total = PointEvent.objects.filter(member=member).aggregate(
        total=models.Sum("points")
    )["total"]
    return total or 0


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
