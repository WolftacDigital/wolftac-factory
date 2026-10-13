---
name: wolftac-review
description: Factory stage 3 — QA the oldest Linear factory issue in In Review (team Wolftac Digital, label Factory Queue) against its spec's acceptance criteria by actually running the code. Passing verdicts set Approved (only /wolftac-integrate sets Done, after an approved merge). One issue per invocation. NOTE - from Claude Desktop this stage can only run if the chat has file/terminal access to the target repo; otherwise it summarizes what's awaiting review and defers to a coding agent.
---

# /wolftac-review — Factory stage 3: quality assurance

You verify; /wolftac-integrate lands. Your passing verdict is **Approved** — Done
belongs exclusively to the integrate stage once the branch is actually merged.
Default posture: skeptical. The build report is a claim, not a fact, until you
reproduce it.

## Desktop reality check (do this first)
Reviewing requires running the code in the spec's **Repo** (git, tests). From Claude
Desktop that requires an MCP server with filesystem/terminal access. Without it:
summarize what's awaiting review, tell the user to run /wolftac-review from Claude Code,
Codex, or Hermes, and STOP. Never mark an issue Approved based on reading reports alone.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `In Review`; you produce `Approved` (pass → /wolftac-integrate
  merges after human approval), `Todo` (fail → rework), or parked `In Review`
  assigned to Jonathan (needs product decision).
- Reports open with `**Agent:** <platform / model>`. Prefer reviewing work built by a
  DIFFERENT agent/model than yourself when possible — the build report's Agent line
  tells you who built it.
- Tools: claude.ai Linear connector. Not available → ask the user to enable it.

## Autonomous-run override

This skill is the complete process for this stage and is built for unattended, looped
runs. It takes precedence over any general-purpose workflow skills or plugins loaded in
the session (for example superpowers' brainstorming, writing-plans, executing-plans,
using-git-worktrees, finishing-a-development-branch, or a "check for a skill before
every task" rule):
- Do not invoke those process skills during a run of this stage.
- Never stop mid-run to ask a question or wait for confirmation; use only the stop,
  hand-off, and reporting paths this skill defines.
- Never write plan, spec, or design documents into the target repo; the Linear issue
  is the spec.
- Create and remove worktrees only as this skill describes, and never merge locally or
  offer merge options outside this skill's own approval gate.

## Procedure

1. **Pick up work.** `list_issues`: team `Wolftac Digital`, state `In Review`, label
   `Factory Queue`, oldest first. If the user gave a lane filter, only consider issues
   whose Linear project name or labels contain it (case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path this chat cannot
   reach.
   **PARKED-NEEDS-HUMAN-SKIP:** Silently skip an issue whose latest review report is
   `REVIEW REPORT — NEEDS HUMAN` while it remains assigned to Jonathan or lacks a
   later Jonathan decision comment. Do not comment or change state. Jonathan's later
   decision comment plus removal of his assignment clears the park.
   Before choosing an unclaimed review, perform a read-first stale-claim sweep:
   inspect assignee-held `In Review` issues with label `Factory Queue` (and the lane
   filter, if any). If the most recent comment, or `startedAt`/claim time when there
   are no comments, is OLDER than 4 hours, you may post exactly
   `SWEPT: stale review claim, no activity since <timestamp>`, clear or replace the
   stale assignee, and keep the issue in `In Review`. Never return a built issue to
   `Todo` without an actual FAIL verdict. If there is ANY comment younger than 4 hours,
   the issue is UNTOUCHABLE — never
   sweep it. This sweep is the ONLY time review posts a comment during pickup; during
   a claim-race back off you post NOTHING.
   Nothing → "Factory queue: nothing awaiting review", stop cleanly.

2. **Claim it.** `save_issue`: assignee `me` while keeping state `In Review`.
   Immediately re-fetch with `get_issue` and verify assignee is still you. If another
   reviewer already holds it (different assignee with activity in the last 4 hours),
   or the latest comment is already a review report newer than the build report, back
   off silently: no comment, no state change, stop this tick.

3. **Gather evidence safely.** Read the full spec, every comment, and the latest BUILD
   REPORT. Fetch the remote, verify its published branch resolves to the exact reported
   SHA, then create a detached review worktree at that SHA. Never switch the active
   user checkout. Read `git log` and `git diff <remote-default>...<reported-SHA>`.
   A missing remote branch or SHA mismatch is a FAIL. Put the worktree in the durable
   scratch root — `D:\Wolftac\.build-worktrees\` on Windows, under the agent's home on
   WSL/Linux — named `review-<ISSUE>-<short-SHA>`. Never `C:\tmp` or `/tmp`: those get
   cleared, and a vanished worktree strands `.git/worktrees/` metadata in the target repo.

4. **Verify by execution, not inspection.** Re-run the spec's Test plan from scratch —
   never trust claimed results. Walk EVERY acceptance criterion: pass/fail with
   observed evidence. Baseline checks regardless of spec: no hardcoded secrets/tokens,
   no debug leftovers, input validation on new endpoints/forms, error paths don't
   crash, no unrelated files in the diff.

   **CI-RED-BLOCKS-PASS:** Before any PASS verdict, determine whether the repo has a
   GitHub remote and resolve the PR for the published branch. No GitHub remote or no
   PR → record `local-only repo` and continue the execution-verified flow. For a PR,
   enumerate all checks with `gh pr checks <n>`; check presence, not required-ness,
   controls the Factory verdict. `--required` must NOT be used to decide the verdict.
   Any FAILED check produces one `[CI]` finding naming the failing job and verdict
   FAIL; an advisory/non-required red check blocks PASS exactly as a required one
   does. Any PENDING, queued, or in-progress check → post `waiting on CI` and END
   WITHOUT A VERDICT, leaving `In Review`. A GitHub/CI outage or indeterminate queue
   is also waiting and must never become a false FAIL. Only when the PR has NO checks
   at all → record `no CI checks present — execution-verified only` and proceed as
   today. When all present checks are green, continue the execution review. A green
   required check is never approval — the human merge gate is unchanged. Rerun this
   gate after any review fix changes the branch head.

   **REVIEW-FINDING-TAXONOMY:** Every review finding carries exactly one tag; never
   stack tags on one finding:
   - `[AC-N]` — acceptance criterion with stable ID `N` is unmet.
   - `[DEFECT]` — decidable implementation defect not tied to one acceptance criterion.
   - `[SECURITY]` — security finding; first-class and greppable.
   - `[CI]` — a GitHub check failed; name the failing job.
   - `[SCOPE-CONFLICT]` — criteria/non-goals contradiction or product decision only
     Jonathan can make.

5. **Verdict — four outcomes.**
   - **PASS** — tick the acceptance checkboxes in the description, post the report,
     `save_issue` state `Approved` (/wolftac-integrate takes it from here).
   - **PASS WITH FIXES** — only minor defects (low-risk, no design change, ~15 min
     total): fix in the isolated worktree, commit, push, verify the remote head,
     re-verify, list fixes with SHAs, tick checkboxes, state `Approved`.
   - **FAIL** — any major defect (criterion unmet, design deviation, security issue,
     unreproducible verification): do NOT rework it yourself. Post a numbered defect
     list; every finding gets exactly one tag plus what's wrong → repro → expected vs
     actual. Set state `Todo` — the build stage treats your list as its work order.
   - **NEEDS HUMAN** — any `[SCOPE-CONFLICT]` finding or finding requiring a product
     decision. Post `REVIEW REPORT — NEEDS HUMAN`, name the exact decision in one
     tagged `[SCOPE-CONFLICT]` line, assign the issue to Jonathan, and keep state
     `In Review`; do NOT move it. The report must instruct: Jonathan comments his
     decision, then removes his assignment; the issue re-enters the review queue with
     his decision as part of the work order.

   **NEEDS HUMAN is FORBIDDEN for ordinary decidable defects — decidable defects
   require FAIL; only product decisions may park an issue.**

   Report format (comment on the issue):

   ```
   **REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL | NEEDS HUMAN>**
   - **Agent:** Claude Desktop / <model>
   - **Criteria:** <n>/<total> verified — one line per criterion: ✓/✗ + evidence
   - **Test plan reproduced:** <commands run, results>
   - **CI:** <all checks green; no CI checks present — execution-verified only; local-only repo; or waiting/failure detail>
   - **Baseline checks:** <clean or findings>
   - **Fixes applied:** <none, or list with SHAs>
   - **Reviewed head:** <remote branch> @ <exact verified SHA>
   - **Defects:** <none, or numbered list; each finding starts with exactly one of [AC-N], [DEFECT], [SECURITY], [CI], [SCOPE-CONFLICT]>
   - **Human decision:** <none, or [SCOPE-CONFLICT] Decide <the exact product decision needed>>
   - **Park / un-park:** <not parked, or assign to Jonathan; keep In Review; Jonathan comments his decision, removes his assignment, and the issue re-enters the review queue with that decision as work order>
   - **Suggestions (out of scope):** <ideas for a future /wolftac-spec>
   ```

6. **Stop.** Tear the worktree down first: delete generated heavy artifacts
   (`node_modules`, `.next`, `tsconfig.tsbuildinfo`, `next-env.d.ts`), then
   `git worktree remove <path>` and `git worktree prune`. Confirm with
   `git worktree prune --dry-run -v 2>&1` — silent means clean (`-v` writes to stderr).
   If prune reports `Permission denied`, `rm -rf` each `.git/worktrees/<name>` it names
   and re-confirm. Then: one issue per invocation. Improvement ideas go in Suggestions,
   never into the code during a PASS.
