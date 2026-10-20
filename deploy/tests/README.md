# deploy / watchdog regression tests

Run from the repo root:

```
pwsh -NoProfile -File deploy/tests/deploy-scope.tests.ps1
```

Each suite prints `PASS`/`FAIL` per assertion and a final `RESULT pass=N fail=N`, and
exits non-zero if anything failed.

| File | What it holds down |
|---|---|
| `deploy-scope.tests.ps1` | `deploy.ps1`: a zero-byte or unreadable deployed copy must never truncate the repo file, and a blank or whitespace-only `-Only` must resolve to `NO-MATCH` rather than widening to every pair. Plus the surrounding acceptance criteria — scoped reconcile, LF output, unscoped `-Force` refusal, parity and semantic checks. |

## Containment

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

## Anti-vacuity

Both defect assertions are mutually anti-vacuous: one requires `git status` to be
*empty* after a refused run, the other requires it to name *exactly one* reconciled
file. A broken `RepoDirty` cannot satisfy both.

Verified by mutation — replacing the blank-scope guard with `$false` produces four
failures naming the widened scope, and reinstating it returns the suite to green.

## Known slow path

Invocations use PowerShell's native argument passing rather than `Start-Process`,
because the suite deliberately passes `''` and `'   '` as `-Only` values and
`Start-Process` argument-joining does not preserve them — it would silently test
something other than the blank scope this file exists for.

The consequence: against a `deploy.ps1` whose scope filter is broken, these scoped calls
become all-pairs calls and the suite gets **slow before it goes red**. Give it a
generous outer timeout in CI rather than reading a stall as a hang.

## Parameters

| Parameter | Default |
|---|---|
| `-Repo` | the checkout this file lives in |
| `-Scratch` | `%TEMP%\wolftac-deploy-scope-tests` (must be outside `-Repo`) |

## Not run by CI

`factory-checks` runs `parity-check.ps1`, `semantic-check.ps1`, and the CR-byte check.
It does **not** run these suites: they need a real `pwsh`, a WSL probe, and live agent
trees to snapshot, none of which exist on a hosted runner. Run them locally before
touching `deploy.ps1` or `factory-watchdog.ps1`.
