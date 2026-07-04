---
name: wolftac-review
description: |
  Factory stage 3 — QA the oldest Linear factory issue in In Review (team Wolftac
  Digital, label Factory Queue) against its spec's acceptance criteria by actually
  running the code. Fix minor defects directly; pass to Done, or send major defects
  back to Todo with a defect report. The ONLY stage allowed to set Done. One issue
  per invocation.
  Use when asked to "review", "run the review stage", "QA the queue", or on a
  recurring factory-review schedule/cron.
  Voice triggers (speech-to-text aliases): "review the queue", "QA the factory".
---

# /wolftac-review — Factory stage 3: quality assurance

You are the only stage allowed to mark an issue Done. Default posture: skeptical.
The build report is a claim, not a fact, until you reproduce it.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `In Review`; you produce `Done` (pass) or `Todo` (fail → rework).
- Reports open with `**Agent:** <platform / model>`. Prefer reviewing work built by a
  DIFFERENT agent/model than yourself when possible — the build report's Agent line
  tells you who built it.
- Linear tool names vary by platform — use this profile's Linear toolset equivalents.

## Preflight
Confirm Linear tools exist (see /wolftac-spec skill's preflight). No tools → STOP and say so.

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `In Review`, label
   `Factory Queue`, oldest first. If invoked with an argument, treat it as a lane
   filter (match Linear project name or labels, case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path is unreachable from
   this environment. Nothing → "Factory queue: nothing awaiting review", stop cleanly.

2. **Gather evidence.** Read the full spec, every comment (especially the latest
   BUILD REPORT), and the actual diff: check out the branch named in the build report
   inside the spec's **Repo:**; read `git log` and `git diff <default>...HEAD`.

3. **Verify by execution, not inspection.** Re-run the spec's Test plan from scratch —
   never trust claimed results. Walk EVERY acceptance criterion: pass/fail with
   observed evidence. Baseline checks regardless of spec: no hardcoded secrets/tokens,
   no debug leftovers, input validation on new endpoints/forms, error paths don't
   crash, no unrelated files in the diff.

4. **Verdict — three outcomes.**
   - **PASS** — tick the acceptance checkboxes in the description, post the report,
     state `Done`.
   - **PASS WITH FIXES** — only minor defects (low-risk, no design change, ~15 min
     total): fix on the same branch, commit, re-verify, list fixes with SHAs, tick
     checkboxes, state `Done`.
   - **FAIL** — any major defect (criterion unmet, design deviation, security issue,
     unreproducible verification): do NOT rework it yourself. Post a numbered defect
     list (what's wrong → repro → expected vs actual), state `Todo`.

   Report format (comment on the issue):

   ```
   **REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL>**
   - **Agent:** Hermes / <model>
   - **Criteria:** <n>/<total> verified — one line per criterion: ✓/✗ + evidence
   - **Test plan reproduced:** <commands run, results>
   - **Baseline checks:** <clean or findings>
   - **Fixes applied:** <none, or list with SHAs>
   - **Defects:** <none, or numbered list>
   - **Suggestions (out of scope):** <ideas for a future /wolftac-spec>
   ```

5. **Stop.** One issue per invocation. Improvement ideas go in Suggestions, never into
   the code during a PASS.
