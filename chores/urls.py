from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("signup/", views.signup, name="signup"),
    path("household/create/", views.household_create, name="household_create"),
    path("household/join/", views.household_join, name="household_join"),
    path("household/members/", views.members, name="members"),
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
