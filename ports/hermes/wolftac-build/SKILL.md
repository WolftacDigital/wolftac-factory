---
name: wolftac-build
description: |
  Factory stage 2 — pull the highest-priority Todo issue labeled Factory Queue from
  Linear (team Wolftac Digital), implement it on a feature branch per its spec, verify
  by executing the test plan, and move it to In Review with a build report. One issue
  per invocation.
  Use when asked to "build", "run the build stage", "work the factory queue", or on a
  recurring factory-build schedule/cron.
  Voice triggers (speech-to-text aliases): "build from the queue", "work the queue".
---

# /wolftac-build — Factory stage 2: execution

Take ONE specced issue from the Linear queue, implement it exactly to spec, prove it
works, hand it to review.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Todo`; you produce `In Review`. `Done` is set ONLY by the review stage — never by you.
- Reports open with `**Agent:** <platform / model>` (e.g. `Hermes / gpt-5.5`).
- Linear tool names vary by platform — use this profile's Linear toolset equivalents.

## Preflight
Confirm Linear tools exist (see /wolftac-spec skill's preflight). No tools → STOP and say so.

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`. If invoked with an argument (e.g. "build agentic"), treat it as a
   lane filter: only consider issues whose Linear project name or labels contain it
   (case-insensitive). Highest priority first, oldest on ties. Skip issues whose latest
   comment starts `BLOCKED:`, issues with unresolved blocking relations, and —
   SILENTLY, no comment or state change — issues whose spec **Repo:** path is
   unreachable from this environment (`BLOCKED:` is reserved for genuine spec defects).
   Queue empty → "Factory queue: nothing buildable", stop cleanly.

2. **Claim it.** State `In Progress`, assignee Jonathan. Read the FULL spec and ALL
   comments — a prior `REVIEW REPORT — FAIL` defect list is your primary work order.

3. **Locate the repo.** The spec's **Repo:** header names the working directory.
   Verify it exists and is a git repo. Missing/ambiguous → comment
   `BLOCKED: <what's unclear>`, state back to `Todo`, stop.

4. **Branch.** Use the issue's Linear-generated branch name; create from the default
   branch if needed. Never commit directly to main/master.

5. **Implement — guardrails.** Read the repo's CLAUDE.md/AGENTS.md first; match
   conventions. Build exactly to the spec's Technical approach and Acceptance criteria.
   NO scope creep — tangents go in the report as "suggest /wolftac-spec". Secrets in .env,
   never hardcoded. Blocking ambiguity → `BLOCKED: <questions>` comment, state `Todo`,
   stop. Don't guess on load-bearing decisions.

6. **Verify.** Execute the spec's Test plan literally. Every criterion needs evidence
   from running code — "looks right" is not verification.

7. **Commit.** Descriptive messages; record SHAs. Review the staged file list before
   committing: never commit artifacts (`__pycache__/`, `*.pyc`, `node_modules/`,
   `dist/`, logs); add .gitignore entries as needed.

8. **Hand off.** Comment:

   ```
   **BUILD REPORT**
   - **Agent:** Hermes / <model>
   - **Implemented:** <what was built, per spec section>
   - **Verified:** <exact commands run and results, per criterion>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Deviations from spec:** <none, or what and why>
   - **Notes for review:** <what the reviewer should check first>
   ```

   Then state `In Review`.

9. **Stop.** One issue per invocation.
