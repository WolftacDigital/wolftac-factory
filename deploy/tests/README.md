# deploy / watchdog regression tests

Run from the repo root:

```
pwsh -NoProfile -File deploy/tests/deploy-scope.tests.ps1
pwsh -NoProfile -File deploy/tests/factory-watchdog-drift.tests.ps1
```

Each suite prints `PASS`/`FAIL` per assertion and a final `RESULT pass=N fail=N`, and
exits non-zero if anything failed.

| File | What it holds down |
|---|---|
| `deploy-scope.tests.ps1` | `deploy.ps1`: a zero-byte or unreadable deployed copy must never truncate the repo file, and a blank or whitespace-only `-Only` must resolve to `NO-MATCH` rather than widening to every pair. Plus the surrounding acceptance criteria — scoped reconcile, LF output, unscoped `-Force` refusal, parity and semantic checks. |
| `factory-watchdog-drift.tests.ps1` | `factory-watchdog.ps1` drift alerting: truthful magnitude on any kind of edit, no false "running normally" while liveness is failing, and `cannot compare` for a deployed copy that is absent. Plus the alert state machine — transition, cooldown, 12h nag, recovery — and agreement with `deploy.ps1 -Check` on which targets are drifted. |

## deploy-scope.tests.ps1

### Containment

These suites drive a tool whose entire job is writing to live agent skill trees, so
containment is not a nicety:

- `USERPROFILE` and `LOCALAPPDATA` are redirected at a scratch root, which moves every
  Windows-side destination into scratch.
- `wsl.exe` is shimmed out of `PATH`, because the `\\wsl$` destinations are derived
  from a WSL probe and **cannot** be redirected by environment variables.
- The real trees are snapshotted before and after, and asserted byte-identical. That
  assertion is the containment proof — if it fails, the run wrote somewhere it should
  not have.

**Every write verb must run inside the shim unless its `-Only` is guaranteed non-blank.**
A blank `-Only` resolves to zero pairs *only while the fix under test is present*;
against broken code it widens to every pair. A regression harness has to stay safe when
the code it tests is broken — that is the whole point of running it. This rule was
written after an unshimmed `-Deploy -Force -Only ''` against a deliberately broken
`deploy.ps1` reverted seven live WSL skill files.

The suite does reconcile into this checkout's own `ports\codex\*.md` and reverts them
with `git` between blocks, so **run it in a worktree**, not a tree with changes you care
about. `RepoDirty` excludes `deploy/deploy.ps1` (the script under test) and
`deploy/tests` (the harness itself); neither is a `$Pairs` destination, so neither can
be something `deploy.ps1` wrote.

### Anti-vacuity

Both defect assertions are mutually anti-vacuous: one requires `git status` to be
*empty* after a refused run, the other requires it to name *exactly one* reconciled
file. A broken `RepoDirty` cannot satisfy both.

Verified by mutation — replacing the blank-scope guard with `$false` produces four
failures naming the widened scope, and reinstating it returns the suite to green.

### Known slow path

Invocations use PowerShell's native argument passing rather than `Start-Process`,
because the suite deliberately passes `''` and `'   '` as `-Only` values and
`Start-Process` argument-joining does not preserve them — it would silently test
something other than the blank scope this file exists for.

The consequence: against a `deploy.ps1` whose scope filter is broken, these scoped calls
become all-pairs calls and the suite gets **slow before it goes red**. Give it a
generous outer timeout in CI rather than reading a stall as a hang.

## factory-watchdog-drift.tests.ps1

### Hermetic by construction

The suite builds its own scratch repo, deployed tree, cron directory, and a stand-in
`deploy.ps1` whose `$Pairs` point at scratch destinations. It never reads or writes a
live deploy target, the real cron directory, the task scheduler, or Slack. The Slack
token it exports is a literal fake (`xoxb-scratch-not-a-real-token`) and delivery is
asserted by inspecting the state sidecar, not by sending anything.

That means it is safe to run at any time, against any working tree, including one where
`factory-watchdog.ps1` is broken.

### The three cases it exists for

All three shipped past a fully green suite once, which is the point:

1. **Magnitude on a same-length substitution.** The original count subtracted total
   file lengths — correct for an append, wrong for an in-place edit. A one-line
   substitution reported `+0 deployed-only, +0 repo-only` while naming the file as
   drifted. The assertions now check the **numbers**, not the labels: equal totals,
   exactly one deployed-only line, exactly one repo-only line. The first harness only
   ever tested an append, which is exactly why the defect survived it.
2. **No false "all normal".** A drift alert must not assert the factory is running
   normally when liveness failed in the same tick. Asserted with a disabled scratch
   worker *and* a drifted target in one run.
3. **Absent deployed copy is `cannot compare`.** Not drift, and never clean — we do not
   know whether the two sides agree, and saying either asserts a comparison that never
   happened.

### Anti-vacuity

AC-8 compares the drifted set the watchdog reports against the set `deploy.ps1 -Check`
reports, and separately asserts that set is **non-empty** — otherwise two empty sets
would agree perfectly and prove nothing.

Verified by mutation — reinstating the length-subtraction magnitude produces exactly two
failures, both naming the `+0 deployed-only, +0 repo-only` output on a five-line-versus-
five-line substitution, and restoring the multiset difference returns the suite to green.

### A note on ordering

Blocks share scratch state. A block that borrows a file must restore **exactly what it
borrowed**, not what it assumes "clean" means: the simultaneous-fault block once reset
`beta.md` to clean instead of to the drifted state later blocks depended on, and failed
five downstream assertions that had nothing wrong with them.

## Parameters

| Parameter | Default |
|---|---|
| `-Repo` | the checkout this file lives in |
| `-Scratch` | `%TEMP%\wolftac-deploy-scope-tests` or `%TEMP%\wolftac-watchdog-drift-tests` (must be outside `-Repo`) |

## Not run by CI

`factory-checks` runs `parity-check.ps1`, `semantic-check.ps1`, and the CR-byte check.
It does **not** run these suites: the deploy suite needs a real `pwsh`, a WSL probe, and
live agent trees to snapshot, and the watchdog suite needs a real `pwsh` and a writable
scratch root. Run them locally before touching `deploy.ps1` or `factory-watchdog.ps1`.
