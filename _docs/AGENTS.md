# Agent Notes

Django app (Python 3.14, Django 6.1) for a single-household chore/points
tracker. Homework-scoped project — see `_docs/plan.md` for the full spec.

## Workflow

Full detail in `_docs/process.md`; the short version:

- Work is picked up from GitHub issues, one at a time — check the "GitHub
  issues" section below and the repo's issue list before starting anything,
  rather than inventing new scope.
- Read the issue's acceptance criteria before starting, and re-check them
  before considering the issue done/closing it.
- Commit regularly rather than as one large diff at the end.

## Roles

If the session is asked to take on a named role, follow its doc under
`_docs/team/` exactly rather than defaulting to general engineering
behavior:

- **PM** (`_docs/team/pm.md`) - grooms a raw issue into the shape defined by
  `_docs/task-template.md` (Goal / Acceptance criteria / Out of scope /
  Constraints) before anyone implements it. Writes no code. Acceptance
  criteria must be checkable by looking at the result; anything moved out of
  scope must link to a filed follow-up issue, not just be dropped.
- **Engineer** (`_docs/team/software-engineer.md`) - implements one groomed
  issue at a time, against its acceptance criteria as written. Writes tests
  for what it builds, commits regularly, and leaves the issue open with a
  comment describing what was done rather than closing it.
- **QA** (`_docs/team/qa-engineer.md`) - checks finished work against the
  issue's acceptance criteria and the running code (not the implementation's
  own claims), runs the test suite, and posts a PASS/FAIL verdict as an
  issue comment. Fixes nothing itself.

With no role specified, assume the Engineer role: implement the groomed
issue as written.

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
  cron/Celery worker (rationale in `_docs/claud-suggestions.md`, if you want
  it). Keep new work consistent with that choice unless the user says
  otherwise.
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

- `_docs/process.md` - how work is organized (GitHub issues, one at a time;
  read this before picking up any task) and which roles exist
- `_docs/team/pm.md`, `_docs/team/software-engineer.md`,
  `_docs/team/qa-engineer.md` - the PM/Engineer/QA role instructions (see
  Roles above)
- `_docs/task-template.md` - the four-section shape (Goal / Acceptance
  criteria / Out of scope / Constraints) a groomed issue should follow
- `_docs/plan.md` - the full product/business-rule spec (read this first for
  any feature work — it's authoritative over paraphrased summaries)

Historical, not required reading — the GitHub issues already carry what
matters from these (each issue cites its `plan.md` § directly):

- `_docs/backlog.md` - the original implementation backlog that sections 4-15
  were split out of into issues #1-#12
- `_docs/claud-suggestions.md` - tech-stack options considered before
  settling on Django; only the lazy-eval rationale (noted under Rules above)
  still applies

## GitHub issues

- Repo: `devyur/Shared-household-chores`
- Remaining feature work (recurring chores, claiming, auto-assignment,
  completion/failure, owner review, point adjustments, leaderboard,
  achievements, activity history, notifications, dashboard, tests) is tracked
  as issues #1-#12 in this repo. Each issue cites the `plan.md` § it's
  derived from — read that section before implementing.
- Per `_docs/process.md`: work one issue at a time, confirm its acceptance
  criteria before starting and before closing it, and commit regularly as
  you go rather than in one final commit.
