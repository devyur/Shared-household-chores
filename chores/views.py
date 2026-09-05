from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ChoreForm, HouseholdCreateForm, HouseholdJoinForm, SignUpForm
from .models import ActivityLog, Chore, Claim, Household, Membership, generate_invite_code


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
        return render(
            request,
            "chores/home.html",
            {"household": household, "membership": membership, "members": members},
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
def chore_list(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    chores = membership.household.chores.filter(deleted_at__isnull=True)
    return render(
        request,
        "chores/chore_list.html",
        {"household": membership.household, "chores": chores},
    )


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
def chore_archive(request):
    membership = get_active_membership(request.user)
    if not membership:
        return redirect("home")
    chores = membership.household.chores.filter(deleted_at__isnull=False)
    return render(request, "chores/chore_archive.html", {"chores": chores})


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
