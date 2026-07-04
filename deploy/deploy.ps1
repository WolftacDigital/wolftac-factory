<#
Wolftac Factory deploy/drift tool.

  pwsh deploy\deploy.ps1 -Check    # diff repo vs deployed copies; exit 1 on drift
  pwsh deploy\deploy.ps1 -Deploy   # copy repo -> agent locations, rebuild Desktop zips

The repo is the source of truth. If -Check reports drift and the DEPLOYED copy is the
improvement (e.g. Hermes /learn edited a skill in place), copy it back into the repo
and commit; otherwise -Deploy overwrites the target with the repo version.
#>
param([switch]$Check, [switch]$Deploy)

$Repo = Split-Path $PSScriptRoot -Parent
$Pairs = @(
    @{ Src = "canonical\spec\SKILL.md";         Dst = "$env:USERPROFILE\.claude\skills\spec\SKILL.md" }
    @{ Src = "canonical\spec\spec-template.md"; Dst = "$env:USERPROFILE\.claude\skills\spec\spec-template.md" }
    @{ Src = "canonical\build\SKILL.md";        Dst = "$env:USERPROFILE\.claude\skills\build\SKILL.md" }
    @{ Src = "canonical\review\SKILL.md";       Dst = "$env:USERPROFILE\.claude\skills\review\SKILL.md" }
    @{ Src = "ports\codex\spec.md";             Dst = "$env:USERPROFILE\.codex\prompts\spec.md" }
    @{ Src = "ports\codex\build.md";            Dst = "$env:USERPROFILE\.codex\prompts\build.md" }
    @{ Src = "ports\codex\review.md";           Dst = "$env:USERPROFILE\.codex\prompts\review.md" }
    @{ Src = "ports\hermes\spec\SKILL.md";      Dst = "$env:LOCALAPPDATA\hermes\skills\spec\SKILL.md" }
    @{ Src = "ports\hermes\build\SKILL.md";     Dst = "$env:LOCALAPPDATA\hermes\skills\build\SKILL.md" }
    @{ Src = "ports\hermes\review\SKILL.md";    Dst = "$env:LOCALAPPDATA\hermes\skills\review\SKILL.md" }
)

# Claude Code inside WSL reads the LINUX home (~/.claude/skills), not the Windows one.
# When WSL is present, add the canonical skills as targets there too (dynamic home
# detection so this ports to other machines, e.g. jon-omen, unchanged).
$wslHome = $null
try { $wslHome = (& wsl.exe -d Ubuntu -- sh -c 'echo $HOME' 2>$null | Select-Object -First 1) } catch {}
if ($wslHome) { $wslHome = ($wslHome -replace "`0", "").Trim() }
if ($wslHome -and $wslHome.StartsWith("/")) {
    $wslSkills = "\\wsl$\Ubuntu" + ($wslHome -replace "/", "\") + "\.claude\skills"
    $Pairs += @{ Src = "canonical\spec\SKILL.md";         Dst = "$wslSkills\spec\SKILL.md" }
    $Pairs += @{ Src = "canonical\spec\spec-template.md"; Dst = "$wslSkills\spec\spec-template.md" }
    $Pairs += @{ Src = "canonical\build\SKILL.md";        Dst = "$wslSkills\build\SKILL.md" }
    $Pairs += @{ Src = "canonical\review\SKILL.md";       Dst = "$wslSkills\review\SKILL.md" }
}

function Get-ContentHash($Path) {
    if (Test-Path $Path) { (Get-FileHash $Path -Algorithm SHA256).Hash } else { $null }
}

if ($Check) {
    $drift = 0
    foreach ($p in $Pairs) {
        $srcHash = Get-ContentHash (Join-Path $Repo $p.Src)
        $dstHash = Get-ContentHash $p.Dst
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
    foreach ($p in $Pairs) {
        $dstDir = Split-Path $p.Dst -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force $dstDir | Out-Null }
        Copy-Item (Join-Path $Repo $p.Src) $p.Dst -Force
        Write-Output "deployed $($p.Src)"
    }
    $zipOut = "$env:USERPROFILE\Downloads\factory-skills"
    if (-not (Test-Path $zipOut)) { New-Item -ItemType Directory -Force $zipOut | Out-Null }
    foreach ($s in @("spec", "build", "review")) {
        Compress-Archive -Path (Join-Path $Repo "ports\claude-desktop\$s") -DestinationPath (Join-Path $zipOut "$s.zip") -Force
        Write-Output "zipped   $s.zip -> $zipOut"
    }
    Write-Output "Deploy complete. Desktop zips must be re-uploaded in the Claude app to take effect."
    exit 0
}

Write-Output "Usage: deploy.ps1 -Check | -Deploy"
