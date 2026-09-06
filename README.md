# Shared Household Chores

A single-household chore tracker with a points system, built as a Django app.
Members create chores, claim the ones they want to do, and get points for
completing them on time. Everything (claims, assignments, completions,
failures, point changes) is logged so responsibility stays visible.

This is a homework-scoped project (see `_docs/plan.md` for the full spec) —
it intentionally supports **one household only**, not a multi-tenant SaaS.

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) for dependency management

## Running it locally

```bash
uv sync                              # install dependencies (Django 6.1+)
uv run python manage.py migrate      # apply migrations, creates db.sqlite3
uv run python manage.py runserver    # start the dev server at http://127.0.0.1:8000/
```

Then open http://127.0.0.1:8000/ in a browser, sign up for an account, and
either create a household (you become its owner) or join one with an invite
code from an existing owner.

Optional: `uv run python manage.py createsuperuser` to get an `/admin/` login
for inspecting data directly.

### Running tests

```bash
uv run python manage.py test chores          # whole suite (200+ tests)
uv run python manage.py test chores.tests.ChoreModelTests   # one test case
```

There's no pytest here — tests are Django's built-in `TestCase` suite in
`chores/tests.py`.

## What the app does

- **Household & members** — the first user to sign up creates the household
  and becomes its **owner**; everyone else joins with an invite code. The
  owner can remove a member (their points/history stay, their active claims
  free up).
- **Chores** — any member can create, edit, or soft-delete a chore (name,
  description, points, due date). Deleted chores stay visible in an archive.
  Chores can be one-off or **recurring** (calendar-based or
  completion-based interval); completing a recurring chore spawns its next
  occurrence automatically.
- **Claiming** — any member can claim an open chore to signal they want to
  do it; multiple members can claim the same one, and a claim can be
  withdrawn.
- **Auto-assignment** — when a chore's due date arrives, it's automatically
  assigned to whichever claimant currently has the most points (ties go to
  whoever claimed first). This and every other due-date-triggered rule below
  is evaluated **lazily, on read** — there's no background scheduler/cron
  job, so state updates the next time a page that touches chores is loaded.
- **Completion & failure** — the assignee marks a chore complete to
  immediately get its points. If the due date passes without completion,
  the chore reopens for claiming and the (former) assignee loses 50% of its
  point value — points can go negative.
- **Owner review window** — for 24 hours after a completion, the owner can
  adjust the points awarded (up or down, capped at ±50% of the chore's
  value, reason required). After that, the result is locked.
- **Direct point adjustments** — the owner can add or subtract points from
  any member at any time, independent of a specific chore, with a
  mandatory reason visible to the whole household.
- **Leaderboard & summaries** — lifetime points, chores completed today, and
  weekly/monthly point totals per member.
- **Achievements** — a small fixed set of badges (first completion, a
  completion streak, a points milestone) awarded automatically the first
  time a member qualifies.
- **Activity history** — a paginated, household-wide audit log of chore
  CRUD, claims, assignments, completions/failures, and point changes, with
  who did it and when.
- **Home dashboard** — the landing page after login: chores due today
  (household-wide, not just "mine") plus a compact leaderboard.

## Project layout

- `household_chores/` — Django project (settings, root URL config)
- `chores/` — the one app: `models.py` (domain model), `views.py`,
  `forms.py`, `urls.py`, `admin.py`, `templates/chores/`,
  `templates/registration/`, `tests.py`
- `chores/models.py` domain model: `Household`, `Membership`, `Chore`,
  `RecurrenceRule`, `Claim`, `PointEvent`, `ActivityLog`, `Achievement`,
  `MemberAchievement`

## Docs

- `_docs/plan.md` — the full product/business-rule spec; authoritative over
  any summary (including this one) if they ever disagree
- `_docs/AGENTS.md` — repo conventions, commands, and the workflow used to
  build this (GitHub issues + PM/Engineer/QA/Orchestrator roles); read this
  first if you're picking the project back up and want the "how this was
  built" context
- `_docs/backlog.md` — the original implementation backlog issues #1-#12
  were split out of
