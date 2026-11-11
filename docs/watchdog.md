# Factory watchdog

`deploy/factory-watchdog.ps1` answers one question every 15 minutes: **are the factory
worker loops actually running?**

## Why it exists, and why it is not a Hermes cron job

On 2026-07-27 the Build and Review workers died on a Codex `Connection error`. The
2026-08-02 Hermes v0.20.0 update left them disabled, and nobody noticed for **twelve
days** — the queue silently froze and detection was accidental, via a manual audit.

Every health signal at the time lived *inside* the thing that had failed: `hermes cron
list` needs the gateway, and a watchdog scheduled through Hermes cron dies with the
scheduler it is supposed to be watching (the old `agentic-os-watchdog` job vanished in
that same incident). From Slack, a dead factory and an idle factory are both silence.

So this watchdog runs under **Windows Task Scheduler**, and reads only files:
`jobs.json`, `ticker_heartbeat`, the newest Gatekeeper output, and the Linear bot
expiry date from `-EnvFile`. Health checks do not need a gateway API or CLI.
Delivery uses the supported `hermes send` CLI. Checks still run when Hermes is
down; if the CLI cannot deliver, exit 3 and the pending sidecar record expose the
failure to Task Scheduler. Delivery is not independent of the Hermes installation.

## What it checks

Per cron directory, every run:

| Check | Fails when |
|---|---|
| `ticker-liveness` | `ticker_heartbeat` is older than `-HeartbeatMaxAgeMinutes` (default 5) |
| `enabled-not-paused` | a `Wolftac Factory *` job has `enabled: false` or `paused: true` |
| `last-status` | a job's `last_status` is `error` |
| `next-run-fresh` | `next_run_at` is more than `-StaleIntervals` (default 3) scheduling intervals in the past |
| `recent-failure` | a job's `last_failure.at` is within `-RecentFailureHours` (default 6). Hermes often writes `last_status: ok` on a tick that failed the same way, so `last-status` alone flaps and posts false recoveries |
| `gatekeeper-linear-auth` | the newest output of `Wolftac Factory Gatekeeper` contains `linear-auth-failed` |
| `worker-safety-net` | an enabled, unpaused agent worker matching `-JobNamePattern` has an interval below `-WorkerMinIntervalMinutes` (default 120), or a present schedule kind other than `interval` |

The safety-net check excludes `Wolftac Factory Gatekeeper`, `no_agent: true`,
disabled or paused jobs, and schedules without `kind`. Every checked worker has
one `ok`/`FAIL` report row; each violation adds one finding to the existing
transition/nag/recovery path, not a new sender. Unreadable jobs are still reported
by the existing cron check. `-WorkerMinIntervalMinutes` overrides the threshold.

After merge, Jonathan can restore the four worker schedules with
`pwsh deploy/worker-schedules.ps1` (dry run), then `-Apply`, then another `-Apply`
to confirm four `OK` lines. The script validates all targets first, backs up each
changed profile once, invokes `hermes -p <profile> cron edit <id> --schedule "every 120m"`,
and verifies the resulting interval. It never writes `jobs.json` directly or
restarts the scheduler. Apply schedules before pulling the new watchdog check.
The gatekeeper and paused rollback/factory-integrate jobs are not targets.
Retain the `jobs.json.bak-*-pre-safety-net` backups; rollback an individual job
through the same Hermes command with its prior interval, not a direct JSON write.
Build/review uses only scratch homes and the fake Hermes command; watchdog
acceptance probes use `-DryRun` and synthetic inputs, never live credentials.

Once per run, `linear-token-expiry` checks only
`LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT` in `-EnvFile`. It adds one finding if the date
is missing, unparseable, expired, or **less than 3 days away**. Valid dates are
ISO-8601 timestamps with a timezone; comparisons and reported dates use UTC. The
expiry key may be quoted. Invalid raw values are never echoed. The watchdog does
not extract, validate, or re-mint a Linear token, and never writes an `.env` file.

The Gatekeeper check runs for each `-CronDir`, independently of `-JobNamePattern`.
It reads only the newest file in `output\<job-id>\`, ordered by last-write time UTC
(descending filename breaks ties). An older rejection does not keep a healthy
newest output unhealthy. A missing Gatekeeper job, output directory or output file
adds no finding; other checks cover scheduler liveness. An unreadable existing
output produces an unhealthy read finding. Output contents are never included in
alerts. Both checks feed the existing transition/nag/recovery path to Discord.

Zero matching jobs is also a failure — deleted, renamed, or a wrong `-CronDir` should
never look healthy. Non-interval schedules report `next-run-fresh` as **skipped**
rather than guessing an interval.

### Deploy drift (WD-348)

Once per run, independent of the cron directories, the watchdog compares repo content
against deployed content for every pair `deploy.ps1` owns.

| Check | Fails when |
|---|---|
| `DRIFT` | a deployed file's LF-normalized hash differs from its repo copy |
| `cannot compare` | either side is unreadable **or the deployed copy is absent** — never counted as clean, and reported distinctly from drift |

The pair list is **imported** from `deploy.ps1` by dot-sourcing it with no switches, so
it falls through to its usage line without checking, writing, or packaging anything. A
pair added to `deploy.ps1` is covered here with no edit to the watchdog; a duplicated
list would quietly stop covering new targets, which is exactly the class of failure this
watchdog exists to catch. Comparison uses `deploy.ps1`'s own `Get-LfHash`, so a
line-ending-only difference is not drift and the two tools cannot disagree about what is
drifted.

Junctioned targets are skipped: the repo file *is* the deployed file, so drift is not
possible. Pass `-SkipDrift` to turn the check off; it then carries the previous drift
state forward rather than asserting a clean result for a check that never ran.

`deploy.ps1` resolves targets through `%USERPROFILE%` and `%LOCALAPPDATA%`. The task
runs as the operator, and the drift check compares against the profile that owns `-EnvFile`
(`<profile>\AppData\Local\hermes\.env`) instead. Pass `-UserProfile C:\Users\<name>` to
set it explicitly.

Drift alerts are a **separate concern from liveness** and say so in their own words —
they lead with the drift count and state that the factory is running normally. Drift
never changes the exit code, so Task Scheduler's Last Result stays a pure liveness
signal. Conflating the two would either cry wolf about a healthy factory or bury a
worker death under a docs-drift message.

Drift magnitude counts **differing lines**, not the difference of the two file
lengths. Subtracting totals is right for an append and wrong for an in-place edit: a
same-length substitution reported `+0 deployed-only, +0 repo-only`, naming a drifted
target while claiming nothing had changed.

A deployed copy that is **absent** is `cannot compare`, not drift — we do not know
whether the two sides agree, so calling it drift asserts a comparison that never
happened. This is deliberately stricter than `deploy.ps1 -Check`, which counts
`MISSING` toward its own drift total; the two tools still agree on the set of
genuinely drifted targets, which is what the parity check measures.

The drift message's liveness clause is **conditional**. It says "the factory is running
normally" only when liveness actually passed this tick; when liveness is unhealthy it
points at the separate worker alert instead. Asserting normal operation in the same tick
as an outage alert can mask the failure the watchdog exists to surface.

Alerts carry **paths and line counts only, never file content**: a `/learn` edit can
pick up anything, and this goes to the private Discord #approvals channel.

Drift has its own transition, its own 12h nag, and its own single recovery message,
tracked in `watchdog-state.json` under `drift_status` / `drift_last_alert_at`. A sidecar
written before this feature has no drift keys; that is treated as "no prior state", not
an error, so rollback and roll-forward both work with no migration step.

### Pending `/learn` edits (WD-362)

Junctioned Hermes skills are the live worktree itself (`D:\Wolftac\.factory-live`,
see `docs/hermes-skill-junctions.md`), so the drift check skips them: there is only
one copy. That made uncommitted `/learn` work there invisible, and on 2026-08-30 a
rewrite destroyed ~5 such lines without anything noticing. This is a third concern,
separate from liveness and drift.

- **Resolve:** the junction roots come from `deploy.ps1`'s own `$JunctionedRoots`
  (the same import the drift check uses), then `git -C <root> rev-parse --show-toplevel`.
- **Detect:** `git status --porcelain -uall` in that worktree. Modified, deleted *and
  untracked* files count. `/learn` creates reference docs unversioned, so untracked
  files are the common case.
- **Report:** one line per file, `PENDING <path> (+added/-removed, kind)`. Paths and
  line counts only, never content: a `/learn` edit can pick up anything.
- **Snapshot:** whenever the pending content changes, every changed file is copied
  byte-exact (and hash-verified) to
  `%LOCALAPPDATA%\hermes\backups\learn-edits\<UTC-timestamp>\<repo-relative-path>`.
  The root resolves under the operator profile and can be
  overridden with `-LearnBackupRoot`. Unchanged content on a later run takes no new
  snapshot. Snapshots older than `-LearnRetentionDays` (default 30) are pruned; the
  most recent is never removed.
- **Restore by hand:** copy the file from the newest snapshot back into the live
  worktree, then review with `git diff` and commit. Nothing is ever auto-committed.
- **Alerts:** its own transition, 12h nag and single recovery message, tracked as
  `learn_status` / `learn_last_alert_at` (plus `learn_fingerprint` / `learn_snapshot`)
  in the same sidecar. A sidecar without these keys counts as "no prior state". The
  check never affects the exit code.
- **Cannot check:** no junction roots, a root outside any git worktree, or a failing
  `git status` reports `LEARN: CANNOT CHECK` and alerts as such. It never reports clean.
- Disable with `-SkipLearn`, which carries the previous state forward untouched.

Regression suite: `deploy/tests/factory-watchdog-learn.tests.ps1` (hermetic: scratch
repo, scratch junctioned worktree, `-AlertOutbox` interception).

An unreadable `jobs.json`, missing heartbeat, or unreadable expiry input is an
UNHEALTHY finding that alerts. Approval policy is no longer a delivery dependency. The watchdog must never fail quiet: quiet failure is
the disease it treats.

### Done-without-integration audit (WD-580)

`Test-GatekeeperAudit` reads the newest Gatekeeper output with the same validated
single-component job ID and ordering as the auth check. It never queries Linear
or reads a Linear token. Only `WD-<digits>` identifiers from the audit marker may
enter alerts; other output text is discarded. Across cron directories, IDs are
unioned and status precedence is `found`, `cannot-check`, `clean`, `not-reported`.

The console prints one of:

```text
AUDIT: <n> Done issue(s) without an INTEGRATION REPORT: <ids>
AUDIT CLEAN: latest Gatekeeper audit found none
AUDIT: CANNOT CHECK - Gatekeeper audit overdue
AUDIT: not reported by the Gatekeeper
AUDIT: skipped (-SkipAudit)
```

This fourth concern never changes the liveness exit code or delays a worker-outage
alert. Failed delivery still produces the existing exit 3. The existing
`Send-SlackAlert`, sender and channel are unchanged. A first finding, changed ID
set, or overdue transition posts once; unchanged problems re-ping at most every
`-NagHours`. A later clean daily window posts one recovery, meaning no **new**
unreported Done issues, not that previously reported issues were repaired.

State is `audit_status`, `audit_ids`, `audit_last_alert_at` in the same scratchable
sidecar. Missing keys mean clean. `not-reported` (old/no-audit/missing output) and
`-SkipAudit` carry previous state forward, like `-SkipLearn`. Failed sends preserve
the prior audit state and alert time so transitions and recoveries retry.

Hermetic tests: `python -B -m unittest discover -s deploy/tests -p test_factory_watchdog_audit.py -v`.
Use scratch cron/policy/fake credential inputs with `-DryRun` or `-SlackOutbox`;
build/review never send a real alert or run against the live credential file.

### Deployment order

WD-547's automatic re-mint starts within 5 days of expiry; the 3-day watchdog
threshold allows that process a 2-day recovery window. Prefer deploying WD-547
first. If this check is deployed before WD-547's first mint writes the date, the
**expiry date missing** alert is intentional and remains until the date exists.
Any manual date seeding is WD-547's post-merge operator step, not a build/review
action. WD-548 supplies the `linear-auth-failed` output marker. This change neither
modifies nor deploys those components.

## Alerts

On a healthy → unhealthy transition it posts to Discord **#approvals**
(`discord:1562236246158221323`), @-mentioning Jonathan (`<@1022999767753691186>`),
naming the job, the failing check, and the observed value. While it stays unhealthy it
re-pings at most every `-NagHours` (default 12) so a dead factory cannot decay into
normalized silence. On unhealthy → healthy it posts exactly one recovery message.

Alerts use Harper's Hermes profile `integrator` (expected bot user
`1562178628211449966`), never a human session. `-DiscordTarget` and
`-HermesProfile` override the destination and profile. The watchdog invokes:

```text
hermes -p integrator send --to discord:1562236246158221323 --file <temporary-file> --json
```

It prefers `%LOCALAPPDATA%\hermes\bin\hermes.exe` when present, else `hermes`
on PATH. Message files are ASCII text encoded as UTF-8 without a BOM, removed
in `finally` after every attempt. The temp root is `TMPDIR` when set, else the
OS temp directory. Success requires exit zero, JSON `success: true`, and a
nonempty numeric `message_id`; raw CLI output/errors are never logged.
`-PolicyPath` remains accepted but unused for old task invocations.
`-AlertOutbox` intercepts delivery to local files for tests; `-SlackOutbox` is a
compatibility alias only, not a messaging transport.

State lives in `watchdog-state.json` **inside the watched cron directory**, so a
fault-injection run against a scratch dir can never read or clobber the live
watchdog's memory.

No messaging token is read by this script. Hermes handles its own credentials.
`-EnvFile` remains solely for the existing Linear expiry-date check.

The optional `delivery_failure` object in the sidecar holds pending messages by
concern (`liveness`, `drift`, `learn`) and a bounded error code. A nonzero CLI exit,
missing id, malformed response or command failure returns exit 3, preserves the
last delivered timestamp and retries on the next run. A pending unhealthy alert
is delivered even if checks have since recovered, then the recovery is delivered.
A failed recovery is also retried. A same-state retry does not produce a duplicate
nag. Each concern is independent; skipped concerns retain their pending records.
Missing, malformed or old-format sidecars start with no pending delivery.
`-DryRun` returns before any send, replay, snapshot or state write.

### Operator-only cutover and rollback

Do not retire credentials or change the running service during build/review.
After WD-551 and WD-546 are both deployed, Jonathan or an authorized interactive
operator backs up the live default `.env` and `config.yaml`, removes the retired
Slack factory credential from default and integrator, disables Logan's Slack
platform by removing its Slack token/allowlist keys, and updates his SOUL wording.
Any gateway restart and verification belongs to that operator step, never this
watchdog or a build tick. Do not delete the workspace, apps, channels or history.
Approval-path removal belongs to WD-546; this change does not retire that path.
Reverting the code restores the old delivery path only while its credential is
still available. Keep credential backups private and do not delete rollback data.

### History

The old watchdog used `SLACK_FACTORY_BOT_TOKEN` and `chat.postMessage`.
These names are retained here only to identify the credentials the operator
removes after cutover. There is no Slack API or credential read in the watchdog.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | healthy |
| 1 | unhealthy |
| 3 | Discord delivery failed |
| 4 | watchdog could not read what it needs |

Non-zero is deliberate: Task Scheduler's **Last Result** column becomes a health signal
on its own, readable without opening Discord.

## Install

Only Jonathan performs these steps after merge. From the fast-forwarded
`D:\Wolftac\.factory-live`, first inspect the default, read-only plan:

```powershell
pwsh -NoProfile -File deploy\install-watchdog-task.ps1 -OperatorUser ai-msi\logan_vwagmmf
# Pin the expected hashes to the approved merge commit (see "Trust preconditions"):
pwsh -NoProfile -File deploy\install-watchdog-task.ps1 -OperatorUser ai-msi\logan_vwagmmf -ApprovedCommit <approved merge sha> -ManifestOut <path>\watchdog-manifest.json
# After inspecting the plan, run this from an elevated PowerShell:
pwsh -NoProfile -File deploy\install-watchdog-task.ps1 -OperatorUser ai-msi\logan_vwagmmf -Apply
# Return to the normal, non-elevated operator session:
pwsh -NoProfile -File deploy\install-watchdog-task.ps1 -OperatorUser ai-msi\logan_vwagmmf -Verify -ExpectedManifest <path>\watchdog-manifest.json
```

Default mode prints five source SHA256 values, the ACL and task definition, without
creating the install directory, changing ACLs or accessing Task Scheduler. `-Apply`
refuses non-elevated sessions with exit 2 before writing anything. Re-running Apply
is idempotent. It installs byte-exact copies of `factory-watchdog.ps1`, `deploy.ps1`,
`junctions.json`, `approval-policy.json` and `junction-lib.ps1` into
`C:\ProgramData\WolftacFactory\watchdog\deploy`. The task executes only that copy.

The install directory is owned by `BUILTIN\Administrators`. Its protected DACL
disables inherited permissions and contains exactly three inheritable ACEs:
`NT AUTHORITY\SYSTEM` FullControl, `BUILTIN\Administrators` FullControl, and the
operator ReadAndExecute. Each installed file is owned by `BUILTIN\Administrators` with
the same three entries, set through the open file handle. The helper loads from the
protected installed directory, never the writable repo.

### Threat boundary

The protection is against the operator account's **non-elevated** token only: the
agents and anything they start. An elevated session of the same account, an
administrator or SYSTEM can change anything here; that is outside this boundary.
The installed copy is protected only while no component on its path can be replaced
by that token. Replacing a folder needs no write access to the folder itself: delete
on it, delete-child on its parent, `WRITE_DAC`/`WRITE_OWNER` on either, or owning
either is enough to rename the protected folder away and put another one in its place.

`-Apply` therefore assesses the whole chain before it changes anything: the volume
root, `C:\ProgramData`, `C:\ProgramData\WolftacFactory`, the install directory,
`deploy` and the five files. For each existing component it reads the owner and
DACL, the reparse flag and the physical identity (volume serial plus file id), and
evaluates the operator's effective rights. The operator SID set is the account SID,
Everyone, Authenticated Users, Users, INTERACTIVE, BATCH (the S4U task logon),
LOCAL, CONSOLE LOGON, This Organization, Local account, the account's local groups
and, when the operator runs Verify itself, its real token's groups. `Administrators`
and "Local account and member of Administrators" are excluded: in a filtered UAC
token they are deny-only and never grant access. A group that cannot be resolved
makes the result UNKNOWN. ACEs are evaluated in order (deny before allow in a
canonical DACL), inherit-only ACEs are skipped on the object itself and generic
rights are mapped. A callback/object ACE, an unreadable descriptor or identity, or
an unresolved account is UNKNOWN, and UNKNOWN is never treated as safe.

* Any component: owned by the operator, or granting it `DELETE`, `WRITE_DAC` or
  `WRITE_OWNER`, or a reparse point, is unsafe. A directory granting delete-child is
  unsafe, and so is every child below it.
* Ordinary create-folder/create-file rights on a system ancestor are allowed:
  `C:\ProgramData` grants `Users: Write`, and that cannot replace an existing
  protected folder. Never change `C:\ProgramData` or other system ACLs to satisfy
  the check.
* The install directory, `deploy` and the five files must not be writable by the
  operator at all, and a file with more than one hard link is unsafe.

Apply refuses (exit 3, `APPLY REFUSED: ...` with one `REFUSED <role> <path>: <verdict>
<reasons>` line per failing component) before copying, setting an ACL or touching
Task Scheduler. It never takes ownership of or repairs an untrusted component, such
as a `WolftacFactory` folder that the operator created before Apply: remove or fix
that folder yourself after confirming what it is, then re-run. A missing
`WolftacFactory`, install directory or `deploy` folder is created by the elevated
Apply with an Administrators owner and the protected DACL set at creation. If the
folder turns out to exist at that moment (raced in), it is treated as existing and
reassessed. Each directory is then held open without `FILE_SHARE_DELETE`, so it
cannot be renamed or deleted while Apply runs, and its identity and security are
reassessed before any file is written through it. After the writes Apply reassesses
the chain and the file hashes again; any change there exits 4 and the task is not
registered.

`-RepoRoot` defaults to `D:\Wolftac\Factory` in the installer and is passed to the
copy. Drift and pending `/learn` checks therefore still compare the repo's content,
not the installed folder. Without `-RepoRoot`, the scripts keep their checkout-root
default. The installed copy is not in `deploy.ps1`'s ordinary deployment pair list.
Git calls disable `core.fsmonitor` and optional index locks process-locally; numstat
also disables external diff/textconv. No wildcard `safe.directory` is set.

When any of the five files differs from the repo (LF-normalized comparison), the
drift lane reports `WATCHDOG-STALE <repo-relative path>` and asks Jonathan to re-run
`deploy\install-watchdog-task.ps1 -Apply` after inspecting the plan. Missing or
unreadable files produce `cannot compare`, not a clean result. `-SkipDrift` skips
this comparison. Staleness shares drift's transition/nag/recovery and does not
change the liveness exit code.

`-Verify` prints PASS/FAIL for task principal, S4U logon, run level, action path,
install-dir owner/ACL and the current non-elevated user's inability to create a file.
It then assesses the same chain as Apply (every ancestor, the install directory,
`deploy` and all five files) with the same rules, one `PASS`/`FAIL path <role>
<path>` line each, and compares the SHA-256 of each installed file, read through the
same held handle, with `-ExpectedManifest`: one `PASS`/`FAIL file <name> sha256`
line each. A missing manifest, a missing or changed file, an unsafe parent, child or
file, a reparse point, an identity change or anything UNKNOWN is a named `FAIL`.
A valid install directory never hides an unsafe parent.
It exits zero only when all checks pass. `-TaskXmlOut` writes plan XML for inspection;
`-Verify -TaskXmlIn <scratch.xml>` verifies offline XML instead of reading Task Scheduler.
Workers may use only these scratch seams, never live Verify or elevated Apply.

What a full `-Verify` PASS proves: at that moment, with the descriptors Windows
returned, the operator's non-elevated token could not write, delete, rename,
re-ACL or take ownership of the installed copy or any folder on its path, and the
five files match the pinned hashes. It does not prove anything about elevated
sessions, about later ACL changes, or about a manifest that was itself wrong.

### Trust preconditions

* **Expected hashes come from the approved commit, not from a checkout.** Create the
  manifest with `-ApprovedCommit <sha> -ManifestOut <path>` from the fast-forwarded
  `D:\Wolftac\.factory-live` at that commit, and compare the five `PLAN COPY` SHA256
  values with the ones recorded in the approval pack or build report for that commit
  before using it. A mutable checkout that agents can write is never the authority.
* **Keep the manifest out of agent reach** (for example in an Administrators-only
  folder), or re-check its five hashes against the approval pack before each Verify.
  Verify prints the manifest's commit and its own SHA-256 for that comparison.
* Verify never runs git, repository hooks or repository code to establish the
  expected hashes; the plan and the manifest are plain file hashes.
* Pass long (not 8.3 short) paths; the final-path check fails a short name.

### Required operator live checks

Offline tests use synthetic descriptors and stubbed side effects; they are not
proof of a live protected install. After Apply, Jonathan checks, from a normal
non-elevated operator session:

1. `-Verify -ExpectedManifest <manifest>` prints only `PASS` lines and exits 0.
2. `Get-Acl C:\ProgramData\WolftacFactory | Format-List Owner, AccessToString` shows an
   Administrators (or SYSTEM) owner and no operator or Users delete, delete-child,
   `WRITE_DAC`, `WRITE_OWNER` or FullControl entry.
3. The S4U task check under "Running without an interactive session" below.

`MultipleInstancesPolicy IgnoreNew` keeps a slow CLI send from stacking runs.

Two mechanics that will bite if you change this command:

* **Use the full path to `pwsh.exe`.** A bare `pwsh.exe` fails with Last Result
  `-2147024894` (`0x80070002`, file not found) — the task's PATH is not your shell's.
* **Never point the task at a Microsoft Store `pwsh.exe`.** The Store install lives in a
  versioned folder (`C:\Program Files\WindowsApps\Microsoft.PowerShell_7.x.y.0_x64__...`)
  that disappears on the next PowerShell update, so the task fails with the same
  `0x80070002`; it was found Disabled for a month this way (2026-09-16 to 2026-10-19).
  `(Get-Command pwsh).Source` returns that versioned path when only the Store version is
  installed. Install the machine-wide MSI (`C:\Program Files\PowerShell\7\pwsh.exe`, fixed
  path, patched by Microsoft Update). Behind Avast HTTPS interception, winget's `msstore`
  source fails certificate pinning and elevated sessions may not open sources: run
  `winget download --id Microsoft.PowerShell --exact --source winget --installer-type wix`
  unelevated, confirm `Get-AuthenticodeSignature` is Valid / Microsoft Corporation, then
  `msiexec /i <msi> /qn ADD_PATH=0 USE_MU=1 ENABLE_MU=1` from an elevated shell.
* **Cron directories may be `;`- or `,`-delimited inside ONE quoted argument.**
  `pwsh -File` passes arguments as literal strings rather than binding a list to an
  array parameter, so the script splits either delimiter itself. Accepting commas
  also prevents a natural operator typo from turning two valid paths into one bogus
  directory and an unmarked false outage page. Passing a real PowerShell array still
  works when you call the script from a session rather than via `-File`.

The watchdog alone defaults to `%LOCALAPPDATA%\hermes\cron`. The installer defaults
to all four cron directories under the named operator's profile: `hermes\cron`,
`hermes\profiles\builder\cron`, `hermes\profiles\reviewer\cron` and
`hermes\profiles\integrator\cron`, plus that operator's env file. Override `-CronDir`
and `-EnvFile` for a nonstandard profile location. Omitting a worker profile
leaves it unwatched. The isolated `factory-integrate` profile (WD-281) is parked with
its job disabled, so it is deliberately not listed: watching it would page about a job
that is meant to be off.

### Running without an interactive session

The installer registers the task as the **operator account**, with `LogonType: S4U`
and `RunLevel: LeastPrivilege`, not SYSTEM. Registration needs elevation, but S4U
stores no password and does not require an interactive login. The action uses the
machine-wide PowerShell 7 path and the protected installed copy. These instructions
are a post-merge operator procedure, not a claim that the live task has been migrated.

Confirm the persisted principal and cadence:

```powershell
$task = Get-ScheduledTask -TaskName "WolftacFactoryWatchdog"
$task.Principal | Select-Object UserId, LogonType
$task.Triggers.Repetition.Interval
schtasks /query /tn WolftacFactoryWatchdog /v /fo LIST
```

Expected: `UserId: ai-msi\logan_vwagmmf`, `LogonType: S4U`, `RunLevel: Limited`
(XML `LeastPrivilege`), interval `PT15M`, and `Last Result: 0` or `1` within 30 minutes,
not a launch error. Confirm the cron sidecar's `LastWriteTime` advances and that
sidecars and `/learn` snapshot directories formerly created by SYSTEM are writable
by the operator. Sidecar format is unchanged.

If S4U cannot start, report the launch error on WD-584 and disable the task
(`Disable-ScheduledTask -TaskName WolftacFactoryWatchdog` in an elevated session).
Do **not** re-register the pre-change task: it ran as SYSTEM from the agent-writable
repo, which is the exact privilege gap this change removes, and it is not a safe
fallback. Restoring it, or any other SYSTEM or agent-writable execution path, needs a
separate explicit decision from Jonathan. Reverting the merge and removing the
installed copy (see Remove) leave the factory without the watchdog until a fixed
install is applied. Do not delete rollback artifacts or learn backups.

The historical SYSTEM task registration above is unchanged by WD-551. Before
cutover, verify that its execution account resolves the intended Hermes executable
and Harper profile. The CLI selects profiles from its own environment; `-EnvFile`
and `-UserProfile` do not redirect Hermes credentials. A successful interactive
stub test does not establish that SYSTEM can deliver as Harper. Missing CLI/profile
access is a delivery failure, not permission to copy tokens or run as another bot.

## Verify

The WD-549 regression suite invokes PowerShell with `-DryRun` only, using scratch
files and fake credentials. It covers expiry thresholds, invalid/missing dates,
newest-output selection, secret suppression, multiple cron directories and unchanged
existing checks/alert routing:

The comparison requires `origin/main`; run `git fetch origin` first locally.
Factory Checks uses `actions/checkout` with `fetch-depth: 0` so the baseline ref
exists even in a fresh Actions checkout. A missing baseline remains an error, not
a skipped test or a fallback comparison against the branch itself. Both script
versions receive the same explicit drift input: a scratch pair list for isolated
cases, or the repo's `deploy.ps1` for TP4. Both also receive an explicit operator
profile, since Ubuntu runners do not define Windows' `USERPROFILE`. Drift and
`/learn` checks remain enabled. Isolated cases provide a real clean scratch Git
repository as the junction root, not an empty root list that means "cannot check".
The suite also compares pending untracked edits, missing deploy inputs and unknown
junction roots against current main, and asserts DryRun creates no snapshot,
alert-outbox file or watchdog state. TP4 compares all finding rows and the complete
drift/learn diagnostics, including `PENDING` rows, without filtering them away.

```powershell
python -B -m unittest discover -s deploy/tests -p test_factory_watchdog.py -v
```

For the optional read-only comparison against the current `origin/main` script,
set `WD549_REAL_CRON` to the real cron directory before that command. The test still
uses a scratch credential file and asserts that watchdog state is unchanged. It
compares findings, not timestamps in the report header. Never remove `-DryRun`
from the test runner.

```powershell
schtasks /query /tn WolftacFactoryWatchdog /v /fo LIST | Select-String "TaskName|Status|Last Run|Last Result|Next Run"
Get-Item "$env:LOCALAPPDATA\hermes\cron\watchdog-state.json" | Select-Object LastWriteTime
```

The sidecar's `LastWriteTime` should advance every 15 minutes. That file is the proof
the task is really executing — a registered task that never runs looks identical to a
healthy factory, which is the failure mode this whole issue exists to kill.

Hermetic Discord transport regression (synthetic CLI response, not live proof):

```powershell
python -B -m unittest discover -s deploy/tests -p test_watchdog_discord.py -v
```

It executes the real watchdog against a scratch paused job, asserts exact CLI
arguments, UTF-8/no-BOM payload and deletion, transition/cooldown/nag/recovery,
nonzero/missing-id/malformed responses, pending retry after recovery, and DryRun
byte-for-byte state preservation. No real Hermes process or messaging API runs.

## Silence test (does it actually alert?)

Never doctor the live cron directory — the workers read it. Use a scratch copy:

```powershell
$s = "$env:TEMP\watchdog-drill"; New-Item -ItemType Directory -Force $s | Out-Null
Copy-Item "$env:LOCALAPPDATA\hermes\cron\jobs.json" "$s\jobs.json"
Set-Content "$s\ticker_heartbeat" "x" -NoNewline
$j = Get-Content "$s\jobs.json" -Raw | ConvertFrom-Json
($j.jobs | Where-Object { $_.name -like '*Build Worker*' }).enabled = $false
$j | ConvertTo-Json -Depth 12 | Set-Content "$s\jobs.json"

pwsh -File deploy\factory-watchdog.ps1 -CronDir $s -AlertPrefix ":test_tube: *DRILL - not a real outage.*"
```

Expect one alert, then silence on an immediate re-run (cooldown), then exactly one
recovery message after restoring `enabled: true`.

**Always pass `-AlertPrefix` for drills.** An unmarked test alert that @-mentions
Jonathan is indistinguishable from the real outage this watchdog exists to announce.
An authorized operator must read back the returned message ids in #approvals,
verify Harper's author id and Jonathan's mention, and delete the drill messages
through the supported Discord surface. Never use a human account or a different
bot as a substitute. A local intercepted/stub run does not prove live delivery.

Use `-DryRun` to see findings with no post and no state write.

## Remove

Post-merge operator only, from an elevated session:

```powershell
Unregister-ScheduledTask -TaskName "WolftacFactoryWatchdog" -Confirm:$false
Remove-Item "C:\ProgramData\WolftacFactory\watchdog" -Recurse -Force
Remove-Item "$env:LOCALAPPDATA\hermes\cron\watchdog-state.json" -ErrorAction SilentlyContinue
```

Remove sidecars in each configured cron directory only if their state is no longer
needed. The source scripts remain repo-owned; removing the task and installed copy
does not remove source, learn snapshots or rollback artifacts.
