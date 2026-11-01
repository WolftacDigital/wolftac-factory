# Windows-hosted Linear factory review proof pattern

Use this when `/wolftac-review` is running on Windows/Git Bash and the Linear spec's `Repo:` is a Windows path such as `C:/Users/.../repo`.

## Goals

- Review in a detached temporary worktree so the active branch and uncommitted local artifacts are not disturbed.
- Produce proof from real commands: diff, tests, lint/typecheck/build, baseline checks.
- Avoid Git-for-Windows path translation traps and cleanup failures.

## Pattern

1. Resolve paths deliberately:
   - Repo for Git commands: use the spec's native `C:/...` path with `git -C`.
   - Worktree root: **always `D:\Wolftac\.build-worktrees\`**, one directory per review named `review-<ISSUE>-<short-SHA>`. Never `C:/tmp` or `/tmp` — a worktree that gets cleared out from under git strands a `.git/worktrees/` entry in the target repo (see step 8).
   - Worktree path in `git worktree add`: native form, e.g. `D:/Wolftac/.build-worktrees/review-WD-185-77fe7c9`.
   - Shell `workdir`: MSYS form for the same worktree, e.g. `/d/Wolftac/.build-worktrees/review-WD-185-77fe7c9`.
   - Do **not** pass `/c/Users/...` or `/d/...` to `git worktree add`; Git for Windows may create/register `C:/c/Users/...` instead.

2. Create the detached worktree:

   ```sh
   repo='C:/Users/logan_vwagmmf/OneDrive/Wolftac Digital/Codex/wolftac-crm'
   branch='jonathan/wd-185-build-crm-conversations-unified-inbox-email-with-approval'
   wt='D:/Wolftac/.build-worktrees/review-WD-185-77fe7c9'

   mkdir -p /d/Wolftac/.build-worktrees
   git -C "$repo" fetch origin "$branch"
   git -C "$repo" worktree add --detach "$wt" "origin/$branch"
   git -C "$wt" status --short --branch
   git -C "$wt" log --oneline --decorate -3
   ```

3. Gather diff proof:

   ```sh
   git -C "$wt" diff --stat origin/main...HEAD
   git -C "$wt" diff --name-only origin/main...HEAD
   git -C "$wt" log --oneline --decorate origin/main..HEAD
   ```

4. Dependency handling:
   - Prefer the project's lockfile install command when a lockfile exists.
   - If no lockfile is present and the source repo already has a known-good `node_modules`, a temporary verification-only reuse is acceptable in a cron review. State this in the report.
   - If no lockfile exists and you must run `npm install --no-audit --no-fund` inside the detached worktree, remember it may generate a local `package-lock.json`. Treat that as a verification artifact, not a source diff; remove it before cleanup and do not report it as an unrelated committed change unless it was present in the reviewed branch diff.
   - For Next/Turbopack projects on Windows, **copy** `node_modules` into the detached worktree rather than using a junction/symlink to the source repo. Turbopack can reject a linked `node_modules` as outside the project filesystem root (`Symlink [project]/node_modules is invalid, it points out of the filesystem root`) even though lint/typecheck/tests may run. Remove any failed link before copying, then re-run build/test proof from the copied dependency tree.
   - Astro/Vercel builds can also reject a junction: the adapter may try to recreate the external node_modules symlink inside `.vercel/output/functions/_render.func` and fail with EPERM. Replace the junction with a real local dependency copy and rerun the complete gate; a browser/dev-server pass does not waive bundling failure. For large Windows trees, use robocopy from a scratch PowerShell script (`/E /MT:32 /R:0 /W:0`), accepting exit codes below 8 as copy success; remove junctions with `os.rmdir` before renaming the local copy so cleanup never traverses the source dependency tree.
   - Do not turn dependency absence or npm cache/install setup failures into durable negative conclusions; they are setup state. Capture the workaround used and keep pursuing real proof.

5. Re-run proof commands:
   - For native Windows Python or `uv.exe` probes, pass the active profile's scratch directory explicitly to `tempfile.TemporaryDirectory(dir=...)` and pytest `--basetemp`; do not assume inherited `TMPDIR` resolves there. Check emitted paths before accepting proof, because an inherited Windows system-temp value can survive into the subprocess. If a probe used the wrong temporary root, clean its own artifacts and rerun with the explicit native scratch path.
   - Full spec command, e.g. `npm run test:crm`.
   - On Windows, `npm run` executes scripts through `cmd.exe`. A package script such as `./node_modules/.bin/tsx ...` or `./node_modules/.bin/tsc ...` can therefore fail with `'.' is not recognized` even though the tool and code are healthy. Reproduce the canonical command and record that portability failure, then run the exact binary directly from Git Bash (`./node_modules/.bin/tsx ...`, `./node_modules/.bin/tsc ...`) to obtain real feature/typecheck proof. Treat the npm-script portability bug separately from the code verdict; recommend scripts use bare `tsx`/`tsc` for cross-platform execution.
   - If full suite exits nonzero from apparently unrelated collection/tooling failures, create a clean `origin/main` detached worktree and run the same command there. If the identical failure reproduces on main, record it as pre-existing and continue with changed-scope verification.
   - When creating a baseline/main worktree, run install/test commands with the shell `workdir` set to that baseline worktree. Do not chain `git worktree add` and `npm ci` from the original repo directory; that installs/tests the wrong project and can produce misleading missing-script or locked-file errors.
   - Run targeted changed-scope tests, lint, typecheck, and build when available.
   - **Supabase reset readiness on Windows:** `supabase db reset` can exit 0 after migrations/seed while Kong still returns `502` for `/auth/v1/health`; auth-backed tests then fail with `AuthRetryableFetchError: An invalid response was received from the upstream server`. Probe the auth health endpoint before judging the branch. If the proxy remains unhealthy and restarting shared services is not approved, run the same auth-backed suite on a clean `origin/main` worktree. An identical upstream-auth failure is baseline infrastructure, not feature evidence; report the changed-scope results separately and do not print `supabase status` key material in the review comment.

6. Baseline checks:
   - For batch-import rollback, seed later consent, suppression and communication-draft records linked to batch-created contacts/deals, then execute rollback against a synthetic database. Inspect every FK `CASCADE`/`SET NULL` path and require atomic refusal or preservation of non-batch records; checking only later messages misses destructive compliance-history cascades.
   - For batch-import rollback, deterministically interleave a real partial-batch resume after rollback reads its ledger but before its write transaction acquires locks. Use a child linked to a pre-existing matched parent so deletion-reference guards cannot accidentally mask the race. Require atomic stale-ledger refusal or complete rollback of the current batch; deleting all import_rows and checking their absence does not prove rollback when newly resumed rows/external ownership survive and a rolledBack marker prevents retry.
   - For batch-import rollback lifecycle repairs, test the reverse interleaving too: finish rollback immediately before a stale initialization, entity write, ordinary finalization, and lost-finalization recovery write. Require all paths to preserve the rolled-back marker and recovery state; locking rollback alone does not stop a stale apply from reviving it. Test both changed-count and same-count ledger/status/ownership/quarantine drift, then prove a fresh safe retry succeeds. Distinguish deterministic embedded-SQL interleavings from real multi-session contention proof.
   - For import planners with a mutable virtual state, add distinct source IDs sharing an email/phone where a later source row fills a blank on a newly planned entity. Execute the resulting SQL and rollback, not just the plan: shared object references can mutate an earlier pending INSERT and cause the planner's own later before-image check to fail as stale.
   - For snapshot/telemetry privacy allowlists, mutate an allowlisted scalar field (for example `last_run_at`) into an object containing synthetic forbidden keys and fake credentials, then inspect both JSON and SSE. A top-level key allowlist can still forward nested `prompt`/`argv`/`pid` and unredacted secrets when field types are not validated. Require scalar/date validation or fail-closed source handling, not only normal-fixture denylist scans.
   - For public form/action selectors implemented with object lookups, execute unknown values plus inherited keys (`__proto__`, `constructor`, `toString`). Require bounded 4xx JSON and no thrown/rejected handler promise. A truthiness check on `config = configs[input]` does not establish an own-property allowlist, and happy-path tests miss this crash class.

   ```sh
   git -C "$wt" diff --check origin/main...HEAD
   git -C "$wt" diff origin/main...HEAD -- '*.ts' '*.tsx' '*.sql' '*.mjs' \
     | grep -En "(sk_|api[_-]?key|secret|token|password|debugger|console\.log|TODO|FIXME)" || true
   ```

7. Live HTTP/browser proof on Windows:
   - Run Playwright orchestration with Node if Bun's Windows Chromium pipe launch hangs. Keep Bun for the shipped app, resolve the actual `bun.exe` path before Node `spawn` (the npm shell shim may not resolve there), and set TEMP/TMP/TMPDIR explicitly to reviewer scratch. For bounded Bun server verification, capture test-owned `Bun.serve` handles and interval handles in a scratch wrapper, close them on stdin EOF, and let the process exit rather than killing it.
   - For theme-switch screenshots, wait until each responsive picture's image is complete, has a nonzero naturalWidth, and currentSrc matches the expected light/dark asset; also await document.fonts.ready before capturing. An immediate screenshot after emulateMedia can show the old white logo on a newly light background and manufacture a contrast finding.
   - For iframe acceptance, prefer a parent actually loaded over the allowed local origin, then replace only its in-memory DOM with a scratch iframe. A route-fulfilled parent can have different browser network/address-space treatment and yield `chrome-error://chromewebdata/` despite correct CSP. Capture console/request failures and rerun using the real network origin before judging the app; never edit the host application's files.
   - Start throwaway servers from the MSYS worktree path, but write `curl -o` artifacts to native paths under the active profile's scratch directory, never `C:/tmp` or `/tmp`. Native Windows `curl` can emit `curl: (23)` or fail to leave files behind when handed MSYS output paths.
   - Treat native `curl http://127.0.0.1:<port>` readiness and browser reachability as separate checks: Hermes Desktop browser automation may fail to reach `localhost`/`127.0.0.1` even after curl proves the server is ready. If `browser_navigate` to localhost times out, use the app's printed Network URL (for example `http://100.x.x.x:<port>`) or another reachable host-bound address before declaring the live browser proof blocked.
   - For route-status loops, use `curl -sS -L -o "$native_profile_scratch/<artifact>.html" -w '%{http_code}' "$base$route"`; then read artifacts with the same native Windows paths from Python or browser tooling.
   - Use browser snapshots for nav/env proof when UI text matters; HTTP status alone proves routing but not sidebar pruning or badges.
   - For manual form acceptance criteria, do not stop at “form opens” or “Save clicked.” Verify the post-submit state: the expected detail route is not a not-found page, the created/edited record is visible after returning to the list, and no console/server error surfaced. This catches session-local/server-action mismatches where a form navigates to an ID that the current data source cannot read back.
   - If browser snapshot refs are confusing after scrolling or duplicate buttons, a trusted local app can be inspected/clicked with `browser_console(expression=...)` using DOM text selection (for example, find the `button` whose `textContent.trim()` is `New contact`). Use this to disambiguate, then return to snapshots/vision for user-visible proof.
   - For form proof in locked-down browser-console sessions, avoid JS that extracts or sets input values: Hermes may block it as sensitive primitive use. Instead, use `browser_vision(annotate=true)` to map off-screen/low-visibility controls to refs, then drive the UI with `browser_type` and `browser_click`. Use `browser_console(expression=...)` only for safe readbacks such as `document.body.innerText` checks, alert text, stage/header lists, and the console-error buffer. This still yields real browser evidence without changing `browser.allow_unsafe_evaluate`.
   - Lighthouse/Chrome on Windows cron can exit nonzero during temp-profile cleanup (`EPERM ... AppData\\Local\\Temp\\lighthouse.*`) after the JSON report has already been written. If this happens, parse the intended `--output-path` JSON files directly and report both facts: the scores are valid only if the files exist and parse, while the cleanup EPERM is an environment artifact, not app proof.
   - When the active company rules prohibit process killing, run a throwaway Next production server and browser in a bounded foreground Node harness using `next({dev:false,dir,hostname,port})`, `app.prepare()` and `http.createServer(app.getRequestHandler())`. In a finally block close the browser, call server.close()/server.closeAllConnections(), then app.close(). Verify the scratch port is closed before cleanup. Report this substitution for the literal next-start launcher; never kill or restart shared processes.
   - For signed-session HTTP reviews, use the same bounded server pattern with `next({dev:true,dir,hostname,port})` when the criterion specifically requires a dev server. Run `curl.exe` asynchronously from the serving Node process, since synchronous curl blocks its event loop. Supply only synthetic auth settings and isolated state/media paths, keep all tokens in memory, and verify the scratch port is closed after each configuration. Use an independently signed known token accepted in the first run to prove rejection after a different-key restart. Inspect the shipped logout response before asserting its status: cookie clearing may intentionally return a 307 redirect rather than JSON 200.
   - Scope browser assertions to the page structure actually shipped: existing CRM pages may use h2 rather than h1, and pipeline boards contain nested header elements. Select `main header` with `.first()` for the page-header sibling proof, rather than assuming a single header. Correct scratch harness assumptions and rerun the full probe before judging the implementation.

8. Cleanup after build/test:
   - Remove generated heavy artifacts inside the worktree before `git worktree remove`, especially on Windows:

   ```sh
   msys_wt='/d/Wolftac/.build-worktrees/review-WD-185-77fe7c9'

   rm -rf "$msys_wt/node_modules" \
          "$msys_wt/.next" \
          "$msys_wt/tsconfig.tsbuildinfo" \
          "$msys_wt/next-env.d.ts"
   git -C "$repo" worktree remove --force "$wt" || true
   git -C "$repo" worktree prune
   git -C "$repo" worktree prune --dry-run -v 2>&1   # silent == clean
   ```

   - If cleanup leaves only an empty review directory and native `rmdir` reports WinError 32, treat it as a process-held directory, not remaining source work. Remove only the stale registration explicitly named by prune, verify prune is silent and the worktree is unregistered, and report the locked empty path as a cleanup limitation. Never kill/restart Hermes or language-server processes to release it.
   - `prune -v` writes to **stderr**. Without `2>&1` a pipe or `$(...)` capture sees nothing and the repo looks clean when it is not.
   - `git worktree prune` can fail with `Permission denied` on every entry when the target repo lives under OneDrive — git's own recursive delete chokes there, while the shell's does not. Recover by removing exactly the directories prune names, then re-confirming:

   ```sh
   git -C "$repo" worktree prune --dry-run -v 2>&1 \
     | sed -n 's|^Removing worktrees/\(.*\): gitdir file does not exist$|\1|p' \
     | while IFS= read -r d; do rm -rf "$repo/.git/worktrees/$d"; done
   git -C "$repo" worktree prune --dry-run -v 2>&1   # must now be silent
   ```

   - Cleanup runs after proof and Linear write verification are complete, but it is **not** optional. Finish with `git worktree prune --dry-run -v 2>&1` silent and your worktree absent from `git worktree list`. Stale `.git/worktrees/*` entries left in the target repo are review debt owed to that repo's owner, not acceptable residue — 29 accumulated in `wolftac-crm` between WD-185 and WD-300 under the older best-effort wording. If stale entries predate your review, still clear them: the command above is safe, since prune only names worktrees whose working directory is already gone.

## Deploy-tool review probes

- Extract independent full-source fixtures with `git archive <exact-reviewed-SHA>` rather than copying a concurrently tested worktree. Deploy regression suites temporarily reconcile tracked source files and restore them; a filesystem copy taken mid-suite can import marker content absent from the reviewed artifact.

- Compare discovered target sets across every verb after merging main. A bounded WSL probe guarded only by `$Check -or $Deploy` silently excludes a newly added `$Reconcile` verb; passing Windows/Codex-only tests cannot establish full reconciliation coverage. Reproduce safely with a scratch copy of tracked source, redirect USERPROFILE/LOCALAPPDATA and the WSL destination root, seed a Windows-clean / WSL-drifted target, then require Check and Reconcile to cover the same target. Never create live WSL drift for this probe.
- Assert numeric scope and summary counts, not merely the word `SCOPED`. PowerShell pipeline assignment can unwrap one selected hashtable, making `.Count` report its Src/Dst key count rather than the number of pairs; use array-shaped assertions such as `@($Pairs).Count`.
- Isolate USERPROFILE and LOCALAPPDATA for every scratch deploy invocation, including fixtures that only replace `$Pairs`: deploy also rebuilds Desktop/plugin ZIPs outside the pair loop. Seed those package source directories, and compare deployed content using the script's LF-normalization contract rather than raw autocrlf checkout bytes.

## Reporting nuance

- When reviewing a send-back caused by rebasing a merge-resolved feature branch, inspect both merge parents, `git merge-base --is-ancestor <default> <head>`, and the final diff before treating historical WIP replay conflicts as unresolved final-tree defects. Read the delivered integrate contract: if it permits “Rebase (or merge)” and the exact current default is already an ancestor of the published reviewed head, preserve that resolution and explicitly hand off the permitted merge-preserving path with exact default/head SHAs and fresh gates. Report any reproduced plain-rebase conflict honestly; never claim it was fixed, mandate a history rewrite without authority, or loop the same head back solely because one optional integration method fails. If the contract actually requires rebase-only or default has advanced, evaluate that requirement and current candidate separately.

A full-suite failure can still be a PASS only when all are true:
- the same failure reproduces on clean `origin/main`,
- feature-targeted tests pass,
- lint/typecheck/build pass,
- diff inspection and baseline checks show no blocking defect,
- the report explicitly names the pre-existing failure and recommends a separate issue.

If the branch fails a full-suite check but a clean current `origin/main` passes, treat the branch as stale rather than blaming baseline. For a low-risk review fix, merge current `origin/main` into the detached review HEAD/branch, re-run the full test plan, and only PASS WITH FIXES if the merged branch is pushed and all required checks pass. Record the merge SHA and baseline evidence in the Linear report.
