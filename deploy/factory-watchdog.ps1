<#
Out-of-band factory watchdog (WD-345).

Watches the Hermes cron scheduler that runs the factory worker loops, from OUTSIDE
Hermes: pure filesystem reads of `jobs.json` and `ticker_heartbeat`, no CLI, no
gateway API. That is the whole point — on 2026-07-27 the Build and Review workers
died, the 2026-08-02 Hermes update left them disabled, and nobody noticed for twelve
days, because every health signal lived inside the thing that had failed. A watchdog
scheduled through Hermes cron would have died with it.

  pwsh -NoProfile -File deploy\factory-watchdog.ps1 -DryRun     # print findings, post nothing
  pwsh -NoProfile -File deploy\factory-watchdog.ps1             # alert on transition
  pwsh -NoProfile -File deploy\factory-watchdog.ps1 -CronDir C:\scratch\cron   # fault-injection

Install as a Windows Scheduled Task (15m): see docs\watchdog.md.

Checks, per cron directory:
  1. ticker liveness   — ticker_heartbeat mtime fresher than -HeartbeatMaxAgeMinutes
  2. job enabled       — every job matching -JobNamePattern is enabled and not paused
  3. job last_status   — not "error"
  4. job next_run_at   — not more than -StaleIntervals scheduling intervals in the past

Alert discipline: post on the healthy -> unhealthy transition, re-ping at most every
-NagHours while it stays unhealthy, and post exactly one recovery message on the
unhealthy -> healthy transition. A dead factory must not decay into normalized
silence, and a flapping check must not spam the channel.

Exit codes: 0 healthy - 1 unhealthy - 3 Slack delivery failed - 4 watchdog could not
read what it needs. A watchdog that fails quiet has the disease it treats, so an
unreadable jobs.json or .env is an UNHEALTHY finding that alerts, never a silent exit.

The Slack bot token is read at runtime and never printed, logged, or persisted.
#>
[CmdletBinding()]
param(
    # Accepts several directories so one task can watch every profile's scheduler.
    # The factory's integrate worker runs under its own Hermes profile, so its cron
    # dir is NOT the default one — see docs\watchdog.md.
    [string[]]$CronDir = @("$env:LOCALAPPDATA\hermes\cron"),
    [switch]$DryRun,
    [string]$PolicyPath = (Join-Path $PSScriptRoot "approval-policy.json"),
    [string]$EnvFile = "$env:LOCALAPPDATA\hermes\.env",
    [string]$JobNamePattern = "Wolftac Factory *",
    [int]$HeartbeatMaxAgeMinutes = 5,
    [int]$StaleIntervals = 3,
    [int]$NagHours = 12,
    # Prepended to alert text. Exists so fault-injection runs against the real channel
    # are unmistakably drills — an unmarked test alert that mentions Jonathan is
    # indistinguishable from the real outage this watchdog exists to announce.
    [string]$AlertPrefix = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# `pwsh -File` passes list-like arguments as literal strings instead of binding them
# to a [string[]] parameter; without normalization, the first Join-Path treats the
# combined text as one bogus directory. Since Task Scheduler invokes us through -File,
# accept a ';'- or ','-delimited string and split it here. A comma cannot
# appear in a valid Windows path, and accepting it prevents a natural operator typo
# from turning two real cron directories into one bogus path and a false outage page.
# Passing a real array still works when the script is called from a PowerShell session.
$CronDir = @(
    $CronDir |
        ForEach-Object { $_ -split '[;,]' } |
        ForEach-Object { $_.Trim().Trim('"').Trim("'") } |
        Where-Object { $_ }
)
if ($CronDir.Count -eq 0) { throw "No cron directory given (-CronDir)." }

# Findings accumulate here; each is one failed check, already human-readable.
$findings = New-Object System.Collections.Generic.List[string]
# Per-job check results, so a healthy run still shows its work rather than asserting
# "all clear" — an unexplained pass is how a mis-pointed watchdog looks healthy.
$report = New-Object System.Collections.Generic.List[object]
$readFailure = $false

function Add-Finding([string]$Text) { $script:findings.Add($Text) }

function Add-Report([string]$Scope, [string]$Check, [string]$Result, [string]$Observed) {
    $script:report.Add([pscustomobject]@{
        Scope = $Scope; Check = $Check; Result = $Result; Observed = $Observed
    })
}

function Get-SlackToken {
    # Runtime read only. The value is never echoed, written to the sidecar, or
    # embedded in the scheduled-task definition.
    if ($env:SLACK_FACTORY_BOT_TOKEN) { return $env:SLACK_FACTORY_BOT_TOKEN }
    if (-not (Test-Path -LiteralPath $EnvFile)) { return $null }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -match '^\s*SLACK_FACTORY_BOT_TOKEN\s*=\s*(.+?)\s*$') { return $Matches[1] }
    }
    return $null
}

function Get-Policy {
    if (-not (Test-Path -LiteralPath $PolicyPath)) {
        Add-Finding "watchdog cannot read approval policy at $PolicyPath"
        $script:readFailure = $true
        return $null
    }
    try { return Get-Content -LiteralPath $PolicyPath -Raw | ConvertFrom-Json }
    catch {
        Add-Finding "watchdog cannot parse approval policy at ${PolicyPath}: $($_.Exception.Message)"
        $script:readFailure = $true
        return $null
    }
}

function Get-Jobs([string]$Path) {
    # Returns $null on any read/parse failure — the caller turns that into a finding.
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $doc = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    } catch { return $null }
    if ($null -eq $doc) { return $null }
    # Hermes writes { jobs: [...], updated_at }; tolerate a bare array or a map too,
    # since this file is Hermes-owned and its shape is not ours to guarantee.
    $jobs = $null
    if ($doc -is [array]) { $jobs = $doc }
    elseif ($doc.PSObject.Properties.Name -contains 'jobs') {
        $jobs = $doc.jobs
        if ($jobs -isnot [array]) { $jobs = @($jobs.PSObject.Properties | ForEach-Object { $_.Value }) }
    } else {
        $jobs = @($doc.PSObject.Properties | ForEach-Object { $_.Value })
    }
    return @($jobs)
}

function Get-Prop($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    if ($Object.PSObject.Properties.Name -contains $Name) { return $Object.$Name }
    return $null
}

function Test-CronDirectory([string]$Dir) {
    # --- check 1: ticker liveness -------------------------------------------------
    $hb = Join-Path $Dir "ticker_heartbeat"
    if (-not (Test-Path -LiteralPath $hb)) {
        Add-Finding "[$Dir] ticker: heartbeat file missing ($hb) - the scheduler has never run or the directory is wrong"
        $script:readFailure = $true
    } else {
        $ageMin = [math]::Round(((Get-Date) - (Get-Item -LiteralPath $hb).LastWriteTime).TotalMinutes, 1)
        if ($ageMin -gt $HeartbeatMaxAgeMinutes) {
            Add-Finding "[$Dir] ticker: DEAD - heartbeat is ${ageMin}m old (limit ${HeartbeatMaxAgeMinutes}m)"
            Add-Report $Dir "ticker-liveness" "FAIL" "${ageMin}m old (limit ${HeartbeatMaxAgeMinutes}m)"
        } else {
            Add-Report $Dir "ticker-liveness" "ok" "${ageMin}m old (limit ${HeartbeatMaxAgeMinutes}m)"
        }
    }

    # --- checks 2-4: per job ------------------------------------------------------
    $jobsPath = Join-Path $Dir "jobs.json"
    $jobs = Get-Jobs $jobsPath
    if ($null -eq $jobs) {
        Add-Finding "[$Dir] jobs: watchdog cannot read or parse $jobsPath"
        $script:readFailure = $true
        return
    }

    $matched = @($jobs | Where-Object { (Get-Prop $_ 'name') -like $JobNamePattern })
    if ($matched.Count -eq 0) {
        # Zero matches is itself a failure: the workers were deleted, renamed, or this
        # is the wrong directory. Silence here would reproduce the original incident.
        Add-Finding "[$Dir] jobs: no job matching '$JobNamePattern' in $jobsPath - workers deleted, renamed, or wrong cron dir"
        return
    }

    foreach ($job in $matched) {
        $name = Get-Prop $job 'name'

        $enabled = Get-Prop $job 'enabled'
        $paused = Get-Prop $job 'paused'
        if (-not $enabled) { Add-Finding "[$Dir] ${name}: DISABLED (enabled: false)" }
        if ($paused) { Add-Finding "[$Dir] ${name}: PAUSED (paused: true)" }
        Add-Report $name "enabled-not-paused" $(if ($enabled -and -not $paused) { "ok" } else { "FAIL" }) `
            "enabled=$enabled paused=$(if ($null -eq $paused) { 'null' } else { $paused })"

        $status = Get-Prop $job 'last_status'
        if ($status -and "$status".ToLower() -eq 'error') {
            $err = Get-Prop $job 'last_error'
            $detail = if ($err) { " - $err" } else { "" }
            Add-Finding "[$Dir] ${name}: last_status=error$detail (last run $(Get-Prop $job 'last_run_at'))"
            Add-Report $name "last-status" "FAIL" "last_status=$status$detail"
        } else {
            Add-Report $name "last-status" "ok" "last_status=$(if ($status) { $status } else { '(none)' })"
        }

        # Overdue is measured in scheduling intervals, so a 5m job and a daily job are
        # judged on their own terms. Non-interval schedules carry no minute count, so
        # the check is reported as skipped rather than guessed at.
        $next = Get-Prop $job 'next_run_at'
        $sched = Get-Prop $job 'schedule'
        $minutes = Get-Prop $sched 'minutes'
        if ($next -and $minutes) {
            try {
                $nextDt = [datetimeoffset]::Parse($next)
                $overdueMin = ((Get-Date) - $nextDt.LocalDateTime).TotalMinutes
                $limit = $minutes * $StaleIntervals
                if ($overdueMin -gt $limit) {
                    Add-Finding ("[$Dir] {0}: OVERDUE - next_run_at was {1:N0}m ago (limit {2}m = {3} x {4}m interval)" -f `
                        $name, $overdueMin, $limit, $StaleIntervals, $minutes)
                    Add-Report $name "next-run-fresh" "FAIL" ("overdue by {0:N0}m (limit {1}m)" -f $overdueMin, $limit)
                } else {
                    Add-Report $name "next-run-fresh" "ok" ("due in {0:N0}m (limit {1}m overdue)" -f (-$overdueMin), $limit)
                }
            } catch {
                Add-Finding "[$Dir] ${name}: unparseable next_run_at '$next'"
                Add-Report $name "next-run-fresh" "FAIL" "unparseable next_run_at"
            }
        } else {
            Add-Report $name "next-run-fresh" "skipped" "no interval schedule or no next_run_at - not guessed at"
        }
    }
}

function Get-StatePath([string]$Dir) { Join-Path $Dir "watchdog-state.json" }

function Send-SlackAlert([string]$Text, $Policy) {
    $token = Get-SlackToken
    if (-not $token) {
        # Write-Host keeps diagnostics visible without adding them to the function's
        # success-output pipeline. The caller must receive exactly one Boolean;
        # otherwise PowerShell returns [diagnostic, Boolean], a truthy array that
        # falsely records failed delivery as success and suppresses retries for 12h.
        Write-Host "SLACK: no SLACK_FACTORY_BOT_TOKEN available (checked env and $EnvFile) - cannot deliver"
        return $false
    }
    $identity = $Policy.fallback_identity   # watchdog alerts are never a worker's face
    $body = @{
        channel    = $Policy.slack_channel_id
        text       = $Text
        username   = $identity.username
        icon_emoji = $identity.icon_emoji
    } | ConvertTo-Json -Depth 5
    try {
        $resp = Invoke-RestMethod -Uri "https://slack.com/api/chat.postMessage" -Method Post `
            -Headers @{ Authorization = "Bearer $token"; "Content-Type" = "application/json; charset=utf-8" } `
            -Body ([Text.Encoding]::UTF8.GetBytes($body))
    } catch {
        Write-Host "SLACK: delivery failed - $($_.Exception.Message)"
        return $false
    }
    if (-not $resp.ok) {
        # $resp.error is a Slack error code (e.g. channel_not_found), never a credential.
        Write-Host "SLACK: API returned not-ok - $($resp.error)"
        return $false
    }
    Write-Host "SLACK: posted as '$($identity.username)' to $($Policy.slack_channel_id) (ts $($resp.ts))"
    return $true
}

# ── run the checks ───────────────────────────────────────────────────────────────
foreach ($dir in $CronDir) { Test-CronDirectory $dir }

$policy = Get-Policy
$healthy = ($findings.Count -eq 0)

Write-Output "Factory watchdog $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - watching: $($CronDir -join ', ')"
foreach ($r in $report) {
    Write-Output ("  {0,-6} {1,-20} {2}  [{3}]" -f $r.Result, $r.Check, $r.Observed, $r.Scope)
}
if ($healthy) {
    Write-Output "HEALTHY: all checks passed (ticker liveness, enabled, last_status, next_run_at)"
} else {
    Write-Output "UNHEALTHY: $($findings.Count) finding(s)"
    foreach ($f in $findings) { Write-Output "  - $f" }
}

if ($DryRun) {
    Write-Output "DRY RUN: no Slack post, no state written."
    exit ($healthy ? 0 : 1)
}

if ($null -eq $policy) {
    # Without the policy we cannot address a message; say so on stdout so the task's
    # Last Result and transcript still carry the signal.
    Write-Output "Cannot alert: approval policy unreadable."
    exit 4
}

# ── transition-based alerting ────────────────────────────────────────────────────
# State lives per cron directory, so a scratch-dir fault-injection run can never
# read or clobber the live watchdog's memory.
$statePath = Get-StatePath $CronDir[0]
$prev = $null
if (Test-Path -LiteralPath $statePath) {
    try { $prev = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { $prev = $null }
}
$prevStatus = if ($prev) { Get-Prop $prev 'status' } else { 'healthy' }
$prevAlert = if ($prev) { Get-Prop $prev 'last_alert_at' } else { $null }

$now = Get-Date
$shouldPost = $false
$message = $null
$mention = "<@$($policy.approver_slack_user_id)>"

if (-not $healthy) {
    $detail = ($findings | ForEach-Object { "- $_" }) -join "`n"
    if ($prevStatus -ne 'unhealthy') {
        $shouldPost = $true
        $message = ":warning: *Factory watchdog: the worker loops need attention* $mention`n$detail"
    } else {
        $hoursSince = if ($prevAlert) { ($now - [datetime]::Parse($prevAlert)).TotalHours } else { [double]::MaxValue }
        if ($hoursSince -ge $NagHours) {
            $shouldPost = $true
            $message = (":warning: *Factory watchdog: still unhealthy after {0:N0}h* {1}`n{2}" -f $hoursSince, $mention, $detail)
        } else {
            Write-Output "COOLDOWN: still unhealthy, last alert $([math]::Round($hoursSince,1))h ago (nag every ${NagHours}h) - not posting."
        }
    }
} elseif ($prevStatus -eq 'unhealthy') {
    $shouldPost = $true
    $message = ":white_check_mark: *Factory watchdog: recovered* - all checks passing again."
}

$delivered = $true
if ($shouldPost) {
    if ($AlertPrefix) { $message = "$AlertPrefix`n$message" }
    $delivered = Send-SlackAlert $message $policy
}

# Persist AFTER the attempt. A failed delivery leaves last_alert_at untouched so the
# next tick retries rather than silently swallowing the alert.
$newState = [ordered]@{
    status        = if ($healthy) { 'healthy' } else { 'unhealthy' }
    checked_at    = $now.ToString('o')
    findings      = @($findings)
    last_alert_at = if ($shouldPost -and $delivered) { $now.ToString('o') } else { $prevAlert }
}
try {
    $newState | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "STATE: $statePath -> $($newState.status)"
} catch {
    Write-Output "STATE: could not write $statePath - $($_.Exception.Message)"
}

if (-not $delivered) { exit 3 }
exit ($healthy ? 0 : 1)
