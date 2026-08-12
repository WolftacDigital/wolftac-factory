<#
Wolftac Factory deploy/drift/package tool.

  pwsh deploy\deploy.ps1 -Check           # diff deployed copies and validate cross-port invariants
  pwsh deploy\deploy.ps1 -Deploy          # copy repo -> agent locations (LF-normalized), rebuild Desktop zips;
                                          #   drifted (live-edited) targets are SKIPPED with a warning, exit 1
  pwsh deploy\deploy.ps1 -Deploy -Force   # overwrite drifted targets too
  pwsh deploy\deploy.ps1 -Package  # zip the whole repo (minus git/temp/package artifacts) for handoff as plain files

The repo is the source of truth. Files are written with LF line endings — CRLF in a
skill's YAML frontmatter silently breaks skill discovery on Linux (WSL / Hermes).
If -Check reports drift and the DEPLOYED copy is the improvement (e.g. Hermes /learn
edited a skill in place), copy it back into the repo and commit; otherwise -Deploy
overwrites the target with the repo version.
#>
param([switch]$Check, [switch]$Deploy, [switch]$Package, [switch]$Force)

$Repo = Split-Path $PSScriptRoot -Parent
$Pairs = @(
    @{ Src = "canonical\wolftac-spec\SKILL.md";         Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\SKILL.md" }
    @{ Src = "canonical\wolftac-spec\spec-template.md"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\spec-template.md" }
    @{ Src = "canonical\wolftac-build\SKILL.md";        Dst = "$env:USERPROFILE\.claude\skills\wolftac-build\SKILL.md" }
    @{ Src = "canonical\wolftac-review\SKILL.md";       Dst = "$env:USERPROFILE\.claude\skills\wolftac-review\SKILL.md" }
    @{ Src = "canonical\wolftac-integrate\SKILL.md";    Dst = "$env:USERPROFILE\.claude\skills\wolftac-integrate\SKILL.md" }
    @{ Src = "canonical\wolftac-integrate\scripts\linear_bot.py"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-integrate\scripts\linear_bot.py" }
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
    foreach ($file in Get-ChildItem -LiteralPath $srcRoot -File -Recurse) {
        $relative = [IO.Path]::GetRelativePath($srcRoot, $file.FullName)
        $Pairs += @{ Src = (Join-Path $tree.Src $relative); Dst = (Join-Path $tree.Dst $relative) }
    }
}

# Claude Code inside WSL reads the LINUX home (~/.claude/skills), not the Windows one.
# When WSL is present, add the canonical skills as targets there too (dynamic home
# detection so this ports to other machines, e.g. jon-omen, unchanged).
$wslHome = $null
try { $wslHome = (& wsl.exe -d Ubuntu -- sh -c 'echo $HOME' 2>$null | Select-Object -First 1) } catch {}
if ($wslHome) { $wslHome = ($wslHome -replace "`0", "").Trim() }
if ($wslHome -and $wslHome.StartsWith("/")) {
    $wslSkills = "\\wsl$\Ubuntu" + ($wslHome -replace "/", "\") + "\.claude\skills"
    $Pairs += @{ Src = "canonical\wolftac-spec\SKILL.md";         Dst = "$wslSkills\wolftac-spec\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-spec\spec-template.md"; Dst = "$wslSkills\wolftac-spec\spec-template.md" }
    $Pairs += @{ Src = "canonical\wolftac-build\SKILL.md";        Dst = "$wslSkills\wolftac-build\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-review\SKILL.md";       Dst = "$wslSkills\wolftac-review\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-integrate\SKILL.md";    Dst = "$wslSkills\wolftac-integrate\SKILL.md" }
    $Pairs += @{ Src = "canonical\wolftac-integrate\scripts\linear_bot.py"; Dst = "$wslSkills\wolftac-integrate\scripts\linear_bot.py" }
    $Pairs += @{ Src = "canonical\wolftac-agent-command-patterns\SKILL.md"; Dst = "$wslSkills\wolftac-agent-command-patterns\SKILL.md" }
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
    foreach ($p in $Pairs) {
        $srcHash = Get-LfHash (Join-Path $Repo $p.Src)
        $dstHash = Get-LfHash $p.Dst
        if ($null -eq $dstHash)        { Write-Output "MISSING  $($p.Dst)"; $drift++ }
        elseif ($srcHash -ne $dstHash) { Write-Output "DRIFT    $($p.Dst)"; $drift++ }
    }
    if ($drift -eq 0) {
        Write-Output "CLEAN: all $($Pairs.Count) deployed files match the repo."
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
    $enc = New-Object System.Text.UTF8Encoding($false)
    $skipped = 0
    $forced = 0
    foreach ($p in $Pairs) {
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

if ($Package) {
    $out = "$env:USERPROFILE\Downloads\wolftac-factory-package.zip"
    if (Test-Path $out) { Remove-Item -Force $out }
    $excludedNames = @(".git", ".review-worktrees", "node_modules")
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

Write-Output "Usage: deploy.ps1 -Check | -Deploy [-Force] | -Package"
