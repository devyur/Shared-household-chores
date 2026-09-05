from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("household/create/", views.household_create, name="household_create"),
    path("household/join/", views.household_join, name="household_join"),
    path("household/members/", views.members, name="members"),
    path("chores/", views.chore_list, name="chore_list"),
    path("chores/new/", views.chore_create, name="chore_create"),
    path("chores/<int:chore_id>/edit/", views.chore_edit, name="chore_edit"),
    path("chores/<int:chore_id>/delete/", views.chore_delete, name="chore_delete"),
    path("chores/archive/", views.chore_archive, name="chore_archive"),
    path(
        "household/members/<int:user_id>/remove/",
        views.remove_member,
        name="remove_member",
    ),
    path(
        "household/invite/regenerate/",
        views.regenerate_invite,
        name="regenerate_invite",
    ),
]
