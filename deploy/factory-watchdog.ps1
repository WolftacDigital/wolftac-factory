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
  5. job last_failure  — no failure recorded in the last -RecentFailureHours (default 6)
  6. gatekeeper auth  — newest Gatekeeper output does not contain linear-auth-failed

Once per run: the Linear bot expiry date in -EnvFile is valid and at least 3 days away.
Only the expiry key is extracted; no Linear credentials are used or validated.

And once per run, independent of the cron directories:
  5. deploy drift (WD-348) — repo vs deployed content for every pair deploy.ps1 owns,
     LF-normalized so a line-ending difference is not drift. Drift is reported as a
     SEPARATE concern from liveness with its own transition, cooldown, and recovery
     message: a drifted-but-running factory is not an outage, and conflating the two
     would either cry wolf about a healthy factory or bury a worker death under a
     docs-drift message. Alerts carry paths and line counts only, never file content
     — a /learn edit can pick up anything, and this goes to a Slack channel.
     Disable with -SkipDrift.
  6. pending /learn edits (WD-362) — `git status` of the junctioned live worktree.
     Junctioned skills are exempt from drift (the repo IS the deployed copy), so
     uncommitted /learn work there was invisible and its destruction silent. Changed
     and untracked files are reported by path and line count only, and snapshotted
     byte-exact to -LearnBackupRoot\<UTC-timestamp>\ whenever the pending content
     changes (30-day retention, newest always kept). Its own transition, cooldown and
     recovery message; never affects the exit code. An unresolvable worktree or a
     failed `git status` is "cannot check", never clean. Disable with -SkipLearn.

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
    [int]$RecentFailureHours = 6,
    # Prepended to alert text. Exists so fault-injection runs against the real channel
    # are unmistakably drills — an unmarked test alert that mentions Jonathan is
    # indistinguishable from the real outage this watchdog exists to announce.
    [string]$AlertPrefix = "",
    # Deploy drift (WD-348). The pair list is IMPORTED from deploy.ps1 rather than
    # copied, so a pair added there is covered here with no edit to this file. A
    # duplicated list would silently stop covering new targets, which is the same
    # class of failure this watchdog exists to catch.
    [string]$DeployScript = (Join-Path $PSScriptRoot "deploy.ps1"),
    [switch]$SkipDrift,
    # Profile whose deployed copies the drift check compares against. deploy.ps1
    # resolves targets through %USERPROFILE%/%LOCALAPPDATA%, which under the SYSTEM
    # scheduled task is C:\WINDOWS\system32\config\systemprofile -- where nothing is
    # deployed, so every pair read as "cannot compare". Defaults to the profile that
    # owns -EnvFile (the task already passes the operator's .env), else the current one.
    [string]$UserProfile = "",
    # Pending /learn edits (WD-362). The junctioned live worktree is invisible to the
    # drift check by design, so uncommitted /learn work there is watched separately and
    # snapshotted locally. Snapshots default under the operator profile's Hermes
    # backups root, for the same SYSTEM-account reason as -UserProfile.
    [string]$LearnBackupRoot = "",
    [int]$LearnRetentionDays = 30,
    [switch]$SkipLearn,
    # Test seam: write each alert payload to a file here instead of posting it, so the
    # regression suite can assert on exact message text without touching Slack.
    [string]$SlackOutbox = ""
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

$DriftProfile = $UserProfile
if (-not $DriftProfile -and $EnvFile -match '^(.+)\\AppData\\Local\\hermes\\') { $DriftProfile = $Matches[1] }
if (-not $DriftProfile) { $DriftProfile = $env:USERPROFILE }
if (-not $LearnBackupRoot) { $LearnBackupRoot = Join-Path $DriftProfile "AppData\Local\hermes\backups\learn-edits" }

# Findings accumulate here; each is one failed check, already human-readable.
$findings = New-Object System.Collections.Generic.List[string]
# Per-job check results, so a healthy run still shows its work rather than asserting
# "all clear" — an unexplained pass is how a mis-pointed watchdog looks healthy.
$report = New-Object System.Collections.Generic.List[object]
$readFailure = $false

function Add-Finding([string]$Text) { $script:findings.Add($Text) }

# ── deploy drift (WD-348) ────────────────────────────────────────────────────────
# Deliberately a SEPARATE concern from liveness. A drifted-but-running factory is not
# an outage: folding drift into $findings would either cry wolf about a healthy
# factory or bury a real worker death under a docs-drift message. Separate lists,
# separate state, separate message, separate transition.
$driftFindings = New-Object System.Collections.Generic.List[string]
$driftUnreadable = New-Object System.Collections.Generic.List[string]
$driftChecked = 0
$driftSkipped = 0
$driftAvailable = $false

# ── pending /learn edits (WD-362) ────────────────────────────────────────────────
# A third concern, separate from liveness and drift. Junctioned skills ARE the live
# worktree, so drift never looks at them; uncommitted /learn work there was invisible
# and its destruction silent. Paths and line counts only, never content.
$learnPending = New-Object System.Collections.Generic.List[object]
$learnUnreadable = New-Object System.Collections.Generic.List[string]
$learnWorktrees = New-Object System.Collections.Generic.List[string]
$junctionRoots = $null
$learnSnapshot = $null

function Add-Drift([string]$Text) { $script:driftFindings.Add($Text) }
function Add-DriftUnreadable([string]$Text) { $script:driftUnreadable.Add($Text) }

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

function Test-LinearTokenExpiry {
    # Match only the date key, never the bot token or app credentials. Do not print
    # raw values or exceptions: a malformed value could itself contain a secret.
    $value = $null
    try {
        foreach ($line in Get-Content -LiteralPath $EnvFile -ErrorAction Stop) {
            if ($line -match '^\s*LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT\s*=\s*(.*?)\s*$') {
                $value = $Matches[1].Trim()
                if ($value -match '^(["''])(.*)\1$') { $value = $Matches[2] }
                break
            }
        }
    } catch {
        Add-Finding "linear-token-expiry: watchdog cannot read expiry date from $EnvFile"
        Add-Report $EnvFile "linear-token-expiry" "FAIL" "expiry file unreadable"
        $script:readFailure = $true
        return
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        Add-Finding "linear-token-expiry: expiry date missing (LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT)"
        Add-Report $EnvFile "linear-token-expiry" "FAIL" "expiry date missing"
        return
    }
    $expires = [datetimeoffset]::MinValue
    # Require an ISO-8601 timestamp with a timezone, not a locale-dependent date.
    $iso = '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+-]\d{2}:\d{2})$'
    if ($value -cnotmatch $iso -or -not [datetimeoffset]::TryParse(
            $value, [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AdjustToUniversal, [ref]$expires)) {
        Add-Finding "linear-token-expiry: expiry date unparseable (expected ISO-8601 timestamp with timezone)"
        Add-Report $EnvFile "linear-token-expiry" "FAIL" "expiry date unparseable"
        return
    }
    $date = $expires.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'")
    if ($expires -lt [datetimeoffset]::UtcNow.AddDays(3)) {
        Add-Finding "linear-token-expiry: expires $date (expired or less than 3 days away); automatic re-mint needs attention"
        Add-Report $EnvFile "linear-token-expiry" "FAIL" "expires $date"
    } else {
        Add-Report $EnvFile "linear-token-expiry" "ok" "expires $date"
    }
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

function Test-GatekeeperAuth([string]$Dir, $Jobs) {
    foreach ($job in @($Jobs | Where-Object { (Get-Prop $_ 'name') -eq 'Wolftac Factory Gatekeeper' })) {
        $id = Get-Prop $job 'id'
        # Job ids must be single path components; never follow a jobs.json path out
        # of its output directory. Missing jobs/output have no finding by contract.
        if (-not $id -or "$id" -notmatch '^[A-Za-z0-9_-]+$') { continue }
        $outputDir = Join-Path (Join-Path $Dir 'output') "$id"
        if (-not (Test-Path -LiteralPath $outputDir -PathType Container)) { continue }
        try {
            $newest = Get-ChildItem -LiteralPath $outputDir -File |
                Sort-Object -Property LastWriteTimeUtc, Name -Descending | Select-Object -First 1
            if ($null -eq $newest) { continue }
            $output = Get-Content -LiteralPath $newest.FullName -Raw -ErrorAction Stop
            if ($output -cmatch 'linear-auth-failed') {
                Add-Finding "[$Dir] gatekeeper-linear-auth: newest Gatekeeper output reports linear-auth-failed"
                Add-Report $Dir "gatekeeper-linear-auth" "FAIL" "linear-auth-failed in newest output"
            } else {
                Add-Report $Dir "gatekeeper-linear-auth" "ok" "no rejection marker in newest output"
            }
        } catch {
            Add-Finding "[$Dir] gatekeeper-linear-auth: watchdog cannot read newest Gatekeeper output"
            Add-Report $Dir "gatekeeper-linear-auth" "FAIL" "output unreadable"
            $script:readFailure = $true
        }
    }
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

    Test-GatekeeperAuth $Dir $jobs
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

        # Hermes records a failed run in last_failure but often still writes
        # last_status=ok on the next tick, even when that tick failed the same way
        # (2026-10-19: integrate hit a Linear 401 on every run for two days while
        # last_status alternated, so this watchdog posted "recovered" twice). A failure
        # inside the window keeps the job unhealthy, so the incident stays open until
        # the job has actually gone -RecentFailureHours without failing.
        $lf = Get-Prop $job 'last_failure'
        $lfAt = Get-Prop $lf 'at'
        if ($lfAt) {
            try {
                $lfDt = [datetimeoffset]::Parse("$lfAt")
                $ageH = ((Get-Date) - $lfDt.LocalDateTime).TotalHours
                if ($ageH -le $RecentFailureHours) {
                    $lfDetail = "$(Get-Prop $lf 'detail')" -split "`n" |
                        ForEach-Object { $_.Trim(' ', '-', '*', '#') } | Where-Object { $_ } | Select-Object -First 3
                    $lfText = ($lfDetail -join ' / ')
                    if ($lfText.Length -gt 300) { $lfText = $lfText.Substring(0, 300) + '...' }
                    Add-Finding ("[$Dir] {0}: FAILED {1:N1}h ago (window {2}h) - {3}" -f $name, $ageH, $RecentFailureHours, $lfText)
                    Add-Report $name "recent-failure" "FAIL" ("last_failure {0:N1}h ago (window {1}h)" -f $ageH, $RecentFailureHours)
                } else {
                    Add-Report $name "recent-failure" "ok" ("last_failure {0:N1}h ago (window {1}h)" -f $ageH, $RecentFailureHours)
                }
            } catch {
                Add-Finding "[$Dir] ${name}: unparseable last_failure.at '$lfAt'"
                Add-Report $name "recent-failure" "FAIL" "unparseable last_failure.at"
            }
        } else {
            Add-Report $name "recent-failure" "ok" "no last_failure recorded"
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
    if ($SlackOutbox) {
        New-Item -ItemType Directory -Force -Path $SlackOutbox | Out-Null
        $file = Join-Path $SlackOutbox ("{0:yyyyMMddTHHmmssfff}-{1}.txt" -f (Get-Date), [guid]::NewGuid().ToString('N').Substring(0, 6))
        Set-Content -LiteralPath $file -Value $Text -Encoding utf8 -NoNewline
        Write-Host "SLACK: intercepted to $file (-SlackOutbox)"
        return $true
    }
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

function Measure-Drift {
    # Imports deploy.ps1's pair definitions (AC-5) plus its LF-normalized hash
    # helpers (AC-6), so the watchdog and `deploy.ps1 -Check` cannot disagree about
    # what a target is or what counts as drift.
    #
    # Dot-sourced with no switches: deploy.ps1 falls through every branch to its
    # usage line and returns, so nothing is checked, written, or packaged here.
    if (-not (Test-Path -LiteralPath $DeployScript)) {
        Add-DriftUnreadable "cannot compare: deploy script not found at $DeployScript"
        return
    }
    $savedProfile = $env:USERPROFILE
    $savedLocal = $env:LOCALAPPDATA
    try {
        $env:USERPROFILE = $script:DriftProfile
        $env:LOCALAPPDATA = Join-Path $script:DriftProfile "AppData\Local"
        . $DeployScript *> $null
    } catch {
        # A watchdog that fails quiet has the disease it treats.
        Add-DriftUnreadable "cannot compare: unable to load pair definitions from ${DeployScript}: $($_.Exception.Message)"
        return
    } finally {
        $env:USERPROFILE = $savedProfile
        $env:LOCALAPPDATA = $savedLocal
    }
    # Dot-sourcing inside a function lands the definitions in THIS scope, not the
    # script scope, which is why $Pairs is looked up here without -Scope Script.
    # Hand the junction roots to the /learn check so deploy.ps1 is loaded once per run.
    $script:junctionRoots = @(if (Get-Variable -Name JunctionedRoots -ErrorAction SilentlyContinue) { $JunctionedRoots })
    if (-not (Get-Variable -Name Pairs -ErrorAction SilentlyContinue)) {
        Add-DriftUnreadable "cannot compare: $DeployScript defined no pair list"
        return
    }

    $script:driftAvailable = $true
    foreach ($p in $Pairs) {
        # A junctioned target IS the repo file; there is one set of bytes and drift
        # is not possible. Matches deploy.ps1 -Check exactly (AC-8).
        if (Test-UnderJunction $p.Dst) { $script:driftSkipped++; continue }

        $repoPath = Join-Path $Repo $p.Src
        $srcHash = $null
        $dstHash = $null
        try { $srcHash = Get-LfHash $repoPath } catch { $srcHash = $null }
        try { $dstHash = Get-LfHash $p.Dst } catch { $dstHash = $null }

        if ($null -eq $srcHash) {
            # Distinct from drift: we could not read one side, so we do not know
            # whether they agree. Never counted as clean (AC-7).
            Add-DriftUnreadable "cannot compare $($p.Src) (repo copy unreadable at $repoPath)"
            continue
        }
        if ($null -eq $dstHash) {
            # AC-7: an unreadable side -- repo OR deployed -- is "cannot compare",
            # never drift. We do not know whether the two agree, so calling it drift
            # asserts a comparison that never happened. Note this is deliberately
            # stricter than `deploy.ps1 -Check`, which counts MISSING toward its drift
            # total; the two still agree on the set of genuinely drifted targets.
            Add-DriftUnreadable "cannot compare $($p.Src) (deployed copy absent at $($p.Dst))"
            continue
        }
        $script:driftChecked++
        if ($srcHash -ne $dstHash) {
            # Line counts only -- never file content. A /learn edit can pick up
            # anything, and this message goes to a Slack channel.
            # Parenthesise the call: `Get-LfText $p -split "..."` binds -split as an
            # argument to the function instead of operating on its result, and every
            # count silently comes back 0 -- an alert that names a drifted target and
            # then reports +0/+0 tells the operator nothing about urgency.
            $repoText = $null
            $dstText = $null
            try { $repoText = @((Get-LfText $repoPath) -split "`n") } catch { $repoText = $null }
            try { $dstText = @((Get-LfText $p.Dst) -split "`n") } catch { $dstText = $null }
            $repoLines = if ($null -eq $repoText) { -1 } else { $repoText.Count }
            $dstLines = if ($null -eq $dstText) { -1 } else { $dstText.Count }
            if ($repoLines -lt 0 -or $dstLines -lt 0) {
                Add-DriftUnreadable "cannot compare $($p.Src) (hashes differ but line counts could not be read)"
                continue
            }
            # Count DIFFERING lines, not the difference of totals. Subtracting file
            # lengths reports +0/+0 for a same-line substitution -- the alert names a
            # drifted target and then claims nothing changed, which is worse than no
            # magnitude at all. A multiset difference is truthful for appends,
            # deletions, and in-place edits alike.
            $counts = @{}
            foreach ($line in $dstText) { $counts[$line] = ($counts[$line] + 1) }
            foreach ($line in $repoText) { $counts[$line] = ($counts[$line] - 1) }
            $deployedOnly = 0
            $repoOnly = 0
            foreach ($v in $counts.Values) {
                if ($v -gt 0) { $deployedOnly += $v } elseif ($v -lt 0) { $repoOnly += (-$v) }
            }
            Add-Drift ("DRIFT    {0} (deployed {1} lines, repo {2} lines; +{3} deployed-only, +{4} repo-only)" -f `
                $p.Dst, $dstLines, $repoLines, $deployedOnly, $repoOnly)
        }
    }
}

function Add-LearnUnreadable([string]$Text) { $script:learnUnreadable.Add($Text) }

function Get-LineCount([string]$Path) {
    try {
        $text = [IO.File]::ReadAllText($Path)
    } catch { return -1 }
    if ($text.Length -eq 0) { return 0 }
    $n = ($text -split "`n").Count
    if ($text.EndsWith("`n")) { $n-- }
    return $n
}

function Measure-Learn {
    # Resolve the live worktree from deploy.ps1's own junction list, so this check and
    # the drift exemption can never disagree about what is junctioned.
    if ($null -eq $script:junctionRoots) {
        # -SkipDrift means Measure-Drift never loaded deploy.ps1; load it here instead.
        if (-not (Test-Path -LiteralPath $DeployScript)) {
            Add-LearnUnreadable "cannot check: deploy script not found at $DeployScript"
            return
        }
        $savedProfile = $env:USERPROFILE
        $savedLocal = $env:LOCALAPPDATA
        try {
            $env:USERPROFILE = $script:DriftProfile
            $env:LOCALAPPDATA = Join-Path $script:DriftProfile "AppData\Local"
            . $DeployScript *> $null
        } catch {
            Add-LearnUnreadable "cannot check: unable to load junction definitions from ${DeployScript}: $($_.Exception.Message)"
            return
        } finally {
            $env:USERPROFILE = $savedProfile
            $env:LOCALAPPDATA = $savedLocal
        }
        $script:junctionRoots = @(if (Get-Variable -Name JunctionedRoots -ErrorAction SilentlyContinue) { $JunctionedRoots })
    }
    # No junctions is "cannot check", not clean: silence must mean "checked and clean".
    if ($script:junctionRoots.Count -eq 0) {
        Add-LearnUnreadable "cannot check: $DeployScript resolved no junctioned skill roots, so the live worktree is unknown"
        return
    }

    $tops = [ordered]@{}
    foreach ($root in $script:junctionRoots) {
        $top = & git -C $root rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $top) {
            Add-LearnUnreadable "cannot check: junction $root does not resolve to a git worktree"
            continue
        }
        $tops[[IO.Path]::GetFullPath("$top".Trim())] = $true
    }

    foreach ($wt in $tops.Keys) {
        $script:learnWorktrees.Add($wt)
        # -uall: Hermes /learn creates reference docs unversioned, so untracked files
        # are the common case, not an edge case.
        $lines = & git -C $wt -c core.quotepath=off status --porcelain=v1 -uall 2>$null
        if ($LASTEXITCODE -ne 0) {
            Add-LearnUnreadable "cannot check: git status failed in $wt (exit $LASTEXITCODE)"
            continue
        }
        foreach ($l in @($lines)) {
            if (-not $l -or $l.Length -lt 4) { continue }
            $code = $l.Substring(0, 2)
            $path = $l.Substring(3)
            if ($path -match ' -> ') { $path = ($path -split ' -> ')[-1] }
            $path = $path.Trim('"')
            $full = Join-Path $wt $path
            if ($code -eq '??') {
                $kind = 'untracked'
                $added = Get-LineCount $full
                $removed = 0
            } else {
                $kind = if ($code -match 'D') { 'deleted' } else { 'modified' }
                $added = -1
                $removed = -1
                $ns = & git -C $wt diff --numstat HEAD -- $path 2>$null
                if ($LASTEXITCODE -eq 0 -and $ns) {
                    $parts = "$(@($ns)[0])" -split "`t"
                    # numstat prints "-" for binary files; report that as unknown.
                    if ($parts[0] -match '^\d+$') { $added = [int]$parts[0] }
                    if ($parts[1] -match '^\d+$') { $removed = [int]$parts[1] }
                }
            }
            $script:learnPending.Add([pscustomobject]@{
                Worktree = $wt; Path = $path; Full = $full; Kind = $kind; Added = $added; Removed = $removed
            })
        }
    }
}

function Format-LearnLine($p) {
    $a = if ($p.Added -lt 0) { '?' } else { $p.Added }
    $r = if ($p.Removed -lt 0) { '?' } else { $p.Removed }
    # Paths and counts only. A /learn edit can pick up anything; this goes to Slack.
    "PENDING  {0} (+{1}/-{2}, {3})" -f $p.Path, $a, $r, $p.Kind
}

function Get-LearnFingerprint {
    # Identity of the pending content, so an unchanged re-run takes no new snapshot.
    $parts = foreach ($p in ($learnPending | Sort-Object Worktree, Path)) {
        $h = if ($p.Kind -eq 'deleted') { 'deleted' } else {
            try { (Get-FileHash -LiteralPath $p.Full -Algorithm SHA256).Hash } catch { 'unreadable' }
        }
        "$($p.Worktree)|$($p.Path)|$($p.Kind)|$h"
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
    $sha = [Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "")
}

function New-LearnSnapshot {
    # Byte-exact copies under <root>\<UTC-timestamp>\<repo-relative-path>. Copies are
    # verified by hash: a snapshot that silently differs is the failure this exists for.
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $dir = Join-Path $LearnBackupRoot $stamp
    $i = 1
    while (Test-Path -LiteralPath $dir) { $dir = Join-Path $LearnBackupRoot "$stamp-$i"; $i++ }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    foreach ($p in $learnPending) {
        if ($p.Kind -eq 'deleted') { continue }   # nothing left to copy
        $rel = if ($learnWorktrees.Count -gt 1) { Join-Path (Split-Path $p.Worktree -Leaf) $p.Path } else { $p.Path }
        $dest = Join-Path $dir $rel
        New-Item -ItemType Directory -Force -Path (Split-Path $dest -Parent) | Out-Null
        Copy-Item -LiteralPath $p.Full -Destination $dest -Force
        if ((Get-FileHash -LiteralPath $p.Full).Hash -ne (Get-FileHash -LiteralPath $dest).Hash) {
            throw "snapshot copy of $($p.Path) does not match the working file"
        }
    }
    return $dir
}

function Invoke-LearnRetention {
    # Prune snapshots older than -LearnRetentionDays, never the most recent one.
    if (-not (Test-Path -LiteralPath $LearnBackupRoot)) { return }
    $dirs = @(Get-ChildItem -LiteralPath $LearnBackupRoot -Directory | Sort-Object Name -Descending)
    $cutoff = (Get-Date).AddDays(-$LearnRetentionDays)
    foreach ($d in ($dirs | Select-Object -Skip 1)) {
        if ($d.LastWriteTime -lt $cutoff) {
            Remove-Item -LiteralPath $d.FullName -Recurse -Force
            Write-Output "LEARN RETENTION: pruned $($d.Name) (older than $LearnRetentionDays days)"
        }
    }
}

# ── run the checks ───────────────────────────────────────────────────────────────
foreach ($dir in $CronDir) { Test-CronDirectory $dir }
Test-LinearTokenExpiry
if (-not $SkipDrift) { Measure-Drift }
if (-not $SkipLearn) { Measure-Learn }

$policy = Get-Policy
# Alert delivery is part of watchdog health, not a capability to discover only after
# the factory is already down. If the token cannot be read, surface that loss now so
# Task Scheduler never records a false healthy result while Slack alerting is dead.
if (-not (Get-SlackToken)) {
    Add-Finding "watchdog cannot read SLACK_FACTORY_BOT_TOKEN (checked environment and $EnvFile)"
    $readFailure = $true
}
$healthy = ($findings.Count -eq 0)

Write-Output "Factory watchdog $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - watching: $($CronDir -join ', ')"
foreach ($r in $report) {
    Write-Output ("  {0,-6} {1,-20} {2}  [{3}]" -f $r.Result, $r.Check, $r.Observed, $r.Scope)
}
if ($healthy) {
    Write-Output "HEALTHY: all checks passed (ticker liveness, enabled, last_status, next_run_at, recent failure, Linear expiry, Gatekeeper auth)"
} else {
    Write-Output "UNHEALTHY: $($findings.Count) finding(s)"
    foreach ($f in $findings) { Write-Output "  - $f" }
}

# Drift is reported on its own line so a reader can tell at a glance which concern
# is speaking. Silence here must mean "compared and clean", never "failed to compare".
$driftClean = ($driftFindings.Count -eq 0 -and $driftUnreadable.Count -eq 0)
if ($SkipDrift) {
    Write-Output "DRIFT: skipped (-SkipDrift)"
} elseif (-not $driftAvailable) {
    Write-Output "DRIFT: CANNOT COMPARE - $($driftUnreadable.Count) condition(s)"
    foreach ($d in $driftUnreadable) { Write-Output "  - $d" }
} elseif ($driftClean) {
    Write-Output "DRIFT CLEAN: all $driftChecked copy-deployed file(s) match the repo ($driftSkipped junctioned, not checked)."
} else {
    Write-Output "DRIFT: $($driftFindings.Count) target(s) out of sync, $($driftUnreadable.Count) that could not be compared (of $driftChecked checked)"
    foreach ($d in $driftFindings) { Write-Output "  - $d" }
    foreach ($d in $driftUnreadable) { Write-Output "  - $d" }
}

# Pending /learn edits get their own line: neither an outage nor drift.
if ($SkipLearn) {
    Write-Output "LEARN: skipped (-SkipLearn)"
} elseif ($learnUnreadable.Count -gt 0) {
    Write-Output "LEARN: CANNOT CHECK - $($learnUnreadable.Count) condition(s)"
    foreach ($u in $learnUnreadable) { Write-Output "  - $u" }
    foreach ($p in $learnPending) { Write-Output "  - $(Format-LearnLine $p)" }
} elseif ($learnPending.Count -eq 0) {
    Write-Output "LEARN CLEAN: no uncommitted /learn edits in $($learnWorktrees -join ', ')"
} else {
    Write-Output "LEARN: $($learnPending.Count) uncommitted file(s) in the live skill worktree $($learnWorktrees -join ', ')"
    foreach ($p in $learnPending) { Write-Output "  - $(Format-LearnLine $p)" }
}

if ($DryRun) {
    Write-Output "DRY RUN: no Slack post, no snapshot, no state written."
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
# A sidecar written before WD-348 has no drift keys. Missing or old-format state is
# "no prior state", never an error -- that is what makes rollback and roll-forward
# both work with no migration step.
$prevDriftStatus = if ($prev) { Get-Prop $prev 'drift_status' } else { $null }
if (-not $prevDriftStatus) { $prevDriftStatus = 'clean' }
$prevDriftAlert = if ($prev) { Get-Prop $prev 'drift_last_alert_at' } else { $null }

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

# ── drift alerting, on its own transition ────────────────────────────────────────
# Same discipline as liveness (post on transition, nag at most every -NagHours, one
# recovery message) but a separate state machine and unmistakably different wording:
# a drift alert must never read as "the factory is dead".
$driftShouldPost = $false
$driftMessage = $null
$driftStatus = $prevDriftStatus

if (-not $SkipDrift) {
    $driftStatus = if ($driftClean -and $driftAvailable) { 'clean' } else { 'drifted' }
    if ($driftStatus -eq 'drifted') {
        $detail = (@($driftFindings) + @($driftUnreadable) | ForEach-Object { "- $_" }) -join "`n"
        # The liveness clause is CONDITIONAL. Asserting "the factory is running
        # normally" in the same tick that liveness reported unhealthy contradicts the
        # outage alert and can mask the very failure this watchdog exists to surface.
        $livenessClause = if ($healthy) {
            " - the factory is running normally"
        } else {
            " - NOTE: liveness is UNHEALTHY this tick; see the separate worker alert"
        }
        $headline = if ($driftAvailable) {
            "*Deploy drift: $($driftFindings.Count) target(s) out of sync*$livenessClause"
        } else {
            "*Deploy drift check could not run*$livenessClause, and drift is currently unmonitored"
        }
        if ($prevDriftStatus -ne 'drifted') {
            $driftShouldPost = $true
            $driftMessage = ":triangular_flag_on_post: $headline`n$detail`nRun ``deploy.ps1 -Check`` to inspect, ``deploy.ps1 -Reconcile`` to pull deployed edits into the repo."
        } else {
            $hoursSinceDrift = if ($prevDriftAlert) { ($now - [datetime]::Parse($prevDriftAlert)).TotalHours } else { [double]::MaxValue }
            if ($hoursSinceDrift -ge $NagHours) {
                $driftShouldPost = $true
                $driftMessage = (":triangular_flag_on_post: *Deploy drift still unresolved after {0:N0}h*{1}`n{2}" -f $hoursSinceDrift, $livenessClause, $detail)
            } else {
                Write-Output "DRIFT COOLDOWN: still drifted, last drift alert $([math]::Round($hoursSinceDrift,1))h ago (nag every ${NagHours}h) - not posting."
            }
        }
    } elseif ($prevDriftStatus -eq 'drifted') {
        $driftShouldPost = $true
        $driftMessage = ":white_check_mark: *Deploy drift resolved* - repo and deployed copies agree again ($driftChecked file(s) checked)."
    }
}

$driftDelivered = $true
if ($driftShouldPost) {
    if ($AlertPrefix) { $driftMessage = "$AlertPrefix`n$driftMessage" }
    $driftDelivered = Send-SlackAlert $driftMessage $policy
}

# ── pending /learn edits, on their own transition (WD-362) ───────────────────────
# Same post-on-transition / nag / one-recovery discipline, a third state machine and
# wording that cannot be mistaken for an outage or for drift. Old sidecars lack these
# keys; that is "no prior state", never an error.
$prevLearnStatus = if ($prev) { Get-Prop $prev 'learn_status' } else { $null }
if (-not $prevLearnStatus) { $prevLearnStatus = 'clean' }
$prevLearnAlert = if ($prev) { Get-Prop $prev 'learn_last_alert_at' } else { $null }
$prevLearnFingerprint = if ($prev) { Get-Prop $prev 'learn_fingerprint' } else { $null }
$prevLearnSnapshot = if ($prev) { Get-Prop $prev 'learn_snapshot' } else { $null }

$learnStatus = $prevLearnStatus
$learnFingerprint = $prevLearnFingerprint
$learnSnapshot = $prevLearnSnapshot
$learnShouldPost = $false
$learnMessage = $null
$snapshotError = $null

if (-not $SkipLearn) {
    $learnStatus = if ($learnUnreadable.Count -gt 0) { 'cannot-check' }
                   elseif ($learnPending.Count -gt 0) { 'pending' }
                   else { 'clean' }
    if ($learnPending.Count -gt 0) {
        # Snapshot whenever the pending content changes, alert or not: the snapshot is
        # the recovery path, and unchanged content needs no second copy.
        $fp = Get-LearnFingerprint
        if ($fp -ne $prevLearnFingerprint) {
            try {
                $learnSnapshot = New-LearnSnapshot
                $learnFingerprint = $fp
                Write-Output "LEARN SNAPSHOT: $learnSnapshot"
            } catch {
                $snapshotError = $_.Exception.Message
                Write-Output "LEARN SNAPSHOT FAILED: $snapshotError"
            }
        } else {
            Write-Output "LEARN SNAPSHOT: content unchanged since $learnSnapshot - not re-snapshotting."
        }
    } else {
        $learnFingerprint = $null
    }

    if ($learnStatus -ne 'clean') {
        $lines = @($learnUnreadable) + @($learnPending | ForEach-Object { Format-LearnLine $_ })
        if ($snapshotError) { $lines += "SNAPSHOT FAILED: $snapshotError" }
        $detail = ($lines | ForEach-Object { "- $_" }) -join "`n"
        $where = $learnWorktrees -join ', '
        $headline = if ($learnStatus -eq 'pending') {
            "*Uncommitted /learn edits in the live skill worktree* ($($learnPending.Count) file(s), committed nowhere yet)"
        } else {
            "*Pending /learn edits: cannot check the live skill worktree* - uncommitted /learn work is currently unmonitored"
        }
        $footer = if ($learnStatus -eq 'pending') {
            "Snapshot: ``$learnSnapshot```nReview with ``git -C $where diff``, commit what belongs, push. (Not a worker outage; not a copy-deploy mismatch.)"
        } else { "(Not a worker outage; not a copy-deploy mismatch.)" }
        if ($prevLearnStatus -ne $learnStatus) {
            $learnShouldPost = $true
            $learnMessage = ":pencil2: $headline`n$detail`n$footer"
        } else {
            $hoursSinceLearn = if ($prevLearnAlert) { ($now - [datetime]::Parse($prevLearnAlert)).TotalHours } else { [double]::MaxValue }
            if ($hoursSinceLearn -ge $NagHours) {
                $learnShouldPost = $true
                $learnMessage = (":pencil2: *Still uncommitted after {0:N0}h:* {1}`n{2}`n{3}" -f $hoursSinceLearn, $headline, $detail, $footer)
            } else {
                Write-Output "LEARN COOLDOWN: last /learn alert $([math]::Round($hoursSinceLearn,1))h ago (nag every ${NagHours}h) - not posting."
            }
        }
    } elseif ($prevLearnStatus -ne 'clean') {
        $learnShouldPost = $true
        $learnMessage = ":white_check_mark: */learn edits resolved* - the live skill worktree is clean again."
    }
    Invoke-LearnRetention
}

$learnDelivered = $true
if ($learnShouldPost) {
    if ($AlertPrefix) { $learnMessage = "$AlertPrefix`n$learnMessage" }
    $learnDelivered = Send-SlackAlert $learnMessage $policy
}

# Persist AFTER the attempt. A failed delivery leaves last_alert_at untouched so the
# next tick retries rather than silently swallowing the alert.
$newState = [ordered]@{
    status              = if ($healthy) { 'healthy' } else { 'unhealthy' }
    checked_at          = $now.ToString('o')
    findings            = @($findings)
    last_alert_at       = if ($shouldPost -and $delivered) { $now.ToString('o') } else { $prevAlert }
    # Drift state rides alongside liveness state in the same disposable sidecar.
    # -SkipDrift carries the previous drift state forward untouched rather than
    # asserting "clean" for a check that never ran.
    drift_status        = $driftStatus
    drift_findings      = @(@($driftFindings) + @($driftUnreadable))
    drift_last_alert_at = if ($driftShouldPost -and $driftDelivered) { $now.ToString('o') } else { $prevDriftAlert }
    learn_status        = $learnStatus
    learn_findings      = @(@($learnUnreadable) + @($learnPending | ForEach-Object { Format-LearnLine $_ }))
    learn_last_alert_at = if ($learnShouldPost -and $learnDelivered) { $now.ToString('o') } else { $prevLearnAlert }
    learn_fingerprint   = $learnFingerprint
    learn_snapshot      = $learnSnapshot
}
try {
    $newState | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "STATE: $statePath -> $($newState.status)"
} catch {
    Write-Output "STATE: could not write $statePath - $($_.Exception.Message)"
}

if (-not $delivered -or -not $driftDelivered -or -not $learnDelivered) { exit 3 }
# Drift and pending /learn edits deliberately do NOT change the exit code: a drifted-but-running factory is
# not an outage, and Task Scheduler's Last Result is the liveness signal (AC-3).
exit ($healthy ? 0 : 1)
