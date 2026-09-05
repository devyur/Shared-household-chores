from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ActivityLog, Chore, Claim, Household, Membership, RecurrenceRule

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
