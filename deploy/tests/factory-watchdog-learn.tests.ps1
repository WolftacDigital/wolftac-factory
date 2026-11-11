# factory-watchdog-learn.tests.ps1 -- regression harness for the watchdog's pending
# /learn edit check (WD-362).
#
#   pwsh -NoProfile -File deploy/tests/factory-watchdog-learn.tests.ps1
#
# Hermetic by construction: a scratch repo, a scratch detached "live" worktree with a
# real directory junction into it, a scratch cron dir, a stand-in deploy.ps1 whose
# $JunctionedRoots names that junction, a scratch snapshot root, and -AlertOutbox so
# every alert is written to a local file instead of posted. It never touches the real
# D:\Wolftac\.factory-live, the live cron dir, the real backups root, or Discord.
[CmdletBinding()]
param(
    [string]$Repo = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    [string]$Scratch = (Join-Path ([IO.Path]::GetTempPath()) 'wolftac-watchdog-learn-tests')
)
$ErrorActionPreference = 'Stop'
$Watchdog = Join-Path $Repo 'deploy\factory-watchdog.ps1'
$script:pass = 0; $script:fail = 0
$failures = @()

function Ok($name, $cond, $detail = '') {
  if ($cond) { $script:pass++; Write-Host "PASS $name" }
  else { $script:fail++; $script:failures += "$name  $detail"; Write-Host "FAIL $name  $detail" }
}

if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
New-Item -ItemType Directory -Force $Scratch | Out-Null
$expiryFile = Join-Path $Scratch 'expiry-fixture.txt'
Set-Content -LiteralPath $expiryFile -Value 'LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=2099-01-01T00:00:00Z'
$Marker = 'LEARN-MARKER-7f3a'   # planted in edits; must never reach a payload (AC-10)

# ── scratch repo + detached live worktree + junction (test-plan step 1) ──
$origin = Join-Path $Scratch 'origin'
$live = Join-Path $Scratch 'live'
New-Item -ItemType Directory -Force (Join-Path $origin 'skills\wolftac-review\references') | Out-Null
Set-Content -LiteralPath (Join-Path $origin 'skills\wolftac-review\SKILL.md') -Value "# review`nline one`nline two`n" -NoNewline
Set-Content -LiteralPath (Join-Path $origin 'skills\wolftac-review\references\keep.md') -Value "kept`n" -NoNewline
& git -C $origin init -q -b main 2>$null
& git -C $origin -c user.name=t -c user.email=t@t add -A
& git -C $origin -c user.name=t -c user.email=t@t commit -q -m seed
& git -C $origin worktree add -q --detach $live main 2>$null
$junction = Join-Path $Scratch 'hermes-skills\wolftac-review'
New-Item -ItemType Directory -Force (Split-Path $junction -Parent) | Out-Null
New-Item -ItemType Junction -Path $junction -Target (Join-Path $live 'skills\wolftac-review') | Out-Null
$top = (& git -C $junction rev-parse --show-toplevel).Trim()
Ok 'step 1 junction resolves to the scratch live worktree' ([IO.Path]::GetFullPath($top) -ieq [IO.Path]::GetFullPath($live)) "top=$top"
$skill = Join-Path $live 'skills\wolftac-review\SKILL.md'

# ── stand-in deploy.ps1: one copy pair (for drift) + the junction root ──
$depRepo = Join-Path $Scratch 'deprepo'
$deployed = Join-Path $Scratch 'deployed'
New-Item -ItemType Directory -Force (Join-Path $depRepo 'deploy'), (Join-Path $depRepo 'canonical'), $deployed | Out-Null
Set-Content -LiteralPath (Join-Path $depRepo 'canonical\alpha.md') -Value "alpha`n" -NoNewline
Set-Content -LiteralPath (Join-Path $deployed 'alpha.md') -Value "alpha`n" -NoNewline
function Write-Deploy([string]$Path, [string[]]$Roots) {
  $rootLines = ($Roots | ForEach-Object { "    '$_'" }) -join "`n"
  @"
param([switch]`$Check, [switch]`$Deploy, [switch]`$Package, [switch]`$Force, [switch]`$Reconcile, [string]`$Only)
`$Repo = Split-Path `$PSScriptRoot -Parent
`$Pairs = @(
    @{ Src = "canonical\alpha.md"; Dst = "$deployed\alpha.md" }
)
`$JunctionedRoots = @(
$rootLines
)
function Test-UnderJunction([string]`$Path) { return `$false }
function Get-LfText(`$Path) { return ([IO.File]::ReadAllText(`$Path) -replace "``r``n", "``n") }
function Get-LfHash(`$Path) {
    if (-not (Test-Path `$Path)) { return `$null }
    `$sha = [Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString(`$sha.ComputeHash([Text.Encoding]::UTF8.GetBytes((Get-LfText `$Path)))).Replace("-", "")
}
Write-Output "Usage: deploy.ps1 -Check | -Deploy [-Force] | -Package"
"@ | Set-Content -LiteralPath $Path
}
$deployScript = Join-Path $depRepo 'deploy\deploy.ps1'
Write-Deploy $deployScript @($junction)

# ── scratch cron dir ──
$cron = Join-Path $Scratch 'cron'
New-Item -ItemType Directory -Force $cron | Out-Null
Set-Content -LiteralPath (Join-Path $cron 'ticker_heartbeat') -Value 'tick'
$jobsPath = Join-Path $cron 'jobs.json'
function Set-Jobs([bool]$Enabled) {
  @{ jobs = @(@{ name = 'Wolftac Factory Build Worker'; enabled = $Enabled; paused = $false; last_status = 'ok'
       next_run_at = (Get-Date).AddMinutes(30).ToString('o'); schedule = @{ minutes = 120 } }) } |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $jobsPath
}
Set-Jobs $true
$statePath = Join-Path $cron 'watchdog-state.json'
$backups = Join-Path $Scratch 'backups\learn-edits'
$outbox = Join-Path $Scratch 'outbox'

function Run([string]$Deploy = $deployScript, [switch]$DryRun) {
  if (Test-Path $outbox) { Remove-Item -Recurse -Force $outbox }
  (Get-Item -LiteralPath $cron\ticker_heartbeat).LastWriteTime = Get-Date
  $a = @('-NoProfile', '-File', $Watchdog, '-CronDir', $cron, '-DeployScript', $Deploy,
         '-LearnBackupRoot', $backups, '-AlertOutbox', $outbox, '-EnvFile', $expiryFile)
  if ($DryRun) { $a += '-DryRun' }
  $out = & pwsh @a 2>&1 | Out-String
  $posts = @(if (Test-Path $outbox) { Get-ChildItem $outbox -File | Sort-Object Name | ForEach-Object { Get-Content -Raw $_.FullName } })
  return @{ out = $out; code = $LASTEXITCODE; posts = $posts }
}
function Snapshots { @(if (Test-Path $backups) { Get-ChildItem $backups -Directory | Sort-Object Name }) }
# The leading comma stops PowerShell unrolling a one-element result into a bare string.
function LearnPosts($r) { , @($r.posts | Where-Object { $_ -match '/learn' }) }

Write-Host '=== AC-1: clean worktree reports nothing, snapshots nothing, posts nothing ==='
$r = Run
Ok 'AC-1 reports clean' ($r.out -match 'LEARN CLEAN') $r.out
Ok 'AC-1 no snapshot directory' ((Snapshots).Count -eq 0)
Ok 'AC-1 zero posts' ($r.posts.Count -eq 0) ($r.posts -join ' || ')
Ok 'AC-1 exit 0' ($r.code -eq 0) "code=$($r.code)"

Write-Host '=== AC-2/AC-3/AC-10: one modified file -> one alert, byte-exact snapshot, no content ==='
Add-Content -LiteralPath $skill -Value "$Marker first`n$Marker second" -NoNewline
$preHash = (Get-FileHash -LiteralPath $skill).Hash
$preBytes = [IO.File]::ReadAllBytes($skill)
$r = Run
$lp = LearnPosts $r
Ok 'AC-2 exactly one /learn post' ($lp.Count -eq 1) "posts=$($r.posts.Count)"
Ok 'AC-2 post names the file with +2/-0' ($lp.Count -eq 1 -and $lp[0] -match [regex]::Escape('skills/wolftac-review/SKILL.md (+2/-0, modified)')) ($lp -join '||')
$snaps = Snapshots
Ok 'AC-3 one snapshot directory' ($snaps.Count -eq 1) "count=$($snaps.Count)"
$copy = if ($snaps.Count) { Join-Path $snaps[0].FullName 'skills\wolftac-review\SKILL.md' } else { '' }
Ok 'AC-3 snapshot copy SHA-256 matches the working file' ((Test-Path $copy) -and (Get-FileHash -LiteralPath $copy).Hash -eq $preHash)
Ok 'AC-10 marker absent from every payload' (-not ($r.posts -join "`n").Contains($Marker))
Ok 'AC-10 marker absent from stdout too' (-not $r.out.Contains($Marker))

Write-Host '=== AC-4: untracked file is reported and snapshotted ==='
$probe = Join-Path $live 'skills\wolftac-review\references\probe.md'
Set-Content -LiteralPath $probe -Value "$Marker probe`nline`n" -NoNewline
$r = Run
Ok 'AC-4 untracked file reported' ($r.out -match [regex]::Escape('references/probe.md (+2/-0, untracked)')) $r.out
$newest = (Snapshots)[-1]
Ok 'AC-4 untracked file snapshotted byte-exact' ((Test-Path (Join-Path $newest.FullName 'skills\wolftac-review\references\probe.md')) -and
   (Get-FileHash (Join-Path $newest.FullName 'skills\wolftac-review\references\probe.md')).Hash -eq (Get-FileHash $probe).Hash)
Ok 'AC-4 already-alerted state does not re-post inside cooldown' ((LearnPosts $r).Count -eq 0) ($r.posts -join '||')

Write-Host '=== AC-5: cooldown, nag, single recovery ==='
$before = (Snapshots).Count
$r = Run
Ok 'AC-5 immediate re-run posts nothing' ((LearnPosts $r).Count -eq 0) ($r.posts -join '||')
Ok 'AC-5 immediate re-run makes no new snapshot' ((Snapshots).Count -eq $before) "before=$before after=$((Snapshots).Count)"
Ok 'AC-5 cooldown is announced' ($r.out -match 'LEARN COOLDOWN')
$st = Get-Content $statePath -Raw | ConvertFrom-Json
$st.learn_last_alert_at = (Get-Date).AddHours(-13).ToString('o')
$st | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$r = Run
$lp = LearnPosts $r
Ok 'AC-5 nag fires after 13h' ($lp.Count -eq 1 -and $lp[0] -match 'Still uncommitted after 13h') ($lp -join '||')
Ok 'AC-5 nag makes no new snapshot (content unchanged)' ((Snapshots).Count -eq $before)

Write-Host '=== AC-6: destruction is survivable ==='
$snapCopy = Join-Path (Snapshots)[0].FullName 'skills\wolftac-review\SKILL.md'
& git -C $live restore . 2>$null
Remove-Item -LiteralPath $probe -Force
Ok 'AC-6 git restore destroyed the edit' (-not ([IO.File]::ReadAllText($skill)).Contains($Marker))
$snapBytes = [IO.File]::ReadAllBytes($snapCopy)
Ok 'AC-6 snapshot still byte-matches pre-destruction content' ([Convert]::ToBase64String($snapBytes) -eq [Convert]::ToBase64String($preBytes))
$r = Run
$lp = LearnPosts $r
Ok 'AC-5 exactly one recovery message once clean' ($lp.Count -eq 1 -and $lp[0] -match '/learn edits resolved') ($r.posts -join '||')
$r = Run
Ok 'AC-5 recovery is not repeated' ((LearnPosts $r).Count -eq 0)

Write-Host '=== AC-7: liveness + drift + /learn in one tick -> three distinct messages, exit from liveness ==='
Set-Jobs $false
Set-Content -LiteralPath (Join-Path $deployed 'alpha.md') -Value "alpha`nDRIFTED`n" -NoNewline
Add-Content -LiteralPath $skill -Value "$Marker again" -NoNewline
Remove-Item $statePath -Force
$r = Run
$live1 = @($r.posts | Where-Object { $_ -match 'worker loops need attention' })
$drift1 = @($r.posts | Where-Object { $_ -match 'Deploy drift' })
$learn1 = @($r.posts | Where-Object { $_ -match 'Uncommitted /learn edits' })
Ok 'AC-7 three posts' ($r.posts.Count -eq 3) "posts=$($r.posts.Count): $($r.posts -join ' || ')"
Ok 'AC-7 one each, mutually distinct' ($live1.Count -eq 1 -and $drift1.Count -eq 1 -and $learn1.Count -eq 1 -and
   $learn1[0] -notmatch 'Deploy drift|worker loops' -and $drift1[0] -notmatch '/learn')
Ok 'AC-7 exit code driven by liveness (1)' ($r.code -eq 1) "code=$($r.code)"
Set-Jobs $true
Set-Content -LiteralPath (Join-Path $deployed 'alpha.md') -Value "alpha`n" -NoNewline
Remove-Item $statePath -Force
$r = Run
Ok 'AC-7 healthy liveness + pending /learn exits 0' ($r.code -eq 0 -and $r.out -match 'LEARN: 1 uncommitted') "code=$($r.code)"
& git -C $live restore . 2>$null

Write-Host '=== AC-8: retention prunes >30 days, keeps the newest even as the sole entry ==='
$old = Join-Path $backups '20000101T000000Z'
New-Item -ItemType Directory -Force $old | Out-Null
(Get-Item $old).LastWriteTime = (Get-Date).AddDays(-31)
$r = Run
Ok 'AC-8 31-day-old snapshot pruned' (-not (Test-Path $old)) $r.out
Remove-Item -Recurse -Force $backups
New-Item -ItemType Directory -Force $old | Out-Null
(Get-Item $old).LastWriteTime = (Get-Date).AddDays(-31)
$r = Run
Ok 'AC-8 sole (newest) snapshot retained even when old' (Test-Path $old) $r.out

Write-Host '=== AC-9: unresolvable worktree and failed git status are "cannot check" ==='
$noJ = Join-Path $depRepo 'deploy\deploy-nojunctions.ps1'
Write-Deploy $noJ @()
Remove-Item $statePath -Force
$r = Run -Deploy $noJ
Ok 'AC-9 no junctions -> CANNOT CHECK' ($r.out -match 'LEARN: CANNOT CHECK' -and $r.out -notmatch 'LEARN CLEAN') $r.out
Ok 'AC-9 no junctions -> distinct alert' ((LearnPosts $r).Count -eq 1 -and (LearnPosts $r)[0] -match 'cannot check')
$st = Get-Content $statePath -Raw | ConvertFrom-Json
Ok 'AC-9 state is cannot-check, not clean' ($st.learn_status -eq 'cannot-check') $st.learn_status
$notRepo = Join-Path $Scratch 'not-a-repo'
New-Item -ItemType Directory -Force $notRepo | Out-Null
$badJ = Join-Path $depRepo 'deploy\deploy-badroot.ps1'
Write-Deploy $badJ @($notRepo)
$r = Run -Deploy $badJ -DryRun
Ok 'AC-9 root outside any git worktree -> CANNOT CHECK' ($r.out -match 'does not resolve to a git worktree' -and $r.out -notmatch 'LEARN CLEAN') $r.out
$idx = Join-Path $origin ".git\worktrees\live\index"
$idxBytes = [IO.File]::ReadAllBytes($idx)
[IO.File]::WriteAllText($idx, 'corrupt')
$r = Run -DryRun
[IO.File]::WriteAllBytes($idx, $idxBytes)
Ok 'AC-9 broken git status -> CANNOT CHECK' ($r.out -match 'git status failed' -and $r.out -notmatch 'LEARN CLEAN') $r.out

Write-Host '=== rollback safety: a pre-WD-362 sidecar is not an error ==='
@{ status = 'healthy'; checked_at = (Get-Date).ToString('o'); findings = @(); last_alert_at = $null; drift_status = 'clean' } |
  ConvertTo-Json | Set-Content -LiteralPath $statePath
$r = Run
Ok 'old sidecar treated as no prior /learn state' ($r.code -eq 0 -and $r.out -match 'LEARN CLEAN' -and $r.posts.Count -eq 0) $r.out

Write-Host '=== -SkipLearn carries prior state forward ==='
$st = Get-Content $statePath -Raw | ConvertFrom-Json
$st.learn_status = 'pending'
$st | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath
$out = & pwsh -NoProfile -File $Watchdog -CronDir $cron -DeployScript $deployScript -LearnBackupRoot $backups -AlertOutbox $outbox -EnvFile $expiryFile -SkipLearn 2>&1 | Out-String
Ok '-SkipLearn reports skipped' ($out -match 'LEARN: skipped')
Ok '-SkipLearn does not assert clean' ((Get-Content $statePath -Raw | ConvertFrom-Json).learn_status -eq 'pending')

& git -C $origin worktree remove --force $live 2>$null
if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
Write-Host ''
Write-Host "RESULT pass=$script:pass fail=$script:fail"
if ($script:failures.Count) { Write-Host 'FAILURES:'; $script:failures | ForEach-Object { Write-Host "  $_" } }
if ($script:fail -gt 0) { exit 1 }
