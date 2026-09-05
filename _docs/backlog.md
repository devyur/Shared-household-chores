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

## 4. Recurring chores
- [ ] On completion of a recurring chore, generate the next occurrence per its recurrence rule (§4)
- [ ] Allow claiming a future (not-yet-due) occurrence (§4)

## 5. Claiming
- [ ] Members can claim a chore; multiple claims allowed per chore (§5, §17.3)
- [ ] Claiming does not award points (§5)
- [ ] Decide and document what happens if a claimant leaves before assignment (§18)

## 6. Auto-assignment at due date
- [ ] Lazy-eval routine: when a chore with claims reaches its due date, assign it to the claimant with the highest current points (§5, §17.4)
- [ ] Define and document the tie-break rule (not specified in plan.md)
- [ ] Unclaimed chores stay available past their due date, no auto-assignment (§5, §17.15)

## 7. Completion & failure
- [ ] Assigned member marks their chore complete → immediately award configured points (§6, §17.6)
- [ ] Lazy-eval routine: if an assigned chore's due date passes uncompleted, mark it failed, deduct 50% of its point value (can go negative), reopen the chore, log to `ActivityLog` (§8, §17.7, §17.8)

## 8. Owner review window
- [ ] Owner can adjust a completed chore's awarded points within 24h of completion, capped at ±50% of the chore's original value, with a required reason (§7, §17.9, §17.10, §17.11)
- [ ] Lazy-eval routine: once 24h have elapsed, lock the points permanently (§7, §17.12)

## 9. Direct owner point adjustments
- [ ] Owner can adjust any member's points at any time, reason required, visible to the whole household (§11, §17.13, §17.14)

## 10. Points, leaderboard & summaries
- [ ] Compute lifetime points per member from `PointEvent` history
- [ ] Weekly/monthly point summaries (simple aggregation queries) (§9)
- [ ] Leaderboard view: total points + chores completed today (§9)

## 11. Achievements/badges
- [ ] Define a small initial rule set (e.g. "first chore completed", "N chores in a week") — exact rules are an implementation detail (§10, §18)
- [ ] Evaluate and award achievements when relevant events occur (completion, point milestones)

## 12. Activity history / audit log
- [ ] Central log covering: chore create/edit/delete, claims, assignments, completions/failures, point changes, membership changes (§13)
- [ ] History view showing actor + timestamp per entry, including deleted chores (§13)

## 13. Notifications
- [ ] Minimal due-date reminder — e.g. a "due today" banner/section on the dashboard is sufficient for MVP; email is optional (§12, §18)

## 14. Main dashboard
- [ ] Home screen: today's chores + compact leaderboard, making responsibility and priority immediately clear (§14)

## 15. Tests
- [ ] Unit tests for the point-math edge cases: failure penalty, ±50% adjustment cap, negative points, 24h lock boundary
- [ ] Unit tests for auto-assignment (highest points wins, tie-break rule)
- [ ] Basic view/permission tests (any member can CRUD chores, only owner can adjust points/remove members)
