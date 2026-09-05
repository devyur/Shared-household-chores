from django.contrib import admin

from .models import (
    Achievement,
    ActivityLog,
    Chore,
    Claim,
    Household,
    MemberAchievement,
    Membership,
    PointEvent,
    RecurrenceRule,
)


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "household", "role", "joined_at", "removed_at")
    list_filter = ("household", "role")


class RecurrenceRuleInline(admin.StackedInline):
    model = RecurrenceRule
    extra = 0


class ClaimInline(admin.TabularInline):
    model = Claim
    extra = 0


@admin.register(Chore)
class ChoreAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "household",
        "points",
        "due_date",
        "status",
        "assigned_to",
        "is_recurring",
        "deleted_at",
    )
    list_filter = ("household", "status", "is_recurring")
    search_fields = ("name", "description")
    inlines = [RecurrenceRuleInline, ClaimInline]


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = ("chore", "member", "claimed_at")
    list_filter = ("chore__household",)


@admin.register(PointEvent)
class PointEventAdmin(admin.ModelAdmin):
    list_display = (
        "member",
        "kind",
        "points",
        "chore",
        "created_by",
        "created_at",
        "review_deadline",
    )
    list_filter = ("kind",)
    search_fields = ("member__username", "reason")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("action", "household", "actor", "member", "chore", "created_at")
    list_filter = ("household", "action")
    readonly_fields = ("created_at",)


@admin.register(Achievement)
class AchievementAdmin(admin.ModelAdmin):
    list_display = ("name", "description")


@admin.register(MemberAchievement)
class MemberAchievementAdmin(admin.ModelAdmin):
    list_display = ("member", "achievement", "awarded_at")
