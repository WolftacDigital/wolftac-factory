<#
Reverse deploy\junction-setup.ps1: remove the directory junctions and restore
real deployed directories from the repo, returning the system to copy-based
deploy.

  pwsh deploy\junction-teardown.ps1
  pwsh deploy\junction-teardown.ps1 -DryRun
  pwsh deploy\junction-teardown.ps1 -SkillsRoot <path>   # scratch/non-prod target

Ordering hazard this script is built around: the junction and the repo are the
SAME bytes, so unlinking must never delete through the link. Every removal goes
through Directory.Delete on a verified reparse point (link entry only), and the
real directory is written afterwards from the repo. Content that only existed as
an uncommitted /learn edit therefore survives teardown - it is still in the repo
working tree, and it is what gets copied back out.

REFUSES (changes nothing, exit 1) when a deployed path is a reparse point aimed
at something other than its repo tree - that is somebody else's link, not ours.

Idempotent: on an already-torn-down tree every pair reports RESTORED/NOOP and
the repo content is simply re-materialized.
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

# ── Phase 1: validate every pair before removing anything. ────────────────
$plan = @()
$refusals = @()

foreach ($pair in $manifest.Pairs) {
    if (-not (Test-Path -LiteralPath $pair.RepoPath -PathType Container)) {
        $refusals += "REFUSE $($pair.Name): repo tree missing - $($pair.RepoPath); restoring a real directory would produce an empty skill"
        continue
    }
    if (Test-Path -LiteralPath $pair.LinkPath -PathType Leaf) {
        $refusals += "REFUSE $($pair.Name): deployed path is a file, not a directory - $($pair.LinkPath)"
        continue
    }
    if (Test-IsReparsePoint $pair.LinkPath) {
        $target = Get-ReparseTarget $pair.LinkPath
        if (Test-SamePath $target $pair.RepoPath) {
            $plan += [pscustomobject]@{ Pair = $pair; Action = "UNLINK"; Detail = "junction -> $target" }
        } else {
            $refusals += "REFUSE $($pair.Name): deployed path is a link to '$target', not this repo's '$($pair.RepoPath)' - not ours to remove"
        }
        continue
    }
    if (Test-Path -LiteralPath $pair.LinkPath -PathType Container) {
        $plan += [pscustomobject]@{ Pair = $pair; Action = "REFRESH"; Detail = "already a real directory - re-materialize from repo" }
        continue
    }
    $plan += [pscustomobject]@{ Pair = $pair; Action = "MATERIALIZE"; Detail = "no deployed copy present" }
}

foreach ($entry in $plan) {
    Write-Output ("{0,-12} {1,-20} {2}" -f $entry.Action, $entry.Pair.Name, $entry.Detail)
}

if ($refusals.Count -gt 0) {
    Write-Output ""
    foreach ($line in $refusals) { Write-Output $line }
    Write-Output ""
    Write-Output "JUNCTION TEARDOWN REFUSED: $($refusals.Count) problem(s). Nothing was changed."
    exit 1
}

if ($DryRun) {
    Write-Output ""
    Write-Output "DRY RUN: validation passed for $($plan.Count) pair(s). Nothing was changed."
    exit 0
}

# ── Phase 2: act. ─────────────────────────────────────────────────────────
$restored = 0
foreach ($entry in $plan) {
    $pair = $entry.Pair
    if ($entry.Action -eq "UNLINK") {
        # Link entry only - never Remove-Item -Recurse a reparse point.
        Remove-DirectoryLink $pair.LinkPath
    }
    Copy-TreeLf $pair.RepoPath $pair.LinkPath
    if (Test-IsReparsePoint $pair.LinkPath) {
        throw "teardown verification failed: $($pair.LinkPath) is still a reparse point"
    }
    $diff = @(Compare-Trees $pair.RepoPath $pair.LinkPath)
    if ($diff.Count -gt 0) {
        throw "teardown verification failed: $($pair.LinkPath) differs from the repo in $($diff.Count) file(s)"
    }
    Write-Output "RESTORED $($pair.LinkPath) (real directory, $((Get-TreeHashMap $pair.LinkPath).Count) file(s))"
    $restored++
}

Write-Output ""
Write-Output "Junction teardown complete: $restored real director(ies) restored from the repo."
Write-Output "Copy-based deploy is active again - run 'pwsh deploy\deploy.ps1 -Check' to confirm these targets are clean."
exit 0
