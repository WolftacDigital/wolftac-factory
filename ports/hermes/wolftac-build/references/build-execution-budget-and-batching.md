# Build execution budget and batching

Use this when a Factory issue spans several files or the host enforces a finite tool-call/turn budget.

## Before claiming

1. Count the specified files and verification commands.
2. Budget for discovery, implementation, one repair cycle, full verification, diff review, commit/push, and Linear handoff.
3. Keep at least 20% of the available run for verification and handoff. If the host does not expose a numeric limit, treat the first third of the run as the implementation ceiling before the first compile/type check.
4. If the issue cannot fit while preserving that reserve, do not claim it in this invocation.

## Efficient execution

- Batch independent queue reads, repository instruction reads, and source inspections.
- Prefer one coherent multi-file patch or one complete component rewrite over dozens of tiny replacements.
- Never issue concurrent edits against the same file. They can race, overwrite one another, or make later results impossible to trust.
- Make shared primitives compile first, then migrate views in bounded groups.
- After every logical migration group, run the cheapest useful gate (typecheck, targeted unit test, or formatter/parser check) before touching more files.
- Bound programmatic read/patch batches to roughly 8 files when tool hooks add latency; a 40-call batch can exceed the 300-second execute_code deadline. After a timeout, inspect actual diffs before retrying because earlier edits may already have landed. If a multiline patch misses CRLF content, match explicit CRLF rather than repeatedly retrying an LF-only anchor.
- When converting an authorization guard to async, enumerate every caller and require awaited calls with an AST assertion or equivalent test. TypeScript can still pass when a route returns the guard's Promise prematurely, so compilation alone does not prove that authenticated requests reach the handler.
- Do not put Node's `--env-file` flag in `NODE_OPTIONS`; Node rejects it there. For a helper use `node --env-file=<scratch-path> --import tsx <script>`, or pass only synthetic scratch configuration through the child process environment when exercising `npm run dev`. Never load the live env file to make a smoke test pass.
- Do not write acceptance tests that merely assert the implementation strings you just added. Favor behavioral rendering, accessibility, navigation, and state tests.
- For snapshot-driven UI requirements, execute the real server projection before testing the consumer. A pure layout test can pass by injecting a field that production strips. Use two synthetic source inputs requiring different results and verify the serialized snapshots retain enough information to distinguish them; if a binding non-goal forbids the missing field or source, report the contract conflict instead of silently weakening the behavior.

## Reserve sequence

Stop feature editing when the reserve begins. Use the remaining budget only for:

1. Typecheck/lint or equivalent fast gate.
2. Repair failures.
3. Spec test plan and browser/API smoke.
4. `git diff --check`, changed-file review, and artifact check.
5. Commit, push, and remote-head equality verification.
6. BUILD REPORT and `In Review` transition.

A partial refactor without executed proof is not a build completion. Do not report it as finished or move it to review.

## Windows `uv` executable collision

On Windows/Git Bash, the bare `uv` command may resolve to a Linux ELF shim under the Hermes bin directory and fail with `Exec format error`, even when the native Windows binary is installed beside it. Verify with `file <resolved-path>` and `command -v uv.exe`; when `uv.exe` is the native `x86_64-pc-windows-msvc` build, run the specified workflow as `uv.exe sync`, `uv.exe run ...`, and `uv.exe build`. Record the exact executable in proof. Do not reinstall Hermes, modify its runtime bin directory, or claim tests failed before trying the native executable.

## Cross-platform verification boundaries

- Probe foreign-architecture execution with a minimal image before spending the build budget on a full cross-platform image. If even `uname` fails with `exec format error`, report the host execution limitation; downloaded target wheels and a successful native image are separate evidence, not a target-architecture runtime pass. Do not install emulators or restart services without approval.
- Keep temporary Playwright configuration outside `test-results` or the configured output directory: the runner can clear that directory during execution. Remove only the temporary config created by the current run after verification.
- Preserve full-suite failures alongside passing scoped tests. Separate platform-specific assertions, unavailable network fixtures, and actual regressions; unchanged files alone do not establish that a failure reproduces on the base branch.

## Windows dependency and test-process lifetime

- Keep combined install/test/lint/build invocations below the transport deadline, even when the terminal accepts a larger timeout. Persist output to an explicit profile-scratch path before long commands; after a transport timeout, inspect exact issue-owned children and wait for exit before rerunning. A later process in an `&&` chain is not a substitute for captured final exit proof.
- If a disposable Node smoke server exits immediately with `stdin is not a tty` under a non-PTY background terminal, inspect its tracked result and retry with `pty=true` rather than changing the runtime. Prefer a bounded server wrapper that gracefully closes its HTTP server and Next app on a timer; disclose the wrapper instead of claiming the literal CLI start command.


- Give `npm ci` a generous foreground timeout (up to 600 seconds), or track it explicitly as a bounded background job. A terminal timeout can leave npm/lifecycle children running; inspect the exact issue-owned command lines before retrying installation. Overlapping `npm ci` attempts can produce misleading EPERM/rmdir failures. Do not kill unrelated processes or reinstall tools to resolve this.
- After stopping a tracked test-server session, enumerate Node command lines for the exact durable issue path, not only `Process.cwd()`. A cwd-only scan can miss a surviving Next process that still locks the empty worktree directory. Verify its command, parent tree, and issue ownership before stopping only that worker-started process; then verify filesystem and worktree registration removal. A listening port alone is not ownership proof because a proxy may also own that port.

## Windows empty-directory cleanup

- If Git cleanup reports Permission denied and Git Bash reports Device or resource busy, first move the persistent terminal cwd outside the issue worktree and verify the leftover directory is empty. Try native Python `Path(<exact-issue-path>).rmdir()` before any process investigation; it can remove an empty directory that the MSYS removal path rejected. Never use this as a recursive deletion shortcut. Then verify both filesystem absence and removal from `git worktree list --porcelain`, and run `git worktree prune --dry-run -v 2>&1`. If native rmdir still returns WinError 32, inspect exact-path process cwd/parent/command ownership before any action. Hermes-managed TypeScript language servers and their tsserver/typings-installer children can hold the empty issue directory as cwd; they are runtime tooling, not disposable test-server processes. Do not terminate them to finish cleanup. Report the empty locked path separately from its already-removed Git registration and preserve it until tooling naturally releases it.

## Offline SQL and migration verification

- For an approved SQL importer or rollback path, prefer executing the real SQL against a synthetic embedded PostgreSQL fixture over a mock that merely reimplements the intended mutations. Apply the repository migrations to that fixture and exercise repeat apply, partial failure/resume, before-image restoration, and cascade/reference guards. Keep the fixture local and never substitute it for production proof.
- When adding an approved migration, inspect existing inventory/source-marker assertions as well as the new migration test. Preserve mirror equality and update only the exact migration/table-count expectations required by the added file; do not weaken schema coverage guards. Search all corpus-count assertions, including older index tests. Match the existing SQL reader's supported declaration syntax (for example `create table if not exists` and `alter table if exists`) rather than widening its parser merely for a new migration.
- For rollback safety, execute negative cases with later consent, suppression, drafts, approvals and provider history on each destructive foreign-key edge. Clean-fixture rollback alone cannot prove preservation of unowned data. Prefer catalog-complete reference checks with transaction-held locks and fail-closed catalog revalidation over a partial CASCADE/SET NULL list; test composite keys and schema changes between discovery and execution.
- Revalidate the full batch status, ledger, ownership and quarantine under transaction-held locks before rollback mutations; a pre-lock read can miss a resumed child on an existing CRM parent even when FK guards pass. Guard initialization, every entity transaction and both normal/recovery finalization against rolled-back lifecycle state. Test actual resume-before-rollback and rollback-before-resume interleavings, same-count ledger drift and surviving physical rows/ownership; checking only the ledger just deleted is not rollback proof. Use a common transaction-scoped lock when the transport does not guarantee session affinity, and distinguish deterministic embedded-SQL interleavings from multi-connection contention proof.
- Keep planned SQL operations and rollback before-images independent of mutable virtual matching state. Test distinct source IDs sharing a new email or phone, blank-field fills, both external-ID aliases, zero-write reapply and repeated rollback; a mutable pending INSERT can otherwise invalidate its own later fill assertion.
- Install disposable browser-proof dependencies in a separate profile-scratch prefix, not with `npm install --no-save --package-lock=false` inside the issue worktree: that command can replace existing locked dependency versions even though package files stay unchanged. Never run installation concurrently with tests. If it already happened, restore with `npm ci` and rerun the full literal gate before claiming lockfile-based proof.
- Serialize npm dependency changes and package.json edits. `npm install --save-dev` writes both package.json and its lockfile, so running a script-entry patch concurrently violates the single-writer rule even though the shell command looks like dependency installation.

## Windows Node/Bun proof harnesses

- Split real-polling browser proof into independently bounded batches and append results after each case. Several unchanged 60-second source polls can exceed the tool transport deadline even when the terminal timeout requests more time. Preserve partial evidence, inspect whether the exact harness/server processes remain, and rerun only bounded batches; do not shorten production polling or call a timed-out suite passed. Measure animation arrival from the observed stream event, not just the first mesh sample, so React commit/render latency is included in a stream-to-arrival acceptance budget.

- Use `file:///D:/...` URLs (or `pathToFileURL`) for absolute ESM imports under Node; a bare drive-letter import is treated as an unsupported URL scheme.
- Resolve Bun's native executable with `bun -e 'console.log(process.execPath)'` before spawning it from a sanitized Node environment; a shell-resolved `bun` shim may not be discoverable by `child_process.spawn`.
- For bounded Bun HTTP proof without runtime/process termination, wrap the shipped entrypoint, retain the `Bun.serve` instance, and have a stdin shutdown request call `server.stop(true)` followed by normal process exit. Keep a self-expiry timer, await the child's exit, disable automatic env-file loading, and pass only explicitly allowed environment variables. Report that server lifetime was wrapped rather than claiming a literal unwrapped start command.
- When proving read-only operation against actively scheduled data, record exact before/after file counts and changed paths. Timestamp coincidence alone does not attribute writers; leave the criterion unverified unless the required attribution is established. Never stop another worker or runtime to manufacture stable hashes.
- For react-three-fiber scene picking, click actual projected mesh coordinates and assert the selected identifier; a keyboard mirror proves accessibility, not raycast correctness. Decorative line segments inside a clickable crate group can intercept an adjacent crate because Three.js line raycasting has a world-space threshold. Disable raycasting on decorative edges and rerun the same real pointer regression.
- For browser visibility proof, verify `document.hidden` actually changes before calling a tab-switch test passed. Automation can keep it false even in headed Chromium after another tab is activated or the window is minimized. A synthetic hidden property plus visibilitychange can test the application boundary, but must be reported separately from unverified native-tab behavior. Use interval polling rather than requestAnimationFrame polling for hidden-state assertions, since a truly hidden page may stop animation callbacks.
- For demand-render performance proof, preserve a DevTools timeline trace and actual renderer frame deltas over the specified duration. Compare crate positions, not whole material snapshots: changing pulse intensity can otherwise make a broken movement assertion pass. Allow React effects to settle for one bounded render before asserting a reduced-motion jump; do not treat floating-point camera drift below a stated tolerance as a reset failure.

## Windows Python subprocess proof

- Set TEMP, TMP and TMPDIR explicitly to the profile scratch directory for test subprocesses; do not assume the shell inherited the advertised scratch root. Python may otherwise choose the Windows system-temp short path. Compare resolved paths when testing discovery because Path.resolve() expands Windows 8.3 aliases.
- When forwarding binary child stdout through Windows text stdout, normalize incoming CRLF to LF before the text stream translates newlines. Otherwise CRLF becomes CR-CR-LF and consumers see extra blank lines. Exercise both CRLF and unterminated output, including stderr and split-write redaction, with actual subprocesses.

## Windows npm-script executable paths

On Windows, npm executes package scripts through `cmd.exe`, even when Hermes launched `npm` from Git Bash. A script such as `./node_modules/.bin/tsx ...` can therefore fail with `'.' is not recognized as an internal or external command` despite a correct `npm ci`. For new or issue-owned scripts, use the cross-platform package binary name (`tsx ...`, `tsc ...`, `next ...`) so npm resolves `node_modules/.bin` itself. Do not rewrite unrelated existing scripts merely to fix one issue; patch only the approved script entries, rerun the literal `npm run <script>`, and record the initial failure plus verified correction in proof.

## Streamed Next.js browser proof

- When the local Next adapter lacks the approved provider fixture, exercise the real components and production domain against an isolated embedded-database/mock-provider harness rather than modifying live configuration. Bundle only the real components with server-action transport shims, send those requests through the real domain, and inspect resulting database rows. Preserve harness sources, original screenshots, assertions and full-gate logs outside the disposable worktree. Separately run the actual Next server smoke and explicitly leave configured Next/provider end-to-end behavior unverified; the harness does not prove that integration.
- For CDP keyboard proof, inspect actual focus and selected state after each activation. On Windows, a keyDown/keyUp pair can focus a button without activating it; send the native rawKeyDown, char, and keyUp sequence for Space when needed. Record the actual successful keyboard method, and distinguish it from pointer submission.
- Treat a non-PTY background wrapper's `stdin is not a tty` exit as inconclusive about its Node child. Check exact worker-script paths in process command lines before retrying or cleaning up. A child can continue serving after its tracked shell exits; only stop a proven worker-owned child, never runtime tooling or broad process classes.

- Copy each `capture_screenshot()` result immediately to a unique proof filename before the next capture; the browser harness may overwrite a fixed `shot.png`. Print the capture path to inspect the image, preserve viewport dimensions, and upload the distinct files before deleting the worktree.


- Wait for the actual route heading or work-surface selector after navigation, not only document load or HTTP 200: streamed routes may initially expose only loading skeletons. Assert the hydrated interaction separately when verifying client controls.
- Inspect time/number input values and field errors after browser-helper typing before claiming a valid save. Controlled inputs can retain a zero or segmented time value. If native input setters plus bubbling input events are needed, disclose that interaction method and verify the server result and reload separately.
- Verify Linear screenshot uploads through the connected `extract_images` tool and read back the report comment. An unauthenticated asset GET may return 401 even after a successful signed PUT; do not expose credentials or call that response proof of a failed upload. Copy browser captures to the explicit profile scratch path, since the browser subprocess's TMPDIR may differ from the advertised scratch root.
- Scope page-header sibling assertions to the first route header; kanban cards can contain additional nested headers, making an unqualified `main header + *` locator ambiguous.
- If the browser harness stalls, use an independent headless Playwright/Chrome session with an explicit viewport and captured page errors. Preserve its script, JSON assertions and screenshots outside the disposable worktree; never treat the stalled skeleton probe as a pass.
- For a build-time font-resolution failure, retry the unchanged literal build once before changing application code or build configuration. Record the initial failure and subsequent outcome, and rerun the complete verification gate if the retry passes. Do not infer a root cause from a successful retry.
