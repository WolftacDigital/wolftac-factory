# deploy / watchdog regression tests

Run from the repo root:

```
pwsh -NoProfile -File deploy/tests/factory-watchdog-drift.tests.ps1
pwsh -NoProfile -File deploy/tests/factory-watchdog-learn.tests.ps1
```

The suite prints `PASS`/`FAIL` per assertion and a final `RESULT pass=N fail=N`, and
exits non-zero if anything failed.

Run `python -m unittest discover -s deploy/tests -p test_junctions.py -v` for
WD-579. Windows + PowerShell 7 are required for its junction integration cases;
CI skips those cases on Ubuntu. It uses scratch manifests, homes and a disposable
Git worktree, never live junctions or `deploy.ps1 -Deploy`.

| File | What it holds down |
|---|---|
| `test_junctions.py` | Four-root manifest and legacy override; validate-all-first setup, idempotent setup/teardown, full-path diagnostics, worker deploy coverage and foreign-repository rejection. Windows scratch junctions only. |
| `factory-watchdog-drift.tests.ps1` | `factory-watchdog.ps1` drift alerting: truthful magnitude on any kind of edit, no false "running normally" while liveness is failing, and `cannot compare` for a deployed copy that is absent. Plus the alert state machine — transition, cooldown, 12h nag, recovery — and agreement with `deploy.ps1 -Check` on which targets are drifted. |
| `factory-watchdog-learn.tests.ps1` | `factory-watchdog.ps1` pending `/learn` edits (WD-362): clean is silent, a modified or untracked file in the junctioned live worktree alerts once by path and line count (never content), is snapshotted byte-exact, and survives `git restore .`; cooldown, 12h nag, single recovery, 30-day retention keeping the newest, and "cannot check" for an unresolvable worktree or failed `git status`. Hermetic: scratch repo, scratch junctioned worktree, `-SlackOutbox` interception. |

## Hermetic by construction

The suite builds its own scratch repo, deployed tree, cron directory, and a stand-in
`deploy.ps1` whose `$Pairs` point at scratch destinations. It never reads or writes a
live deploy target, the real cron directory, the task scheduler, or Slack. The Slack
token it exports is a literal fake (`xoxb-scratch-not-a-real-token`) and delivery is
asserted by inspecting the state sidecar, not by sending anything.

That means it is safe to run at any time, against any working tree, including one where
`factory-watchdog.ps1` is broken.

## The three cases it exists for

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

## Anti-vacuity

AC-8 compares the drifted set the watchdog reports against the set `deploy.ps1 -Check`
reports, and separately asserts that set is **non-empty** — otherwise two empty sets
would agree perfectly and prove nothing.

Verified by mutation — reinstating the length-subtraction magnitude produces exactly two
failures, both naming the `+0 deployed-only, +0 repo-only` output on a five-line-versus-
five-line substitution, and restoring the multiset difference returns the suite to green.

## A note on ordering

Blocks share scratch state. A block that borrows a file must restore **exactly what it
borrowed**, not what it assumes "clean" means: the simultaneous-fault block once reset
`beta.md` to clean instead of to the drifted state later blocks depended on, and failed
five downstream assertions that had nothing wrong with them.

## Parameters

| Parameter | Default |
|---|---|
| `-Repo` | the checkout this file lives in |
| `-Scratch` | `%TEMP%\wolftac-watchdog-drift-tests` (must be outside `-Repo`) |

## Not run by CI

`factory-checks` runs `parity-check.ps1`, `semantic-check.ps1`, and the CR-byte check.
It does **not** run this suite — it needs a real `pwsh` and a writable scratch root.
Run it locally before touching `factory-watchdog.ps1`.
