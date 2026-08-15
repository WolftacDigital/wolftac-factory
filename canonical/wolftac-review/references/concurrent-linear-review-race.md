# Concurrent Linear review race pattern

Use this when a scheduled `wolftac-review` worker selected an `In Review` issue, reproduced proof locally, but another reviewer completed the issue before this worker could mutate Linear.

## Durable lesson

Linear state is not locked by selecting an issue. A review worker must re-fetch the issue and comments immediately before posting a REVIEW REPORT or changing state.

## Safe behavior

1. After verification and before any Linear write, re-fetch:
   - issue status/state
   - latest comments
   - whether a REVIEW REPORT already exists
2. If the issue is no longer `In Review`, especially if it is `Done`:
   - do not post a duplicate comment when your review only repeats existing evidence
   - do not change state
   - do not pick a replacement issue in the same cron tick
   - clean up temporary worktrees/artifacts
   - final-report the concurrent completion and summarize the evidence already reproduced
3. Exception: if this worker materially changed the branch after selecting the issue (for example, a low-risk review merge from current `origin/main` that resolves a full-suite failure) or produced new proof the existing report lacks, it may post a clearly labeled follow-up REVIEW REPORT. The report must explain the race, list the new commit/evidence, and still read Linear back afterward.
4. If the issue remains `In Review` and no review report exists, proceed with the normal PASS / PASS WITH FIXES / FAIL path.

## Why this matters

A duplicate reviewer report after another agent has already set `Done` pollutes Linear and can imply conflicting ownership. The review stage is one-issue-per-invocation; once an issue was selected and work began, the invocation is consumed even if a race makes mutation unnecessary.
