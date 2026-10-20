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
`jobs.json` and `ticker_heartbeat`. No Hermes CLI, no gateway API. It keeps working
when Hermes is entirely down — which is exactly when it matters.

## What it checks

Per cron directory, every run:

| Check | Fails when |
|---|---|
| `ticker-liveness` | `ticker_heartbeat` is older than `-HeartbeatMaxAgeMinutes` (default 5) |
| `enabled-not-paused` | a `Wolftac Factory *` job has `enabled: false` or `paused: true` |
| `last-status` | a job's `last_status` is `error` |
| `next-run-fresh` | `next_run_at` is more than `-StaleIntervals` (default 3) scheduling intervals in the past |

Zero matching jobs is also a failure — deleted, renamed, or a wrong `-CronDir` should
never look healthy. Non-interval schedules report `next-run-fresh` as **skipped**
rather than guessing an interval.

An unreadable `jobs.json`, missing heartbeat, or unreadable approval policy is an
UNHEALTHY finding that alerts. The watchdog must never fail quiet: quiet failure is
the disease it treats.

## Alerts

On a healthy → unhealthy transition it posts to the channel in
`deploy/approval-policy.json` (`slack_channel_id`), @-mentioning `approver_slack_user_id`,
naming the job, the failing check, and the observed value. While it stays unhealthy it
re-pings at most every `-NagHours` (default 12) so a dead factory cannot decay into
normalized silence. On unhealthy → healthy it posts exactly one recovery message.

Alerts post as the policy's `fallback_identity` (**Wolftac Factory** / `:factory:`) —
deliberately not any worker's face, and never as Jonathan.

State lives in `watchdog-state.json` **inside the watched cron directory**, so a
fault-injection run against a scratch dir can never read or clobber the live
watchdog's memory.

`SLACK_FACTORY_BOT_TOKEN` is read at runtime from `%LOCALAPPDATA%\hermes\.env` (or the
environment). The value never appears in the script, the task definition, the sidecar,
logs, or any alert.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | healthy |
| 1 | unhealthy |
| 3 | Slack delivery failed |
| 4 | watchdog could not read what it needs |

Non-zero is deliberate: Task Scheduler's **Last Result** column becomes a health signal
on its own, readable without opening Slack.

## Install

```powershell
$repo  = "D:\Wolftac\Factory"
$pwsh  = "C:\Program Files\PowerShell\7\pwsh.exe"   # machine-wide MSI install; stable across updates
if (-not (Test-Path $pwsh)) { throw "Install PowerShell 7 machine-wide first (see below)" }
$dirs  = "$env:LOCALAPPDATA\hermes\cron"   # add ";$env:LOCALAPPDATA\hermes\profiles\factory-integrate\cron"
                                           # only while that profile runs a live integrate job (parked 2026-10-19)
$arg   = "-NoProfile -NonInteractive -File `"$repo\deploy\factory-watchdog.ps1`" -CronDir `"$dirs`""
$action  = New-ScheduledTaskAction -Execute $pwsh -Argument $arg
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Limited
Register-ScheduledTask -TaskName "WolftacFactoryWatchdog" `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Out-of-band liveness watchdog for the Wolftac factory worker loops (WD-345)."
```

`-MultipleInstances IgnoreNew` keeps a slow Slack call from stacking runs.

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

The default watches only `%LOCALAPPDATA%\hermes\cron`. The **integrate worker runs in
its own Hermes profile** with a separate cron directory, so the command above lists
both — omit the second path and the integrate worker can die unwatched.

### Running without an interactive session

The install command above registers the task as the Windows **SYSTEM** service
account (`LogonType: ServiceAccount`). That needs an elevated PowerShell, but no
stored user password, interactive login, or batch-logon grant. SYSTEM has been
verified on this machine with the installed Microsoft Store PowerShell 7 path.

Confirm the persisted principal and cadence:

```powershell
$task = Get-ScheduledTask -TaskName "WolftacFactoryWatchdog"
$task.Principal | Select-Object UserId, LogonType
$task.Triggers.Repetition.Interval
schtasks /query /tn WolftacFactoryWatchdog /v /fo LIST
```

Expected: `UserId: SYSTEM`, `LogonType: ServiceAccount`, interval `PT15M`, and
`Last Result: 0` after a scheduled tick.

## Verify

```powershell
schtasks /query /tn WolftacFactoryWatchdog /v /fo LIST | Select-String "TaskName|Status|Last Run|Last Result|Next Run"
Get-Item "$env:LOCALAPPDATA\hermes\cron\watchdog-state.json" | Select-Object LastWriteTime
```

The sidecar's `LastWriteTime` should advance every 15 minutes. That file is the proof
the task is really executing — a registered task that never runs looks identical to a
healthy factory, which is the failure mode this whole issue exists to kill.

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
Delete drill messages afterwards (`chat.delete` with the same bot token) and leave the
channel clean — a channel that cries wolf trains people to ignore it.

Use `-DryRun` to see findings with no post and no state write.

## Remove

```powershell
Unregister-ScheduledTask -TaskName "WolftacFactoryWatchdog" -Confirm:$false
Remove-Item "$env:LOCALAPPDATA\hermes\cron\watchdog-state.json" -ErrorAction SilentlyContinue
```

The script itself is repo-owned; deleting the task and the sidecar restores the prior
state completely.
