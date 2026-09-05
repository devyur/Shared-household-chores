# Shared Household Chores — Homework Scope

## 1. Goal

Build a tool for managing chores shared by members of a single household. The system should make responsibility visible, encourage participation through a points system, and keep an accountable history of household activity.

## 2. Household and Members

- The system supports **one household**.
- A household is created by an **owner**.
- The owner invites other members.
- Everyone in the household can participate in chores.
- The owner can remove a member at any time.
- When a member leaves or is removed:
  - Their points and history remain.
  - Their active chores become available again.

## 3. Chores

The system supports both:
- **One-off chores**
- **Recurring chores**

A chore contains:
- Name
- Description
- Points
- Due date

The due date is **date-only**, not time-specific.

All members can:
- Create chores
- Edit any chore
- Delete any chore

Edits apply immediately and do not cancel an existing claim or assignment.

Deleted chores remain visible in an archive/history.

## 4. Recurring Chores

Recurring chores support different recurrence rules, including:
- Calendar-based recurrence
- Custom/completion-based intervals

After a recurring chore is completed, its next occurrence appears on its scheduled date.

A future chore can be claimed before its due date.

## 5. Claiming and Assignment

A chore can be claimed by multiple household members.

Claiming means that a member wants to take responsibility for the chore.

The claim lasts until the due date. If the due date is far away, the claim therefore lasts longer than one day.

When the due date arrives:
1. All claimants are considered.
2. The claimant with the **highest current points** gets the chore automatically.
3. That person becomes responsible for completing it.

Points are not awarded for claiming.

If nobody claims the chore, it remains available, including after its due date.

## 6. Completion

The person responsible for a chore marks it as completed themselves.

On completion:
- The chore's configured points are immediately awarded.
- There is no automatic late-completion penalty.

The owner can review the completed chore.

## 7. Owner Review and Point Adjustments

The owner has **24 hours** after completion to review the work.

The owner can:
- Increase the awarded points
- Decrease the awarded points
- Add a reason/comment

The adjustment is separate from the original point award.

Owner adjustments are limited to **±50% of the chore's original point value**.

After 24 hours, the points become permanently locked.

## 8. Failed Chores

If an assigned chore is not completed:
- It becomes available again.
- The responsible member loses **50% of the chore's point value**.
- Points can go below zero.

The failure should be recorded in the activity history.

## 9. Points and Ranking

Points are the main incentive system.

Points affect:
1. Priority when competing for chores
2. Leaderboard ranking
3. Achievements/badges

Points are:
- Lifetime/persistent
- Also summarized as weekly and monthly scores

The leaderboard displays:
- Current total points
- Number of chores completed today

## 10. Achievements / Badges

The system includes achievements or badges based on household activity and/or points.

Exact achievement rules can be defined later as an implementation detail.

## 11. Owner Point Management

The household owner can directly change a member's points at any time.

Every direct point change must include a reason.

Point-change reasons are visible to everyone in the household.

## 12. Notifications

Notifications are intentionally minimal for the homework scope.

Members receive:
- A reminder on the chore's due date

No additional claim, assignment, completion, or point-change notifications are required for the MVP.

## 13. Activity History / Audit Log

The activity history records at least:
- Chore creation
- Chore editing
- Chore deletion
- Chore claims
- Chore assignments
- Chore completions/failures
- Point changes and owner adjustments
- Membership/invitation changes

History should identify who performed the action and when.

Deleted chores remain accessible through the history/archive.

## 14. Main Screen

The main screen focuses on:
- **Today's chores**
- A compact **leaderboard / points summary**

The interface should make it immediately clear:
- Which chores need attention
- Who is responsible
- Current points/priority

## 15. Core Workflow Example

Example:

1. Alice creates “Clean kitchen” worth 20 points.
2. Bob and Carol both claim it.
3. Bob currently has 120 points; Carol has 90.
4. On the due date, Bob automatically receives the assignment.
5. Bob completes the chore and receives +20 points immediately.
6. The owner has 24 hours to review the work.
7. The owner may add or subtract up to 10 points and provide a reason.
8. After 24 hours, the result is locked.
9. If Bob had failed instead, the chore would become available again and Bob would receive a −10 point penalty.

## 16. MVP Scope

### Must have

- Household creation
- Owner/member roles
- Invitations
- Chore CRUD
- One-off chores
- Recurring chores
- Due dates
- Multiple claims
- Automatic priority based on points
- Automatic assignment
- Completion
- Failure and point penalty
- Points system
- Owner point review
- Direct owner point adjustments
- Leaderboard
- Daily completed-chore count
- Weekly/monthly score summaries
- Achievements/badges
- Activity history
- Deleted-chore archive
- Due-date reminders

### Explicitly out of scope / unnecessary for MVP

- Multiple households per tool
- Complex notification system
- Exact due times
- Manual voting for chore assignment
- Automatic rotation unrelated to points
- Automatic late-completion penalties
- Categories/difficulty/estimated time
- Complex permissions for editing chores
- Advanced analytics

## 17. Important Business Rules

1. **Everyone can create, edit, and delete chores.**
2. **Every chore change is auditable.**
3. **Multiple people can claim the same chore.**
4. **Higher points give higher priority.**
5. **Claims last until the due date.**
6. **Points are awarded only when a chore is completed.**
7. **Failure costs 50% of the chore's points.**
8. **Points may become negative.**
9. **Owner review adjustments are separate from the original award.**
10. **Owner adjustments are limited to ±50% of the original chore value.**
11. **Owner has 24 hours to adjust completion points.**
12. **After 24 hours, completion points are locked.**
13. **Direct owner point changes require a reason.**
14. **Point-change reasons are visible to everyone.**
15. **Unclaimed chores remain available after their due date.**
16. **Removed members retain their historical data and points; their active chores become available.**

## 18. Open Design Details to Decide During Implementation

These details were not explicitly specified and can be chosen based on what keeps the homework simple:

- Exact invitation mechanism (email/link/code)
- Exact recurring-chore rule representation
- What happens if a claimant leaves before assignment
- Exact achievement/badge definitions
- Exact reminder delivery mechanism
- Exact UI layout and technology stack
- Authentication implementation
- Database schema
- Whether history is immutable or supports administrative correction
