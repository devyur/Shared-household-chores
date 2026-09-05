Orchestrator

The main session is the orchestrator. It launches engineer
and QA as subagents. It does not implement or test itself.

Lifecycle

1. Pick the next open issue from the github issues
2. Engineer implements it
3. QA verifies it
4. On FAIL, back to step 2 with the QA comment as input
5. On PASS, close the issue
6. Repeat until all issues are empty

Rules

- The engineer does not close the issue
- QA does not fix the code, only outputs PASS or FAIL
- The orchestrator closes the issue only after QA outputs PASS
- Tasks are GitHub issues, one at a time
- Read the acceptance criteria before starting and before closing
- Commit regularly

Roles

- PM - grooms a task before anyone implements it, follows _docs/team/pm.md
- Engineer - implements one groomed task, follows _docs/team/software-engineer.md
- QA - checks the result against the acceptance criteria, follows _docs/team/qa-engineer.md