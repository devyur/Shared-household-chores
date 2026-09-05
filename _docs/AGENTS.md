# Agent Notes

Django app (Python 3.14, Django 6.1) for a single-household chore/points
tracker. Homework-scoped project — see `_docs/plan.md` for the full spec.

## Commands

- `uv sync` - install dependencies
- `uv run python manage.py runserver` - run the dev server
- `uv run python manage.py test` - the whole test suite
- `uv run python manage.py test chores` - just the `chores` app
- `uv run python manage.py test chores.tests.ChoreModelTests` - one test case
- `uv run python manage.py makemigrations` - generate migrations after model changes
- `uv run python manage.py migrate` - apply migrations

There is no `pytest` here — tests are Django's built-in `TestCase` suite in
`chores/tests.py`, run via `manage.py test`.

## Rules

- Dependencies are added in `pyproject.toml`. Do not add one without asking.
- Only one household exists in this system — don't build multi-tenant
  scaffolding (see `_docs/plan.md` §2, §16 "explicitly out of scope").
- Due-date-triggered state changes (auto-assignment, failure penalty, 24h
  point lock) are meant to be evaluated **lazily on read**, not via a
  cron/Celery worker — see `_docs/claud-suggestions.md` and
  `_docs/backlog.md` for the reasoning. Keep new work consistent with that
  choice unless the user says otherwise.
- Every chore create/edit/delete and every point change must be logged to
  `ActivityLog` (see `chores/models.py`) — this is a stated business rule,
  not optional bookkeeping.
- All household members have equal permissions on chores (create/edit/delete)
  — there is no per-chore ownership/permission model to build.

## Project layout

- `household_chores/` - Django project (settings, root urls)
- `chores/` - the single app: `models.py`, `views.py`, `forms.py`, `urls.py`,
  `admin.py`, `templates/chores/`, `templates/registration/`, `tests.py`
- `chores/models.py` - domain model: `Household`, `Membership`, `Chore`,
  `RecurrenceRule`, `Claim`, `PointEvent`, `ActivityLog`, `Achievement`,
  `MemberAchievement`

## Documents

- `_docs/plan.md` - the full product/business-rule spec (read this first for
  any feature work — it's authoritative over paraphrased summaries)
- `_docs/backlog.md` - implementation backlog; sections 4-15 have moved to
  GitHub issues (linked from that file) — check issue status there before
  assuming a feature is unbuilt
- `_docs/claud-suggestions.md` - tech-stack rationale, notably why lazy
  evaluation was chosen over a scheduler/worker

## GitHub issues

- Repo: `devyur/Shared-household-chores`
- Remaining feature work (recurring chores, claiming, auto-assignment,
  completion/failure, owner review, point adjustments, leaderboard,
  achievements, activity history, notifications, dashboard, tests) is tracked
  as issues #1-#12 in this repo, linked from `_docs/backlog.md`. Read the
  relevant `plan.md` section (§ numbers are cited per issue) before
  implementing one.
