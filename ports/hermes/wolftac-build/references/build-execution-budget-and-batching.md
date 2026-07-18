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