# Stale reset with an already-built branch

Use this reference when a Factory Queue issue was previously built and moved to `In Review`, later swept/reset to `Todo`, and the Linear-generated branch still contains the implementation commits.

## Durable pattern

1. Treat the reset `Todo` issue as buildable only after the normal pickup rules pass: lane filter, no latest `BLOCKED:` comment, no unresolved blockers, reachable repo.
2. Claim it normally (`In Progress`, assignee `me`) and verify the claim with a fresh `get_issue` read.
3. Inspect the issue comments for prior `BUILD REPORT` entries and compare their branch/commit SHAs with the live repo.
4. If the branch already contains the approved implementation and there is no new `REVIEW REPORT — FAIL` work order, do **not** create a duplicate no-op commit. Re-run the spec's test plan and git proof against the existing branch commits.
5. Post a fresh `BUILD REPORT` that explicitly says this pass re-verified existing branch commits after the stale reset, lists the commands/results, and notes any pre-existing unstaged files that were not touched/staged.
6. Move the issue back to `In Review` after proof is posted.

## Reverification pitfalls

- Do not treat an old Actions-budget failure as proof that capacity is still unavailable. Inspect the exact-head workflow for side effects before an authorized, bounded retry; read back its attempt, head SHA, executed steps, and conclusion. If the retry succeeds, rerun the local test plan and restore the normal review handoff rather than repeating the historical blocker report.
- After a Windows `npm ci` timeout, inspect the exact install process before retrying; the Node child may continue after the shell times out. Wait for that process to finish rather than running concurrent installs. Once idle, `npm ci --offline --no-audit --no-fund` can recover from a populated cache without changing the lockfile. Missing cache entries are a real blocker, not permission to invent successful installation proof.

## Proof-only rework preflight

When the latest FAIL concerns live filesystem write attribution rather than a code defect, check the observation tooling before claiming. A repeated before/after hash run on actively scheduled files does not establish who wrote the differences; timestamps and a source-code write scan alone do not close that finding. Do not send the same unproven result back through review.

On Windows, `wpr.exe -status` and `wpr.exe -profiles` can succeed without permission to record. If no recording is active, a bounded `wpr.exe -start FileIO` probe tests actual access; `0x80070005` / exit 5 means recording was denied. Recheck `wpr.exe -status` afterward. If the probe starts successfully, stop only that recording promptly to an explicit profile-scratch `.etl` path. Never stop another operator's recording, elevate, install tracing tools, or stop Hermes to obtain a quiet hash window. Keep any trace private and do not publish raw system metadata.

If no permitted attribution method is available, leave the issue unclaimed and report the concrete preflight blocker. Do not confuse read-only WPR availability with successful writer-attribution proof.

## Why this matters

A stale-claim sweep can legitimately return an already-built branch to `Todo`. The build worker's job is to produce current proof and restore the handoff state, not to manufacture empty commits or reimplement completed scope. Current review sweeps keep built work in `In Review`; this reference remains for historical or manual resets.
