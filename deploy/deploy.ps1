<#
Wolftac Factory deploy/drift tool.

  pwsh deploy\deploy.ps1 -Check    # diff repo vs deployed copies; exit 1 on drift
  pwsh deploy\deploy.ps1 -Deploy   # copy repo -> agent locations (LF-normalized), rebuild Desktop zips

The repo is the source of truth. Files are written with LF line endings — CRLF in a
skill's YAML frontmatter silently breaks skill discovery on Linux (WSL / Hermes).
If -Check reports drift and the DEPLOYED copy is the improvement (e.g. Hermes /learn
edited a skill in place), copy it back into the repo and commit; otherwise -Deploy
overwrites the target with the repo version.
#>
param([switch]$Check, [switch]$Deploy)

$Repo = Split-Path $PSScriptRoot -Parent
$Pairs = @(
    @{ Src = "canonical\wolftac-spec\SKILL.md";         Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\SKILL.md" }
    @{ Src = "canonical\wolftac-spec\spec-template.md"; Dst = "$env:USERPROFILE\.claude\skills\wolftac-spec\spec-template.md" }
    @{ Src = "canonical\wolftac-build\SKILL.md";        Dst = "$env:USERPROFILE\.claude\skills\wolftac-build\SKILL.md" }
    @{ Src = "canonical\wolftac-review\SKILL.md";       Dst = "$env:USERPROFILE\.claude\skills\wolftac-review\SKILL.md" }
    @{ Src = "ports\codex\wolftac-spec.md";             Dst = "$env:USERPROFILE\.codex\prompts\wolftac-spec.md" }
    @{ Src = "ports\codex\wolftac-build.md";            Dst = "$env:USERPROFILE\.codex\prompts\wolftac-build.md" }
    @{ Src = "ports\codex\wolftac-review.md";           Dst = "$env:USERPROFILE\.codex\prompts\wolftac-review.md" }
    @{ Src = "ports\hermes\wolftac-spec\SKILL.md";      Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-spec\SKILL.md" }
    @{ Src = "ports\hermes\wolftac-build\SKILL.md";     Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-build\SKILL.md" }
    @{ Src = "ports\hermes\wolftac-review\SKILL.md";    Dst = "$env:LOCALAPPDATA\hermes\skills\wolftac-review\SKILL.md" }
)

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
        exit 0
    }
    Write-Output "$drift file(s) out of sync. If the deployed copy is the improvement, copy it into the repo and commit; otherwise run -Deploy."
    exit 1
}

if ($Deploy) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    foreach ($p in $Pairs) {
        $dstDir = Split-Path $p.Dst -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force $dstDir | Out-Null }
        [IO.File]::WriteAllText($p.Dst, (Get-LfText (Join-Path $Repo $p.Src)), $enc)
        Write-Output "deployed $($p.Src)"
    }
    $zipOut = "$env:USERPROFILE\Downloads\factory-skills"
    if (-not (Test-Path $zipOut)) { New-Item -ItemType Directory -Force $zipOut | Out-Null }
    foreach ($s in @("wolftac-spec", "wolftac-build", "wolftac-review")) {
        Compress-Archive -Path (Join-Path $Repo "ports\claude-desktop\$s") -DestinationPath (Join-Path $zipOut "$s.zip") -Force
        Write-Output "zipped   $s.zip -> $zipOut"
    }
    Write-Output "Deploy complete. Restart any open agent session to pick up skill changes; re-upload the Desktop zips in the Claude app."
    exit 0
}

Write-Output "Usage: deploy.ps1 -Check | -Deploy"
