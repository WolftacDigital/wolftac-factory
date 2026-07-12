# Windows-hosted Linear factory review proof pattern

Use this when `/wolftac-review` is running on Windows/Git Bash and the Linear spec's `Repo:` is a Windows path such as `C:/Users/.../repo`.

## Goals

- Review in a detached temporary worktree so the active branch and uncommitted local artifacts are not disturbed.
- Produce proof from real commands: diff, tests, lint/typecheck/build, baseline checks.
- Avoid Git-for-Windows path translation traps and cleanup failures.

## Pattern

1. Resolve paths deliberately:
   - Repo for Git commands: use the spec's native `C:/...` path with `git -C`.
   - Temporary worktree: use a native path in `git worktree add`, e.g. `C:/tmp/wolftac-review-WD-185`.
   - Shell `workdir`: use MSYS form for the same worktree, e.g. `/c/tmp/wolftac-review-WD-185`.
   - Do **not** pass `/c/Users/...` to `git worktree add`; Git for Windows may create/register `C:/c/Users/...` instead.

2. Create the detached worktree:

   ```sh
   repo='C:/Users/logan_vwagmmf/OneDrive/Wolftac Digital/Codex/wolftac-crm'
   branch='jonathan/wd-185-build-crm-conversations-unified-inbox-email-with-approval'
   wt='C:/tmp/wolftac-review-WD-185'

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
   - For Next/Turbopack projects on Windows, **copy** `node_modules` into the detached worktree rather than using a junction/symlink to the source repo. Turbopack can reject a linked `node_modules` as outside the project filesystem root (`Symlink [project]/node_modules is invalid, it points out of the filesystem root`) even though lint/typecheck/tests may run. Remove any failed link before copying, then re-run build/test proof from the copied dependency tree.
   - Do not turn dependency absence or npm cache/install setup failures into durable negative conclusions; they are setup state. Capture the workaround used and keep pursuing real proof.

5. Re-run proof commands:
   - Full spec command, e.g. `npm run test:crm`.
   - If full suite exits nonzero from apparently unrelated collection/tooling failures, create a clean `origin/main` detached worktree and run the same command there. If the identical failure reproduces on main, record it as pre-existing and continue with changed-scope verification.
   - When creating a baseline/main worktree, run install/test commands with the shell `workdir` set to that baseline worktree. Do not chain `git worktree add` and `npm ci` from the original repo directory; that installs/tests the wrong project and can produce misleading missing-script or locked-file errors.
   - Run targeted changed-scope tests, lint, typecheck, and build when available.

6. Baseline checks:

   ```sh
   git -C "$wt" diff --check origin/main...HEAD
   git -C "$wt" diff origin/main...HEAD -- '*.ts' '*.tsx' '*.sql' '*.mjs' \
     | grep -En "(sk_|api[_-]?key|secret|token|password|debugger|console\.log|TODO|FIXME)" || true
   ```

7. Live HTTP/browser proof on Windows:
   - Start throwaway servers from the MSYS worktree path, but write `curl -o` artifacts to native `C:/tmp/...` paths. Native Windows `curl` can emit `curl: (23)` or fail to leave files behind when handed MSYS `/tmp/...` output paths.
   - Treat native `curl http://127.0.0.1:<port>` readiness and browser reachability as separate checks: Hermes Desktop browser automation may fail to reach `localhost`/`127.0.0.1` even after curl proves the server is ready. If `browser_navigate` to localhost times out, use the app's printed Network URL (for example `http://100.x.x.x:<port>`) or another reachable host-bound address before declaring the live browser proof blocked.
   - For route-status loops, use `curl -sS -L -o 'C:/tmp/<artifact>.html' -w '%{http_code}' "$base$route"`; then read artifacts with Windows-aware paths (`C:/tmp/...`) from Python or browser tooling.
   - Use browser snapshots for nav/env proof when UI text matters; HTTP status alone proves routing but not sidebar pruning or badges.
   - For manual form acceptance criteria, do not stop at “form opens” or “Save clicked.” Verify the post-submit state: the expected detail route is not a not-found page, the created/edited record is visible after returning to the list, and no console/server error surfaced. This catches session-local/server-action mismatches where a form navigates to an ID that the current data source cannot read back.
   - If browser snapshot refs are confusing after scrolling or duplicate buttons, a trusted local app can be inspected/clicked with `browser_console(expression=...)` using DOM text selection (for example, find the `button` whose `textContent.trim()` is `New contact`). Use this to disambiguate, then return to snapshots/vision for user-visible proof.
   - For form proof in locked-down browser-console sessions, avoid JS that extracts or sets input values: Hermes may block it as sensitive primitive use. Instead, use `browser_vision(annotate=true)` to map off-screen/low-visibility controls to refs, then drive the UI with `browser_type` and `browser_click`. Use `browser_console(expression=...)` only for safe readbacks such as `document.body.innerText` checks, alert text, stage/header lists, and the console-error buffer. This still yields real browser evidence without changing `browser.allow_unsafe_evaluate`.
   - Lighthouse/Chrome on Windows cron can exit nonzero during temp-profile cleanup (`EPERM ... AppData\\Local\\Temp\\lighthouse.*`) after the JSON report has already been written. If this happens, parse the intended `--output-path` JSON files directly and report both facts: the scores are valid only if the files exist and parse, while the cleanup EPERM is an environment artifact, not app proof.
   - After killing a background `npm run start`/`next start`, verify the port is closed before cleanup. On Windows, the tracked shell process can die while a child `next` process keeps the worktree busy; check the scratch port (`netstat -ano | grep ':<port>'`) and terminate only that specific PID with `taskkill.exe /PID <pid> /F` if needed. Do not kill broad `node`/`python`/Hermes processes.

8. Cleanup after build/test:
   - Remove generated heavy artifacts inside the worktree before `git worktree remove`, especially on Windows:

   ```sh
   rm -rf /c/tmp/wolftac-review-WD-185/node_modules \
          /c/tmp/wolftac-review-WD-185/.next \
          /c/tmp/wolftac-review-WD-185/tsconfig.tsbuildinfo \
          /c/tmp/wolftac-review-WD-185/next-env.d.ts
   git -C "$repo" worktree remove --force "$wt" || true
   git -C "$repo" worktree prune
   ```

   - Treat cleanup as best-effort only after proof and Linear write verification are complete. If `git worktree remove --force` deregisters the worktree but Windows refuses to delete an empty temp directory (`Permission denied`), verify `git -C "$repo" worktree list` no longer contains it, verify the scratch server port is closed, and make sure heavy artifacts were removed. If `git worktree prune` also emits `Permission denied` for unrelated stale `.git/worktrees/*` entries, do not treat that stale prune noise as a blocker for the current review; confirm the target worktree is absent from `git worktree list` and that the target temp directory is empty or contains no heavy artifacts. Report leftover empty temp directories/stale prune noise as cleanup residue, not as review blockers.

## Reporting nuance

A full-suite failure can still be a PASS only when all are true:
- the same failure reproduces on clean `origin/main`,
- feature-targeted tests pass,
- lint/typecheck/build pass,
- diff inspection and baseline checks show no blocking defect,
- the report explicitly names the pre-existing failure and recommends a separate issue.

If the branch fails a full-suite check but a clean current `origin/main` passes, treat the branch as stale rather than blaming baseline. For a low-risk review fix, merge current `origin/main` into the detached review HEAD/branch, re-run the full test plan, and only PASS WITH FIXES if the merged branch is pushed and all required checks pass. Record the merge SHA and baseline evidence in the Linear report.
