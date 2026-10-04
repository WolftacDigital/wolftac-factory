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
- Do not write acceptance tests that merely assert the implementation strings you just added. Favor behavioral rendering, accessibility, navigation, and state tests.

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

## Windows npm-script executable paths

On Windows, npm executes package scripts through `cmd.exe`, even when Hermes launched `npm` from Git Bash. A script such as `./node_modules/.bin/tsx ...` can therefore fail with `'.' is not recognized as an internal or external command` despite a correct `npm ci`. For new or issue-owned scripts, use the cross-platform package binary name (`tsx ...`, `tsc ...`, `next ...`) so npm resolves `node_modules/.bin` itself. Do not rewrite unrelated existing scripts merely to fix one issue; patch only the approved script entries, rerun the literal `npm run <script>`, and record the initial failure plus verified correction in proof.