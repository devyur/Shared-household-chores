from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ChoreForm, HouseholdCreateForm, HouseholdJoinForm, SignUpForm
from .models import (
    ActivityLog,
    Chore,
    Claim,
    Household,
    MemberAchievement,
    Membership,
    PointEvent,
    chores_completed_on,
    current_point_total,
    evaluate_achievements,
    generate_invite_code,
    monthly_point_total,
    week_bounds,
    weekly_point_total,
)


# Entries per page for the activity history view (#9). plan.md §13 doesn't
# specify a page size, so this is the issue's documented choice.
ACTIVITY_LOG_PAGE_SIZE = 50


def get_household():
    """The system supports exactly one household."""
    return Household.objects.first()


def get_active_membership(user):
    if not user.is_authenticated:
        return None
    return (
        user.memberships.select_related("household")
        .filter(removed_at__isnull=True)
        .first()
    )


def signup(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("home")
    else:
        form = SignUpForm()
    return render(request, "registration/signup.html", {"form": form})


@login_required
def home(request):
    membership = get_active_membership(request.user)
    if membership:
        household = membership.household
        members = household.memberships.filter(
            removed_at__isnull=True
        ).select_related("user")
        # "Due today" reminder (#10, §12): every non-deleted, non-completed
        # chore whose due_date is today, household-wide regardless of status
        # (open or assigned) or who it's assigned to (§14) — not just chores
        # assigned to the viewer. This is the only notification MVP builds;
        # no claim/assignment/completion/point-change notifications and no
        # email (§12, §16, §18). The full dashboard layout is #11's job —
        # this just gets the reminder onto the existing home page.
        due_today_chores = (
            household.chores.filter(
                due_date=timezone.localdate(), deleted_at__isnull=True
            )
            .exclude(status=Chore.Status.COMPLETED)
            .select_related("assigned_to")
            .order_by("name")
        )
        return render(
            request,
            "chores/home.html",
            {
                "household": household,
                "membership": membership,
                "members": members,
                "due_today_chores": due_today_chores,
            },
        )
    if get_household():
        return redirect("household_join")
    return redirect("household_create")


@login_required
def household_create(request):
    if get_active_membership(request.user):
        return redirect("home")
    if get_household():
        return redirect("household_join")

    if request.method == "POST":
        form = HouseholdCreateForm(request.POST)
        if form.is_valid():
            household = form.save()
            Membership.objects.create(
                household=household, user=request.user, role=Membership.Role.OWNER
            )
            ActivityLog.objects.create(
                household=household,
                actor=request.user,
                action=ActivityLog.Action.MEMBER_JOINED,
                member=request.user,
                description=f"{request.user} created the household and became owner.",
            )
            messages.success(
                request,
                "Household created. Share the invite code on the household page "
                "so others can join.",
            )
            return redirect("home")
    else:
        form = HouseholdCreateForm()
    return render(request, "chores/household_create.html", {"form": form})


@login_required
def household_join(request):
    if get_active_membership(request.user):
        return redirect("home")
    household = get_household()
    if not household:
        return redirect("household_create")

    if request.method == "POST":
        form = HouseholdJoinForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data["invite_code"].strip()
            if code.upper() != household.invite_code.upper():
                form.add_error("invite_code", "Invalid invite code.")
            else:
                Membership.objects.create(household=household, user=request.user)
                ActivityLog.objects.create(
                    household=household,
                    actor=request.user,
                    action=ActivityLog.Action.MEMBER_JOINED,
                    member=request.user,
                    description=f"{request.user} joined the household.",
                )
                messages.success(request, "You joined the household.")
                return redirect("home")
    else:
        form = HouseholdJoinForm()
    return render(request, "chores/household_join.html", {"form": form})


@login_required
def members(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    household = membership.household
    member_list = household.memberships.filter(
        removed_at__isnull=True
    ).select_related("user")
    # Always computed fresh from PointEvent (#5, #7): any review adjustment
    # submitted via chore_review_adjust is reflected here on the very next
    # render, with no caching to invalidate.
    for m in member_list:
        m.point_total = current_point_total(m.user)
        # Direct adjustment reasons are visible to every member, not just
        # the owner (#6, §17.14) — rendered unconditionally in the template,
        # unlike the adjust-points form itself which is owner-only.
        m.point_adjustments = PointEvent.objects.filter(
            member=m.user, kind=PointEvent.Kind.MANUAL_ADJUSTMENT
        ).order_by("-created_at")
        # Earned badges (#8) — what and when (`awarded_at`), newest first.
        m.earned_achievements = MemberAchievement.objects.filter(
            member=m.user
        ).select_related("achievement").order_by("-awarded_at")
    return render(
        request,
        "chores/members.html",
        {
            "household": household,
            "members": member_list,
            "is_owner": membership.role == Membership.Role.OWNER,
        },
    )


@login_required
def leaderboard(request):
    """Lifetime-points leaderboard plus today's completed-chore count, and
    weekly/monthly point summaries, for every *active* household member
    (#7, §9).

    A removed member never appears here (§2's "points/history remain" is
    about their `PointEvent` rows staying queryable, e.g. via #9's activity
    history — not about staying on this live view), so this is built from
    `removed_at__isnull=True` memberships only, same as `members`/`home`
    above. All three point figures are computed on read via the shared
    aggregation helpers in `models.py` (`current_point_total`,
    `weekly_point_total`, `monthly_point_total`) rather than stored
    redundantly, and "completed today" via `chores_completed_on` — so a
    household with zero chores/point events simply renders every active
    member at 0 across the board, with no special-casing needed here.
    """
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    household = membership.household
    active_members = household.memberships.filter(
        removed_at__isnull=True
    ).select_related("user")

    today = timezone.localdate()
    week_start, week_end = week_bounds(today)
    rows = [
        {
            "member": m,
            "lifetime_points": current_point_total(m.user),
            "completed_today": chores_completed_on(m.user, today),
            "weekly_points": weekly_point_total(m.user, today),
            "monthly_points": monthly_point_total(m.user, today),
        }
        for m in active_members
    ]
    # Lifetime points descending (§9) — Python-side sort since the three
    # figures per row come from three separate aggregation calls rather
    # than one annotated queryset.
    rows.sort(key=lambda row: row["lifetime_points"], reverse=True)

    return render(
        request,
        "chores/leaderboard.html",
        {
            "household": household,
            "rows": rows,
            "today": today,
            "week_start": week_start,
            "week_end": week_end,
        },
    )


@login_required
def remove_member(request, user_id):
    membership = get_active_membership(request.user)
    if not membership or membership.role != Membership.Role.OWNER:
        return redirect("home")
    if request.method != "POST":
        return redirect("members")

    household = membership.household
    target_membership = get_object_or_404(
        Membership, household=household, user_id=user_id, removed_at__isnull=True
    )
    if target_membership.user_id == request.user.id:
        messages.error(request, "The owner cannot remove themselves.")
        return redirect("members")

    target_user = target_membership.user
    target_membership.removed_at = timezone.now()
    target_membership.save(update_fields=["removed_at"])

    # Their active assignment becomes available again (§2, §16); their points
    # and history are untouched.
    Chore.objects.filter(
        household=household, assigned_to=target_user, status=Chore.Status.ASSIGNED
    ).update(assigned_to=None, status=Chore.Status.OPEN)

    # Pending claims on still-open chores no longer make sense once they've left.
    Claim.objects.filter(
        chore__household=household,
        member=target_user,
        chore__status=Chore.Status.OPEN,
    ).delete()

    ActivityLog.objects.create(
        household=household,
        actor=request.user,
        action=ActivityLog.Action.MEMBER_REMOVED,
        member=target_user,
        description=f"{request.user} removed {target_user} from the household.",
    )
    messages.success(request, f"{target_user} was removed from the household.")
    return redirect("members")


@login_required
def adjust_points(request, user_id):
    """Direct owner point adjustment (#6, §11, §17.8, §17.13, §17.14).

    Independent of any chore/review (#5's `chore_review_adjust` is the
    completion-tied, capped path) — this one has no cap per plan.md, can
    target any active member including the owner themselves, and the
    resulting total may go negative (§17.8).
    """
    membership = get_active_membership(request.user)
    if not membership or membership.role != Membership.Role.OWNER:
        return redirect("home")
    if request.method != "POST":
        return redirect("members")

    household = membership.household
    # Only an active member can be targeted (§2, §16): a removed member's
    # history stays untouched, but no new point event is created against
    # them once they've left.
    target_membership = get_object_or_404(
        Membership, household=household, user_id=user_id, removed_at__isnull=True
    )
    target_user = target_membership.user

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "A reason is required to adjust points.")
        return redirect("members")

    try:
        amount = int(request.POST.get("amount", ""))
    except (TypeError, ValueError):
        messages.error(request, "Enter a whole number of points to adjust by.")
        return redirect("members")

    # No cap here (§11) — unlike #5's ±50%-of-chore-value review-adjustment
    # ceiling, plan.md places no limit on direct owner adjustments.
    PointEvent.objects.create(
        member=target_user,
        chore=None,
        kind=PointEvent.Kind.MANUAL_ADJUSTMENT,
        points=amount,
        reason=reason,
        created_by=request.user,
    )
    ActivityLog.objects.create(
        household=household,
        actor=request.user,
        action=ActivityLog.Action.POINTS_ADJUSTED,
        member=target_user,
        description=(
            f"{request.user} adjusted {target_user}'s points by {amount:+d} "
            f"({reason})."
        ),
    )
    # A direct owner adjustment changes the member's lifetime point total,
    # so re-evaluate achievements (#8) here too.
    evaluate_achievements(target_user)

    messages.success(
        request, f"Adjusted {target_user}'s points by {amount:+d}."
    )
    return redirect("members")


@login_required
def chore_list(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    # Lazy-eval hooks, no scheduler involved. Order matters: fail_overdue()
    # (#4, §8) must run *before* auto_assign_due() (#3, §5) so that a chore
    # auto-assigned during *this same* call (whose due date has, by
    # definition, just arrived) is never immediately failed in the same
    # pass, before its new assignee had any chance. fail_overdue() only
    # ever matches `assigned` chores, so a chore that's actually
    # `completed` is never touched by it regardless of ordering.
    household_chores = membership.household.chores.filter(deleted_at__isnull=True)
    household_chores.fail_overdue()
    household_chores.auto_assign_due()
    chores = membership.household.chores.filter(
        deleted_at__isnull=True
    ).prefetch_related("claims__member", "point_events")
    is_owner = membership.role == Membership.Role.OWNER
    now = timezone.now()
    for chore in chores:
        chore.user_has_claimed = any(
            claim.member_id == request.user.id for claim in chore.claims.all()
        )
        chore.user_is_assignee = (
            chore.status == Chore.Status.ASSIGNED
            and chore.assigned_to_id == request.user.id
        )
        # Owner review window (#5, §7, §17.9-§17.12): the adjustment action
        # is only ever offered here for a `completed` chore whose completion
        # PointEvent's `review_deadline` hasn't passed yet. This only
        # controls whether the form renders — `chore_review_adjust` below
        # re-checks all of this server-side, since a POST can be crafted
        # directly regardless of what the UI shows.
        completion_event = next(
            (
                pe
                for pe in chore.point_events.all()
                if pe.kind == PointEvent.Kind.COMPLETION
            ),
            None,
        )
        chore.review_adjustments = [
            pe
            for pe in chore.point_events.all()
            if pe.kind == PointEvent.Kind.REVIEW_ADJUSTMENT
        ]
        chore.can_review = bool(
            is_owner
            and chore.status == Chore.Status.COMPLETED
            and completion_event
            and completion_event.review_deadline
            and now < completion_event.review_deadline
        )
    return render(
        request,
        "chores/chore_list.html",
        {"household": membership.household, "chores": chores, "is_owner": is_owner},
    )


@login_required
def chore_review_adjust(request, chore_id):
    """Owner review adjustment on a completed chore (#5, §7, §17.9-§17.12).

    Decision (documented per the issue's explicit ask): the owner MAY submit
    more than one adjustment per completion within the 24h window — nothing
    in plan.md forbids it, and an owner correcting their own earlier
    adjustment is a reasonable case. To prevent a string of small
    adjustments from bypassing the ±50%-of-`chore.points` ceiling, the cap
    below is enforced on the *cumulative* total of all `review_adjustment`
    events for this chore's completion (existing total + this delta), not
    just on the individual delta.
    """
    membership = get_active_membership(request.user)
    if not membership or membership.role != Membership.Role.OWNER:
        return redirect("chore_list")
    if request.method != "POST":
        return redirect("chore_list")

    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )
    if chore.status != Chore.Status.COMPLETED or chore.assigned_to_id is None:
        messages.error(request, f"'{chore.name}' isn't completed yet.")
        return redirect("chore_list")

    completion_event = (
        chore.point_events.filter(kind=PointEvent.Kind.COMPLETION)
        .order_by("-created_at")
        .first()
    )
    if completion_event is None or completion_event.review_deadline is None:
        messages.error(request, f"'{chore.name}' has no completion to review.")
        return redirect("chore_list")

    # Mandatory server-side lock (§17.12) — not just a hidden/disabled
    # button. This is the same lazy, read/submit-time check the rest of the
    # app uses for due-date-triggered state (no scheduler involved): the
    # deadline is simply compared against `now()` at submit time.
    if timezone.now() >= completion_event.review_deadline:
        messages.error(
            request,
            f"The 24-hour review window for '{chore.name}' has passed; "
            "its points are locked.",
        )
        return redirect("chore_list")

    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "A reason is required to adjust points.")
        return redirect("chore_list")

    try:
        delta = int(request.POST.get("delta", ""))
    except (TypeError, ValueError):
        messages.error(request, "Enter a whole number of points to adjust by.")
        return redirect("chore_list")

    if delta == 0:
        messages.error(request, "Enter a non-zero point adjustment.")
        return redirect("chore_list")

    # ±50% of the chore's *original* point value (§7, §17.10) — floored
    # (integer division) so an odd point value (e.g. 15 -> 7.5) never lets
    # the cap round *up* past the stated 50% ceiling.
    cap = chore.points // 2

    existing_total = (
        chore.point_events.filter(kind=PointEvent.Kind.REVIEW_ADJUSTMENT).aggregate(
            total=Sum("points")
        )["total"]
        or 0
    )
    new_total = existing_total + delta
    if abs(new_total) > cap:
        messages.error(
            request,
            f"That adjustment would bring the total review adjustment for "
            f"'{chore.name}' to {new_total:+d}, outside the ±{cap} cap "
            f"(50% of {chore.points} points).",
        )
        return redirect("chore_list")

    PointEvent.objects.create(
        member=chore.assigned_to,
        chore=chore,
        kind=PointEvent.Kind.REVIEW_ADJUSTMENT,
        points=delta,
        reason=reason,
        created_by=request.user,
    )
    ActivityLog.objects.create(
        household=membership.household,
        actor=request.user,
        action=ActivityLog.Action.POINTS_ADJUSTED,
        chore=chore,
        member=chore.assigned_to,
        description=(
            f"{request.user} adjusted {chore.assigned_to}'s points for "
            f"'{chore.name}' by {delta:+d} ({reason})."
        ),
    )
    # A review adjustment changes the member's lifetime point total, so
    # re-evaluate achievements (#8) here too — e.g. this adjustment could be
    # what pushes them past the "Point Milestone" threshold.
    evaluate_achievements(chore.assigned_to)

    messages.success(
        request, f"Adjusted {chore.assigned_to}'s points by {delta:+d}."
    )
    return redirect("chore_list")


@login_required
def chore_create(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")

    if request.method == "POST":
        form = ChoreForm(request.POST)
        if form.is_valid():
            chore = form.save(commit=False)
            chore.household = membership.household
            chore.created_by = request.user
            chore.save()
            form.save_recurrence_rule(chore)
            ActivityLog.objects.create(
                household=membership.household,
                actor=request.user,
                action=ActivityLog.Action.CHORE_CREATED,
                chore=chore,
                description=f"{request.user} created chore '{chore.name}'.",
            )
            messages.success(request, "Chore created.")
            return redirect("chore_list")
    else:
        form = ChoreForm()
    return render(
        request, "chores/chore_form.html", {"form": form, "title": "New chore"}
    )


@login_required
def chore_edit(request, chore_id):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )

    if request.method == "POST":
        form = ChoreForm(request.POST, instance=chore)
        if form.is_valid():
            form.save()
            form.save_recurrence_rule(chore)
            ActivityLog.objects.create(
                household=membership.household,
                actor=request.user,
                action=ActivityLog.Action.CHORE_EDITED,
                chore=chore,
                description=f"{request.user} edited chore '{chore.name}'.",
            )
            messages.success(request, "Chore updated.")
            return redirect("chore_list")
    else:
        form = ChoreForm(instance=chore)
    return render(
        request,
        "chores/chore_form.html",
        {"form": form, "title": "Edit chore", "chore": chore},
    )


@login_required
def chore_delete(request, chore_id):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    if request.method != "POST":
        return redirect("chore_list")

    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )
    chore.deleted_at = timezone.now()
    chore.save(update_fields=["deleted_at"])
    ActivityLog.objects.create(
        household=membership.household,
        actor=request.user,
        action=ActivityLog.Action.CHORE_DELETED,
        chore=chore,
        description=f"{request.user} deleted chore '{chore.name}'.",
    )
    messages.success(request, f"'{chore.name}' was deleted.")
    return redirect("chore_list")


@login_required
def chore_claim(request, chore_id):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    if request.method != "POST":
        return redirect("chore_list")

    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )
    if chore.status != Chore.Status.OPEN:
        messages.error(request, f"'{chore.name}' is no longer open for claiming.")
        return redirect("chore_list")

    try:
        with transaction.atomic():
            Claim.objects.create(chore=chore, member=request.user)
    except IntegrityError:
        # unique_together(chore, member) — already claimed, not an error state.
        # The inner atomic() keeps this failure from poisoning the outer
        # request transaction.
        messages.info(request, f"You already claimed '{chore.name}'.")
    else:
        ActivityLog.objects.create(
            household=membership.household,
            actor=request.user,
            action=ActivityLog.Action.CHORE_CLAIMED,
            chore=chore,
            description=f"{request.user} claimed chore '{chore.name}'.",
        )
        messages.success(request, f"You claimed '{chore.name}'.")
    return redirect("chore_list")


@login_required
def chore_unclaim(request, chore_id):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    if request.method != "POST":
        return redirect("chore_list")

    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )
    if chore.status != Chore.Status.OPEN:
        messages.error(request, f"'{chore.name}' is no longer open.")
        return redirect("chore_list")

    # Withdrawing a claim has no point/status effect (§5) and is not logged:
    # there's no ActivityLog.Action for it, and unlike claiming, editing, or
    # deleting a chore, plan.md §13 doesn't list "unclaimed" among the
    # required audit events.
    deleted_count, _ = Claim.objects.filter(chore=chore, member=request.user).delete()
    if deleted_count:
        messages.success(request, f"You withdrew your claim on '{chore.name}'.")
    return redirect("chore_list")


@login_required
def chore_complete(request, chore_id):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    if request.method != "POST":
        return redirect("chore_list")

    chore = get_object_or_404(
        Chore,
        id=chore_id,
        household=membership.household,
        deleted_at__isnull=True,
    )
    # Only the current assignee, and only while assigned (#4). This mirrors
    # the "no action at all" gating in chore_list.html: the button is only
    # rendered for the assignee on an `assigned` chore, but the view enforces
    # it independently since a POST could be crafted directly.
    if chore.status != Chore.Status.ASSIGNED or chore.assigned_to_id != request.user.id:
        messages.error(request, f"You can't mark '{chore.name}' complete right now.")
        return redirect("chore_list")

    now = timezone.now()
    chore.status = Chore.Status.COMPLETED
    chore.completed_at = now
    chore.save(update_fields=["status", "completed_at"])

    # No automatic late-completion penalty (§16): full points are awarded
    # here regardless of whether `due_date` has already passed, as long as
    # fail_overdue() hasn't reopened the chore first — and it can't have,
    # since that requires `status == assigned` to already be false.
    PointEvent.objects.create(
        member=chore.assigned_to,
        chore=chore,
        kind=PointEvent.Kind.COMPLETION,
        points=chore.points,
        created_by=request.user,
        review_deadline=now + timedelta(hours=24),
    )
    ActivityLog.objects.create(
        household=membership.household,
        actor=request.user,
        action=ActivityLog.Action.CHORE_COMPLETED,
        chore=chore,
        member=chore.assigned_to,
        description=(
            f"{request.user} completed '{chore.name}' and earned "
            f"{chore.points} points."
        ),
    )
    # Recurring next-occurrence creation is #1's hook — called, not
    # reimplemented.
    chore.create_next_occurrence()

    # Evaluated right after the triggering event (#8), not via a poll:
    # awards any newly-met achievement (e.g. "First Chore", "On a Roll") as
    # a badge only — no point effect.
    evaluate_achievements(chore.assigned_to)

    messages.success(
        request, f"'{chore.name}' marked complete — you earned {chore.points} points."
    )
    return redirect("chore_list")


@login_required
def chore_archive(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    chores = membership.household.chores.filter(deleted_at__isnull=False)
    return render(request, "chores/chore_archive.html", {"chores": chores})


@login_required
def activity_history(request):
    """Paginated, browsable `ActivityLog` history for the household (#9,
    §13).

    Newest-first ordering comes from `ActivityLog.Meta.ordering` (already
    `["-created_at"]`), so no explicit `order_by` is needed here.

    An entry about a since-soft-deleted chore or since-removed member still
    renders correctly with no special-casing: chores are only ever
    soft-deleted (`Chore.deleted_at`, `Chore` rows are never actually
    removed from the DB) and members are only ever deactivated
    (`Membership.removed_at`, their `User` row is never deleted either), so
    `ActivityLog.chore`/`.actor`/`.member` keep resolving to the real row
    regardless — `chore`'s `on_delete=SET_NULL` only matters for a `Chore`
    row that's actually deleted, which this app never does. Entries are
    never filtered by `chore.deleted_at` or by membership status.

    Paginated at `ACTIVITY_LOG_PAGE_SIZE` (50) entries per page via
    `Paginator`, per the issue's ask to bound an otherwise-unbounded log.
    """
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    household = membership.household
    entries = household.activity_logs.select_related("actor", "chore", "member")
    paginator = Paginator(entries, ACTIVITY_LOG_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "chores/activity_history.html",
        {"household": household, "page_obj": page_obj},
    )


@login_required
def regenerate_invite(request):
    membership = get_active_membership(request.user)
    if not membership or membership.role != Membership.Role.OWNER:
        return redirect("home")
    if request.method == "POST":
        membership.household.invite_code = generate_invite_code()
        membership.household.save(update_fields=["invite_code"])
        messages.success(request, "Invite code regenerated.")
    return redirect("members")
