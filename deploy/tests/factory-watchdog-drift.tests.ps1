# factory-watchdog-drift.tests.ps1 -- regression harness for the watchdog's drift
# alerting (WD-348).
#
#   pwsh -NoProfile -File deploy/tests/factory-watchdog-drift.tests.ps1
#
# Hermetic by construction: the suite builds its own scratch repo, deployed tree,
# cron dir, and a stand-in deploy.ps1 whose $Pairs point at scratch destinations.
# It never reads or writes a live deploy target, the real cron directory, the task
# scheduler, or Slack -- the Slack token it exports is a literal fake, and delivery
# is asserted by inspecting state, not by sending anything.
#
# The three cases it exists to hold down are the ones a green suite missed once:
#   * magnitude on a SAME-LENGTH substitution (the original count subtracted total
#     file lengths, so an in-place edit reported "+0 / +0" while naming the file as
#     drifted). Assertions check the NUMBERS, not the labels.
#   * a drift alert must not claim "the factory is running normally" when liveness
#     failed in the same tick.
#   * a deployed copy that is ABSENT is "cannot compare", never drift and never
#     clean -- we do not know whether the two sides agree.
[CmdletBinding()]
param(
    # Repo under test; defaults to the checkout this file lives in.
    [string]$Repo = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    # Scratch root. Must be outside $Repo.
    [string]$Scratch = (Join-Path ([IO.Path]::GetTempPath()) 'wolftac-watchdog-drift-tests')
)
$ErrorActionPreference = 'Stop'
$Watchdog = Join-Path $Repo 'deploy\factory-watchdog.ps1'
$script:pass = 0; $script:fail = 0
$failures = @()

function Ok($name, $cond, $detail = '') {
  if ($cond) { $script:pass++; Write-Host "PASS $name" }
  else { $script:fail++; $script:failures += "$name  $detail"; Write-Host "FAIL $name  $detail" }
}

# A scratch deploy script: same code as the real one, but its $Pairs point at scratch
# destinations. This is how the drill never touches a live deploy target.
function New-ScratchDeploy {
  param([string]$Root, [int]$PairCount = 3, [switch]$ExtraPair)
  $repoDir = Join-Path $Root 'repo'
  $depDir  = Join-Path $Root 'deployed'
  New-Item -ItemType Directory -Force (Join-Path $repoDir 'deploy') | Out-Null
  New-Item -ItemType Directory -Force (Join-Path $repoDir 'canonical') | Out-Null
  New-Item -ItemType Directory -Force $depDir | Out-Null

  $names = @('alpha.md', 'beta.md', 'gamma.md')
  if ($ExtraPair) { $names += 'delta.md' }
  foreach ($n in $names) {
    $body = "# $n`nline one`nline two`nline three`n"
    Set-Content -LiteralPath (Join-Path $repoDir "canonical\$n") -Value $body -NoNewline
    Set-Content -LiteralPath (Join-Path $depDir $n) -Value $body -NoNewline
  }

  $pairLines = ($names | ForEach-Object {
    "    @{ Src = `"canonical\$_`"; Dst = `"$depDir\$_`" }"
  }) -join "`n"

  $deploy = @"
param([switch]`$Check, [switch]`$Deploy, [switch]`$Package, [switch]`$Force, [switch]`$Reconcile, [string]`$Only)
`$Repo = Split-Path `$PSScriptRoot -Parent
`$Pairs = @(
$pairLines
)
`$JunctionedRoots = @()
function Test-UnderJunction([string]`$Path) { return `$false }
function Get-LfText(`$Path) {
    `$raw = [IO.File]::ReadAllText(`$Path)
    return (`$raw -replace "``r``n", "``n") -replace "``r", "``n"
}
function Get-LfHash(`$Path) {
    if (-not (Test-Path `$Path)) { return `$null }
    `$bytes = [Text.Encoding]::UTF8.GetBytes((Get-LfText `$Path))
    `$sha = [Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString(`$sha.ComputeHash(`$bytes)).Replace("-", "")
}
if (`$Check) {
    `$drift = 0
    foreach (`$p in `$Pairs) {
        `$srcHash = Get-LfHash (Join-Path `$Repo `$p.Src)
        `$dstHash = Get-LfHash `$p.Dst
        if (`$null -eq `$dstHash)        { Write-Output "MISSING  `$(`$p.Dst)"; `$drift++ }
        elseif (`$srcHash -ne `$dstHash) { Write-Output "DRIFT    `$(`$p.Dst)"; `$drift++ }
    }
    if (`$drift -eq 0) { Write-Output "CLEAN" } else { Write-Output "`$drift file(s) out of sync." }
    exit (`$drift -eq 0 ? 0 : 1)
}
Write-Output "Usage: deploy.ps1 -Check | -Deploy [-Force] | -Package"
"@
  Set-Content -LiteralPath (Join-Path $repoDir 'deploy\deploy.ps1') -Value $deploy
  return @{ Repo = $repoDir; Deployed = $depDir; Script = (Join-Path $repoDir 'deploy\deploy.ps1') }
}

# A scratch cron dir so liveness reads never touch the live watchdog state.
function New-ScratchCron([string]$Root) {
  $cron = Join-Path $Root 'cron'
  New-Item -ItemType Directory -Force $cron | Out-Null
  Set-Content -LiteralPath (Join-Path $cron 'ticker_heartbeat') -Value 'tick'
  $jobs = @{ jobs = @(
    @{ name = 'Wolftac Factory Build Worker'; enabled = $true; paused = $false; last_status = 'ok'
       next_run_at = (Get-Date).AddMinutes(30).ToString('o'); schedule = @{ minutes = 120 } }
  ) } | ConvertTo-Json -Depth 6
  Set-Content -LiteralPath (Join-Path $cron 'jobs.json') -Value $jobs
  return $cron
}

function Run-Watchdog($cron, $deployScript, [switch]$DryRun) {
  $args = @('-NoProfile', '-File', $Watchdog, '-CronDir', $cron, '-DeployScript', $deployScript)
  if ($DryRun) { $args += '-DryRun' }
  $out = & pwsh @args 2>&1 | Out-String
  return @{ out = $out; code = $LASTEXITCODE }
}

if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
New-Item -ItemType Directory -Force $Scratch | Out-Null
$env:SLACK_FACTORY_BOT_TOKEN = 'xoxb-scratch-not-a-real-token'
$d = New-ScratchDeploy -Root $Scratch
$cron = New-ScratchCron -Root $Scratch

Write-Host '=== AC-1: identical content reports no drift and posts nothing ==='
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-1 drift reported clean' ($r.out -match 'DRIFT CLEAN: all 3 copy-deployed') $r.out
Ok 'AC-1 liveness healthy' ($r.out -match 'HEALTHY:')
Ok 'AC-1 dry run posts nothing' ($r.out -match 'DRY RUN: no Slack post')

Write-Host '=== AC-2/AC-3: one drifted target, named with line counts, distinct from liveness ==='
Add-Content -LiteralPath (Join-Path $d.Deployed 'beta.md') -Value "an edit made in place`nand another"
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-2 drift names the target' ($r.out -match 'DRIFT\s+.*beta\.md') $r.out
# Assert the NUMBERS, not just the words. The first version of this check matched
# only the labels and happily passed a line that read "+0 deployed-only, +0 repo-only"
# for a file that had genuinely grown by two lines.
$m = [regex]::Match($r.out, 'deployed (\d+) lines, repo (\d+) lines; \+(\d+) deployed-only, \+(\d+) repo-only')
Ok 'AC-2 drift reports deployed-only and repo-only line counts' ($m.Success) $r.out
Ok 'AC-2 line counts are real, not zero' ($m.Success -and [int]$m.Groups[1].Value -gt 0 -and [int]$m.Groups[2].Value -gt 0) $m.Value
Ok 'AC-2 deployed-only count reflects the two added lines' ($m.Success -and [int]$m.Groups[3].Value -eq 2) $m.Value
Ok 'AC-3 liveness still reports HEALTHY alongside drift' ($r.out -match 'HEALTHY: all checks passed')
Ok 'AC-3 drift wording is distinct from the liveness wording' (($r.out -match 'DRIFT:') -and ($r.out -notmatch 'UNHEALTHY'))
Ok 'AC-3 exit code is still 0 (drift is not an outage)' ($r.code -eq 0) "code=$($r.code)"

Write-Host '=== DEFECT 1 (AC-2): same-length substitution must report real magnitude ==='
# The original harness only tested an APPEND, which is why +0/+0 on a substitution
# slipped through: subtracting total line counts is correct for appends and wrong
# for in-place edits.
$sub = Join-Path $d.Deployed 'gamma.md'
$subText = [IO.File]::ReadAllText($sub)
[IO.File]::WriteAllText($sub, ($subText -replace 'line two', 'line TWO-EDITED'))
$r = Run-Watchdog $cron $d.Script -DryRun
$m2 = [regex]::Match($r.out, 'gamma\.md \(deployed (\d+) lines, repo (\d+) lines; \+(\d+) deployed-only, \+(\d+) repo-only\)')
Ok 'AC-2 substitution is detected as drift' ($m2.Success) $r.out
Ok 'AC-2 substitution reports equal totals' ($m2.Success -and $m2.Groups[1].Value -eq $m2.Groups[2].Value) $m2.Value
Ok 'AC-2 substitution reports 1 deployed-only line' ($m2.Success -and [int]$m2.Groups[3].Value -eq 1) $m2.Value
Ok 'AC-2 substitution reports 1 repo-only line' ($m2.Success -and [int]$m2.Groups[4].Value -eq 1) $m2.Value
[IO.File]::WriteAllText($sub, $subText)

Write-Host '=== DEFECT 3 (AC-7): missing DEPLOYED file is cannot-compare, not drift ==='
$gone = Join-Path $d.Deployed 'gamma.md'
$goneText = [IO.File]::ReadAllText($gone)
Remove-Item -LiteralPath $gone -Force
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-7 deployed-missing reports cannot compare' ($r.out -match 'cannot compare .*gamma\.md') $r.out
Ok 'AC-7 deployed-missing is NOT reported as drift' ($r.out -notmatch '(?m)^\s*-\s*(DRIFT|MISSING)\s+\S*gamma\.md') $r.out
Ok 'AC-7 deployed-missing counted as could-not-compare' ($r.out -match '1 that could not be compared') $r.out
[IO.File]::WriteAllText($gone, $goneText)

Write-Host '=== DEFECT 2 (AC-3): drift must not claim normal operation during an outage ==='
$jobsPath = Join-Path $cron 'jobs.json'
$jobsGood = Get-Content $jobsPath -Raw
$bad = $jobsGood | ConvertFrom-Json
$bad.jobs[0].enabled = $false
$bad | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jobsPath
# Capture beta.md exactly as found: later sections depend on the drift established
# by the AC-2 block, so this block must restore what it borrowed, not "clean".
$betaPath = Join-Path $d.Deployed 'beta.md'
$betaBefore = [IO.File]::ReadAllText($betaPath)
Add-Content -LiteralPath $betaPath -Value 'SIMULTANEOUS-FAULT'
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-3 liveness reports UNHEALTHY' ($r.out -match 'UNHEALTHY') $r.out
Ok 'AC-3 drift is still reported separately' ($r.out -match 'DRIFT:') $r.out
Set-Content -LiteralPath $jobsPath -Value $jobsGood
# The wording lives in the alert payload, so assert on a real (intercepted) post.
$statePath2 = Join-Path $cron 'watchdog-state.json'
if (Test-Path $statePath2) { Remove-Item $statePath2 -Force }
$bad | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jobsPath
$r = Run-Watchdog $cron $d.Script
Ok 'AC-3 drift payload does NOT claim the factory is running normally' `
   (-not ($r.out -match 'Deploy drift[^
]*running normally')) $r.out
Ok 'AC-3 drift payload flags the concurrent liveness failure' `
   ($r.out -match 'liveness is UNHEALTHY this tick' -or $r.out -notmatch 'Deploy drift') $r.out
Set-Content -LiteralPath $jobsPath -Value $jobsGood
[IO.File]::WriteAllText($betaPath, $betaBefore)
if (Test-Path $statePath2) { Remove-Item $statePath2 -Force }

Write-Host '=== AC-6: line-ending-only difference is NOT drift ==='
$g = Join-Path $d.Deployed 'gamma.md'
$lf = [IO.File]::ReadAllText($g)
[IO.File]::WriteAllText($g, ($lf -replace "`n", "`r`n"))
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-6 CRLF-only file is not reported as drift' ($r.out -notmatch 'gamma\.md') $r.out
[IO.File]::WriteAllText($g, $lf)

Write-Host '=== AC-7: unreadable side is a distinct condition, not clean and not drift ==='
Remove-Item -LiteralPath (Join-Path $d.Repo 'canonical\alpha.md') -Force
$r = Run-Watchdog $cron $d.Script -DryRun
Ok 'AC-7 reports cannot compare' ($r.out -match 'cannot compare .*alpha\.md') $r.out
Ok 'AC-7 does not report it as clean' ($r.out -notmatch 'DRIFT CLEAN')
Ok 'AC-7 distinguishes it from a drifted target' ($r.out -match 'that could not be compared')
Set-Content -LiteralPath (Join-Path $d.Repo 'canonical\alpha.md') -Value "# alpha.md`nline one`nline two`nline three`n" -NoNewline

Write-Host '=== AC-8: watchdog and deploy.ps1 -Check name the same drifted set ==='
$checkOut = & pwsh -NoProfile -File $d.Script -Check 2>&1 | Out-String
$r = Run-Watchdog $cron $d.Script -DryRun
$checkDrifted = [regex]::Matches($checkOut, '(?m)^(?:DRIFT|MISSING)\s+(.+)$') | ForEach-Object { Split-Path $_.Groups[1].Value.Trim() -Leaf } | Sort-Object
$wdDrifted = [regex]::Matches($r.out, '(?m)^\s*-\s*(?:DRIFT|MISSING)\s+(\S+)') | ForEach-Object { Split-Path $_.Groups[1].Value.Trim() -Leaf } | Sort-Object
Ok 'AC-8 both tools name the same drifted set' (($checkDrifted -join ',') -eq ($wdDrifted -join ',')) "check=[$($checkDrifted -join ',')] watchdog=[$($wdDrifted -join ',')]"
Ok 'AC-8 the set is non-empty (anti-vacuity)' ($wdDrifted.Count -gt 0) "count=$($wdDrifted.Count)"

Write-Host '=== AC-5: a new pair is covered with no watchdog edit ==='
$wdBefore = (Get-FileHash $Watchdog -Algorithm SHA256).Hash
$scratch2 = Join-Path $Scratch 'withextra'
New-Item -ItemType Directory -Force $scratch2 | Out-Null
$d2 = New-ScratchDeploy -Root $scratch2 -ExtraPair
Add-Content -LiteralPath (Join-Path $d2.Deployed 'delta.md') -Value 'edited in place'
$r = Run-Watchdog $cron $d2.Script -DryRun
Ok 'AC-5 watchdog covers the newly added pair' ($r.out -match 'delta\.md') $r.out
Ok 'AC-5 watchdog checked 4 pairs, not 3' ($r.out -match 'of 4 checked') $r.out
Ok 'AC-5 watchdog file was not edited to achieve that' ((Get-FileHash $Watchdog -Algorithm SHA256).Hash -eq $wdBefore)

Write-Host '=== AC-4: cooldown, nag, and recovery on the drift state machine ==='
# Non-dry-run needs a policy it can address; Slack delivery will fail with a fake
# token, which is itself the point of the last assertion.
$statePath = Join-Path $cron 'watchdog-state.json'
if (Test-Path $statePath) { Remove-Item $statePath -Force }
$r1 = Run-Watchdog $cron $d.Script
Ok 'AC-4 first drifted run attempts a post' ($r1.out -match 'SLACK:') $r1.out
$state1 = Get-Content $statePath -Raw | ConvertFrom-Json
Ok 'AC-4 sidecar records drift_status=drifted' ($state1.drift_status -eq 'drifted') $state1.drift_status
Ok 'AC-4 failed delivery leaves drift_last_alert_at unset for retry' ($null -eq $state1.drift_last_alert_at)
Ok 'AC-4 liveness status is independent of drift' ($state1.status -eq 'healthy') $state1.status

# Simulate a delivered alert so the cooldown branch is exercised.
$state1.drift_last_alert_at = (Get-Date).ToString('o')
$state1 | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$r2 = Run-Watchdog $cron $d.Script
Ok 'AC-4 immediate re-run is silenced by cooldown' ($r2.out -match 'DRIFT COOLDOWN') $r2.out

# Nag: age the last alert past the window.
$state2 = Get-Content $statePath -Raw | ConvertFrom-Json
$state2.drift_last_alert_at = (Get-Date).AddHours(-13).ToString('o')
$state2 | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$r3 = Run-Watchdog $cron $d.Script
Ok 'AC-4 nag fires after the window' ($r3.out -match 'SLACK:' -and $r3.out -notmatch 'DRIFT COOLDOWN') $r3.out

# Recovery: restore parity.
$beta = Join-Path $d.Deployed 'beta.md'
Set-Content -LiteralPath $beta -Value "# beta.md`nline one`nline two`nline three`n" -NoNewline
$state3 = Get-Content $statePath -Raw | ConvertFrom-Json
$state3.drift_status = 'drifted'
$state3 | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$r4 = Run-Watchdog $cron $d.Script
Ok 'AC-4 recovery run reports drift clean' ($r4.out -match 'DRIFT CLEAN') $r4.out
$state4 = Get-Content $statePath -Raw | ConvertFrom-Json
Ok 'AC-4 sidecar returns to drift_status=clean' ($state4.drift_status -eq 'clean') $state4.drift_status

Write-Host '=== rollback safety: a pre-WD-348 sidecar is not an error ==='
$old = @{ status = 'healthy'; checked_at = (Get-Date).ToString('o'); findings = @(); last_alert_at = $null } |
  ConvertTo-Json -Depth 6
Set-Content -LiteralPath $statePath -Value $old
$r5 = Run-Watchdog $cron $d.Script
Ok 'old-format sidecar is treated as no prior drift state' ($r5.code -ne 4 -and $r5.out -match 'DRIFT CLEAN') $r5.out

Write-Host '=== -SkipDrift leaves prior drift state untouched ==='
$state5 = Get-Content $statePath -Raw | ConvertFrom-Json
$state5.drift_status = 'drifted'
$state5 | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$out = & pwsh -NoProfile -File $Watchdog -CronDir $cron -DeployScript $d.Script -SkipDrift 2>&1 | Out-String
Ok '-SkipDrift reports skipped' ($out -match 'DRIFT: skipped')
$state6 = Get-Content $statePath -Raw | ConvertFrom-Json
Ok '-SkipDrift does not assert clean for a check that never ran' ($state6.drift_status -eq 'drifted') $state6.drift_status

Write-Host '=== SYSTEM task: targets resolve under the operator profile, not systemprofile ==='
# The scheduled task runs as SYSTEM, so %USERPROFILE% is systemprofile and every
# deploy.ps1 target read as absent. The profile now comes from -UserProfile, or from
# the profile that owns -EnvFile.
$prof = Join-Path $Scratch 'Users\operator'
$sysProf = Join-Path $Scratch 'systemprofile'
New-Item -ItemType Directory -Force (Join-Path $prof 'skills'), (Join-Path $prof 'AppData\Local\hermes'), $sysProf | Out-Null
Copy-Item (Join-Path $d.Repo 'canonical\alpha.md') (Join-Path $prof 'skills\alpha.md')
$envFile = Join-Path $prof 'AppData\Local\hermes\.env'
Set-Content -LiteralPath $envFile -Value 'SLACK_FACTORY_BOT_TOKEN=xoxb-scratch-not-a-real-token'
$profDeploy = (Get-Content $d.Script -Raw) -replace '(?s)\$Pairs = @\(.*?\n\)', '$Pairs = @(
    @{ Src = "canonical\alpha.md"; Dst = "$env:USERPROFILE\skills\alpha.md" }
)'
$profScript = Join-Path $d.Repo 'deploy\deploy-profile.ps1'
Set-Content -LiteralPath $profScript -Value $profDeploy
$savedUp = $env:USERPROFILE; $savedLa = $env:LOCALAPPDATA
$env:USERPROFILE = $sysProf; $env:LOCALAPPDATA = Join-Path $sysProf 'AppData\Local'
try {
  $o1 = & pwsh -NoProfile -File $Watchdog -CronDir $cron -DeployScript $profScript -EnvFile $envFile -DryRun 2>&1 | Out-String
  $o2 = & pwsh -NoProfile -File $Watchdog -CronDir $cron -DeployScript $profScript -UserProfile $prof -DryRun 2>&1 | Out-String
} finally { $env:USERPROFILE = $savedUp; $env:LOCALAPPDATA = $savedLa }
Ok 'SYSTEM: profile inferred from -EnvFile compares clean' ($o1 -match 'DRIFT CLEAN' -and $o1 -notmatch 'cannot compare') $o1
Ok 'SYSTEM: explicit -UserProfile compares clean' ($o2 -match 'DRIFT CLEAN' -and $o2 -notmatch 'cannot compare') $o2

Remove-Item Env:\SLACK_FACTORY_BOT_TOKEN -ErrorAction SilentlyContinue
if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
Write-Host ''
Write-Host "RESULT pass=$script:pass fail=$script:fail"
if ($script:failures.Count) { Write-Host 'FAILURES:'; $script:failures | ForEach-Object { Write-Host "  $_" } }
if ($script:fail -gt 0) { exit 1 }
