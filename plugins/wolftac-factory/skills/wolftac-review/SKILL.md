---
name: wolftac-review
description: Independently review one In Review issue from the Wolftac Digital Linear Factory Queue at its published commit, reproduce the test plan, and set Approved on verified success (handing off to $wolftac-integrate, which alone sets Done) or Todo on a documented failure. Use for Wolftac Factory QA and acceptance review.
---

# Wolftac Review

Independently verify exactly one published build. Treat the build report as a claim.

## Contract

- Team `Wolftac Digital`; label `Factory Queue`; consume `In Review`.
- Produce `Approved` on PASS/PASS WITH FIXES (handoff to `$wolftac-integrate`, the
  only stage that sets Done) or `Todo` on FAIL.
- One issue per invocation. Prefer a different model/agent than the builder.
- Reports start with `**Agent:** Codex / <actual model>`.

## Procedure

1. Confirm Linear tools. Inspect assigned `In Review` claims before choosing work.
   When latest activity is older than four hours, post
   `SWEPT: stale review claim, no activity since <timestamp>`, clear or replace the
   stale assignee, and keep `In Review`. Never return a built issue to `Todo` without
   a FAIL verdict.
2. List oldest `In Review` + `Factory Queue`, apply any lane filter, and silently skip
   unreachable `Repo:` paths. Claim by assigning `me` while preserving `In Review`;
   immediately re-read and back off silently on a race or newer review report.
3. Read the full spec, all comments, and latest BUILD REPORT. Fetch the remote and
   verify its branch resolves to the exact published SHA. Create a detached review
   worktree at that SHA; never switch the active user checkout. A missing branch or
   SHA mismatch is a FAIL.
4. Inspect `git diff <remote-default>...<reported-SHA>` and rerun the complete test plan
   from scratch. Walk every criterion with observed evidence. Always check for new
   hardcoded secrets, debug leftovers, missing input validation, crashing error paths,
   and unrelated changes.
5. Choose exactly one verdict:
   - PASS: all criteria and baseline checks pass.
   - PASS WITH FIXES: only low-risk fixes taking about 15 minutes total. Fix in the
     isolated worktree, commit, push, verify remote SHA, and rerun proof.
   - FAIL: any unmet criterion, design deviation, security issue, unavailable published
     artifact, or unreproducible verification. Do not implement major rework.
6. Re-read Linear immediately before writing. Back off if another reviewer already
   completed it. Otherwise post:

   ```text
   **REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL>**
   - **Agent:** Codex / <actual model>
   - **Criteria:** <n>/<total>; one evidence line per criterion
   - **Test plan reproduced:** <commands and results>
   - **Baseline checks:** <clean or findings>
   - **Fixes applied:** <none or SHAs>
   - **Reviewed head:** <remote branch> @ <exact verified SHA>
   - **Defects:** <none or numbered repro/expected/actual list>
   - **Suggestions (out of scope):** <future $wolftac-spec ideas>
   ```

7. On PASS, tick criteria and set `Approved` — `$wolftac-integrate` lands it after
   human approval. On FAIL, set `Todo`; the defect list becomes the next build order.
   Read back the state, report, and checkboxes. Clean up the detached worktree and stop.
