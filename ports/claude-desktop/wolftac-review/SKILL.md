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
- You consume state `In Review`; you produce `Approved` (pass → /wolftac-integrate merges after human approval) or `Todo` (fail → rework).
- Reports open with `**Agent:** <platform / model>`. Prefer reviewing work built by a
  DIFFERENT agent/model than yourself when possible — the build report's Agent line
  tells you who built it.
- Tools: claude.ai Linear connector. Not available → ask the user to enable it.

## Procedure

1. **Pick up work.** `list_issues`: team `Wolftac Digital`, state `In Review`, label
   `Factory Queue`, oldest first. If the user gave a lane filter, only consider issues
   whose Linear project name or labels contain it (case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path this chat cannot
   reach.
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
   A missing remote branch or SHA mismatch is a FAIL.

4. **Verify by execution, not inspection.** Re-run the spec's Test plan from scratch —
   never trust claimed results. Walk EVERY acceptance criterion: pass/fail with
   observed evidence. Baseline checks regardless of spec: no hardcoded secrets/tokens,
   no debug leftovers, input validation on new endpoints/forms, error paths don't
   crash, no unrelated files in the diff.

5. **Verdict — three outcomes.**
   - **PASS** — tick the acceptance checkboxes in the description, post the report,
     `save_issue` state `Approved` (/wolftac-integrate takes it from here).
   - **PASS WITH FIXES** — only minor defects (low-risk, no design change, ~15 min
     total): fix in the isolated worktree, commit, push, verify the remote head,
     re-verify, list fixes with SHAs, tick checkboxes, state `Approved`.
   - **FAIL** — any major defect (criterion unmet, design deviation, security issue,
     unreproducible verification): do NOT rework it yourself. Post a numbered defect
     list (what's wrong → repro → expected vs actual), state `Todo` — the build stage
     treats your list as its work order.

   Report format (comment on the issue):

   ```
   **REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL>**
   - **Agent:** Claude Desktop / <model>
   - **Criteria:** <n>/<total> verified — one line per criterion: ✓/✗ + evidence
   - **Test plan reproduced:** <commands run, results>
   - **Baseline checks:** <clean or findings>
   - **Fixes applied:** <none, or list with SHAs>
   - **Reviewed head:** <remote branch> @ <exact verified SHA>
   - **Defects:** <none, or numbered list>
   - **Suggestions (out of scope):** <ideas for a future /wolftac-spec>
   ```

6. **Stop.** One issue per invocation. Improvement ideas go in Suggestions, never into
   the code during a PASS.
