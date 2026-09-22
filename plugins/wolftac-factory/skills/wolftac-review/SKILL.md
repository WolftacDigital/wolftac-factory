---
name: wolftac-review
description: Independently review one In Review issue from the Wolftac Digital Linear Factory Queue at its published commit, reproduce the test plan, and set Approved on verified success (handing off to $wolftac-integrate, which alone sets Done) or Todo on a documented failure. Use for Wolftac Factory QA and acceptance review.
---

# Wolftac Review

Independently verify exactly one published build. Treat the build report as a claim.

## Contract

- Team `Wolftac Digital`; label `Factory Queue`; consume `In Review`.
- Produce `Approved` on PASS/PASS WITH FIXES (handoff to `$wolftac-integrate`, the
  only stage that sets Done), `Todo` on FAIL, or parked `In Review` assigned to
  Jonathan on NEEDS HUMAN.
- One issue per invocation. Prefer a different model/agent than the builder.
- Reports start with `**Agent:** Codex / <actual model>`.

## Procedure

1. Confirm Linear tools. Inspect assigned `In Review` claims before choosing work.
   When latest activity is older than four hours, post
   `SWEPT: stale review claim, no activity since <timestamp>`, clear or replace the
   stale assignee, and keep `In Review`. Never return a built issue to `Todo` without
   a FAIL verdict.
2. List oldest `In Review` + `Factory Queue`, apply any lane filter, and silently skip
   unreachable `Repo:` paths. **PARKED-NEEDS-HUMAN-SKIP:** Silently skip an issue
   whose latest review report is `REVIEW REPORT — NEEDS HUMAN` while it remains
   assigned to Jonathan or lacks a later Jonathan decision comment; do not comment or
   change state. Jonathan's later decision comment plus removal of his assignment
   clears the park.

   **CLAIM-IDENTITY:** Claim only through this platform's Linear toolset acting
   as the operator, with `assignee: me`; a Build/Review worker claim lives in
   `assignee`, never in `delegate`. Before writing, resolve the operator's identity
   and read the issue's state, assignee and delegate so you know what this run changes.
   Use this platform's Linear MCP issue-update tool to set `assignee: me`, keeping `In Review`.
   Immediately re-read and verify state `In Review` AND assignee equals the operator.

   **NO-BOT-TOKEN:** Never read or use `LINEAR_FACTORY_BOT_TOKEN`, hand-roll a
   Linear GraphQL or HTTP call, import `deploy/factory-gatekeeper.py` or
   `linear_bot.py` to obtain a credential, or write a helper script that fetches or
   embeds one. If the platform's Linear write tool is unavailable, stop without
   claiming and say so in the run output.

   **CLAIM-DID-NOT-TAKE:** Distinguish your own failed claim from a lost race.
   If your own mutation applied but the read-back assignee is not you (empty, or
   accompanied by a `delegate` this run set), and no other session won the claim,
   restore only what this run changed: keep `In Review` and clear a delegate you set.
   Preserve pre-existing fields; never clear another worker's delegate. Re-read to
   verify restoration, post nothing on the issue, stop, and report
   `CLAIM-DID-NOT-TAKE <ID>` with the failed read-back state, assignee and delegate
   plus the restoration result. If restoration fails, report that the issue may
   be stranded; do not silently exit.
   If another reviewer already holds it (different assignee with activity in the
   last 4 hours), state is no longer `In Review`, or the latest comment is already
   a review report newer than the build report, back off silently: no comment,
   no state change, stop this tick.
3. Read the full spec, all comments, and latest BUILD REPORT. Fetch the remote and
   verify its branch resolves to the exact published SHA. Create a detached review
   worktree at that SHA; never switch the active user checkout. A missing branch or
   SHA mismatch is a FAIL. Put the worktree in the durable scratch root —
   `D:\Wolftac\.build-worktrees\` on Windows, under the agent's home on WSL/Linux —
   named `review-<ISSUE>-<short-SHA>`. Never `C:\tmp` or `/tmp`: those get cleared, and
   a worktree that vanishes strands a `.git/worktrees/` entry in the target repo.
4. Inspect `git diff <remote-default>...<reported-SHA>` and rerun the complete test plan
   from scratch. Walk every criterion with observed evidence. Always check for new
   hardcoded secrets, debug leftovers, missing input validation, crashing error paths,
   and unrelated changes.
   **CI-RED-BLOCKS-PASS:** Before any PASS verdict, determine whether the repo has a
   GitHub remote and resolve the PR for the published branch. No GitHub remote or no
   PR → record `local-only repo` and continue the execution-verified flow. For a PR,
   enumerate all checks with `gh pr checks <n>`; check presence, not required-ness,
   controls the Factory verdict. `--required` must NOT be used to decide the verdict.
   Any FAILED check → one `[CI]` finding naming the failing job and verdict FAIL; an
   advisory/non-required red check blocks PASS exactly as a required one does. Any
   PENDING, queued, or in-progress check → post `waiting on CI` and END WITHOUT A
   VERDICT, preserving `In Review`. A GitHub/CI outage or indeterminate queue is also
   waiting and must never become a false FAIL. Only when the PR has NO checks at all
   → record `no CI checks present — execution-verified only` and proceed as today.
   When all present checks are green, continue the execution review. A green required
   check is never approval — the human merge gate is unchanged. Rerun this gate after
   any review fix changes the head.
   **REVIEW-FINDING-TAXONOMY:** Every finding carries exactly one tag; never stack
   tags on one finding:
   - `[AC-N]` — acceptance criterion with stable ID `N` is unmet.
   - `[DEFECT]` — decidable implementation defect not tied to one acceptance criterion.
   - `[SECURITY]` — security finding; first-class and greppable.
   - `[CI]` — a GitHub check failed; name the failing job.
   - `[SCOPE-CONFLICT]` — criteria/non-goals contradiction or product decision only
     Jonathan can make.
5. Choose exactly one verdict:
   - PASS: all criteria and baseline checks pass.
   - PASS WITH FIXES: only low-risk fixes taking about 15 minutes total. Fix in the
     isolated worktree, commit, push, verify remote SHA, and rerun proof.
   - FAIL: any unmet criterion, design deviation, security issue, unavailable published
     artifact, or unreproducible verification. Do not implement major rework. Each
     defect gets exactly one finding tag.
   - NEEDS HUMAN: any `[SCOPE-CONFLICT]` or finding requiring a product decision.
     Post `REVIEW REPORT — NEEDS HUMAN`, name the exact decision in one tagged
     `[SCOPE-CONFLICT]` line, assign the issue to Jonathan, and keep state `In Review`;
     do not move it. The report must say: Jonathan comments his decision, then removes
     his assignment; the issue re-enters the review queue with his decision as part
     of the work order.
   **NEEDS HUMAN is FORBIDDEN for ordinary decidable defects — decidable defects
   require FAIL; only product decisions may park an issue.**
6. Re-read Linear immediately before writing. Back off if another reviewer already
   completed it. Otherwise post:

   ```text
   **REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL | NEEDS HUMAN>**
   - **Agent:** Codex / <actual model>
   - **Criteria:** <n>/<total>; one evidence line per criterion
   - **Test plan reproduced:** <commands and results>
   - **CI:** <all checks green; no CI checks present — execution-verified only; local-only repo; or waiting/failure detail>
   - **Baseline checks:** <clean or findings>
   - **Fixes applied:** <none or SHAs>
   - **Reviewed head:** <remote branch> @ <exact verified SHA>
   - **Defects:** <none or numbered list; each finding starts with exactly one of [AC-N], [DEFECT], [SECURITY], [CI], [SCOPE-CONFLICT], then repro/expected/actual>
   - **Human decision:** <none, or [SCOPE-CONFLICT] Decide <the exact product decision needed>>
   - **Park / un-park:** <not parked, or assign to Jonathan; keep In Review; Jonathan comments his decision, removes his assignment, and the issue re-enters the review queue with that decision as work order>
   - **Suggestions (out of scope):** <future $wolftac-spec ideas>
   ```

7. On PASS, tick criteria and set `Approved` — `$wolftac-integrate` lands it after
   human approval. On FAIL, set `Todo`; the defect list becomes the next build order.
   On NEEDS HUMAN, assign Jonathan and preserve `In Review`.
   Read back the state, report, and checkboxes. Then clean up: delete heavy artifacts
   (`node_modules`, `.next`), `git worktree remove <path>`, `git worktree prune`, and
   confirm with `git worktree prune --dry-run -v 2>&1` (silent == clean; `-v` writes to
   stderr). On `Permission denied`, `rm -rf` each `.git/worktrees/<name>` prune names
   and re-confirm. Then stop.
