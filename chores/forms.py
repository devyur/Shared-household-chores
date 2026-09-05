from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Chore, Household


class SignUpForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("username",)


class HouseholdCreateForm(forms.ModelForm):
    class Meta:
        model = Household
        fields = ("name",)


class HouseholdJoinForm(forms.Form):
    invite_code = forms.CharField(max_length=20, label="Invite code")


class ChoreForm(forms.ModelForm):
    class Meta:
        model = Chore
        fields = ("name", "description", "points", "due_date", "is_recurring")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}
