# deploy-scope.tests.ps1 -- regression harness for deploy.ps1 (WD-347).
#
#   pwsh -NoProfile -File deploy/tests/deploy-scope.tests.ps1
#
# Covers the two defects the first review pass caught, plus the acceptance criteria
# they sit next to:
#   * a zero-byte (or unreadable) deployed copy must never truncate the repo file
#   * a blank or whitespace-only -Only must resolve to NO-MATCH, never widen to
#     every pair -- an empty scope that behaves as a wildcard is the failure mode
#     that made -Force dangerous in the first place
#
# Containment: the suite redirects USERPROFILE and LOCALAPPDATA at a scratch root
# and shims wsl.exe out of PATH, so no live agent skill tree is ever a deploy
# destination. It snapshots the real trees before and after and asserts they are
# byte-identical. That assertion is the containment proof -- if it ever fails, the
# run wrote somewhere it should not have.
#
# It DOES reconcile into this checkout's own ports\codex\*.md and reverts them with
# git between blocks, so run it in a worktree rather than a tree with changes you
# care about.
[CmdletBinding()]
param(
    # Repo under test; defaults to the checkout this file lives in.
    [string]$Repo = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
    # Scratch root for the redirected HOME/LOCALAPPDATA. Must be outside $Repo.
    [string]$Scratch = (Join-Path ([IO.Path]::GetTempPath()) 'wolftac-deploy-scope-tests')
)
$ErrorActionPreference = 'Stop'
$Deploy = Join-Path $Repo 'deploy\deploy.ps1'
$script:pass = 0
$script:fail = 0
$A = 'ports\codex\wolftac-build.md'
$B = 'ports\codex\wolftac-review.md'
$repoA = Join-Path $Repo $A

# --- blast-radius instrumentation -------------------------------------------
# Capture the REAL live locations before any env redirection, so the suite can
# prove it never wrote outside scratch. -Deploy -Force unscoped is the one test
# that cannot be scoped by definition, and the WSL pairs it would reach are not
# reachable by env redirection at all -- so that test also runs with wsl.exe
# shimmed out, and this snapshot is the independent check on that containment.
$RealUserProfile = $env:USERPROFILE
$RealLocalAppData = $env:LOCALAPPDATA
$LiveRoots = @()
$wslHome = $null
try { $wslHome = (& wsl.exe -d Ubuntu -- sh -c 'echo $HOME' 2>$null | Select-Object -First 1) } catch {}
if ($wslHome) { $wslHome = ($wslHome -replace "`0", '').Trim() }
if ($wslHome -and $wslHome.StartsWith('/')) {
  $LiveRoots += ("\\wsl`$\Ubuntu" + ($wslHome -replace '/', '\') + '\.claude\skills')
}
$LiveRoots += (Join-Path $RealLocalAppData 'hermes\skills')
$LiveRoots += (Join-Path $RealUserProfile '.claude\skills')
$LiveRoots += (Join-Path $RealUserProfile '.codex\prompts')

function Snapshot($roots) {
  $acc = @()
  foreach ($root in $roots) {
    if (-not (Test-Path $root)) { $acc += "ABSENT|$root"; continue }
    # Size + mtime, not hash: the WSL root contains links that break Get-FileHash,
    # and a deploy write always moves mtime, so this detects any write.
    $acc += Get-ChildItem -Recurse -File -Force $root -ErrorAction SilentlyContinue |
      Where-Object {
        # The running Hermes gateway maintains its own bookkeeping dotfiles in its
        # skills root (.bundled_manifest, .usage.json) and touches them as it loads
        # and uses skills. None of them is a deploy pair destination -- neither name
        # occurs anywhere in deploy.ps1, parity-check.ps1, or semantic-check.ps1 --
        # so their mtimes are another process's noise, not evidence about this
        # script. Every real deployed skill file stays under watch.
        -not ($_.Name.StartsWith('.') -and $_.FullName -match [regex]::Escape('\hermes\skills\'))
      } |
      ForEach-Object { "$($_.FullName)|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)" }
  }
  return ($acc | Sort-Object)
}

# wsl.exe shim: a copy of cmd.exe answers the deploy script's home probe with
# something that is not an absolute POSIX path, so no WSL pair is ever added.
$ShimDir = Join-Path $Scratch '..\_wd347-shim'
# Every verb that can WRITE must run inside this shim unless its -Only is a scope
# that is guaranteed non-blank. A blank -Only resolves to zero pairs only while the
# fix under test is present; against broken code it widens to every pair, and the
# \wsl$ destinations are the one set that USERPROFILE/LOCALAPPDATA redirection
# cannot contain. A regression harness must stay safe when the code it tests is
# broken -- that is the whole point of running it. The blank-scope -Check calls
# are shimmed too: they are read-only, but a widened scope makes them walk \wsl$
# over the network, which turns a fast suite into a multi-minute one.
function WithWslShimmed([scriptblock]$Body) {
  $saved = $env:PATH
  New-Item -ItemType Directory -Force -Path $ShimDir | Out-Null
  Copy-Item "$env:SystemRoot\System32\cmd.exe" (Join-Path $ShimDir 'wsl.exe') -Force
  $env:PATH = "$ShimDir;$saved"
  try { & $Body } finally { $env:PATH = $saved }
}

function Ok($name, $cond, $detail = '') {
  if ($cond) { $script:pass++; Write-Host "PASS $name" }
  else { $script:fail++; Write-Host "FAIL $name  $detail" }
}

# Invocations use PowerShell's native argument passing, not Start-Process: the
# suite deliberately passes '' and '   ' as -Only values, and Start-Process
# argument-joining does not preserve an empty or whitespace-only argument -- it
# would silently test something other than the blank scope this file exists for.
#
# Consequence worth knowing: a deploy.ps1 whose scope filter is broken turns these
# scoped calls into all-pairs calls, and an all-pairs -Reconcile can run for
# minutes. Against badly broken code the suite gets SLOW before it goes red. Give
# it a generous outer timeout in CI rather than assuming a stall means a hang.
function Run([string[]]$ScriptArgs) {
  $o = & pwsh -NoProfile -File $Deploy @ScriptArgs 2>&1 | Out-String
  return @{ out = $o; code = $LASTEXITCODE }
}

function ResetScratch {
  if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
  New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
  $env:USERPROFILE = $Scratch
  $env:LOCALAPPDATA = Join-Path $Scratch 'AppData\Local'
  New-Item -ItemType Directory -Force -Path $env:LOCALAPPDATA | Out-Null
}

function RepoClean {
  # Never revert the file under test.
  & git -C $Repo checkout -- ':(exclude)deploy/deploy.ps1' 2>&1 | Out-Null
}

function AssertPatched {
  $t = Get-Content -Raw -LiteralPath $Deploy
  if ($t -notmatch [regex]::Escape("PSBoundParameters.ContainsKey")) { throw 'TRIPWIRE: scope patch missing from script under test' }
  if ($t -notmatch [regex]::Escape('refusing to truncate the repo file')) { throw 'TRIPWIRE: zero-byte patch missing from script under test' }
}

function DstOf($src) {
  if ($src -eq $A) { return Join-Path $Scratch '.codex\prompts\wolftac-build.md' }
  return Join-Path $Scratch '.codex\prompts\wolftac-review.md'
}

function ScratchSnap {
  # pwsh itself writes a startup-profile cache under the redirected LOCALAPPDATA.
  # That is the test host's footprint, not deploy.ps1's, so exclude it or every
  # "wrote nothing" assertion fails on the harness's own noise.
  return Get-ChildItem -Recurse -File $Scratch |
    Where-Object { $_.FullName -notmatch [regex]::Escape('\AppData\Local\Microsoft\PowerShell\') } |
    ForEach-Object { "$($_.FullName)|$($_.Length)|$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)" } |
    Sort-Object
}

function RepoDirty {
  # deploy.ps1 is excluded because it is the script under test, and deploy/tests
  # because that is this harness itself -- neither is a reconcile destination (no
  # $Pairs entry points at either), so both would be noise in an assertion about
  # what deploy.ps1 wrote. Everything else, tracked or untracked, still counts.
  return (& git -C $Repo status --porcelain -- ':(exclude)deploy/deploy.ps1' ':(exclude)deploy/tests' | Out-String).Trim()
}

Write-Host '=== phase 0: snapshot live agent trees, seed scratch destinations ==='
$liveBefore = Snapshot $LiveRoots
Write-Host "live files under watch: $($liveBefore.Count)"
ResetScratch
AssertPatched
$r = Run @('-Deploy', '-Only', 'ports\codex')
Ok 'seed deploy scoped to ports\codex produced deployed A' (Test-Path (DstOf $A)) "exit=$($r.code)"
Ok 'seed produced deployed B' (Test-Path (DstOf $B))

Write-Host '=== DEFECT 1 (AC-8): zero-byte deployed copy must not truncate the repo ==='
AssertPatched
$beforeLen = (Get-Item $repoA).Length
$beforeHash = (Get-FileHash $repoA -Algorithm SHA256).Hash
Set-Content -LiteralPath (DstOf $A) -Value '' -NoNewline
Ok 'deployed A is 0 bytes' ((Get-Item (DstOf $A)).Length -eq 0)
$r = Run @('-Reconcile', '-Only', $A)
Ok 'AC-8 zero-byte exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-8 zero-byte reports UNREADABLE' ($r.out -match 'UNREADABLE')
Ok 'AC-8 zero-byte names the empty-copy reason' ($r.out -match 'refusing to truncate')
Ok 'AC-8 zero-byte never claims reconciled' ($r.out -notmatch 'reconciled ')
$afterLen = (Get-Item $repoA).Length
Ok 'AC-8 repo file still non-empty' ($afterLen -gt 0) "len=$afterLen"
Ok 'AC-8 repo file byte-identical' ((Get-FileHash $repoA -Algorithm SHA256).Hash -eq $beforeHash) "before=$beforeLen after=$afterLen"

Write-Host '=== DEFECT 2 (AC-9): whitespace-only -Only must NO-MATCH, not widen ==='
ResetScratch
AssertPatched
WithWslShimmed { $script:r = Run @('-Check', '-Only', '   ') }
$r = $script:r
Ok 'AC-9 whitespace -Check exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-9 whitespace -Check prints NO-MATCH' ($r.out -match 'NO-MATCH')
Ok 'AC-9 whitespace -Check lists available scopes' ($r.out -match 'Available scopes')
Ok 'AC-9 whitespace -Check processed no pair' ($r.out -notmatch 'canonical.wolftac-spec')
WithWslShimmed { $script:r = Run @('-Reconcile', '-Only', '   ') }
$r = $script:r
Ok 'AC-9 whitespace -Reconcile exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-9 whitespace -Reconcile prints NO-MATCH' ($r.out -match 'NO-MATCH')
$dirty = RepoDirty
Ok 'AC-9 whitespace -Reconcile wrote no repo file' ($dirty -eq '') "dirty=$dirty"

Write-Host '=== DEFECT 2 corollary: empty -Only must not act as a match-all wildcard ==='
WithWslShimmed { $script:r = Run @('-Check', '-Only', '') }
$r = $script:r
Ok 'empty -Only exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'empty -Only prints NO-MATCH' ($r.out -match 'NO-MATCH')
Ok 'empty -Only did not scope to every pair' ($r.out -notmatch 'SCOPED')
WithWslShimmed { $script:r = Run @('-Deploy', '-Force', '-Only', '') }
$r = $script:r
Ok 'empty -Only with -Force exits non-zero at NO-MATCH' ($r.code -ne 0 -and $r.out -match 'NO-MATCH') "exit=$($r.code)"
Ok 'empty -Only with -Force never force-overwrites' ($r.out -notmatch 'FORCED-OVERWRITE')

Write-Host '=== AC-9 regression: ordinary scopes still work on all three verbs ==='
ResetScratch
AssertPatched
$r = Run @('-Check', '-Only', 'zzz-no-such-pair')
Ok 'AC-9 typo scope exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-9 typo scope prints NO-MATCH and scopes' (($r.out -match 'NO-MATCH') -and ($r.out -match 'Available scopes'))
foreach ($verb in @('-Check', '-Deploy', '-Reconcile')) {
  $r = Run @($verb, '-Only', 'ports\codex\wolftac-build.md')
  Ok "AC-9 $verb honours -Only (1 pair)" ($r.out -match 'SCOPED') "out=$($r.out)"
  # Assert the NUMBER: a one-element scope used to unwrap to a bare hashtable whose
  # .Count is its key count, so the line read "-> 2 of N" for one pair.
  Ok "AC-9 $verb reports exactly one scoped pair" ($r.out -match 'SCOPED\s+-Only .* -> 1 of \d+ pair') "out=$($r.out)"
}
RepoClean

Write-Host '=== AC-1 regression: -Reconcile discovers the same targets as -Check (WSL included) ==='
# A non-blank zero-match scope exits at NO-MATCH before anything is read or written,
# so this is safe without the shim, and it is the one place the real WSL probe runs:
# -Reconcile used to skip WSL discovery entirely and see fewer pairs than -Check.
ResetScratch
AssertPatched
$totals = @{}
foreach ($verb in @('-Check', '-Reconcile')) {
  $r = Run @($verb, '-Only', 'zzz-no-such-pair')
  $m = [regex]::Match($r.out, 'none of the (\d+) deploy pairs')
  $totals[$verb] = if ($m.Success) { [int]$m.Groups[1].Value } else { -1 }
}
Write-Host "  pair totals: -Check=$($totals['-Check']) -Reconcile=$($totals['-Reconcile'])"
Ok 'AC-1 -Check and -Reconcile see the same pair total' ($totals['-Check'] -gt 0 -and $totals['-Check'] -eq $totals['-Reconcile']) "check=$($totals['-Check']) reconcile=$($totals['-Reconcile'])"
RepoClean

Write-Host '=== AC-8 regression: missing deployed copy still reported, repo untouched ==='
ResetScratch
AssertPatched
Run @('-Deploy', '-Only', 'ports\codex') | Out-Null
$beforeHash = (Get-FileHash $repoA -Algorithm SHA256).Hash
Remove-Item -LiteralPath (DstOf $A) -Force
$r = Run @('-Reconcile', '-Only', $A)
Ok 'AC-8 missing source exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-8 missing source reports UNREADABLE' ($r.out -match 'UNREADABLE')
Ok 'AC-8 missing source left repo file identical' ((Get-FileHash $repoA -Algorithm SHA256).Hash -eq $beforeHash)

Write-Host '=== AC-1/AC-2/AC-4 regression: reconcile still works and writes LF ==='
ResetScratch
AssertPatched
Run @('-Deploy', '-Only', 'ports\codex') | Out-Null
Add-Content -LiteralPath (DstOf $A) -Value 'WD347-MARKER-A'
Add-Content -LiteralPath (DstOf $B) -Value 'WD347-MARKER-B'
$r = Run @('-Reconcile', '-Only', 'ports\codex\wolftac-build.md')
Ok 'AC-4 scoped reconcile exits 0' ($r.code -eq 0) "exit=$($r.code)"
$n = ([regex]::Matches($r.out, 'reconciled ')).Count
Ok 'AC-4 scoped reconcile moved exactly one file' ($n -eq 1) "n=$n"
Ok 'AC-1 marker landed in repo A' ((Get-Content -Raw $repoA) -match 'WD347-MARKER-A')
Ok 'AC-4 non-matching target B untouched in repo' ((Get-Content -Raw (Join-Path $Repo $B)) -notmatch 'WD347-MARKER-B')
$bytes = [IO.File]::ReadAllBytes($repoA)
$crCount = ($bytes | Where-Object { $_ -eq 13 }).Count
Ok 'AC-2 reconciled file has no CR bytes' ($crCount -eq 0) "cr=$crCount"
$status = RepoDirty
Ok 'AC-1 git status lists exactly the reconciled file' ((($status -split "`n").Count -eq 1) -and ($status -match 'wolftac-build.md')) "status=$status"
RepoClean

Write-Host '=== AC-3 regression: clean tree no-op ==='
ResetScratch
AssertPatched
Run @('-Deploy', '-Only', 'ports\codex') | Out-Null
$r = Run @('-Reconcile', '-Only', 'ports\codex')
Ok 'AC-3 clean reconcile exits 0' ($r.code -eq 0) "exit=$($r.code)"
Ok 'AC-3 clean reconcile says nothing to reconcile' ($r.out -match 'Nothing to reconcile')

Write-Host '=== AC-5 regression: unscoped -Force still refused, writes nothing ==='
ResetScratch
AssertPatched
Run @('-Deploy', '-Only', 'ports\codex') | Out-Null
Add-Content -LiteralPath (DstOf $A) -Value 'WD347-DRIFT'
$snapBefore = ScratchSnap
$r = $null
WithWslShimmed { $script:r = Run @('-Deploy', '-Force') }
$r = $script:r
Ok 'AC-5 ran with no WSL pair in range (shim held)' ($r.out -notmatch 'wsl') "out=$($r.out)"
$snapAfter = ScratchSnap
Ok 'AC-5 unscoped force exits non-zero' ($r.code -ne 0) "exit=$($r.code)"
Ok 'AC-5 unscoped force says REFUSED' ($r.out -match 'REFUSED')
Ok 'AC-5 unscoped force names would-clobber targets' ($r.out -match 'would-clobber')
$forceDelta = Compare-Object $snapBefore $snapAfter
Ok 'AC-5 unscoped force changed zero files' ($null -eq $forceDelta) "delta=$($forceDelta | Out-String)"
# Zips can legitimately pre-exist from the seed deploy; what AC-5 forbids is the
# refused run producing or refreshing one. Compare the set, do not assert absence.
$zipsBefore = @($snapBefore | Where-Object { $_ -match '\.zip\|' })
$zipsAfter = @($snapAfter | Where-Object { $_ -match '\.zip\|' })
Ok 'AC-5 unscoped force wrote no package artifact' ($null -eq (Compare-Object $zipsBefore $zipsAfter)) "zips=$($zipsAfter.Count)"

Write-Host '=== AC-6 regression: scoped force still overwrites just its target ==='
$r = Run @('-Deploy', '-Force', '-Only', 'ports\codex\wolftac-build.md')
Ok 'AC-6 scoped force logs FORCED-OVERWRITE' ($r.out -match 'FORCED-OVERWRITE')
Ok 'AC-6 scoped force removed the drift' ((Get-Content -Raw (DstOf $A)) -notmatch 'WD347-DRIFT')

Write-Host '=== AC-7 regression: parity and semantic checks ==='
& pwsh -NoProfile -File (Join-Path $Repo 'deploy\parity-check.ps1') *>&1 | Out-Null
Ok 'AC-7 parity-check exits 0' ($LASTEXITCODE -eq 0) "exit=$LASTEXITCODE"
& pwsh -NoProfile -File (Join-Path $Repo 'deploy\semantic-check.ps1') *>&1 | Out-Null
Ok 'AC-7 semantic-check exits 0' ($LASTEXITCODE -eq 0) "exit=$LASTEXITCODE"

Write-Host '=== blast radius: live agent trees must be byte-identical ==='
$env:USERPROFILE = $RealUserProfile
$env:LOCALAPPDATA = $RealLocalAppData
$liveAfter = Snapshot $LiveRoots
$delta = Compare-Object $liveBefore $liveAfter
Ok 'no live agent file was created, changed, or deleted' ($null -eq $delta) "delta=$($delta | Out-String)"

RepoClean
if (Test-Path $Scratch) { Remove-Item -Recurse -Force $Scratch }
if (Test-Path $ShimDir) { Remove-Item -Recurse -Force $ShimDir }
Write-Host ''
Write-Host "RESULT pass=$script:pass fail=$script:fail"
if ($script:fail -gt 0) { exit 1 }
