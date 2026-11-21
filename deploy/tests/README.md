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
| `test_integrate_queue_order.py` | WD-657: fake-gq state-entry ordering, numeric ties, unchanged lane matching, write-stable ordering, six-copy equality and approval-first/delivery-check contract. No live credentials, API requests or merges. |
| `test_provider_holds.py` | WD-630: provider-wide reset/escalation holds, one integrate-first release probe, independent providers, retained-failure handling, no-write dry-run and failed launches. Real watchdog CLI under a synthetic clock proves one incident/recovery and three daily digests, unknown-inventory continuity and failed-delivery retry. Scratch jobs/state/expiry/outbox only; no live worker or sender. |
| `test_operate_contract.py` | WD-646 Contract v1: strict JSON/record validators, fenced parsing and prose markers, policy/spec/operator provenance, deterministic round trips, actual two-commit Git blob/parent hashes, map equality and six-copy bytes. Synthetic isolated Git config and a socket guard reject non-loopback connections. Run `python -B -m unittest discover -s deploy/tests -p test_operate_contract.py -v`. No installed target reads, credentials or Linear calls. |
| `test_linux_host.py` / `linux_host_smoke.py` | WD-634: real dry-run entry points, four Gatekeeper scenarios using fake HTTP and ten watchdog cases, scratch-only inputs and immutable fixture trees. Run `python -B deploy/tests/linux_host_smoke.py --output <scratch>/proof.json` on Windows and Linux, then compare JSON bytes. Dedicated Ubuntu CI requires PowerShell and checks committed expected results; see `docs/linux-host.md` for the exact boundary. |
| `watchdog-install.tests.ps1` | WD-584 offline installer: five-file SHA256 plan, S4U/LeastPrivilege XML, throwing task/ACL stubs, non-elevated Apply refusal, Verify with TaskXmlIn only, installed-copy parity/staleness and helper isolation, fsmonitor positive/negative controls and index mtime, RepoRoot override. Run only with allowlist-built scratch profile/config/temp roots, refused-local proxies, and `-Repo` / `-Scratch` under session scratch. The legacy drift/learn suites must retain their bytes and run under the same isolation after a fail-closed alert probe. No live Apply, live Task Scheduler, ACL changes or credentials. |
| `test_worker_schedules.py` | WD-576 operator script: scratch Hermes homes and `fixtures/fake-hermes.ps1` prove dry-run immutability, exact CLI argv, per-profile backups, read-back, idempotency, validate-all refusal and fail/noop errors. Imports the gatekeeper's job-map constants only, never its credential loader or dispatch. Run `python -B -m unittest discover -s deploy/tests -p test_worker_schedules.py -v`. |
| `test_factory_watchdog.py` | WD-549 expiry/auth and unchanged-main regressions plus WD-576 safety-net boundaries, exclusions, non-interval schedules and configurable minimum. Every invocation is `-DryRun` with synthetic inputs. Leave `WD549_REAL_CRON` unset for WD-576. |
| `test_junctions.py` | Four-root manifest and legacy override; validate-all-first setup, idempotent setup/teardown, full-path diagnostics, worker deploy coverage and foreign-repository rejection. Windows scratch junctions only. |
| `test_run_gate.py` | WD-574 gate runner: synthetic-only subprocess environment, output redaction, timeout/tree cleanup, exit codes, repo/worktree identity, seed list and six-copy/deploy parity. Run `python -m unittest discover -s deploy/tests -p test_run_gate.py -v`. Uses scratch repos and a fake Hermes home, never live credentials or real repo gates. |
| `factory-watchdog-drift.tests.ps1` | `factory-watchdog.ps1` drift alerting: truthful magnitude on any kind of edit, no false "running normally" while liveness is failing, and `cannot compare` for a deployed copy that is absent. Plus the alert state machine — transition, cooldown, 12h nag, recovery — and agreement with `deploy.ps1 -Check` on which targets are drifted. |
| `factory-watchdog-learn.tests.ps1` | `factory-watchdog.ps1` pending `/learn` edits (WD-362): clean is silent, a modified or untracked file in the junctioned live worktree alerts once by path and line count (never content), is snapshotted byte-exact, and survives `git restore .`; cooldown, 12h nag, single recovery, 30-day retention keeping the newest, and "cannot check" for an unresolvable worktree or failed `git status`. Hermetic: scratch repo, scratch junctioned worktree, `-SlackOutbox` interception. |

## WD-673 gate timeout and bounded retry contract

Run `python -B -m unittest discover -s deploy/tests -p test_run_gate.py -v`.
New tests exercise the real parser's 1800-second default and a real two-second
child with no timeout override. The existing three-second timeout/tree-cleanup
case remains unchanged. Contract assertions cover the six identical retry
paragraphs, bot-authored per-reviewed-SHA counting, the 60-minute cooldown,
third-attempt WAITING and unreadable-history refusal. A scratch parity test
removes the Hermes marker, asserts the named failure, then restores it and passes.
These are subprocess and skill-text proofs, not a live integrate retry.

Run `pwsh -NoProfile -File deploy/parity-check.ps1`,
`pwsh -NoProfile -File deploy/semantic-check.ps1`, and the complete
`python -B -m unittest discover -s deploy/tests -v` suite with an explicit
1800-second limit and `TEMP`, `TMP`, `TMPDIR` under long-form profile scratch.
No live jobs, installed skills, credentials or approval state are touched.
AC-8 remains a post-merge operator check after the live-checkout update.

## WD-672 sync regression additions

Run `python -B -m unittest discover -s deploy/tests -p test_factory_sync.py -v`
and `python -B -m unittest discover -s deploy/tests -p test_factory_watchdog.py -v`.
Then run `pwsh deploy/parity-check.ps1`, `pwsh deploy/semantic-check.ps1` and the
full deploy unittest suite. Set TEMP, TMP and TMPDIR to profile scratch first.

The new sync suite creates a local bare remote and two real clones for every
test, with an empty scratch global Git config, `GIT_CONFIG_NOSYSTEM=1` and
`core.autocrlf=false`. All seven path/remote flags are explicit. It proves clean
fast-forwards and idempotence, dirty/diverged/mismatched-remote refusal,
in-flight/completed/expired/unreadable markers, byte-exact atomic install and
backup behavior, failed replacement cleanup, seven-install retention, protected
read-only comparison, full-tree dry-run immutability and one-line sanitized output.
Watchdog additions execute the real scratch `-DryRun` path for six/24-hour
thresholds, recovery, in-flight exclusion, missing inputs and untrusted text.
Existing test methods remain unchanged. Operator AC-12/AC-13 are not automated
or claimed by these tests; never run the sync command with live default paths
inside a worker session.

## WD-641 installer regression additions

Run `pwsh -NoProfile -File deploy/tests/watchdog-install.tests.ps1 -Repo <worktree> -Scratch <new-scratch-directory>`
with scratch `TEMP`, `TMP`, `TMPDIR` and profile roots. The existing WD-584 cases
remain; their stub task XML now uses a complete planned definition for read-back.
New `-LibraryOnly` cases execute the real Apply core with synthetic profile facts
and recording Io methods: absent/unreadable task, each of seven mismatched fields,
equal read-back, sanitized registration exception, missing profile with zero I/O,
caller-independent paths, overrides and five-file refresh with zero task calls.
CLI plan/argument cases prove PT10M plus IgnoreNew and invalid FilesOnly rejection.

Degraded verification uses the production Verify entry block obtained through the
PowerShell AST, unchanged, in a child process. Only OS owner/ACL/write-probe facts
and path-reader Io are stubbed. Actual XML checks, pinned-manifest reader, shared
path assessor, output and exit calculation run: S4U exits 0; Interactive and the
Windows XML spelling InteractiveToken exit 6 only when explicitly expected; the
default and an unrelated failing ACL exit 1. Literal `-Verify -TaskXmlIn` cases
also retain real scratch-owner failure checks. This is fixture proof, never a live
Task Scheduler/ACL check. No runtime test option bypasses production security.

## Hermetic by construction

WD-580 adds `test_factory_gatekeeper_audit.py` (fake `urlopen`, fixed clock,
scratch state/credentials: windows, pagination, failures, isolation, flags and
secret suppression) and `test_factory_watchdog_audit.py` (real PowerShell with
scratch cron/policy/fake env and `-DryRun` / `-SlackOutbox`: status parsing,
multi-directory precedence, cooldown/nag/recovery and failed-delivery retries).

```text
python -B -m unittest discover -s deploy/tests -p "test_factory_gatekeeper*.py" -v
python -B -m unittest discover -s deploy/tests -p test_factory_watchdog_audit.py -v
python -B -m unittest discover -s deploy/tests -v
```

Set `TEMP`, `TMP` and `TMPDIR` to a writable scratch root. Do not opt into live
cron tests or supply live credentials for build/review audit verification.

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

`factory-checks` runs `parity-check.ps1`, `semantic-check.ps1`, the Python suite
(`python3 -m unittest discover -s deploy/tests`) and the CR-byte check, all on Ubuntu.
It does **not** run the two `factory-watchdog-*.tests.ps1` suites, and it skips the
Windows-only junction cases in `test_junctions.py`. Run those locally on Windows
before touching `factory-watchdog.ps1`, `deploy.ps1` or the junction scripts.
