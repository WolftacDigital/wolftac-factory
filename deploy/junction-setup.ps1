<#
Replace the deployed Hermes factory-skill directories with directory junctions
into this repo, so a /learn edit lands in the working tree instead of drifting.

  pwsh deploy\junction-setup.ps1 -RepoRoot <dedicated-worktree>   # the supported form
  pwsh deploy\junction-setup.ps1 -RepoRoot <worktree> -DryRun     # validate, print the plan
  pwsh deploy\junction-setup.ps1 -SkillsRoot <path>               # scratch/non-prod skills dir

-RepoRoot must be a DEDICATED worktree pinned to main, not the checkout you work
in. The bare zero-argument form is refused against a primary checkout - see the
D5 guard below and decision 1 in docs\hermes-skill-junctions.md.

Why a junction and not a symlink: junctions need no elevation and no Developer
Mode; symlinks need one of them (verified — see docs\hermes-skill-junctions.md).

REFUSES (changes nothing, exit 1) when:
  * -RepoRoot is a PRIMARY git checkout rather than a dedicated worktree, unless
    -AllowActiveCheckout is passed (decision 1 calls this configuration unsafe);
  * the repo tree and the deployed tree are not byte-identical after LF
    normalization — junctioning then silently discards one side;
  * the deployed path is already a reparse point aimed somewhere unexpected;
  * the deployed path is a file, or the repo tree is missing;
  * EITHER side — repo tree or deployed tree — contains a credential-bearing file
    or a nested reparse point. Setup deletes the deployed tree recursively before
    linking, so scanning only the repo side leaves the destructive direction blind.

Idempotent: re-running against an already-junctioned tree is a no-op. Nothing is
deleted until every pair has validated, so running this out of order cannot
destroy content. Reverse it with deploy\junction-teardown.ps1.
#>
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$SkillsRoot,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "junctions.json"),
    [switch]$DryRun,
    # Opt-in escape hatch for the case decision 1 calls measurably unsafe: pointing
    # the junctions at a checkout somebody switches branches in. Never implied.
    [switch]$AllowActiveCheckout
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "junction-lib.ps1")

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

# ── D5 guard: refuse the bare default against a PRIMARY checkout. ──────────
# The zero-argument invocation resolves $RepoRoot to this script's own repo. In a
# primary checkout that is exactly the configuration decision 1 rejects: once the
# deployed skills ARE the working tree, a `git checkout` of another branch
# rewrites every live skill under the agents' feet, and a stash or a reset
# discards /learn edits with no copy anywhere. A linked worktree (`.git` is a
# FILE) is the supported layout; a primary checkout (`.git` is a DIRECTORY) is not.
$gitEntry = Join-Path $RepoRoot ".git"
$isPrimaryCheckout = (Test-Path -LiteralPath $gitEntry -PathType Container)
if ($isPrimaryCheckout -and -not $AllowActiveCheckout) {
    Write-Output "repo root  : $RepoRoot"
    Write-Output ""
    Write-Output "JUNCTION SETUP REFUSED: '$RepoRoot' is a PRIMARY git checkout, not a dedicated worktree."
    Write-Output "Junctioning it makes the live skills follow whatever branch this checkout is on -"
    Write-Output "a `git checkout` would rewrite every deployed skill, and a stash or reset would"
    Write-Output "discard /learn edits that exist nowhere else. Decision 1 in"
    Write-Output "docs\hermes-skill-junctions.md records this as measurably unsafe."
    Write-Output ""
    Write-Output "Use a dedicated worktree pinned to main:"
    Write-Output "  git -C `"$RepoRoot`" worktree add D:/Wolftac/.factory-live main"
    Write-Output "  pwsh deploy\junction-setup.ps1 -RepoRoot D:/Wolftac/.factory-live"
    Write-Output ""
    Write-Output "Pass -AllowActiveCheckout only if you accept that hazard deliberately."
    exit 1
}
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

    # Scan the DEPLOYED side too (WD-349 defect 3). Setup deletes the deployed tree
    # with Remove-Item -Recurse before creating the junction; if that tree contains
    # a nested reparse point, the recursive delete can follow it into an unrelated
    # location. A guard that scans only the repo side is proven safe in one
    # direction and blind in the other, and junction-lib's own comment calls PS
    # reparse handling historically unreliable — so do not rely on the runtime
    # declining to follow it.
    if ((Test-Path -LiteralPath $pair.LinkPath -PathType Container) -and -not (Test-IsReparsePoint $pair.LinkPath)) {
        foreach ($problem in (Test-JunctionTreeSafety $pair.LinkPath)) {
            $refusals += "REFUSE $($pair.Name): deployed side - $problem"
            $refuseCount++
        }
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
