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

ALSO REFUSES when a deployed path is a REAL directory whose content differs from
the repo. That is the wrong-order / repeat-teardown case: the link is already
gone, so the deployed copy is an independent tree that may hold /learn edits
existing nowhere else. Re-materializing over it would destroy exactly the content
this mechanism exists to protect. Reconcile the deployed edits into the repo
first (see docs\drift-reconciliation.md), or pass -DiscardDeployedEdits to state
explicitly that the deployed copy is worthless.

Idempotent: on an already-torn-down tree whose content MATCHES the repo, every
pair reports NOOP and nothing is rewritten.
#>
param(
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$SkillsRoot,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "junctions.json"),
    [switch]$DryRun,
    # Opt-in, deliberately verbose: overwrite a divergent real deployed directory
    # from the repo, discarding whatever only existed there. Never implied.
    [switch]$DiscardDeployedEdits
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
        # Already a real directory: the link is gone, so this is an INDEPENDENT tree.
        # Diff before touching it - an unconditional copy here is the data-loss bug
        # this script's own header promises it does not have (WD-349 defect 1).
        $divergent = @(Compare-Trees $pair.RepoPath $pair.LinkPath)
        if ($divergent.Count -eq 0) {
            $plan += [pscustomobject]@{ Pair = $pair; Action = "NOOP"; Detail = "already a real directory and identical to the repo" }
        } elseif ($DiscardDeployedEdits) {
            $plan += [pscustomobject]@{ Pair = $pair; Action = "OVERWRITE"; Detail = "DISCARDING $($divergent.Count) divergent file(s) - -DiscardDeployedEdits was passed" }
        } else {
            $sample = ($divergent | Select-Object -First 5) -join ', '
            $refusals += "REFUSE $($pair.Name): deployed path is a real directory that DIFFERS from the repo in $($divergent.Count) file(s) [$sample] - $($pair.LinkPath). It is not a junction, so this content exists only there. Reconcile it into the repo first (docs\drift-reconciliation.md), or re-run with -DiscardDeployedEdits if it is genuinely worthless."
        }
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
    if ($entry.Action -eq "NOOP") {
        # Already real and byte-identical to the repo. Writing would churn mtimes
        # for no gain, and every write is a chance to lose something.
        Write-Output "NOOP     $($pair.LinkPath) (already a real directory matching the repo)"
        $restored++
        continue
    }
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
