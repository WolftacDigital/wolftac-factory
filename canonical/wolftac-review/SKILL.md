---
name: wolftac-review
description: |
  Factory stage 3 — QA the oldest Linear factory issue in In Review (team Wolftac
  Digital, label Factory Queue) against its spec's acceptance criteria by actually
  running the code. Fix minor defects directly; pass to Approved (handing off to
  wolftac-integrate, which alone sets Done after an approved merge), or send major
  defects back to Todo with a defect report. One issue per invocation.
  Use when asked to "review", "run the review stage", "QA the queue", or on a
  recurring factory-review schedule/cron.
  Voice triggers (speech-to-text aliases): "review the queue", "QA the factory".
---

# /wolftac-review — Factory stage 3: quality assurance

You verify; wolftac-integrate lands. Your passing verdict is **Approved** — Done
belongs exclusively to the integrate stage once the branch is actually merged.
Default posture: skeptical. The build report is a claim, not a fact, until you
reproduce it.

## Hermes MoA routing

Preferred Hermes execution preset: `wolftac-factory-review`.

When launching this stage from Hermes, run it under:

```text
/model wolftac-factory-review --provider moa
```

Model roles inside the preset:
- **GPT-5.6 Sol** is the aggregator / acting reviewer. It owns tool calls, test execution, diff inspection, Linear state changes, and the final verdict.
- **Claude Fable 5** advises on product/spec fidelity, acceptance criteria, scope control, and reviewer judgment.
- **Gemma 4 31B QAT via LM Studio** is a low-cost local advisor for code-review sanity checks and possible defects. Treat Gemma as advisory only; never let a local-model opinion override reproduced test/build evidence.

Use MoA reference output as private counsel, not as proof. Proof still comes only from running the repo's commands/tests and inspecting real diffs. If this skill is invoked while Hermes is not already on `provider=moa`, `model=wolftac-factory-review`, do not fake model switching. Continue only if Jonathan explicitly wants the current model to run it, or ask him to relaunch/switch the session to the preset above. A skill document is loaded after the model call starts; it can prescribe the correct model but cannot itself change the active model for the current turn. In unattended/scheduled runs, never pause to ask about model routing: if the job definition requires this stage preset and it is not active, log the mismatch and exit cleanly WITHOUT claiming any issue; otherwise proceed under the active model — the report's Agent line records what actually ran.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `In Review`; you produce `Approved` (pass → wolftac-integrate
  merges after human approval), `Todo` (fail → rework), or parked `In Review`
  assigned to Jonathan (needs product decision).
- Reports open with `**Agent:** <platform / model>`. Prefer reviewing work built by a
  DIFFERENT agent/model than yourself when possible — the build report's Agent line
  tells you who built it.
- Linear tool names vary by platform — use this profile's Linear toolset equivalents.

## Preflight
Confirm Linear tools exist (see /wolftac-spec skill's preflight). No tools → STOP and say so.

If the primary Linear MCP transport is temporarily down but an authenticated Composio Linear toolset is available, continue through Composio rather than abandoning the review: use small GraphQL queries (`teams`, filtered `issues`, `issue`) to avoid complexity limits, `LINEAR_CREATE_LINEAR_COMMENT` for the report, `LINEAR_UPDATE_ISSUE` with resolved state IDs for Approved/Todo and checkbox updates, then read the issue back to verify the report/state/checkboxes persisted. Treat the failed MCP call as transport state, not proof that Linear is unavailable. If a Composio Remote Workbench helper (`run_composio_tool` / `proxy_execute`) reports an expired/revoked project API key while direct `COMPOSIO_MULTI_EXECUTE_TOOL` calls in the same workflow still work, do not abandon the Linear side effects: perform the final comment/update/readback with direct multi-execute calls, keeping strict sequencing so the comment is created before the state/description update and verification readback is last.

## WSL-hosted repo review pattern

When a spec's **Repo:** path is WSL-hosted (for example `/home/... (WSL Ubuntu)`) but the active Hermes host is Windows, do not skip the issue just because Git Bash cannot see `/home/...`. Probe it through `wsl.exe`, then review in a detached temporary worktree so the active branch and uncommitted local artifacts are not disturbed. See `references/wsl-linear-factory-review-proof.md` for the exact pattern, including clean worktree checkout, lockfile dependency install, diff-scoped secret/debug scans, and Linear comment/state verification.

For WSL deploy/ops features whose criteria depend on live PM2/Tailscale behavior, use `references/wsl-live-maintenance-proxy-review.md`: do not repeat destructive live reload/stop actions from unattended cron, but do reproduce the mechanism with a scratch-port harness, verify read-only live posture, inspect the actual commit range if the branch is already merged to `origin/main`, and confirm PM2 ecosystem config filenames use a recognized `.config.cjs`/`ecosystem.config.cjs` suffix.

For live browser/API checks against WSL Agentic OS branches, especially token-gated 401 CTA proof, use `references/wsl-token-gated-live-review.md`: run throwaway `next start` servers on scratch ports, use Windows `curl` for tokenless API status, use real browser clicks/snapshots for UI proof, then kill servers and remove the detached worktree before finalizing.

For guarded router/provider status endpoints, use `references/wsl-router-status-health-review.md`: auth status codes are not enough. Inspect the authenticated JSON shape and fail any “per-provider health” criterion if the payload only lists static chain metadata without provider-level state/health/detail evidence.

For production backfill/data-repair reviews, use `references/production-db-backfill-review.md`: verify live effects with read-only aggregate SQL, confirm fail-closed behavior when secrets are unavailable, check no fabricated foundation rows, verify consent/suppression invariants in both true and false/missing paths, and report only counts/IDs/statuses — never PII or secrets.

For reviews whose acceptance criteria require proving a Supabase production schema/data migration already landed, prefer a bounded read-only aggregate query through the available authenticated Supabase toolchain rather than trusting the build report. If direct Supabase MCP is unavailable but Composio Supabase is active, use `COMPOSIO_SEARCH_TOOLS` for the Supabase read-only SQL tool, then run `SUPABASE_BETA_RUN_SQL_QUERY` with `read_only: true` (or the returned read-only query tool) against the exact project ref. Keep projections small: `information_schema.columns`, `count(*)`, `count(*) filter (...)`, and no PII. Record only schema shape, row counts, default/null summaries, and the project ref in the review report.

## Windows detached worktree review pattern

When reviewing a Windows-hosted repo from Git Bash, create detached review worktrees under `D:\Wolftac\.build-worktrees\`, one directory per review named `review-<ISSUE>-<short-SHA>`. Pass the native form to `git worktree add` (`D:/Wolftac/.build-worktrees/review-WD-291-ff1ae93`) and use the MSYS form for shell commands (`/d/Wolftac/.build-worktrees/review-WD-291-ff1ae93`). Create the root if it does not exist. Avoid passing `/c/Users/...` or `/d/...` to `git worktree add`; Git for Windows can interpret those as `C:/c/Users/...`, creating confusing ghost/stale worktree entries. See `references/windows-linear-factory-review-proof.md` for the pattern.

Never put a worktree in `C:\tmp`, `C:/tmp`, or `/tmp`. Those are cleared by reboots and cleanup tools, and a worktree that disappears out from under git strands a `.git/worktrees/` entry in the *target* repo for its owner to clean up — 29 such entries accumulated in `wolftac-crm` between WD-185 and WD-300 before this rule existed.

If the detached worktree has no dependencies, prefer the repo lockfile install command. If the source repo already has a valid `node_modules` and the cron budget is tight, a temporary link/copy is acceptable only for verification, and the report should state what was done. **For Next.js/Turbopack builds on Windows, prefer a real local copy:** a junction/symlink whose target sits outside the worktree can make Turbopack abort with `Symlink [project]/node_modules is invalid, it points out of the filesystem root`. A link may still be used for tests/typecheck/lint, but if build hits that error, replace it with a local copy and rerun the build before judging the branch. Before removing a worktree after build/test runs, delete generated heavy artifacts (`node_modules`, `.next`, `tsconfig.tsbuildinfo`, `next-env.d.ts`) inside the worktree first; these can block `git worktree remove` on Windows.

## Remote-ref maintenance review pattern

Some factory issues intentionally have no feature branch or working-tree diff because the build performed remote repository maintenance (for example deleting merged branches after a staging gate). Do not rerun destructive actions such as `git push origin --delete`, deployment changes, or branch recreation during review. Reproduce the proof read-only: inspect the SHA record comment and its timestamp, read the build report, verify deployment/status evidence with `gh api` or the relevant deployment tool, probe the live URL if the spec requires it, and run `git ls-remote --heads origin` from a detached worktree to confirm the exact branch pattern is absent while `main` and unrelated heads remain. A rendered app auth/sign-in gate is valid live-probe evidence when the criterion is that the integrated app is serving and the gate is app-owned rather than provider SSO or an error page.

When ticking acceptance checkboxes after a remote-ref maintenance PASS, preserve the current Linear description as much as the platform allows. Prefer minimal checkbox-state updates over hand-rewriting the whole description; if a full description update is unavoidable, keep issue-link text and all technical commands intact, then immediately read the issue back to confirm the checkboxes, final state, and review report persisted.

If this profile's Linear MCP wrapper only supports full-description replacement and the shell has `LINEAR_API_KEY` available, use a small internal GraphQL helper instead of hand-rewriting from model context: query `issue(id:) { id description }`, replace only the intended `- [ ]` acceptance lines with `- [x]`, call `issueUpdate(id:, input:{description: ...})`, and read the issue back through MCP. Never print the API key; print only counts/status.

When the full test command fails due unrelated suite collection/tooling failures but the feature-targeted tests pass, do not automatically fail the issue. Reproduce the same full command on a clean `origin/main` detached worktree. If the identical failure exists on main and all changed-scope tests/build/lint/typecheck pass, record the baseline proof in the review report and treat it as non-blocking, with a Suggestions item for a separate issue.

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `In Review`, label
   `Factory Queue`, oldest first. If invoked with an argument, treat it as a lane
   filter (match Linear project name or labels, case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path is unreachable from
   this environment.
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

2. **Claim it.** Set assignee: the operator (`me`) while keeping state `In Review`.
   Immediately re-fetch with `get_issue` and verify assignee is still you. If another
   reviewer already holds it (different assignee with activity in the last 4 hours),
   or the latest comment is already a review report newer than the build report, back
   off silently: no comment, no state change, stop this tick.

3. **Gather evidence safely.** Read the full spec, every comment, and the latest BUILD
   REPORT. Fetch the remote, verify its published branch resolves to the exact reported
   SHA, then create a detached review worktree at that SHA. Never switch the active
   user checkout. Read `git log` and `git diff <remote-default>...<reported-SHA>`.
   A missing remote branch or SHA mismatch is a FAIL. Put the worktree in the durable
   scratch root — `D:\Wolftac\.build-worktrees\` on Windows, a directory under the
   agent's home on WSL/Linux — never `C:\tmp` or `/tmp`; see "Windows detached worktree
   review pattern" below.

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
   FAIL; an advisory/non-required red check blocks PASS exactly as a required one does.
   Any PENDING, queued, or in-progress check → post a short `waiting on CI`
   note and END WITHOUT A VERDICT, leaving `In Review`. A GitHub/CI outage or
   indeterminate queue result is also waiting and must never become a false FAIL.
   Only when the PR has NO checks at all → record
   `no CI checks present — execution-verified only` and proceed as today. When all
   present checks are green, continue the execution review. A green required check
   is never approval — the human merge gate is unchanged. After any review fix
   changes the head, rerun the gate and wait for the new checks.

   **Fail-open config check:** when an acceptance criterion says a safety/compliance artifact
   must always be present (unsubscribe links, auth checks, audit writes, tenant scoping,
   suppression gates), inspect the config-missing path as well as the happy path. Optional
   env vars and fallback helpers can silently produce `undefined` and let the action continue;
   that is a blocking review defect if the requirement is mandatory. Prefer evidence such as
   `builder()` returns `undefined` → caller still sends/writes/approves → renderer omits the
   artifact, then require the build to fail closed or block before the external action.

   **Citation-backed content checks:** when acceptance requires cited research, source-backed claims,
   or claim-to-source mapping, do not accept URL presence plus a syntactic citation ID as proof.
   Submit an intentionally false factual claim with an unrelated but public URL and inspect whether
   the system still marks the claim and every downstream readiness check as cited/ready. Require
   either reproduced source-content support, an immutable approved internal-proof reference, or an
   explicit human claim-approval gate; a generic templated “research brief” and indiscriminate
   claim-to-all-sources mapping are blocking fail-open evidence defects. Also test legacy-data
   migration with migration override env vars absent when the previous release had a hard-coded
   default path; a fixture that injects the new override proves the mapper, not automatic cutover.

   **Authentication callback and invitation-flow checks:** when acceptance depends on email-link,
   OAuth, PKCE, SSO, password-reset, or invitation callbacks, do not accept an E2E that signs in
   directly with a fixture password or pre-seeded cookie. Exercise the shipped callback route with
   a real or intentionally invalid callback parameter and distinguish its expected invalid-link
   response from a generic exception path. Inspect whether the handler receives every request/context
   object it passes downstream, and verify that an authenticated account without an active membership
   or staff grant cannot use an otherwise valid auth session to enter an invitation-only product.

   **Human-approval and cross-invocation artifact checks:** for merge/deploy/publish or other
   privileged workflows, do not accept `author == owner` as human proof when workers use the
   owner's account. Verify that automation cannot mint an accepted approval, and prove the
   gate-tested rebased/artifact SHA is fetchable from a fresh clone or is deterministically
   reconstructed with gates rerun. A temporary worktree/local object is not a durable handoff.
   When the design depends on a dedicated bot/service identity, inspect the live authenticated
   viewer and workspace user inventory, then prove both sides: worker-authored approval is
   rejected and a genuine human approval from a different immutable ID is accepted end to end.
   A missing bot identity makes the positive path unverified, not “operator setup complete.”
   A provisioned bot account and an ad hoc bot-auth fixture are still insufficient:
   inspect the delivered onboarding/deploy/account-selection path and the actual scheduled
   worker's viewer. Probe every Linear transport available to that actual runtime,
   not only the preferred bot path. Prove the privileged worker is capability-isolated
   to the bot identity and cannot also write through the human credential: a process
   that authenticates as the bot through one transport but as the human through a
   default MCP is still dual-capable, and prompt text saying “never use MCP” is not
   isolation. If production bot-only wiring is left as follow-up, the delivered
 approval path is inoperable or self-approvable; treat that as blocking unless the
 approved spec explicitly makes wiring a separate prerequisite.
 If Jonathan later amends the approved spec to move runtime capability isolation into a
 named successor issue, re-read the current issue description and comments before judging:
 do not keep failing the current issue against superseded scope. Still audit the delivered
 contract for internal consistency. A skill cannot mandate bot-authenticated GraphQL while
 also telling workers to load or call the session/default Linear MCP/toolset; remove that
 contradiction and add negative semantic/invariant coverage that rejects default-transport
 instructions. A low-risk text-and-guard correction may be PASS WITH FIXES only after the
 remote head is pushed, re-fetched, and every gate is rerun.
 Require every proof pack to persist the exact default ref/SHA used for gates; a later worker
   cannot enforce “default has not moved” from ahead/behind counts or an unpublished rebased SHA.
   For Telegram/Slack/webhook relays, verify immutable source-event provenance that the merge
   worker cannot mint rather than assuming a relayed Linear comment is human-authored.
   Follow `references/human-approval-and-rebased-artifact-review.md`. Its Hermes-specific
   section covers binding preflight proof to the real `HERMES_HOME`, enumerating direct
   Linear/Composio/delegated transports, immutable bot IDs, profile-scoped cron paths,
   subprocess credential-inheritance probes, and persisted-job/session timeline checks.
   A clean profile YAML or successful one-shot chat is not scheduled-runtime proof: read
   the current job prompt and newest run output, and require the end-to-end fixture to
   postdate runtime creation in that profile's session inventory.
   Treat self-approvable gates, an unexercised positive identity path, missing default-head
   proof, fresh-clone-missing approved artifacts, or fixture-only isolation as blocking defects.

   **Automation bypass / URL secret masking checks:** when reviewing tooling that passes a
   secret through query params (for example Vercel `x-vercel-protection-bypass`), do not
   accept raw-string redaction alone. Force a regression that logs an error containing the
   constructed URL and assert that raw, `encodeURIComponent`, and `URLSearchParams`-encoded
   forms of a fake secret are all masked. See
   `references/preview-bypass-secret-masking-review.md`.

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

   **Public map / premium homepage checks:** when acceptance criteria require a Mapbox globe,
   terrain, hillshade, route arcs, map controls, hero media, responsive first viewport, or
   Lighthouse thresholds, follow `references/public-map-homepage-review.md`. Do not treat
   “Mapbox runtime loaded” as enough: inspect resource status by class, scroll lazy map
   sections into view, exercise filters/tour/reset/terrain, and verify both complete Mapbox
   blocking and partial source failures (for example terrain DEM 403/401 after style load).
   A blank/unstable canvas, misleading terrain-control state, uncaught page errors, or a
   missed explicit Lighthouse threshold is a blocking review defect. If homepage program links
   or map controls are in scope, also run the lazy-init/fallback probe in
   `references/homepage-map-lazy-init-review.md`: click a map filter before/while the map
   engine initializes, click a homepage program/service link, capture `pageerror`, and verify
   unusable placeholder Mapbox tokens fall back cleanly instead of throwing.

   **Static route shell / no-JS checks:** when acceptance criteria say every route uses a
   shared shell, preserves clean legacy routes, or keeps primary content progressively
   enhanced, follow `references/static-route-shell-no-js-review.md`. Do not trust a passing
   manifest/smoke/e2e suite until you inspect whether tests skip `/`, `/404`, or utility
   routes. With JavaScript disabled, explicitly probe `/` and at least one generated route
   for real nav links, footer/legal links, active-route state, CTA links, H1/body content,
   and noscript copy. If generated pages pass but the homepage still says JavaScript is
   required or has zero nav links/footer, fail the review and call out the test blind spot.

   **Static-site CTA contrast checks:** for marketing/static pages with shared button
   styles, DOM text and href checks are not enough. Inspect the rendered CTA bands visually
   and/or with computed styles to confirm primary and secondary labels remain readable on
   their actual backgrounds in light and dark sections. A low-risk CSS-only contrast fix can
   be handled as PASS WITH FIXES only after route/e2e/smoke tests rerun, browser/vision proof
   confirms the label is visible, and the pushed remote head is verified. See
   `references/static-site-cta-contrast-review.md`.

   **Editorial static-site review checks:** for blog/article/library issues with migrated
   legacy copy, unique metadata, filterable report cards, newsletter failure states, and
   stacked PRs, follow `references/editorial-static-site-review-proof.md`. In particular,
   diff against the actual PR base branch when stacked, run smoke/e2e against an explicit
   scratch-port `BASE_URL` rather than a default localhost server, normalize live legacy
   Webflow text before judging copy preservation, use browser/vision proof for readable
   editorial layout, and use focused secret/debug patterns to avoid false positives from
   public-token documentation.

   **Content-drift reconciliation checks:** when an issue reconciles live/deployed
   skills, runbooks, or generated documentation back into a repository, follow
   `references/content-drift-reconciliation-review.md`. Freeze the authoritative drift
   input and reviewed SHA, normalize line endings before content comparison, account
   for every deleted live-side line, compare imported support files, verify every linked
   reference exists, and mutate only the acceptance-criteria checkbox block in Linear.

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
     state `Approved` (wolftac-integrate takes it from here).
   - **PASS WITH FIXES** — only minor defects (low-risk, no design change, ~15 min
     total): fix in the isolated worktree, commit, push, verify the remote head,
     re-verify, list fixes with SHAs, tick checkboxes, state `Approved`.
   - **FAIL** — any major defect (criterion unmet, design deviation, security issue,
     unreproducible verification): do NOT rework it yourself. Post a numbered defect
     list; every finding gets exactly one tag plus what's wrong → repro → expected vs
     actual. Set state `Todo`.
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
   - **Agent:** Hermes / <model>
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

6. **Verify Linear side effects.** After posting the review report and moving the issue,
   read the issue back (and the latest comments when available) to confirm the report exists,
   acceptance boxes were updated when applicable, and the final state is actually `Approved`,
   `Todo`, or parked `In Review` assigned to Jonathan. When verifying Markdown checkboxes,
   accept Linear's normalized uppercase `- [X]`
   form as checked; GraphQL writes using `- [x]` may read back as `- [X]`. Do not claim
   completion from the write call alone.

   **Concurrent-review race check:** after long installs/tests/builds or branch-fix pushes,
   re-fetch the issue status and latest comments immediately before any Linear write. Linear
   selection is not a lock; another reviewer may have posted a REVIEW REPORT or moved the issue
   while you were executing proof. Follow `references/concurrent-linear-review-race.md`: normally
   do not duplicate or overwrite a completed review; only add a clearly labeled follow-up report
   if your worker materially changed the branch or produced new evidence the existing report lacks.
   In scheduled/cron runs, if readback shows the issue has already moved (Approved/Todo/Done) with a newer REVIEW
   REPORT, treat the tick as a clean concurrent-review backoff: cancel the local Linear write,
   perform best-effort temp worktree cleanup, and final-report the backoff/verified state rather
   than posting another Linear comment.

7. **Stop.** Tear the worktree down first, from the target repo: delete generated heavy
   artifacts (`node_modules`, `.next`, `tsconfig.tsbuildinfo`, `next-env.d.ts`), then
   `git worktree remove <path>` and `git worktree prune`. Confirm with
   `git worktree prune --dry-run -v 2>&1` — silent output means clean (`-v` writes to
   stderr, so a bare pipe sees nothing and looks falsely clean). If prune reports
   `Permission denied` — common when the target repo lives under OneDrive, where git's
   own recursive delete fails but the shell's does not — `rm -rf` each
   `.git/worktrees/<name>` directory prune names, then re-confirm. Then: one issue per
   invocation. Improvement ideas go in Suggestions, never into the code during a PASS.
