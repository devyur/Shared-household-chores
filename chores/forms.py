from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Chore, Household, RecurrenceRule


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
    recurrence_rule_type = forms.ChoiceField(
        choices=[("", "---------")] + list(RecurrenceRule.RuleType.choices),
        required=False,
        label="Recurrence type",
    )
    recurrence_interval_days = forms.IntegerField(
        required=False, min_value=1, label="Repeat every (days)"
    )

    class Meta:
        model = Chore
        fields = ("name", "description", "points", "due_date", "is_recurring")
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rule = getattr(self.instance, "recurrence_rule", None) if self.instance.pk else None
        if rule:
            self.fields["recurrence_rule_type"].initial = rule.rule_type
            self.fields["recurrence_interval_days"].initial = rule.interval_days

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("is_recurring"):
            if not cleaned_data.get("recurrence_rule_type"):
                self.add_error(
                    "recurrence_rule_type", "Required for a recurring chore."
                )
            if not cleaned_data.get("recurrence_interval_days"):
                self.add_error(
                    "recurrence_interval_days", "Required for a recurring chore."
                )
        return cleaned_data

    def save_recurrence_rule(self, chore):
        """Create/update/remove `chore`'s RecurrenceRule to match the
        submitted recurrence fields. Call once `chore` itself is saved."""
        if self.cleaned_data.get("is_recurring"):
            RecurrenceRule.objects.update_or_create(
                chore=chore,
                defaults={
                    "rule_type": self.cleaned_data["recurrence_rule_type"],
                    "interval_days": self.cleaned_data["recurrence_interval_days"],
                },
            )
        else:
            RecurrenceRule.objects.filter(chore=chore).delete()
