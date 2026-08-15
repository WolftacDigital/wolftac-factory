# Stale reset with an already-built branch

Use this reference when a Factory Queue issue was previously built and moved to `In Review`, later swept/reset to `Todo`, and the Linear-generated branch still contains the implementation commits.

## Durable pattern

1. Treat the reset `Todo` issue as buildable only after the normal pickup rules pass: lane filter, no latest `BLOCKED:` comment, no unresolved blockers, reachable repo.
2. Claim it normally (`In Progress`, assignee `me`) and verify the claim with a fresh `get_issue` read.
3. Inspect the issue comments for prior `BUILD REPORT` entries and compare their branch/commit SHAs with the live repo.
4. If the branch already contains the approved implementation and there is no new `REVIEW REPORT — FAIL` work order, do **not** create a duplicate no-op commit. Re-run the spec's test plan and git proof against the existing branch commits.
5. Post a fresh `BUILD REPORT` that explicitly says this pass re-verified existing branch commits after the stale reset, lists the commands/results, and notes any pre-existing unstaged files that were not touched/staged.
6. Move the issue back to `In Review` after proof is posted.

## Why this matters

A stale-claim sweep can legitimately return an already-built branch to `Todo`. The build worker's job is to produce current proof and restore the handoff state, not to manufacture empty commits or reimplement completed scope. Current review sweeps keep built work in `In Review`; this reference remains for historical or manual resets.
