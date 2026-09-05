# Implementation Backlog (Django)

Derived from `_docs/plan.md`. Ordered so each task builds on a working slice of the app rather than one big-bang implementation. Section references (§N) point back to `plan.md`.

Design choice carried over from `_docs/claud-suggestions.md`: due-date-triggered state changes (auto-assignment, failure penalty, 24h point lock) are evaluated **lazily on read** (e.g. a `chore.refresh_state()` call invoked whenever a chore/dashboard is loaded) rather than via a Celery/cron worker. Simpler for a single-household homework app, no background worker needed.

## 1. Data model
- [ ] `Household` model + `Membership` (User ↔ Household, `role`: owner/member) (§2)
- [ ] `Chore` model: name, description, points, due_date (date, not datetime), is_recurring, deleted flag/timestamp for archive (§3, §13)
- [ ] `RecurrenceRule` model or field on `Chore`: calendar-based vs completion-based interval (§4, §18)
- [ ] `Claim` model: chore ↔ member, claimed_at (§5)
- [ ] `PointEvent` model: member, chore, delta, reason, kind (completion/adjustment/failure/manual), created_at, review deadline for completions (§6, §7, §8, §11)
- [ ] `ActivityLog` model: actor, action type, target, timestamp, metadata (§13)
- [ ] `Achievement`/`Badge` models + a member-achievement join table (§10)
- [ ] Register everything in `admin.py` for quick inspection while building

## 2. Auth & household bootstrap
- [ ] Use Django's built-in `auth.User` for accounts
- [ ] Household creation flow: first user becomes owner (§2)
- [ ] Invitation mechanism — simplest viable option: an invite code/link the owner shares (§2, §18)
- [ ] Owner can remove a member; removed member's points/history stay, their active claims/assignments free up (§2, §16)

## 3. Chore CRUD
- [x] List/create/edit/delete views (or DRF endpoints) for chores, open to all members (§3, §17.1)
- [x] Edits apply immediately without cancelling existing claims/assignment (§3)
- [x] Soft-delete chores into an archive/history view instead of hard delete (§3, §13)
- [x] Log every create/edit/delete to `ActivityLog`

Sections 4-15 have been moved to GitHub issues:

- [#1 Recurring chores](https://github.com/devyur/Shared-household-chores/issues/1)
- [#2 Claiming](https://github.com/devyur/Shared-household-chores/issues/2)
- [#3 Auto-assignment at due date](https://github.com/devyur/Shared-household-chores/issues/3)
- [#4 Completion & failure](https://github.com/devyur/Shared-household-chores/issues/4)
- [#5 Owner review window](https://github.com/devyur/Shared-household-chores/issues/5)
- [#6 Direct owner point adjustments](https://github.com/devyur/Shared-household-chores/issues/6)
- [#7 Points, leaderboard & summaries](https://github.com/devyur/Shared-household-chores/issues/7)
- [#8 Achievements/badges](https://github.com/devyur/Shared-household-chores/issues/8)
- [#9 Activity history / audit log](https://github.com/devyur/Shared-household-chores/issues/9)
- [#10 Notifications](https://github.com/devyur/Shared-household-chores/issues/10)
- [#11 Main dashboard](https://github.com/devyur/Shared-household-chores/issues/11)
- [#12 Tests](https://github.com/devyur/Shared-household-chores/issues/12)
