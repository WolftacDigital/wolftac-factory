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

## Hermes MoA routing

Preferred Hermes execution preset: `wolftac-factory-review`.

When launching this stage from Hermes, run it under:

```text
/model wolftac-factory-review --provider moa
```

Model roles inside the preset:
- **GPT 5.5** is the aggregator / acting reviewer. It owns tool calls, test execution, diff inspection, Linear state changes, and the final verdict.
- **Claude Fable 5** advises on product/spec fidelity, acceptance criteria, scope control, and reviewer judgment.
- **Gemma 4 31B QAT via LM Studio** is a low-cost local advisor for code-review sanity checks and possible defects. Treat Gemma as advisory only; never let a local-model opinion override reproduced test/build evidence.

Use MoA reference output as private counsel, not as proof. Proof still comes only from running the repo's commands/tests and inspecting real diffs. If this skill is invoked while Hermes is not already on `provider=moa`, `model=wolftac-factory-review`, do not fake model switching. Continue only if Jonathan explicitly wants the current model to run it, or ask him to relaunch/switch the session to the preset above. A skill document is loaded after the model call starts; it can prescribe the correct model but cannot itself change the active model for the current turn. In unattended/scheduled runs, never pause to ask about model routing: if the job definition requires this stage preset and it is not active, log the mismatch and exit cleanly WITHOUT claiming any issue; otherwise proceed under the active model — the report's Agent line records what actually ran.

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

If the primary Linear MCP transport is temporarily down but an authenticated Composio Linear toolset is available, continue through Composio rather than abandoning the review: use small GraphQL queries (`teams`, filtered `issues`, `issue`) to avoid complexity limits, `LINEAR_CREATE_LINEAR_COMMENT` for the report, `LINEAR_UPDATE_ISSUE` with resolved state IDs for Done/Todo and checkbox updates, then read the issue back to verify the report/state/checkboxes persisted. Treat the failed MCP call as transport state, not proof that Linear is unavailable. If a Composio Remote Workbench helper (`run_composio_tool` / `proxy_execute`) reports an expired/revoked project API key while direct `COMPOSIO_MULTI_EXECUTE_TOOL` calls in the same workflow still work, do not abandon the Linear side effects: perform the final comment/update/readback with direct multi-execute calls, keeping strict sequencing so the comment is created before the state/description update and verification readback is last.

## WSL-hosted repo review pattern

When a spec's **Repo:** path is WSL-hosted (for example `/home/... (WSL Ubuntu)`) but the active Hermes host is Windows, do not skip the issue just because Git Bash cannot see `/home/...`. Probe it through `wsl.exe`, then review in a detached temporary worktree so the active branch and uncommitted local artifacts are not disturbed. See `references/wsl-linear-factory-review-proof.md` for the exact pattern, including clean worktree checkout, lockfile dependency install, diff-scoped secret/debug scans, and Linear comment/state verification.

For WSL deploy/ops features whose criteria depend on live PM2/Tailscale behavior, use `references/wsl-live-maintenance-proxy-review.md`: do not repeat destructive live reload/stop actions from unattended cron, but do reproduce the mechanism with a scratch-port harness, verify read-only live posture, inspect the actual commit range if the branch is already merged to `origin/main`, and confirm PM2 ecosystem config filenames use a recognized `.config.cjs`/`ecosystem.config.cjs` suffix.

For live browser/API checks against WSL Agentic OS branches, especially token-gated 401 CTA proof, use `references/wsl-token-gated-live-review.md`: run throwaway `next start` servers on scratch ports, use Windows `curl` for tokenless API status, use real browser clicks/snapshots for UI proof, then kill servers and remove the detached worktree before finalizing.

For guarded router/provider status endpoints, use `references/wsl-router-status-health-review.md`: auth status codes are not enough. Inspect the authenticated JSON shape and fail any “per-provider health” criterion if the payload only lists static chain metadata without provider-level state/health/detail evidence.

## Windows detached worktree review pattern

When reviewing a Windows-hosted repo from Git Bash, create detached review worktrees with native `C:/...` paths in `git worktree add`, then access them through MSYS `/c/...` paths for shell commands. Avoid passing `/c/Users/...` to `git worktree add`; Git for Windows can interpret it as `C:/c/Users/...`, creating confusing ghost/stale worktree entries. See `references/windows-linear-factory-review-proof.md` for the pattern.

If the detached worktree has no dependencies, prefer the repo lockfile install command. If the source repo already has a valid `node_modules` and the cron budget is tight, a temporary link/copy is acceptable only for verification, and the report should state what was done. Before removing a worktree after build/test runs, delete generated heavy artifacts (`node_modules`, `.next`, `tsconfig.tsbuildinfo`, `next-env.d.ts`) inside the worktree first; these can block `git worktree remove` on Windows.

When the full test command fails due unrelated suite collection/tooling failures but the feature-targeted tests pass, do not automatically fail the issue. Reproduce the same full command on a clean `origin/main` detached worktree. If the identical failure exists on main and all changed-scope tests/build/lint/typecheck pass, record the baseline proof in the review report and treat it as non-blocking, with a Suggestions item for a separate issue.

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `In Review`, label
   `Factory Queue`, oldest first. If invoked with an argument, treat it as a lane
   filter (match Linear project name or labels, case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path is unreachable from
   this environment.
   Before choosing an unclaimed review, perform a read-first stale-claim sweep:
   inspect assignee-held `In Review` issues with label `Factory Queue` (and the lane
   filter, if any). If the most recent comment, or `startedAt`/claim time when there
   are no comments, is OLDER than 4 hours, you may post exactly
   `SWEPT: stale claim, no activity since <timestamp>` and reset the issue to `Todo`.
   If there is ANY comment younger than 4 hours, the issue is UNTOUCHABLE — never
   sweep it. This sweep is the ONLY time review posts a comment during pickup; during
   a claim-race back off you post NOTHING.
   Nothing → "Factory queue: nothing awaiting review", stop cleanly.

2. **Claim it.** Set assignee: the operator (`me`) while keeping state `In Review`.
   Immediately re-fetch with `get_issue` and verify assignee is still you. If another
   reviewer already holds it (different assignee with activity in the last 4 hours),
   or the latest comment is already a review report newer than the build report, back
   off silently: no comment, no state change, stop this tick.

3. **Gather evidence.** Read the full spec, every comment (especially the latest
   BUILD REPORT), and the actual diff: check out the branch named in the build report
   inside the spec's **Repo:**; read `git log` and `git diff <default>...HEAD`.

4. **Verify by execution, not inspection.** Re-run the spec's Test plan from scratch —
   never trust claimed results. Walk EVERY acceptance criterion: pass/fail with
   observed evidence. Baseline checks regardless of spec: no hardcoded secrets/tokens,
   no debug leftovers, input validation on new endpoints/forms, error paths don't
   crash, no unrelated files in the diff.

   **Fail-open config check:** when an acceptance criterion says a safety/compliance artifact
   must always be present (unsubscribe links, auth checks, audit writes, tenant scoping,
   suppression gates), inspect the config-missing path as well as the happy path. Optional
   env vars and fallback helpers can silently produce `undefined` and let the action continue;
   that is a blocking review defect if the requirement is mandatory. Prefer evidence such as
   `builder()` returns `undefined` → caller still sends/writes/approves → renderer omits the
   artifact, then require the build to fail closed or block before the external action.

   **Theme/brand acceptance checks:** when a spec requires zero legacy colors/classes,
   token drift-locks, or usage-rule compliance, do not restrict proof to the directories
   named in a builder's report. Search every source path that can produce rendered or
   persisted UI values, including schemas, migrations, seed/mock data, import/export scripts,
   and smoke scripts. If components render data fields such as `stage.color` directly, old
   values in those producers are blocking even when `app/` and `components/` scan clean.
   For drift-lock tests, run a negative mutation against the exact CSS variable or source
   value the criterion names; fail the review if the test only checks that token values appear
   somewhere in a file and still passes after the mapped value is changed. See
   `references/theme-brand-review-proof.md`.

5. **Verdict — three outcomes.**
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

6. **Verify Linear side effects.** After posting the review report and moving the issue,
   read the issue back (and the latest comments when available) to confirm the report exists,
   acceptance boxes were updated when applicable, and the final state is actually `Done` or
   `Todo`. Do not claim completion from the write call alone.

   **Concurrent-review race check:** after long installs/tests/builds or branch-fix pushes,
   re-fetch the issue status and latest comments immediately before any Linear write. Linear
   selection is not a lock; another reviewer may have posted a REVIEW REPORT or moved the issue
   while you were executing proof. Follow `references/concurrent-linear-review-race.md`: normally
   do not duplicate or overwrite a completed review; only add a clearly labeled follow-up report
   if your worker materially changed the branch or produced new evidence the existing report lacks.

7. **Stop.** One issue per invocation. Improvement ideas go in Suggestions, never into
   the code during a PASS.
