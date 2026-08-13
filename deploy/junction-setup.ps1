<#
Replace the deployed Hermes factory-skill directories with directory junctions
into this repo, so a /learn edit lands in the working tree instead of drifting.

  pwsh deploy\junction-setup.ps1              # validate, then junction
  pwsh deploy\junction-setup.ps1 -DryRun      # validate and print the plan only
  pwsh deploy\junction-setup.ps1 -SkillsRoot <path>   # target a scratch/non-prod skills dir

Why a junction and not a symlink: junctions need no elevation and no Developer
Mode; symlinks need one of them (verified — see docs\hermes-skill-junctions.md).

REFUSES (changes nothing, exit 1) when:
  * the repo tree and the deployed tree are not byte-identical after LF
    normalization — junctioning then silently discards one side;
  * the deployed path is already a reparse point aimed somewhere unexpected;
  * the deployed path is a file, or the repo tree is missing;
  * the repo tree contains a credential-bearing file or a nested reparse point.

Idempotent: re-running against an already-junctioned tree is a no-op. Nothing is
deleted until every pair has validated, so running this out of order cannot
destroy content. Reverse it with deploy\junction-teardown.ps1.
#>
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$SkillsRoot,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "junctions.json"),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "junction-lib.ps1")

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$manifest = Get-JunctionManifest -ManifestPath $ManifestPath -RepoRoot $RepoRoot -SkillsRoot $SkillsRoot

Write-Output "repo root  : $RepoRoot"
Write-Output "skills root: $($manifest.SkillsRoot)"
Write-Output ""

# ── Phase 1: validate every pair. Decide nothing destructive yet. ──────────
$plan = @()
$refusals = @()
$refuseCount = 0

foreach ($pair in $manifest.Pairs) {
    if (-not (Test-Path -LiteralPath $pair.RepoPath -PathType Container)) {
        $refusals += "REFUSE $($pair.Name): repo tree missing - $($pair.RepoPath)"
        $refuseCount++
        continue
    }

    foreach ($problem in (Test-JunctionTreeSafety $pair.RepoPath)) {
        $refusals += "REFUSE $($pair.Name): $problem"
        $refuseCount++
    }

    if (Test-Path -LiteralPath $pair.LinkPath -PathType Leaf) {
        $refusals += "REFUSE $($pair.Name): deployed path is a file, not a directory - $($pair.LinkPath)"
        $refuseCount++
        continue
    }

    if (Test-IsReparsePoint $pair.LinkPath) {
        $target = Get-ReparseTarget $pair.LinkPath
        if (Test-SamePath $target $pair.RepoPath) {
            $plan += [pscustomobject]@{ Pair = $pair; Action = "NOOP"; Detail = "already junctioned -> $target" }
        } else {
            $refusals += "REFUSE $($pair.Name): deployed path is a link to an unexpected target '$target' (expected '$($pair.RepoPath)')"
            $refuseCount++
        }
        continue
    }

    if (-not (Test-Path -LiteralPath $pair.LinkPath)) {
        $plan += [pscustomobject]@{ Pair = $pair; Action = "CREATE"; Detail = "no deployed copy present" }
        continue
    }

    $diff = @(Compare-Trees $pair.RepoPath $pair.LinkPath)
    if ($diff.Count -gt 0) {
        $refusals += "REFUSE $($pair.Name): repo and deployed content differ in $($diff.Count) file(s) - reconcile first (see WD-346), junctioning now would discard one side"
        $refuseCount++
        foreach ($line in ($diff | Select-Object -First 10)) { $refusals += "        $line" }
        if ($diff.Count -gt 10) { $refusals += "        ... and $($diff.Count - 10) more" }
        continue
    }

    $plan += [pscustomobject]@{ Pair = $pair; Action = "REPLACE"; Detail = "$($diff.Count) differences - content identical, safe to replace with a junction" }
}

foreach ($entry in $plan) {
    Write-Output ("{0,-8} {1,-20} {2}" -f $entry.Action, $entry.Pair.Name, $entry.Detail)
}

if ($refusals.Count -gt 0) {
    Write-Output ""
    foreach ($line in $refusals) { Write-Output $line }
    Write-Output ""
    Write-Output "JUNCTION SETUP REFUSED: $refuseCount problem(s). Nothing was changed."
    exit 1
}

if ($DryRun) {
    Write-Output ""
    Write-Output "DRY RUN: validation passed for $($plan.Count) pair(s). Nothing was changed."
    exit 0
}

# ── Phase 2: act. Every pair validated above, so this cannot lose content. ──
$created = 0
$noop = 0
foreach ($entry in $plan) {
    $pair = $entry.Pair
    if ($entry.Action -eq "NOOP") {
        Write-Output "NOOP     $($pair.Name) -> $($pair.RepoPath)"
        $noop++
        continue
    }
    if ($entry.Action -eq "REPLACE") {
        Remove-Item -LiteralPath $pair.LinkPath -Recurse -Force
    }
    $parent = Split-Path $pair.LinkPath -Parent
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Force $parent | Out-Null
    }
    New-Item -ItemType Junction -Path $pair.LinkPath -Target $pair.RepoPath | Out-Null
    if (-not (Test-SamePath (Get-ReparseTarget $pair.LinkPath) $pair.RepoPath)) {
        throw "junction verification failed for $($pair.LinkPath)"
    }
    Write-Output "LINKED   $($pair.LinkPath) -> $($pair.RepoPath)"
    $created++
}

Write-Output ""
Write-Output "Junction setup complete: $created linked, $noop already correct."
Write-Output "A /learn edit to these skills is now an uncommitted change in $RepoRoot - review it with git diff and commit it."
Write-Output "Restart the Hermes gateway so the cached skills index is rebuilt (skill BODIES are re-read per invocation; the index blurb is cached in-process)."
exit 0
