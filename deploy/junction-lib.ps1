<#
Shared helpers for the Hermes skill-junction migration (WD-349).

Dot-sourced by deploy\junction-setup.ps1 and deploy\junction-teardown.ps1.
Contains no top-level side effects, so it is safe to source from either script.

The design rule these helpers exist to enforce: NOTHING is ever deleted before
every pair has been validated. Both scripts run a full validate pass, print the
plan, and only then act — so running them in the wrong order, or against a tree
that has drifted, is a refusal rather than a data loss.
#>

Set-StrictMode -Version Latest

# Files that must never live inside a junctioned tree: the junction publishes the
# repo subtree at an agent-writable path (and vice versa), so a credential in
# there would become reachable from both sides and committable by accident.
$script:CredentialNamePatterns = @(
    '^\.env($|\..*)', '^auth\.json$', '^credentials(\..*)?$', '^id_(rsa|ed25519|ecdsa)$',
    '\.pem$', '\.pfx$', '\.p12$', '\.key$', '^\.npmrc$', '^\.netrc$', '_token(\..*)?$'
)

function Get-JunctionManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [string]$SkillsRoot
    )
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "junction manifest not found: $ManifestPath"
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $root = if ($SkillsRoot) { $SkillsRoot } else { [Environment]::ExpandEnvironmentVariables($manifest.skills_root) }
    $root = [IO.Path]::GetFullPath($root)
    $pairs = @()
    foreach ($pair in @($manifest.pairs)) {
        $pairs += [pscustomobject]@{
            Name     = $pair.name
            RepoRel  = $pair.repo
            RepoPath = [IO.Path]::GetFullPath((Join-Path $RepoRoot ($pair.repo -replace '/', [IO.Path]::DirectorySeparatorChar)))
            LinkPath = [IO.Path]::GetFullPath((Join-Path $root $pair.name))
        }
    }
    return [pscustomobject]@{ SkillsRoot = $root; Pairs = $pairs }
}

# Read a file as text with LF line endings. Mirrors deploy.ps1's Get-LfText so the
# equality test used here is the SAME test deploy.ps1 -Check uses for drift.
function Get-JunctionLfText([string]$Path) {
    $raw = [IO.File]::ReadAllText($Path)
    return ($raw -replace "`r`n", "`n") -replace "`r", "`n"
}

function Get-JunctionLfHash([string]$Path) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-JunctionLfText $Path))
    $sha = [Security.Cryptography.SHA256]::Create()
    return [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "")
}

function Get-TreeHashMap([string]$Root) {
    $map = @{}
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $map }
    foreach ($file in Get-ChildItem -LiteralPath $Root -File -Recurse -Force) {
        $relative = [IO.Path]::GetRelativePath($Root, $file.FullName)
        $map[$relative] = Get-JunctionLfHash $file.FullName
    }
    return $map
}

# Returns the list of relative paths that differ between two trees (missing on
# either side counts as a difference). Empty list means byte-identical after LF
# normalization — the ONLY state in which junctioning is non-destructive.
function Compare-Trees([string]$Left, [string]$Right) {
    $a = Get-TreeHashMap $Left
    $b = Get-TreeHashMap $Right
    $names = New-Object 'System.Collections.Generic.SortedSet[string]'
    foreach ($k in $a.Keys) { [void]$names.Add($k) }
    foreach ($k in $b.Keys) { [void]$names.Add($k) }
    $diff = @()
    foreach ($name in $names) {
        if (-not $a.ContainsKey($name)) { $diff += "only in ${Right}: $name"; continue }
        if (-not $b.ContainsKey($name)) { $diff += "only in ${Left}: $name"; continue }
        if ($a[$name] -ne $b[$name]) { $diff += "differs: $name" }
    }
    return $diff
}

function Test-IsReparsePoint([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return $false }
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Get-ReparseTarget([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item -or -not $item.LinkTarget) { return $null }
    try { return [IO.Path]::GetFullPath($item.LinkTarget) } catch { return $item.LinkTarget }
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    return ($A.TrimEnd('\', '/') -ieq $B.TrimEnd('\', '/'))
}

# Quality gate: nothing outside the linked skill subtree may become reachable
# through the link, and no credential-bearing file may sit inside it.
function Test-JunctionTreeSafety([string]$Root) {
    $problems = @()
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $problems }
    foreach ($entry in Get-ChildItem -LiteralPath $Root -Recurse -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $problems += "reparse point inside the junctioned tree would widen its reach: $($entry.FullName)"
            continue
        }
        if ($entry.PSIsContainer) { continue }
        foreach ($pattern in $script:CredentialNamePatterns) {
            if ($entry.Name -match $pattern) {
                $problems += "credential-bearing file inside the junctioned tree: $($entry.FullName)"
                break
            }
        }
    }
    return $problems
}

# Delete a directory junction WITHOUT touching its contents. Remove-Item -Recurse
# on a reparse point has historically deleted through the link on some Windows /
# PowerShell combinations; Directory.Delete removes only the link entry.
function Remove-DirectoryLink([string]$Path) {
    if (-not (Test-IsReparsePoint $Path)) {
        throw "refusing to unlink $Path - it is not a reparse point"
    }
    [IO.Directory]::Delete($Path, $false)
}

# Materialize a real directory from the repo tree, LF-normalized, exactly as
# deploy.ps1 -Deploy would write it.
function Copy-TreeLf([string]$Source, [string]$Destination) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        New-Item -ItemType Directory -Force $Destination | Out-Null
    }
    foreach ($file in Get-ChildItem -LiteralPath $Source -File -Recurse -Force) {
        $relative = [IO.Path]::GetRelativePath($Source, $file.FullName)
        $target = Join-Path $Destination $relative
        $targetDir = Split-Path $target -Parent
        if (-not (Test-Path -LiteralPath $targetDir -PathType Container)) {
            New-Item -ItemType Directory -Force $targetDir | Out-Null
        }
        [IO.File]::WriteAllText($target, (Get-JunctionLfText $file.FullName), $enc)
    }
}
