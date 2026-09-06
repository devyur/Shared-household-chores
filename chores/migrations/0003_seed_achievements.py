from django.db import migrations

# Kept as literal data here (not imported from chores.models) so this
# migration's behaviour stays fixed even if the current rule definitions
# ever change (#8, §10). See chores/models.py `ACHIEVEMENT_DEFINITIONS` for
# the live source of truth used by `evaluate_achievements`.
ACHIEVEMENTS = [
    ("First Chore", "Complete your first chore."),
    ("On a Roll", "Complete 5 chores within a rolling 7-day window."),
    ("Point Milestone", "Reach 100 lifetime points."),
]


def seed_achievements(apps, schema_editor):
    Achievement = apps.get_model("chores", "Achievement")
    for name, description in ACHIEVEMENTS:
        Achievement.objects.get_or_create(name=name, defaults={"description": description})


def remove_achievements(apps, schema_editor):
    Achievement = apps.get_model("chores", "Achievement")
    Achievement.objects.filter(name__in=[name for name, _ in ACHIEVEMENTS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("chores", "0002_household_invite_code"),
    ]

    operations = [
        migrations.RunPython(seed_achievements, remove_achievements),
    ]
