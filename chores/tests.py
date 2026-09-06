from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ActivityLog,
    Chore,
    Claim,
    Household,
    Membership,
    PointEvent,
    RecurrenceRule,
    current_point_total,
    failure_penalty_points,
)

STRONG_PASSWORD = "aVeryStrongPass1!"


def create_user(username, password=STRONG_PASSWORD):
    return User.objects.create_user(username=username, password=password)


def create_household_with_owner(owner, name="Smith House"):
    household = Household.objects.create(name=name)
    Membership.objects.create(
        household=household, user=owner, role=Membership.Role.OWNER
    )
    return household


def add_member(household, user):
    return Membership.objects.create(
        household=household, user=user, role=Membership.Role.MEMBER
    )


class HouseholdModelTests(TestCase):
    def test_invite_code_is_generated_and_unique(self):
        h1 = Household.objects.create(name="House One")
        h2 = Household.objects.create(name="House Two")

        self.assertTrue(h1.invite_code)
        self.assertNotEqual(h1.invite_code, h2.invite_code)


class MembershipModelTests(TestCase):
    def test_is_active_reflects_removed_at(self):
        user = create_user("alice")
        household = create_household_with_owner(user)
        membership = Membership.objects.get(household=household, user=user)

        self.assertTrue(membership.is_active)

        membership.removed_at = timezone.now()
        membership.save()

        self.assertFalse(membership.is_active)


class ChoreModelTests(TestCase):
    def test_is_deleted_reflects_deleted_at(self):
        user = create_user("alice")
        household = create_household_with_owner(user)
        chore = Chore.objects.create(
            household=household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )

        self.assertFalse(chore.is_deleted)

        chore.deleted_at = timezone.now()
        chore.save()

        self.assertTrue(chore.is_deleted)


class ChoreCreateNextOccurrenceTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)

    def make_chore(self, **overrides):
        defaults = dict(
            household=self.household,
            name="Take out trash",
            description="Bins to the curb",
            points=10,
            due_date=timezone.localdate(),
            is_recurring=True,
            created_by=self.alice,
        )
        defaults.update(overrides)
        return Chore.objects.create(**defaults)

    def test_non_recurring_chore_spawns_nothing(self):
        chore = self.make_chore(is_recurring=False)

        self.assertIsNone(chore.create_next_occurrence())
        self.assertEqual(Chore.objects.count(), 1)

    def test_recurring_chore_without_saved_rule_spawns_nothing(self):
        chore = self.make_chore()

        self.assertIsNone(chore.create_next_occurrence())
        self.assertEqual(Chore.objects.count(), 1)

    def test_calendar_based_next_due_from_original_due_date(self):
        chore = self.make_chore(due_date=timezone.datetime(2026, 1, 1).date())
        RecurrenceRule.objects.create(
            chore=chore, rule_type=RecurrenceRule.RuleType.CALENDAR, interval_days=7
        )
        chore.status = Chore.Status.COMPLETED
        chore.completed_at = timezone.datetime(2026, 1, 5, tzinfo=timezone.get_current_timezone())
        chore.save()

        next_chore = chore.create_next_occurrence()

        self.assertIsNotNone(next_chore)
        self.assertEqual(next_chore.due_date, timezone.datetime(2026, 1, 8).date())

    def test_completion_based_next_due_from_completion_date(self):
        chore = self.make_chore(due_date=timezone.datetime(2026, 1, 1).date())
        RecurrenceRule.objects.create(
            chore=chore, rule_type=RecurrenceRule.RuleType.COMPLETION, interval_days=3
        )
        chore.status = Chore.Status.COMPLETED
        chore.completed_at = timezone.datetime(2026, 1, 10, tzinfo=timezone.get_current_timezone())
        chore.save()

        next_chore = chore.create_next_occurrence()

        self.assertEqual(next_chore.due_date, timezone.datetime(2026, 1, 13).date())

    def test_next_occurrence_copies_fields_and_rule_and_starts_open(self):
        chore = self.make_chore()
        RecurrenceRule.objects.create(
            chore=chore, rule_type=RecurrenceRule.RuleType.CALENDAR, interval_days=14
        )

        next_chore = chore.create_next_occurrence()

        self.assertEqual(next_chore.household, chore.household)
        self.assertEqual(next_chore.name, chore.name)
        self.assertEqual(next_chore.description, chore.description)
        self.assertEqual(next_chore.points, chore.points)
        self.assertEqual(next_chore.created_by, chore.created_by)
        self.assertEqual(next_chore.status, Chore.Status.OPEN)
        self.assertTrue(next_chore.is_recurring)
        self.assertEqual(next_chore.recurrence_rule.rule_type, RecurrenceRule.RuleType.CALENDAR)
        self.assertEqual(next_chore.recurrence_rule.interval_days, 14)

    def test_only_one_next_occurrence_created_per_call(self):
        chore = self.make_chore()
        RecurrenceRule.objects.create(
            chore=chore, rule_type=RecurrenceRule.RuleType.CALENDAR, interval_days=7
        )

        chore.create_next_occurrence()

        self.assertEqual(Chore.objects.count(), 2)

    def test_next_occurrence_is_logged_as_system_triggered(self):
        chore = self.make_chore()
        RecurrenceRule.objects.create(
            chore=chore, rule_type=RecurrenceRule.RuleType.CALENDAR, interval_days=7
        )

        next_chore = chore.create_next_occurrence()

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_CREATED,
                chore=next_chore,
                actor__isnull=True,
            ).exists()
        )


class CurrentPointTotalTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")

    def test_member_with_no_point_events_has_zero_total(self):
        self.assertEqual(current_point_total(self.alice), 0)

    def test_total_sums_signed_point_events(self):
        PointEvent.objects.create(
            member=self.alice, kind=PointEvent.Kind.COMPLETION, points=20
        )
        PointEvent.objects.create(
            member=self.alice, kind=PointEvent.Kind.FAILURE_PENALTY, points=-10
        )
        PointEvent.objects.create(
            member=self.alice, kind=PointEvent.Kind.MANUAL_ADJUSTMENT, points=5
        )

        self.assertEqual(current_point_total(self.alice), 15)

    def test_total_can_go_negative(self):
        PointEvent.objects.create(
            member=self.alice, kind=PointEvent.Kind.FAILURE_PENALTY, points=-10
        )

        self.assertEqual(current_point_total(self.alice), -10)

    def test_total_only_counts_the_given_member(self):
        bob = create_user("bob")
        PointEvent.objects.create(
            member=self.alice, kind=PointEvent.Kind.COMPLETION, points=100
        )

        self.assertEqual(current_point_total(bob), 0)


class ChoreAutoAssignDueTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.carol = create_user("carol")
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)

    def make_chore(self, **overrides):
        defaults = dict(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )
        defaults.update(overrides)
        return Chore.objects.create(**defaults)

    def run_auto_assign(self):
        self.household.chores.filter(deleted_at__isnull=True).auto_assign_due()

    def test_due_open_chore_with_one_claim_is_assigned_to_the_claimant(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.ASSIGNED)
        self.assertEqual(chore.assigned_to, self.bob)

    def test_assigned_to_claimant_with_highest_current_points(self):
        chore = self.make_chore()
        PointEvent.objects.create(
            member=self.bob, kind=PointEvent.Kind.COMPLETION, points=10
        )
        PointEvent.objects.create(
            member=self.carol, kind=PointEvent.Kind.COMPLETION, points=50
        )
        Claim.objects.create(chore=chore, member=self.bob)
        Claim.objects.create(chore=chore, member=self.carol)

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.assigned_to, self.carol)

    def test_tie_in_points_is_broken_by_earliest_claim(self):
        chore = self.make_chore()
        # Equal points (both zero) - earliest claim should win.
        first_claim = Claim.objects.create(chore=chore, member=self.carol)
        Claim.objects.filter(pk=first_claim.pk).update(
            claimed_at=timezone.now() - timedelta(days=1)
        )
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.assigned_to, self.carol)

    def test_due_open_chore_with_zero_claims_stays_open_and_unassigned(self):
        chore = self.make_chore()

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertIsNone(chore.assigned_to)

    def test_assignment_is_logged_as_system_triggered(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_ASSIGNED,
                chore=chore,
                actor__isnull=True,
                member=self.bob,
            ).exists()
        )

    def test_assignment_does_not_delete_other_claims(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)
        Claim.objects.create(chore=chore, member=self.carol)

        self.run_auto_assign()

        self.assertEqual(chore.claims.count(), 2)

    def test_reevaluating_an_assigned_chore_is_a_noop(self):
        chore = self.make_chore(
            status=Chore.Status.ASSIGNED, assigned_to=self.bob
        )
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()
        self.run_auto_assign()

        self.assertEqual(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_ASSIGNED,
                chore=chore,
            ).count(),
            0,
        )

    def test_reevaluating_a_completed_chore_is_a_noop(self):
        chore = self.make_chore(
            status=Chore.Status.COMPLETED,
            assigned_to=self.bob,
            completed_at=timezone.now(),
        )
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.COMPLETED)
        self.assertFalse(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_ASSIGNED,
                chore=chore,
            ).exists()
        )

    def test_running_auto_assign_twice_creates_only_one_log_entry(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()
        self.run_auto_assign()

        self.assertEqual(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_ASSIGNED,
                chore=chore,
            ).count(),
            1,
        )

    def test_future_due_date_chore_is_not_assigned(self):
        chore = self.make_chore(
            due_date=timezone.localdate() + timedelta(days=5)
        )
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertIsNone(chore.assigned_to)

    def test_pushing_due_date_to_the_future_before_assignment_delays_it(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)

        chore.due_date = timezone.localdate() + timedelta(days=5)
        chore.save()

        self.run_auto_assign()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertIsNone(chore.assigned_to)


class ChoreListAutoAssignViewTests(TestCase):
    def test_visiting_chore_list_triggers_auto_assignment(self):
        alice = create_user("alice")
        household = create_household_with_owner(alice)
        bob = create_user("bob")
        add_member(household, bob)
        chore = Chore.objects.create(
            household=household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )
        Claim.objects.create(chore=chore, member=bob)
        self.client.force_login(alice)

        self.client.get(reverse("chore_list"))

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.ASSIGNED)
        self.assertEqual(chore.assigned_to, bob)


class SignupViewTests(TestCase):
    def test_signup_creates_user_and_redirects_to_create_household(self):
        response = self.client.post(
            reverse("signup"),
            {
                "username": "alice",
                "password1": STRONG_PASSWORD,
                "password2": STRONG_PASSWORD,
            },
            follow=True,
        )

        self.assertTrue(User.objects.filter(username="alice").exists())
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertRedirects(response, reverse("household_create"))


class HouseholdCreateViewTests(TestCase):
    def test_creating_household_makes_owner_and_logs_activity(self):
        alice = create_user("alice")
        self.client.force_login(alice)

        response = self.client.post(
            reverse("household_create"), {"name": "Smith House"}, follow=True
        )

        household = Household.objects.get(name="Smith House")
        membership = Membership.objects.get(household=household, user=alice)
        self.assertEqual(membership.role, Membership.Role.OWNER)
        self.assertRedirects(response, reverse("home"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=household,
                action=ActivityLog.Action.MEMBER_JOINED,
                member=alice,
            ).exists()
        )

    def test_blocked_when_household_already_exists(self):
        owner = create_user("alice")
        create_household_with_owner(owner)
        bob = create_user("bob")
        self.client.force_login(bob)

        response = self.client.get(reverse("household_create"))

        self.assertRedirects(response, reverse("household_join"))
        self.assertEqual(Household.objects.count(), 1)

    def test_blocked_for_user_with_existing_membership(self):
        alice = create_user("alice")
        create_household_with_owner(alice)
        self.client.force_login(alice)

        response = self.client.get(reverse("household_create"))

        self.assertRedirects(response, reverse("home"))


class HouseholdJoinViewTests(TestCase):
    def setUp(self):
        self.owner = create_user("alice")
        self.household = create_household_with_owner(self.owner)
        self.bob = create_user("bob")
        self.client.force_login(self.bob)

    def test_wrong_invite_code_shows_error_and_creates_no_membership(self):
        response = self.client.post(
            reverse("household_join"), {"invite_code": "WRONGCODE"}
        )

        self.assertContains(response, "Invalid invite code")
        self.assertFalse(
            Membership.objects.filter(household=self.household, user=self.bob).exists()
        )

    def test_correct_invite_code_creates_membership_and_logs(self):
        response = self.client.post(
            reverse("household_join"),
            {"invite_code": self.household.invite_code},
            follow=True,
        )

        membership = Membership.objects.get(household=self.household, user=self.bob)
        self.assertEqual(membership.role, Membership.Role.MEMBER)
        self.assertRedirects(response, reverse("home"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.MEMBER_JOINED,
                member=self.bob,
            ).exists()
        )

    def test_blocked_for_user_with_existing_membership(self):
        add_member(self.household, self.bob)

        response = self.client.get(reverse("household_join"))

        self.assertRedirects(response, reverse("home"))


class HomeViewRedirectTests(TestCase):
    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('home')}"
        )

    def test_no_membership_and_no_household_redirects_to_create(self):
        alice = create_user("alice")
        self.client.force_login(alice)

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("household_create"))

    def test_no_membership_but_household_exists_redirects_to_join(self):
        owner = create_user("alice")
        create_household_with_owner(owner)
        bob = create_user("bob")
        self.client.force_login(bob)

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("household_join"))

    def test_active_membership_renders_household_page(self):
        alice = create_user("alice")
        household = create_household_with_owner(alice)
        self.client.force_login(alice)

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, household.name)


class MembersViewTests(TestCase):
    def test_is_owner_flag_true_for_owner_false_for_member(self):
        alice = create_user("alice")
        household = create_household_with_owner(alice)
        bob = create_user("bob")
        add_member(household, bob)

        self.client.force_login(alice)
        response = self.client.get(reverse("members"))
        self.assertTrue(response.context["is_owner"])

        self.client.force_login(bob)
        response = self.client.get(reverse("members"))
        self.assertFalse(response.context["is_owner"])


class RemoveMemberViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.bob_membership = add_member(self.household, self.bob)

    def test_non_owner_cannot_remove_member(self):
        carol = create_user("carol")
        add_member(self.household, carol)
        self.client.force_login(self.bob)

        self.client.post(
            reverse("remove_member", args=[carol.id]), follow=True
        )

        carol_membership = Membership.objects.get(household=self.household, user=carol)
        self.assertTrue(carol_membership.is_active)

    def test_owner_removes_member_reopens_chore_drops_open_claims_and_logs(self):
        assigned_chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        open_chore_with_claim = Chore.objects.create(
            household=self.household,
            name="Take out trash",
            points=5,
            due_date=timezone.localdate(),
        )
        Claim.objects.create(chore=open_chore_with_claim, member=self.bob)

        completed_chore = Chore.objects.create(
            household=self.household,
            name="Mow lawn",
            points=10,
            due_date=timezone.localdate(),
            status=Chore.Status.COMPLETED,
            assigned_to=self.bob,
        )

        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("remove_member", args=[self.bob.id]), follow=True
        )

        self.bob_membership.refresh_from_db()
        self.assertIsNotNone(self.bob_membership.removed_at)

        assigned_chore.refresh_from_db()
        self.assertEqual(assigned_chore.status, Chore.Status.OPEN)
        self.assertIsNone(assigned_chore.assigned_to)

        self.assertFalse(
            Claim.objects.filter(chore=open_chore_with_claim, member=self.bob).exists()
        )

        completed_chore.refresh_from_db()
        self.assertEqual(completed_chore.assigned_to, self.bob)

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.MEMBER_REMOVED,
                member=self.bob,
            ).exists()
        )
        self.assertRedirects(response, reverse("members"))

    def test_removing_one_claimant_leaves_other_claimants_and_chore_open(self):
        carol = create_user("carol")
        add_member(self.household, carol)
        chore = Chore.objects.create(
            household=self.household,
            name="Take out trash",
            points=5,
            due_date=timezone.localdate(),
        )
        Claim.objects.create(chore=chore, member=self.bob)
        Claim.objects.create(chore=chore, member=carol)

        self.client.force_login(self.alice)
        self.client.post(reverse("remove_member", args=[self.bob.id]), follow=True)

        self.assertFalse(Claim.objects.filter(chore=chore, member=self.bob).exists())
        self.assertTrue(Claim.objects.filter(chore=chore, member=carol).exists())
        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)

    def test_owner_cannot_remove_self(self):
        self.client.force_login(self.alice)

        self.client.post(reverse("remove_member", args=[self.alice.id]), follow=True)

        owner_membership = Membership.objects.get(household=self.household, user=self.alice)
        self.assertTrue(owner_membership.is_active)


class ChoreCreateViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        add_member(self.household, self.bob)

    def test_any_member_can_create_a_chore_and_it_is_logged(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_create"),
            {
                "name": "Clean kitchen",
                "description": "Wipe counters and sweep",
                "points": 20,
                "due_date": timezone.localdate(),
                "is_recurring": False,
            },
            follow=True,
        )

        chore = Chore.objects.get(household=self.household, name="Clean kitchen")
        self.assertEqual(chore.created_by, self.bob)
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertRedirects(response, reverse("chore_list"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_CREATED,
                chore=chore,
            ).exists()
        )

    def test_user_without_membership_is_redirected(self):
        carol = create_user("carol")
        self.client.force_login(carol)

        response = self.client.post(
            reverse("chore_create"),
            {
                "name": "Clean kitchen",
                "points": 20,
                "due_date": timezone.localdate(),
            },
        )

        self.assertRedirects(
            response, reverse("home"), fetch_redirect_response=False
        )
        self.assertFalse(Chore.objects.filter(name="Clean kitchen").exists())

    def test_recurring_chore_requires_rule_fields(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_create"),
            {
                "name": "Water plants",
                "points": 5,
                "due_date": timezone.localdate(),
                "is_recurring": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Chore.objects.filter(name="Water plants").exists())
        self.assertFormError(
            response.context["form"],
            "recurrence_rule_type",
            "Required for a recurring chore.",
        )
        self.assertFormError(
            response.context["form"],
            "recurrence_interval_days",
            "Required for a recurring chore.",
        )

    def test_recurring_chore_saves_its_recurrence_rule(self):
        self.client.force_login(self.bob)

        self.client.post(
            reverse("chore_create"),
            {
                "name": "Water plants",
                "points": 5,
                "due_date": timezone.localdate(),
                "is_recurring": True,
                "recurrence_rule_type": RecurrenceRule.RuleType.CALENDAR,
                "recurrence_interval_days": 3,
            },
            follow=True,
        )

        chore = Chore.objects.get(name="Water plants")
        self.assertEqual(chore.recurrence_rule.rule_type, RecurrenceRule.RuleType.CALENDAR)
        self.assertEqual(chore.recurrence_rule.interval_days, 3)


class ChoreEditViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        add_member(self.household, self.bob)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        Claim.objects.create(chore=self.chore, member=self.bob)

    def test_any_member_can_edit_without_disturbing_claim_or_assignment(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_edit", args=[self.chore.id]),
            {
                "name": "Deep clean kitchen",
                "points": 30,
                "due_date": self.chore.due_date,
                "is_recurring": False,
            },
            follow=True,
        )

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.name, "Deep clean kitchen")
        self.assertEqual(self.chore.points, 30)
        self.assertEqual(self.chore.status, Chore.Status.ASSIGNED)
        self.assertEqual(self.chore.assigned_to, self.bob)
        self.assertTrue(Claim.objects.filter(chore=self.chore, member=self.bob).exists())
        self.assertRedirects(response, reverse("chore_list"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_EDITED,
                chore=self.chore,
            ).exists()
        )

    def test_deleted_chore_cannot_be_edited(self):
        self.chore.deleted_at = timezone.now()
        self.chore.save()
        self.client.force_login(self.bob)

        response = self.client.get(reverse("chore_edit", args=[self.chore.id]))

        self.assertEqual(response.status_code, 404)

    def test_unchecking_is_recurring_removes_its_recurrence_rule(self):
        RecurrenceRule.objects.create(
            chore=self.chore,
            rule_type=RecurrenceRule.RuleType.CALENDAR,
            interval_days=7,
        )
        self.client.force_login(self.bob)

        self.client.post(
            reverse("chore_edit", args=[self.chore.id]),
            {
                "name": self.chore.name,
                "points": self.chore.points,
                "due_date": self.chore.due_date,
                "is_recurring": False,
            },
            follow=True,
        )

        self.assertFalse(
            RecurrenceRule.objects.filter(chore=self.chore).exists()
        )


class ChoreDeleteViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        add_member(self.household, self.bob)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )

    def test_any_member_can_soft_delete_and_it_is_logged(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_delete", args=[self.chore.id]), follow=True
        )

        self.chore.refresh_from_db()
        self.assertTrue(self.chore.is_deleted)
        self.assertRedirects(response, reverse("chore_list"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_DELETED,
                chore=self.chore,
            ).exists()
        )

    def test_deleted_chore_disappears_from_list_but_stays_in_archive(self):
        self.client.force_login(self.bob)
        self.client.post(reverse("chore_delete", args=[self.chore.id]))

        list_response = self.client.get(reverse("chore_list"))
        archive_response = self.client.get(reverse("chore_archive"))

        self.assertNotContains(list_response, "<strong>Clean kitchen</strong>")
        self.assertContains(archive_response, "<strong>Clean kitchen</strong>")


class RegenerateInviteViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        add_member(self.household, self.bob)

    def test_non_owner_cannot_regenerate(self):
        original_code = self.household.invite_code
        self.client.force_login(self.bob)

        self.client.post(reverse("regenerate_invite"), follow=True)

        self.household.refresh_from_db()
        self.assertEqual(self.household.invite_code, original_code)

    def test_owner_can_regenerate_invite_code(self):
        original_code = self.household.invite_code
        self.client.force_login(self.alice)

        self.client.post(reverse("regenerate_invite"), follow=True)

        self.household.refresh_from_db()
        self.assertNotEqual(self.household.invite_code, original_code)


class ChoreClaimViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.carol = create_user("carol")
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )

    def test_active_member_can_claim_an_open_chore(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_claim", args=[self.chore.id]), follow=True
        )

        self.assertTrue(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )
        self.assertRedirects(response, reverse("chore_list"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_CLAIMED,
                chore=self.chore,
                actor=self.bob,
            ).exists()
        )

    def test_claiming_does_not_require_due_date_to_have_arrived(self):
        future_chore = Chore.objects.create(
            household=self.household,
            name="Deep clean fridge",
            points=15,
            due_date=timezone.localdate() + timedelta(days=30),
        )
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_claim", args=[future_chore.id]), follow=True)

        self.assertTrue(
            Claim.objects.filter(chore=future_chore, member=self.bob).exists()
        )

    def test_multiple_members_can_claim_the_same_chore(self):
        self.client.force_login(self.bob)
        self.client.post(reverse("chore_claim", args=[self.chore.id]))
        self.client.force_login(self.carol)
        self.client.post(reverse("chore_claim", args=[self.chore.id]))

        self.assertEqual(self.chore.claims.count(), 2)

    def test_claiming_twice_does_not_error_and_stays_a_single_claim(self):
        self.client.force_login(self.bob)
        self.client.post(reverse("chore_claim", args=[self.chore.id]))

        response = self.client.post(
            reverse("chore_claim", args=[self.chore.id]), follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Claim.objects.filter(chore=self.chore, member=self.bob).count(), 1
        )
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("already claimed" in m for m in messages))

    def test_claiming_does_not_change_status_assignment_or_points(self):
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_claim", args=[self.chore.id]))

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.OPEN)
        self.assertIsNone(self.chore.assigned_to)
        self.assertFalse(self.bob.point_events.exists())

    def test_claiming_is_rejected_once_chore_is_no_longer_open(self):
        self.chore.status = Chore.Status.ASSIGNED
        self.chore.assigned_to = self.carol
        self.chore.save()
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_claim", args=[self.chore.id]), follow=True)

        self.assertFalse(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )

    def test_claim_button_hidden_once_chore_is_no_longer_open(self):
        self.chore.status = Chore.Status.ASSIGNED
        self.chore.assigned_to = self.carol
        self.chore.save()
        self.client.force_login(self.bob)

        response = self.client.get(reverse("chore_list"))

        self.assertNotContains(
            response, f'action="{reverse("chore_claim", args=[self.chore.id])}"'
        )

    def test_get_request_does_not_claim(self):
        self.client.force_login(self.bob)

        self.client.get(reverse("chore_claim", args=[self.chore.id]))

        self.assertFalse(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )

    def test_user_without_membership_cannot_claim(self):
        outsider = create_user("dave")
        self.client.force_login(outsider)

        response = self.client.post(reverse("chore_claim", args=[self.chore.id]))

        self.assertRedirects(
            response, reverse("home"), fetch_redirect_response=False
        )
        self.assertFalse(
            Claim.objects.filter(chore=self.chore, member=outsider).exists()
        )

    def test_chore_list_shows_claimants(self):
        Claim.objects.create(chore=self.chore, member=self.bob)
        self.client.force_login(self.carol)

        response = self.client.get(reverse("chore_list"))

        self.assertContains(response, "bob")


class ChoreUnclaimViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.carol = create_user("carol")
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
        )
        Claim.objects.create(chore=self.chore, member=self.bob)

    def test_member_can_withdraw_own_claim_while_open(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_unclaim", args=[self.chore.id]), follow=True
        )

        self.assertFalse(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )
        self.assertRedirects(response, reverse("chore_list"))

    def test_withdrawing_does_not_change_status_assignment_or_points(self):
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_unclaim", args=[self.chore.id]))

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.OPEN)
        self.assertIsNone(self.chore.assigned_to)
        self.assertFalse(self.bob.point_events.exists())

    def test_withdrawing_is_not_logged(self):
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_unclaim", args=[self.chore.id]))

        self.assertFalse(
            ActivityLog.objects.filter(household=self.household, chore=self.chore).exists()
        )

    def test_withdrawing_does_not_affect_other_members_claims(self):
        Claim.objects.create(chore=self.chore, member=self.carol)
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_unclaim", args=[self.chore.id]))

        self.assertTrue(
            Claim.objects.filter(chore=self.chore, member=self.carol).exists()
        )

    def test_cannot_withdraw_once_chore_is_no_longer_open(self):
        self.chore.status = Chore.Status.ASSIGNED
        self.chore.assigned_to = self.bob
        self.chore.save()
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_unclaim", args=[self.chore.id]), follow=True)

        self.assertTrue(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )

    def test_get_request_does_not_withdraw(self):
        self.client.force_login(self.bob)

        self.client.get(reverse("chore_unclaim", args=[self.chore.id]))

        self.assertTrue(
            Claim.objects.filter(chore=self.chore, member=self.bob).exists()
        )


class FailurePenaltyPointsTests(TestCase):
    def test_even_points_halve_exactly(self):
        self.assertEqual(failure_penalty_points(20), 10)

    def test_odd_points_round_half_up(self):
        # 15 * 0.5 = 7.5 -> rounds up to 8, not down to 7.
        self.assertEqual(failure_penalty_points(15), 8)
        self.assertEqual(failure_penalty_points(21), 11)
        self.assertEqual(failure_penalty_points(1), 1)

    def test_zero_points_has_zero_penalty(self):
        self.assertEqual(failure_penalty_points(0), 0)


class ChoreFailOverdueTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.carol = create_user("carol")
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)

    def make_chore(self, **overrides):
        defaults = dict(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate() - timedelta(days=1),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        defaults.update(overrides)
        return Chore.objects.create(**defaults)

    def run_fail_overdue(self):
        self.household.chores.filter(deleted_at__isnull=True).fail_overdue()

    def test_overdue_assigned_chore_is_reopened_and_unassigned(self):
        chore = self.make_chore()

        self.run_fail_overdue()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertIsNone(chore.assigned_to)

    def test_penalty_point_event_is_created_for_former_assignee(self):
        chore = self.make_chore(points=15)

        self.run_fail_overdue()

        event = PointEvent.objects.get(chore=chore, kind=PointEvent.Kind.FAILURE_PENALTY)
        self.assertEqual(event.member, self.bob)
        self.assertEqual(event.points, -8)  # round-half-up(15 * 0.5) == 8

    def test_penalty_can_take_points_negative(self):
        chore = self.make_chore(points=20)

        self.run_fail_overdue()

        self.assertEqual(current_point_total(self.bob), -10)

    def test_failure_is_logged_with_no_actor(self):
        chore = self.make_chore()

        self.run_fail_overdue()

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_FAILED,
                chore=chore,
                actor__isnull=True,
                member=self.bob,
            ).exists()
        )

    def test_prior_claims_are_cleared_on_failure(self):
        chore = self.make_chore()
        Claim.objects.create(chore=chore, member=self.bob)
        Claim.objects.create(chore=chore, member=self.carol)

        self.run_fail_overdue()

        self.assertEqual(chore.claims.count(), 0)

    def test_open_chore_past_due_is_left_alone(self):
        chore = self.make_chore(status=Chore.Status.OPEN, assigned_to=None)
        Claim.objects.create(chore=chore, member=self.bob)

        self.run_fail_overdue()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertFalse(
            PointEvent.objects.filter(chore=chore, kind=PointEvent.Kind.FAILURE_PENALTY).exists()
        )

    def test_completed_chore_past_due_is_never_touched(self):
        chore = self.make_chore(
            status=Chore.Status.COMPLETED, completed_at=timezone.now()
        )

        self.run_fail_overdue()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.COMPLETED)
        self.assertFalse(
            PointEvent.objects.filter(chore=chore, kind=PointEvent.Kind.FAILURE_PENALTY).exists()
        )

    def test_assigned_chore_due_today_is_not_yet_failed(self):
        chore = self.make_chore(due_date=timezone.localdate())

        self.run_fail_overdue()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.ASSIGNED)

    def test_assigned_chore_due_in_future_is_not_failed(self):
        chore = self.make_chore(due_date=timezone.localdate() + timedelta(days=3))

        self.run_fail_overdue()

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.ASSIGNED)

    def test_reevaluating_an_already_reopened_chore_does_not_repenalize(self):
        chore = self.make_chore()

        self.run_fail_overdue()
        self.run_fail_overdue()

        self.assertEqual(
            PointEvent.objects.filter(
                chore=chore, kind=PointEvent.Kind.FAILURE_PENALTY
            ).count(),
            1,
        )
        self.assertEqual(
            ActivityLog.objects.filter(
                chore=chore, action=ActivityLog.Action.CHORE_FAILED
            ).count(),
            1,
        )

    def test_multiple_overdue_chores_are_each_failed_independently(self):
        chore1 = self.make_chore(name="Chore one")
        chore2 = self.make_chore(name="Chore two", assigned_to=self.carol)

        self.run_fail_overdue()

        chore1.refresh_from_db()
        chore2.refresh_from_db()
        self.assertEqual(chore1.status, Chore.Status.OPEN)
        self.assertEqual(chore2.status, Chore.Status.OPEN)
        self.assertEqual(
            PointEvent.objects.filter(kind=PointEvent.Kind.FAILURE_PENALTY).count(), 2
        )


class ChoreListLazyEvalOrderingTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        add_member(self.household, self.bob)

    def test_visiting_chore_list_fails_overdue_assigned_chores(self):
        chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate() - timedelta(days=1),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        self.client.force_login(self.alice)

        self.client.get(reverse("chore_list"))

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.OPEN)
        self.assertIsNone(chore.assigned_to)
        self.assertEqual(current_point_total(self.bob), -10)

    def test_a_chore_auto_assigned_this_pass_is_not_failed_in_the_same_pass(self):
        # due_date is already in the past, so both fail_overdue() and
        # auto_assign_due() are eligible to act on it in the same request.
        # fail_overdue() must run first (see chore_list) so this chore -
        # which starts OPEN with a claim, and only becomes ASSIGNED partway
        # through this same request via auto_assign_due() - is not
        # immediately failed afterwards in the same pass.
        chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        Claim.objects.create(chore=chore, member=self.bob)
        self.client.force_login(self.alice)

        self.client.get(reverse("chore_list"))

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.ASSIGNED)
        self.assertEqual(chore.assigned_to, self.bob)
        self.assertFalse(
            PointEvent.objects.filter(
                chore=chore, kind=PointEvent.Kind.FAILURE_PENALTY
            ).exists()
        )

    def test_completed_chore_survives_a_lazy_eval_pass_untouched(self):
        chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate() - timedelta(days=1),
            status=Chore.Status.COMPLETED,
            assigned_to=self.bob,
            completed_at=timezone.now(),
        )
        self.client.force_login(self.alice)

        self.client.get(reverse("chore_list"))

        chore.refresh_from_db()
        self.assertEqual(chore.status, Chore.Status.COMPLETED)
        self.assertEqual(chore.assigned_to, self.bob)


class ChoreCompleteViewTests(TestCase):
    def setUp(self):
        self.alice = create_user("alice")
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")
        self.carol = create_user("carol")
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )

    def test_assignee_can_mark_complete_and_it_is_logged(self):
        self.client.force_login(self.bob)

        response = self.client.post(
            reverse("chore_complete", args=[self.chore.id]), follow=True
        )

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.COMPLETED)
        self.assertIsNotNone(self.chore.completed_at)
        self.assertRedirects(response, reverse("chore_list"))
        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.CHORE_COMPLETED,
                chore=self.chore,
                actor=self.bob,
                member=self.bob,
            ).exists()
        )

    def test_completion_awards_full_points_with_24h_review_deadline(self):
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_complete", args=[self.chore.id]))

        self.chore.refresh_from_db()
        event = PointEvent.objects.get(chore=self.chore, kind=PointEvent.Kind.COMPLETION)
        self.assertEqual(event.member, self.bob)
        self.assertEqual(event.points, 20)
        self.assertEqual(event.created_by, self.bob)
        self.assertEqual(event.review_deadline, self.chore.completed_at + timedelta(hours=24))

    def test_non_assignee_cannot_mark_complete(self):
        self.client.force_login(self.carol)

        self.client.post(reverse("chore_complete", args=[self.chore.id]), follow=True)

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.ASSIGNED)
        self.assertFalse(
            PointEvent.objects.filter(chore=self.chore, kind=PointEvent.Kind.COMPLETION).exists()
        )

    def test_chore_not_in_assigned_status_cannot_be_completed(self):
        self.chore.status = Chore.Status.OPEN
        self.chore.assigned_to = None
        self.chore.save()
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_complete", args=[self.chore.id]), follow=True)

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.OPEN)

    def test_get_request_does_not_complete(self):
        self.client.force_login(self.bob)

        self.client.get(reverse("chore_complete", args=[self.chore.id]))

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.ASSIGNED)

    def test_completing_late_before_failure_eval_still_awards_full_points(self):
        self.chore.due_date = timezone.localdate() - timedelta(days=5)
        self.chore.save()
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_complete", args=[self.chore.id]))

        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.COMPLETED)
        event = PointEvent.objects.get(chore=self.chore, kind=PointEvent.Kind.COMPLETION)
        self.assertEqual(event.points, 20)
        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.FAILURE_PENALTY
            ).exists()
        )

    def test_completing_a_recurring_chore_creates_next_occurrence(self):
        self.chore.is_recurring = True
        self.chore.save()
        RecurrenceRule.objects.create(
            chore=self.chore, rule_type=RecurrenceRule.RuleType.CALENDAR, interval_days=7
        )
        self.client.force_login(self.bob)

        self.client.post(reverse("chore_complete", args=[self.chore.id]))

        self.assertEqual(Chore.objects.count(), 2)
        self.assertTrue(
            Chore.objects.filter(name=self.chore.name, status=Chore.Status.OPEN).exists()
        )

    def test_complete_button_shown_only_to_assignee_of_assigned_chore(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("chore_list"))
        self.assertContains(
            response, f'action="{reverse("chore_complete", args=[self.chore.id])}"'
        )

        self.client.force_login(self.carol)
        response = self.client.get(reverse("chore_list"))
        self.assertNotContains(
            response, f'action="{reverse("chore_complete", args=[self.chore.id])}"'
        )

    def test_complete_button_hidden_once_chore_is_no_longer_assigned(self):
        self.chore.status = Chore.Status.OPEN
        self.chore.assigned_to = None
        self.chore.save()
        self.client.force_login(self.bob)

        response = self.client.get(reverse("chore_list"))

        self.assertNotContains(
            response, f'action="{reverse("chore_complete", args=[self.chore.id])}"'
        )

    def test_user_without_membership_cannot_complete(self):
        outsider = create_user("dave")
        self.client.force_login(outsider)

        response = self.client.post(reverse("chore_complete", args=[self.chore.id]))

        self.assertRedirects(
            response, reverse("home"), fetch_redirect_response=False
        )
        self.chore.refresh_from_db()
        self.assertEqual(self.chore.status, Chore.Status.ASSIGNED)


class ChoreReviewAdjustViewTests(TestCase):
    """#5, §7, §17.9-§17.12: owner review window and point adjustments."""

    def setUp(self):
        self.alice = create_user("alice")  # owner
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")  # assignee/member
        self.carol = create_user("carol")  # other member
        add_member(self.household, self.bob)
        add_member(self.household, self.carol)
        self.chore = Chore.objects.create(
            household=self.household,
            name="Clean kitchen",
            points=20,
            due_date=timezone.localdate(),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        self.client.force_login(self.bob)
        self.client.post(reverse("chore_complete", args=[self.chore.id]))
        self.chore.refresh_from_db()
        self.completion_event = PointEvent.objects.get(
            chore=self.chore, kind=PointEvent.Kind.COMPLETION
        )
        self.client.logout()

    def adjust(self, user, delta, reason="Did a great job"):
        self.client.force_login(user)
        return self.client.post(
            reverse("chore_review_adjust", args=[self.chore.id]),
            {"delta": delta, "reason": reason},
            follow=True,
        )

    # -- Visibility / permissions --------------------------------------

    def test_owner_can_submit_a_valid_adjustment(self):
        self.adjust(self.alice, 5, "Extra thorough")

        event = PointEvent.objects.get(
            chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
        )
        self.assertEqual(event.member, self.bob)
        self.assertEqual(event.points, 5)
        self.assertEqual(event.reason, "Extra thorough")
        self.assertEqual(event.created_by, self.alice)
        # Separate from, not a replacement of, the original award (§7/§17.9).
        self.completion_event.refresh_from_db()
        self.assertEqual(self.completion_event.points, 20)
        self.assertEqual(
            PointEvent.objects.filter(chore=self.chore).count(), 2
        )

    def test_non_owner_member_cannot_submit_adjustment(self):
        self.adjust(self.carol, 5, "Nice try")

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_assignee_cannot_adjust_their_own_completion(self):
        self.adjust(self.bob, 5, "Self review")

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_get_request_does_not_adjust(self):
        self.client.force_login(self.alice)
        self.client.get(reverse("chore_review_adjust", args=[self.chore.id]))

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_adjustment_rejected_for_a_chore_that_is_not_completed(self):
        other = Chore.objects.create(
            household=self.household,
            name="Mow lawn",
            points=10,
            due_date=timezone.localdate(),
            status=Chore.Status.ASSIGNED,
            assigned_to=self.bob,
        )
        self.client.force_login(self.alice)
        self.client.post(
            reverse("chore_review_adjust", args=[other.id]),
            {"delta": 2, "reason": "Premature"},
        )

        self.assertFalse(
            PointEvent.objects.filter(
                chore=other, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_review_adjust_button_shown_only_to_owner(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("chore_list"))
        self.assertContains(
            response,
            f'action="{reverse("chore_review_adjust", args=[self.chore.id])}"',
        )

        self.client.force_login(self.bob)
        response = self.client.get(reverse("chore_list"))
        self.assertNotContains(
            response,
            f'action="{reverse("chore_review_adjust", args=[self.chore.id])}"',
        )

    # -- Reason requirement -----------------------------------------------

    def test_empty_reason_is_rejected(self):
        self.adjust(self.alice, 5, "")

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_whitespace_only_reason_is_rejected(self):
        self.adjust(self.alice, 5, "   ")

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    # -- ±50%-of-chore.points cap -------------------------------------

    def test_adjustment_within_50_percent_cap_is_accepted(self):
        # chore.points == 20 -> cap is 10.
        self.adjust(self.alice, 10, "Max positive")
        self.adjust(self.bob, 0)  # no-op, just to be safe about isolation
        self.assertTrue(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT, points=10
            ).exists()
        )

    def test_negative_adjustment_within_cap_is_accepted(self):
        self.adjust(self.alice, -10, "Max negative")
        self.assertTrue(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT, points=-10
            ).exists()
        )

    def test_adjustment_exceeding_50_percent_cap_is_rejected(self):
        self.adjust(self.alice, 11, "Too much")
        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_negative_adjustment_exceeding_cap_is_rejected(self):
        self.adjust(self.alice, -11, "Too harsh")
        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_cap_is_floored_for_an_odd_chore_point_value(self):
        # 15 points -> 50% is 7.5; the cap must never round up to 8.
        self.chore.points = 15
        self.chore.save(update_fields=["points"])

        self.adjust(self.alice, 8, "Rounds up over the line")
        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

        self.adjust(self.alice, 7, "Right at the floor")
        self.assertTrue(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT, points=7
            ).exists()
        )

    def test_zero_delta_is_rejected(self):
        self.adjust(self.alice, 0, "No change")
        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    # -- Multiple adjustments: cumulative cap (documented decision) -------

    def test_multiple_adjustments_are_allowed_within_the_cumulative_cap(self):
        self.adjust(self.alice, 6, "First pass")
        self.adjust(self.alice, 4, "Second pass")  # total +10, at the cap

        events = PointEvent.objects.filter(
            chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
        )
        self.assertEqual(events.count(), 2)
        self.assertEqual(sum(e.points for e in events), 10)

    def test_second_adjustment_exceeding_cumulative_cap_is_rejected(self):
        self.adjust(self.alice, 6, "First pass")
        self.adjust(self.alice, 5, "Second pass pushes past the cap")  # 6+5=11 > 10

        events = PointEvent.objects.filter(
            chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().points, 6)

    def test_opposite_sign_adjustments_can_offset_within_cumulative_cap(self):
        self.adjust(self.alice, 10, "Bumped up to the cap")
        self.adjust(self.alice, -15, "Correcting the previous adjustment")

        events = PointEvent.objects.filter(
            chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
        )
        self.assertEqual(events.count(), 2)
        self.assertEqual(sum(e.points for e in events), -5)

    # -- 24h deadline: hidden/disabled in UI, rejected server-side --------

    def test_button_hidden_once_deadline_has_passed(self):
        PointEvent.objects.filter(id=self.completion_event.id).update(
            review_deadline=timezone.now() - timedelta(seconds=1)
        )
        self.client.force_login(self.alice)

        response = self.client.get(reverse("chore_list"))

        self.assertNotContains(
            response,
            f'action="{reverse("chore_review_adjust", args=[self.chore.id])}"',
        )
        self.assertContains(response, "locked")

    def test_submission_after_deadline_is_rejected_server_side(self):
        PointEvent.objects.filter(id=self.completion_event.id).update(
            review_deadline=timezone.now() - timedelta(seconds=1)
        )

        # A crafted POST straight to the endpoint, bypassing the UI entirely.
        self.adjust(self.alice, 5, "Too late")

        self.assertFalse(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    def test_submission_right_before_deadline_is_accepted(self):
        PointEvent.objects.filter(id=self.completion_event.id).update(
            review_deadline=timezone.now() + timedelta(seconds=30)
        )

        self.adjust(self.alice, 5, "Just in time")

        self.assertTrue(
            PointEvent.objects.filter(
                chore=self.chore, kind=PointEvent.Kind.REVIEW_ADJUSTMENT
            ).exists()
        )

    # -- ActivityLog & displayed totals -------------------------------

    def test_adjustment_is_logged_to_activity_log(self):
        self.adjust(self.alice, -5, "Left a mess")

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.POINTS_ADJUSTED,
                chore=self.chore,
                actor=self.alice,
                member=self.bob,
            ).exists()
        )

    def test_activity_log_entry_is_not_owner_restricted(self):
        """The log entry itself carries no owner-only flag, so whenever/
        wherever activity history is surfaced (a later issue's concern) it
        is visible to every member, per §17.14 — not gated to the owner."""
        self.adjust(self.alice, -5, "Left a mess")

        log = ActivityLog.objects.get(
            household=self.household, action=ActivityLog.Action.POINTS_ADJUSTED
        )
        self.assertIn("alice", log.description.lower())
        self.assertIn("-5", log.description)

    def test_member_point_total_reflects_adjustment_immediately(self):
        self.assertEqual(current_point_total(self.bob), 20)

        self.adjust(self.alice, -8, "Left a mess")

        self.assertEqual(current_point_total(self.bob), 12)

    def test_members_page_shows_updated_total_immediately(self):
        self.adjust(self.alice, -8, "Left a mess")

        self.client.force_login(self.alice)
        response = self.client.get(reverse("members"))

        self.assertContains(response, "12 pts")

    def test_review_adjustment_visible_on_chore_list_to_all_members(self):
        self.adjust(self.alice, -8, "Left a mess")

        self.client.force_login(self.carol)
        response = self.client.get(reverse("chore_list"))

        self.assertContains(response, "Left a mess")
        self.assertContains(response, "-8 pts")


class AdjustPointsViewTests(TestCase):
    """#6, §11, §17.8, §17.13, §17.14: direct owner point adjustments,
    independent of any chore completion/review."""

    def setUp(self):
        self.alice = create_user("alice")  # owner
        self.household = create_household_with_owner(self.alice)
        self.bob = create_user("bob")  # member
        self.bob_membership = add_member(self.household, self.bob)
        self.carol = create_user("carol")  # other member
        add_member(self.household, self.carol)

    def adjust(self, user, target_id, amount, reason="Extra help this week"):
        self.client.force_login(user)
        return self.client.post(
            reverse("adjust_points", args=[target_id]),
            {"amount": amount, "reason": reason},
            follow=True,
        )

    # -- Visibility / permissions --------------------------------------

    def test_owner_can_adjust_a_members_points(self):
        self.adjust(self.alice, self.bob.id, 15, "Helped a neighbour move")

        event = PointEvent.objects.get(
            member=self.bob, kind=PointEvent.Kind.MANUAL_ADJUSTMENT
        )
        self.assertEqual(event.points, 15)
        self.assertEqual(event.reason, "Helped a neighbour move")
        self.assertEqual(event.created_by, self.alice)
        self.assertIsNone(event.chore)

    def test_owner_can_adjust_their_own_points(self):
        self.adjust(self.alice, self.alice.id, 10, "Owner did chores too")

        self.assertTrue(
            PointEvent.objects.filter(
                member=self.alice,
                kind=PointEvent.Kind.MANUAL_ADJUSTMENT,
                points=10,
                created_by=self.alice,
            ).exists()
        )

    def test_non_owner_member_cannot_adjust_points(self):
        self.adjust(self.bob, self.carol.id, 10, "Trying to cheat")

        self.assertFalse(
            PointEvent.objects.filter(kind=PointEvent.Kind.MANUAL_ADJUSTMENT).exists()
        )

    def test_get_request_does_not_adjust(self):
        self.client.force_login(self.alice)
        self.client.get(reverse("adjust_points", args=[self.bob.id]))

        self.assertFalse(
            PointEvent.objects.filter(kind=PointEvent.Kind.MANUAL_ADJUSTMENT).exists()
        )

    def test_adjust_points_form_shown_only_to_owner(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("members"))
        self.assertContains(
            response, f'action="{reverse("adjust_points", args=[self.bob.id])}"'
        )

        self.client.force_login(self.bob)
        response = self.client.get(reverse("members"))
        self.assertNotContains(
            response, f'action="{reverse("adjust_points", args=[self.bob.id])}"'
        )

    # -- Reason requirement -----------------------------------------------

    def test_empty_reason_is_rejected(self):
        self.adjust(self.alice, self.bob.id, 10, "")

        self.assertFalse(
            PointEvent.objects.filter(kind=PointEvent.Kind.MANUAL_ADJUSTMENT).exists()
        )

    def test_whitespace_only_reason_is_rejected(self):
        self.adjust(self.alice, self.bob.id, 10, "   ")

        self.assertFalse(
            PointEvent.objects.filter(kind=PointEvent.Kind.MANUAL_ADJUSTMENT).exists()
        )

    # -- No cap, can go negative ----------------------------------------

    def test_large_positive_adjustment_has_no_cap(self):
        self.adjust(self.alice, self.bob.id, 500, "Went above and beyond")

        self.assertTrue(
            PointEvent.objects.filter(
                member=self.bob, kind=PointEvent.Kind.MANUAL_ADJUSTMENT, points=500
            ).exists()
        )

    def test_large_negative_adjustment_has_no_cap(self):
        self.adjust(self.alice, self.bob.id, -500, "Serious violation")

        self.assertTrue(
            PointEvent.objects.filter(
                member=self.bob, kind=PointEvent.Kind.MANUAL_ADJUSTMENT, points=-500
            ).exists()
        )

    def test_total_can_go_negative(self):
        self.adjust(self.alice, self.bob.id, -30, "Way over budget")

        self.assertEqual(current_point_total(self.bob), -30)

    # -- Removed members --------------------------------------------------

    def test_removed_member_cannot_be_targeted(self):
        self.bob_membership.removed_at = timezone.now()
        self.bob_membership.save(update_fields=["removed_at"])

        response = self.adjust(self.alice, self.bob.id, 10, "Too late")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            PointEvent.objects.filter(kind=PointEvent.Kind.MANUAL_ADJUSTMENT).exists()
        )

    def test_removed_members_prior_history_is_untouched(self):
        self.adjust(self.alice, self.bob.id, 20, "Before leaving")
        self.bob_membership.removed_at = timezone.now()
        self.bob_membership.save(update_fields=["removed_at"])

        self.assertTrue(
            PointEvent.objects.filter(
                member=self.bob, kind=PointEvent.Kind.MANUAL_ADJUSTMENT, points=20
            ).exists()
        )
        self.assertEqual(current_point_total(self.bob), 20)

    # -- ActivityLog & visibility to all members --------------------------

    def test_adjustment_is_logged_to_activity_log(self):
        self.adjust(self.alice, self.bob.id, -5, "Left dishes dirty")

        self.assertTrue(
            ActivityLog.objects.filter(
                household=self.household,
                action=ActivityLog.Action.POINTS_ADJUSTED,
                actor=self.alice,
                member=self.bob,
            ).exists()
        )

    def test_reason_visible_to_every_member_on_members_page(self):
        self.adjust(self.alice, self.bob.id, -5, "Left dishes dirty")

        self.client.force_login(self.carol)
        response = self.client.get(reverse("members"))

        self.assertContains(response, "Left dishes dirty")
        self.assertContains(response, "-5 pts")

    def test_member_point_total_reflects_adjustment_immediately(self):
        self.assertEqual(current_point_total(self.bob), 0)

        self.adjust(self.alice, self.bob.id, 7, "Nice work")

        self.assertEqual(current_point_total(self.bob), 7)
