<#
Wolftac Factory deploy/drift/package tool.

  pwsh deploy\deploy.ps1 -Check           # diff deployed copies and validate cross-port invariants
  pwsh deploy\deploy.ps1 -Deploy          # copy repo -> agent locations (LF-normalized), rebuild Desktop zips;
                                          #   drifted (live-edited) targets are SKIPPED with a warning, exit 1
  pwsh deploy\deploy.ps1 -Deploy -Force -Only <scope>
                                          # overwrite drifted targets, but only within <scope>.
                                          #   Unscoped -Force is REFUSED while drift exists (WD-347).
  pwsh deploy\deploy.ps1 -Reconcile       # pull DEPLOYED copies back into the repo working tree
                                          #   (LF-normalized) so `git diff` is the review surface
  pwsh deploy\deploy.ps1 -Package  # zip the whole repo (minus git/temp/package artifacts) for handoff as plain files

  -Only <substring> scopes -Check, -Deploy, and -Reconcile to pairs whose repo-relative
  source path contains <substring>, case-insensitively. Supplying -Only always scopes:
  a value matching no pair exits non-zero and lists the available scopes, so a typo
  never reads as a clean run and never widens to every pair.

The repo is the source of truth. Files are written with LF line endings — CRLF in a
skill's YAML frontmatter silently breaks skill discovery on Linux (WSL / Hermes).
If -Check reports drift and the DEPLOYED copy is the improvement (e.g. Hermes /learn
edited a skill in place), run -Reconcile to copy it back into the repo working tree and
commit what belongs; otherwise -Deploy overwrites the target with the repo version.
-Reconcile never commits, branches, or pushes: it leaves the changes in the working tree
so `git diff` is the review surface and `git checkout --` is the undo.

Targets listed in deploy\junctions.json that are live directory junctions into this
repo are neither checked nor written: repo and deployed are one set of bytes. See
docs\hermes-skill-junctions.md.
#>
param([switch]$Check, [switch]$Deploy, [switch]$Package, [switch]$Force,
      [switch]$Reconcile, [string]$Only)

$Repo = Split-Path $PSScriptRoot -Parent
$Pairs = @(
    @{ Src = "canonical\wolftac-spec\SKILL.md";         Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\SKILL.md" }
    @{ Src = "canonical\wolftac-spec\spec-template.md"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\spec-template.md" }
    @{ Src = "canonical\wolftac-build\SKILL.md";        Dst = "$env:USERPROFILE\.claude\skills\wolftac-build\SKILL.md" }
    @{ Src = "canonical\wolftac-review\SKILL.md";       Dst = "$env:USERPROFILE\.claude\skills\wolftac-review\SKILL.md" }
    @{ Src = "canonical\wolftac-integrate\SKILL.md";    Dst = "$env:USERPROFILE\.claude\skills\wolftac-integrate\SKILL.md" }
    @{ Src = "canonical\wolftac-integrate\scripts\linear_bot.py"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-integrate\scripts\linear_bot.py" }
    @{ Src = "canonical\wolftac-integrate\scripts\slack_bot.py"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-integrate\scripts\slack_bot.py" }
    # Shared doctrine skill (not a pipeline stage) — ships to every local agent surface.
    @{ Src = "canonical\wolftac-agent-command-patterns\SKILL.md"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-agent-command-patterns\SKILL.md" }
    @{ Src = "ports\codex\wolftac-agent-command-patterns.md"; Dst = "$env:USERPROFILE\.codex\prompts\wolftac-agent-command-patterns.md" }
    @{ Src = "ports\codex\wolftac-spec.md";             Dst = "$env:USERPROFILE\.codex\prompts\wolftac-spec.md" }
    @{ Src = "ports\codex\wolftac-build.md";            Dst = "$env:USERPROFILE\.codex\prompts\wolftac-build.md" }
    @{ Src = "ports\codex\wolftac-review.md";           Dst = "$env:USERPROFILE\.codex\prompts\wolftac-review.md" }
    @{ Src = "ports\codex\wolftac-integrate.md";        Dst = "$env:USERPROFILE\.codex\prompts\wolftac-integrate.md" }
    # Codex prompts are flat .md files, so the integrate helper ships in a sibling
    # directory whose name cannot collide with a prompt file.
    @{ Src = "ports\codex\wolftac-integrate-scripts\linear_bot.py"; Dst = "$env:USERPROFILE\.codex\prompts\wolftac-integrate-scripts\linear_bot.py" }
    @{ Src = "ports\codex\wolftac-integrate-scripts\slack_bot.py"; Dst = "$env:USERPROFILE\.codex\prompts\wolftac-integrate-scripts\slack_bot.py" }
)

# Tree-backed ports must stay complete as their skills gain references and UI metadata.
# Enumerating them removes the old failure mode where SKILL.md referenced files that
# deploy/check never copied or validated.
$trees = @(
    @{ Src = "ports\hermes\wolftac-spec"; Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-spec" },
    @{ Src = "ports\hermes\wolftac-build"; Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-build" },
    @{ Src = "ports\hermes\wolftac-review"; Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-review" },
    @{ Src = "ports\hermes\wolftac-integrate"; Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-integrate" },
    @{ Src = "ports\hermes\wolftac-agent-command-patterns"; Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-agent-command-patterns" },
    # The isolated factory-integrate profile is a SEPARATE Hermes home with its own
    # skills tree; the default-profile target above never reaches it. It ran for weeks
    # on an unversioned SKILL.md with no linear_bot.py, so the worker hand-rolled a
    # GraphQL client every tick and never processed the queue. Both halves ship here:
    # the skill (helper paths pointed at THIS profile) and the isolation preflight the
    # worker must clear before claiming anything.
    @{ Src = "ports\hermes-factory-integrate\skills\wolftac-integrate"; Dst = "$env:LOCALAPPDATA\hermes\profiles\factory-integrate\skills\wolftac-integrate" },
    @{ Src = "ports\hermes-factory-integrate\deploy"; Dst = "$env:LOCALAPPDATA\hermes\profiles\factory-integrate\deploy" },
    @{ Src = "plugins\wolftac-factory\skills\wolftac-spec"; Dst = "$env:USERPROFILE\.agents\skills\wolftac-spec" },
    @{ Src = "plugins\wolftac-factory\skills\wolftac-build"; Dst = "$env:USERPROFILE\.agents\skills\wolftac-build" },
    @{ Src = "plugins\wolftac-factory\skills\wolftac-review"; Dst = "$env:USERPROFILE\.agents\skills\wolftac-review" },
    @{ Src = "plugins\wolftac-factory\skills\wolftac-integrate"; Dst = "$env:USERPROFILE\.agents\skills\wolftac-integrate" }
)
foreach ($tree in $trees) {
    $srcRoot = Join-Path $Repo $tree.Src
    # This enumerates the FILESYSTEM, not git, so .gitignore does not protect it: running a
    # shipped script (e.g. integrate-preflight.py) leaves a __pycache__ beside it, which was
    # then deployed as a stray .pyc and reported as MISSING by -Check. Bytecode is never a
    # deploy artifact.
    $treeFiles = Get-ChildItem -LiteralPath $srcRoot -File -Recurse | Where-Object {
        $_.Extension -ne ".pyc" -and $_.FullName -notmatch '[\\/]__pycache__[\\/]'
    }
    foreach ($file in $treeFiles) {
        $relative = [IO.Path]::GetRelativePath($srcRoot, $file.FullName)
        $Pairs += @{ Src = (Join-Path $tree.Src $relative); Dst = (Join-Path $tree.Dst $relative) }
    }
}

# Claude Code inside WSL reads the LINUX home (~/.claude/skills), not the Windows one.
# When WSL is present, add the canonical skills as targets there too (dynamic home
# detection so this ports to other machines, e.g. jon-omen, unchanged).
#
# The probe is bounded: a wedged WSL service makes every wsl.exe call (even
# `wsl -l -v`) block forever, which used to hang -Check/-Deploy/-Package outright.
# Only -Check and -Deploy touch WSL targets, so nothing else probes. A timed-out
# probe is killed and reported loudly; WSL targets are then skipped, not passed.
# 60s, not less: a cold WSL boot (first call after `wsl --shutdown` or idle stop)
# measured 41-48s on a working machine.
$wslHome = $null
$WslProbeTimedOut = $false
# -Reconcile reads the same deployed targets -Check compares, WSL included (WD-347 review defect 1).
if ($Check -or $Deploy -or $Reconcile) {
    try {
        $psi = [Diagnostics.ProcessStartInfo]::new("wsl.exe")
        foreach ($a in @("-d", "Ubuntu", "--", "sh", "-c", 'echo $HOME')) { $psi.ArgumentList.Add($a) }
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.UseShellExecute = $false
        $proc = [Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEndAsync()
        $null = $proc.StandardError.ReadToEndAsync()
        if ($proc.WaitForExit(60000)) {
            $wslHome = ($stdout.Result -split "`n" | Select-Object -First 1)
        } else {
            $WslProbeTimedOut = $true
            try { $proc.Kill($true) } catch {}
            Write-Output "WARN: WSL probe timed out after 60s (WSL unresponsive; try 'wsl --shutdown'). WSL skill targets were NOT checked or deployed."
        }
    } catch {}
}
if ($wslHome) { $wslHome = ($wslHome -replace "`0", "").Trim() }
if ($wslHome -and $wslHome.StartsWith("/")) {
    $wslSkills = "\\wsl$\Ubuntu" + ($wslHome -replace "/", "\") + "\.claude\skills"
    $Pairs += @{ Src = "canonical\wolftac-spec\SKILL.md";         Dst = "$wslSkills\wolftac-spec\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-spec\spec-template.md"; Dst = "$wslSkills\wolftac-spec\spec-template.md" }
    $Pairs += @{ Src = "canonical\wolftac-build\SKILL.md";        Dst = "$wslSkills\wolftac-build\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-review\SKILL.md";       Dst = "$wslSkills\wolftac-review\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-integrate\SKILL.md";    Dst = "$wslSkills\wolftac-integrate\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-integrate\scripts\linear_bot.py"; Dst = "$wslSkills\wolftac-integrate\scripts\linear_bot.py" }
    $Pairs += @{ Src = "canonical\wolftac-integrate\scripts\slack_bot.py"; Dst = "$wslSkills\wolftac-integrate\scripts\slack_bot.py" }
    $Pairs += @{ Src = "canonical\wolftac-agent-command-patterns\SKILL.md"; Dst = "$wslSkills\wolftac-agent-command-patterns\SKILL.md" }
}

# ── Junctioned targets (WD-349) ───────────────────────────────────────────────
# When a deployed Hermes skill directory is a directory junction into this repo
# (deploy\junction-setup.ps1), repo and "deployed" are the SAME bytes: there is
# no copy to make and no drift that can exist. Writing to such a path writes INTO
# the repo working tree, which would silently clobber an uncommitted /learn edit
# — so -Deploy skips those pairs and -Check reports them as JUNCTIONED rather
# than counting them. See docs\hermes-skill-junctions.md.
$JunctionedRoots = @()
$JunctionedTargets = @()
$junctionManifestPath = Join-Path $PSScriptRoot "junctions.json"

# Identity of THIS repository, shared by every one of its worktrees. Comparing the
# git common dir is what distinguishes "a worktree of this repo" from "any folder
# whose path happens to end the same way" (WD-349 defect 2).
function Get-GitCommonDir([string]$Dir) {
    if (-not (Test-Path -LiteralPath $Dir -PathType Container)) { return $null }
    try {
        $out = & git -C $Dir rev-parse --git-common-dir 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $p = ($out | Select-Object -First 1).Trim()
        if (-not [IO.Path]::IsPathRooted($p)) { $p = Join-Path $Dir $p }
        return ([IO.Path]::GetFullPath($p)).TrimEnd('\').ToLowerInvariant()
    } catch { return $null }
}
$RepoCommonDir = Get-GitCommonDir $Repo

if (Test-Path -LiteralPath $junctionManifestPath -PathType Leaf) {
    try {
        $junctionManifest = Get-Content -LiteralPath $junctionManifestPath -Raw | ConvertFrom-Json
        $junctionSkillsRoot = [Environment]::ExpandEnvironmentVariables($junctionManifest.skills_root)
        foreach ($jp in @($junctionManifest.pairs)) {
            $linkPath = Join-Path $junctionSkillsRoot $jp.name
            $linkItem = Get-Item -LiteralPath $linkPath -Force -ErrorAction SilentlyContinue
            if (-not $linkItem -or -not ($linkItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
            # Trust the manifest only as a hint. Two independent conditions must
            # hold before a link silences drift checking for its target:
            #
            #   1. the tail matches this pair's repo-relative path — the tail
            #      rather than the full path because the recommended layout points
            #      the junction at a DEDICATED always-on-main worktree, not at the
            #      checkout deploy.ps1 happens to be running from; and
            #   2. the target is a worktree of THIS repository, proven by comparing
            #      `git rev-parse --git-common-dir`.
            #
            # Condition 2 is the WD-349 defect-2 fix. With the tail check alone, a
            # junction into ANY unrelated repo whose path merely ends the same way
            # was reported "JUNCTIONED ... no drift is possible" and exit 0 — the
            # drift alarm went permanently silent over content Hermes actually
            # loads. A suffix is not an identity.
            $suffix = "\" + ($jp.repo -replace '/', '\').Trim('\')
            $actual = $null
            try { $actual = [IO.Path]::GetFullPath($linkItem.LinkTarget) } catch {}
            $tailOk = $actual -and $actual.TrimEnd('\').EndsWith($suffix, [StringComparison]::OrdinalIgnoreCase) -and
                      (Test-Path -LiteralPath $actual -PathType Container)
            if ($tailOk) {
                $targetCommonDir = Get-GitCommonDir $actual
                if ($RepoCommonDir -and $targetCommonDir -and $targetCommonDir -eq $RepoCommonDir) {
                    $JunctionedRoots += [IO.Path]::GetFullPath($linkPath)
                    $JunctionedTargets += "$([IO.Path]::GetFullPath($linkPath)) -> $actual"
                } else {
                    # Fail LOUD and fail SAFE: not trusted, so the pair keeps being
                    # drift-checked as an ordinary copy target rather than silently
                    # exempted.
                    Write-Output "FOREIGN-LINK $([IO.Path]::GetFullPath($linkPath)) -> $actual (not a worktree of this repo; drift is still checked)"
                }
            }
        }
    } catch {
        Write-Output "WARN: could not read $junctionManifestPath ($($_.Exception.Message)); treating all targets as copy-based."
    }
}

function Test-UnderJunction([string]$Path) {
    $full = try { [IO.Path]::GetFullPath($Path) } catch { $Path }
    foreach ($root in $JunctionedRoots) {
        if ($full -ieq $root -or $full.StartsWith($root.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    return $false
}

# ── Scope filter (-Only, WD-347) ──────────────────────────────────────────────
# Matches the pair's repo-relative SOURCE path, which encodes both the surface and
# the skill (ports\hermes\wolftac-build\SKILL.md), so one flag scopes either way.
# Ordinal substring rather than -like: an operator scope containing [ or * would
# otherwise be silently reinterpreted as a wildcard pattern.
function Test-ScopeMatch([string]$Src, [string]$Scope) {
    return $Src.IndexOf($Scope, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-AvailableScopes($pairs) {
    $set = New-Object 'System.Collections.Generic.SortedSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($p in $pairs) {
        foreach ($seg in ($p.Src -split '[\\/]')) {
            if ($seg -and $seg -notmatch '\.[A-Za-z0-9]+$') { [void]$set.Add($seg) }
        }
    }
    return $set
}

# An explicitly supplied -Only always activates scoping, even when the value is
# empty or whitespace. Deciding scope by IsNullOrWhiteSpace let `-Only '   '`
# silently fall through to the unscoped path and operate on every pair, which is
# the broad-operation failure AC-9 exists to prevent. An explicit scope that
# matches nothing must take the NO-MATCH exit, never widen.
$ScopeFilterActive = $PSBoundParameters.ContainsKey('Only')
if ($ScopeFilterActive) {
    # An empty or whitespace-only scope names no pair. Ordinal IndexOf treats ''
    # as a substring of every path, so matching it directly would turn `-Only ''`
    # into a wildcard selecting all pairs -- and, worse, satisfy the scoped-force
    # guard. Resolve it to an explicit zero match instead.
    # @( ) around the whole if: an if-expression unrolls a one-element result into the bare
    # hashtable, whose .Count is its key count, not 1 (WD-347 review defect 2).
    $scoped = @(if ([string]::IsNullOrWhiteSpace($Only)) {
        @()
    } else {
        @($Pairs | Where-Object { Test-ScopeMatch $_.Src $Only })
    })
    if ($scoped.Count -eq 0) {
        # A zero-match scope must never read as success. Silently matching nothing
        # would make `-Deploy -Force -Only typo` look like a clean run while shipping
        # nothing at all — the exact failure this guard exists to prevent.
        Write-Output "NO-MATCH -Only '$Only' matched none of the $($Pairs.Count) deploy pairs. Nothing was checked, written, or reconciled."
        Write-Output "Available scopes:"
        foreach ($sc in (Get-AvailableScopes $Pairs)) { Write-Output "  $sc" }
        exit 2
    }
    Write-Output "SCOPED   -Only '$Only' -> $($scoped.Count) of $($Pairs.Count) pair(s)."
    $Pairs = $scoped
}

# Read a file and return its content with LF line endings (no trailing-CR).
function Get-LfText($Path) {
    $raw = [IO.File]::ReadAllText($Path)
    return ($raw -replace "`r`n", "`n") -replace "`r", "`n"
}

function Get-LfHash($Path) {
    if (-not (Test-Path $Path)) { return $null }
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-LfText $Path))
    $sha = [Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "")
}

if ($Check) {
    $drift = 0
    $junctioned = 0
    foreach ($p in $Pairs) {
        if (Test-UnderJunction $p.Dst) { $junctioned++; continue }
        $srcHash = Get-LfHash (Join-Path $Repo $p.Src)
        $dstHash = Get-LfHash $p.Dst
        if ($null -eq $dstHash)        { Write-Output "MISSING  $($p.Dst)"; $drift++ }
        elseif ($srcHash -ne $dstHash) { Write-Output "DRIFT    $($p.Dst)"; $drift++ }
    }
    if ($junctioned -gt 0) {
        Write-Output "JUNCTIONED $junctioned file(s) across $($JunctionedRoots.Count) linked skill dir(s) - the repo IS the deployed copy, so no drift is possible and none is checked."
        foreach ($t in $JunctionedTargets) { Write-Output "  linked   $t" }
    }
    if ($drift -eq 0) {
        $wslNote = if ($WslProbeTimedOut) { " (WSL targets unchecked: probe timed out)" } else { "" }
        Write-Output "CLEAN: all $($Pairs.Count - $junctioned) copy-deployed files match the repo.$wslNote"
    } else {
        Write-Output "$drift file(s) out of sync. If the deployed copy is the improvement, copy it into the repo and commit; otherwise run -Deploy."
    }

    & (Join-Path $PSScriptRoot "parity-check.ps1")
    $parityExit = $LASTEXITCODE
    & (Join-Path $PSScriptRoot "semantic-check.ps1")
    $semanticExit = $LASTEXITCODE
    if ($drift -ne 0 -or $parityExit -ne 0 -or $semanticExit -ne 0) { exit 1 }
    exit 0
}

if ($Deploy) {
    # Unscoped -Force is refused while drift exists (WD-347). The check is a PRE-PASS:
    # it must complete before any write, so a refusal leaves the tree byte-identical
    # rather than half-clobbered. Runs only on the -Force-without-Only path, so plain
    # -Deploy pays no extra hashing and its behaviour is provably unchanged.
    if ($Force -and -not $ScopeFilterActive) {
        $drifted = @()
        foreach ($p in $Pairs) {
            if (Test-UnderJunction $p.Dst) { continue }
            $dstHash = Get-LfHash $p.Dst
            if ($null -ne $dstHash -and (Get-LfHash (Join-Path $Repo $p.Src)) -ne $dstHash) { $drifted += $p }
        }
        if ($drifted.Count -gt 0) {
            Write-Output "REFUSED: -Force without -Only would overwrite $($drifted.Count) drifted target(s):"
            foreach ($p in $drifted) { Write-Output "  would-clobber $($p.Dst)" }
            Write-Output "-Force must state its scope. Re-run as: -Deploy -Force -Only <substring>"
            Write-Output "Or run -Reconcile first to pull those live edits into the repo and review them with git diff."
            Write-Output "No file was written."
            exit 2
        }
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    $skipped = 0
    $forced = 0
    $junctioned = 0
    foreach ($p in $Pairs) {
        # Never write through a junction: the destination IS the repo working
        # tree, and an uncommitted /learn edit there must survive -Deploy. This
        # test precedes the $Force branch, so -Force cannot clobber it either.
        if (Test-UnderJunction $p.Dst) { $junctioned++; continue }
        $dstDir = Split-Path $p.Dst -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force $dstDir | Out-Null }
        $srcHash = Get-LfHash (Join-Path $Repo $p.Src)
        $dstHash = Get-LfHash $p.Dst
        $isDrift = ($null -ne $dstHash -and $srcHash -ne $dstHash)
        if ($isDrift -and -not $Force) {
            Write-Output "DRIFT-SKIPPED $($p.Dst) (use -Force, or reconcile the live edit into the repo)"
            $skipped++
            continue
        }
        if ($isDrift) {
            # -Force is clobbering a target that had diverged from the repo — make it audible.
            Write-Output "FORCED-OVERWRITE $($p.Dst)"
            $forced++
        } else {
            Write-Output "deployed $($p.Src)"
        }
        [IO.File]::WriteAllText($p.Dst, (Get-LfText (Join-Path $Repo $p.Src)), $enc)
    }
    if ($junctioned -gt 0) {
        Write-Output "JUNCTION-NOOP $junctioned file(s) across $($JunctionedRoots.Count) linked skill dir(s) - single copy, nothing to write, no working-tree content touched."
        foreach ($t in $JunctionedTargets) { Write-Output "  linked   $t" }
    }
    $zipOut = "$env:USERPROFILE\Downloads\factory-skills"
    if (-not (Test-Path $zipOut)) { New-Item -ItemType Directory -Force $zipOut | Out-Null }
    # Desktop skills are hand-uploaded, so they get no $Pairs entry; Compress-Archive
    # recurses, which is how wolftac-integrate\scripts\linear_bot.py reaches the bundle.
    # Desktop still RUNS the helper from the Claude Code target above, because its MCP
    # terminal server sees the local filesystem, not the uploaded bundle.
    foreach ($s in @("wolftac-spec", "wolftac-build", "wolftac-review", "wolftac-integrate", "wolftac-agent-command-patterns")) {
        Compress-Archive -Path (Join-Path $Repo "ports\claude-desktop\$s") -DestinationPath (Join-Path $zipOut "$s.zip") -Force
        Write-Output "zipped   $s.zip -> $zipOut"
    }
    $codexPluginOut = Join-Path $zipOut "wolftac-factory-codex-plugin.zip"
    $codexPluginItems = Get-ChildItem -LiteralPath (Join-Path $Repo "plugins\wolftac-factory") -Force
    Compress-Archive -Path $codexPluginItems.FullName -DestinationPath $codexPluginOut -Force
    Write-Output "zipped   wolftac-factory-codex-plugin.zip -> $zipOut"
    if ($skipped -gt 0) {
        Write-Output "Deploy finished with $skipped drift-skipped file(s). Reconcile the live edit(s) into the repo, or re-run with -Force to overwrite."
        exit 1
    }
    if ($forced -gt 0) {
        Write-Output "$forced target(s) force-overwritten despite drift — verify no live edit was lost."
    }
    Write-Output "Deploy complete. Restart any open agent session to pick up skill changes; re-upload the Desktop zips in the Claude app."
    exit 0
}

if ($Reconcile) {
    # Copies the DEPLOYED bytes over the repo copy, LF-normalized, and stops there.
    # No commit, no branch, no push (NG-1) and no content merging (NG-2): deciding
    # what to keep is a human's call, made against `git diff`.
    $enc = New-Object System.Text.UTF8Encoding($false)
    $changed = 0
    $failed = 0
    $junctioned = 0
    foreach ($p in $Pairs) {
        # A junctioned target IS the repo file; there is nothing to copy back.
        if (Test-UnderJunction $p.Dst) { $junctioned++; continue }
        $repoPath = Join-Path $Repo $p.Src
        $dstHash = Get-LfHash $p.Dst
        if ($null -eq $dstHash) {
            Write-Output "UNREADABLE $($p.Dst) (no deployed copy to reconcile from; repo file left untouched)"
            $failed++
            continue
        }
        if ((Get-LfHash $repoPath) -eq $dstHash) { continue }
        $text = $null
        try { $text = Get-LfText $p.Dst } catch {
            Write-Output "UNREADABLE $($p.Dst) ($($_.Exception.Message)); repo file left untouched"
            $failed++
            continue
        }
        # Belt and braces for AC-8: never let a partial or failed read land in the
        # repo as a truncated/empty file. ReadAllText either returns everything or
        # throws, so a non-empty file yielding empty text means something is wrong.
        if ([string]::IsNullOrEmpty($text)) {
            # Empty is never reconcilable content. A zero-byte deployed copy is the
            # signature of an interrupted write or a truncated sync, and copying it
            # over a populated repo file is the data loss AC-8 forbids -- so refuse
            # regardless of whether the source is 0 bytes or read back empty.
            $why = if ((Get-Item -LiteralPath $p.Dst).Length -gt 0) {
                "read returned empty for a non-empty file"
            } else {
                "deployed copy is empty (0 bytes); refusing to truncate the repo file"
            }
            Write-Output "UNREADABLE $($p.Dst) ($why); repo file left untouched"
            $failed++
            continue
        }
        [IO.File]::WriteAllText($repoPath, $text, $enc)
        Write-Output "reconciled $($p.Src)  <- $($p.Dst)"
        $changed++
    }
    if ($junctioned -gt 0) {
        Write-Output "JUNCTION-NOOP $junctioned file(s) across $($JunctionedRoots.Count) linked skill dir(s) - single copy, already the repo, nothing to reconcile."
    }
    if ($changed -eq 0 -and $failed -eq 0) {
        Write-Output "Nothing to reconcile: all $($Pairs.Count - $junctioned) copy-deployed file(s) already match the repo. No file was written."
        exit 0
    }
    if ($changed -gt 0) {
        Write-Output "Reconciled $changed file(s) into the repo working tree. Review with 'git diff', keep what belongs, and 'git checkout -- <path>' to discard the rest. Nothing was committed."
    }
    if ($failed -gt 0) {
        Write-Output "$failed target(s) could not be read and were skipped; no partial content was written for them."
        exit 1
    }
    exit 0
}

if ($Package) {
    $out = "$env:USERPROFILE\Downloads\wolftac-factory-package.zip"
    if (Test-Path $out) { Remove-Item -Force $out }
    $excludedNames = @(".git", ".review-worktrees", ".integrate-sandbox", ".integrate-worktrees", "node_modules")
    $items = Get-ChildItem $Repo -Force | Where-Object {
        $name = $_.Name
        ($excludedNames -notcontains $name) -and
        ($name -notlike "*.zip") -and
        ($name -notlike "*.log")
    }
    Compress-Archive -Path $items.FullName -DestinationPath $out
    Write-Output "packaged $out"
    Write-Output "Hand this file to an agent (Hermes, Codex, or a fresh Claude session);"
    Write-Output "have it unzip, read AGENTS.md, and follow the setup instructions there."
    exit 0
}

Write-Output "Usage: deploy.ps1 -Check | -Deploy [-Force] | -Reconcile | -Package  [-Only <substring>]"
Write-Output "       -Force requires -Only while drift exists; -Reconcile pulls deployed edits back into the repo."
