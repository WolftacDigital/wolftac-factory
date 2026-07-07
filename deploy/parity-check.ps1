<#
Checks cross-port factory guardrail invariants.

Defaults to deploy/invariants.json and resolves relative file paths from the repo
root, which is the parent directory of this script's directory.
#>
param(
    [string]$InvariantsPath = (Join-Path $PSScriptRoot "invariants.json"),
    [string]$RepoRoot = (Split-Path $PSScriptRoot -Parent)
)

$ErrorActionPreference = "Stop"
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$InvariantsPath = [IO.Path]::GetFullPath($InvariantsPath)

if (-not (Test-Path -LiteralPath $InvariantsPath -PathType Leaf)) {
    Write-Output "ERROR: invariants file not found: $InvariantsPath"
    Write-Output "PARITY FAILED"
    exit 1
}

try {
    $invariants = @(Get-Content -LiteralPath $InvariantsPath -Raw | ConvertFrom-Json)
} catch {
    Write-Output "ERROR: failed to read invariants JSON: $($_.Exception.Message)"
    Write-Output "PARITY FAILED"
    exit 1
}

$regexOptions = [Text.RegularExpressions.RegexOptions]::IgnoreCase `
    -bor [Text.RegularExpressions.RegexOptions]::Multiline `
    -bor [Text.RegularExpressions.RegexOptions]::Singleline `
    -bor [Text.RegularExpressions.RegexOptions]::CultureInvariant

$missing = New-Object System.Collections.Generic.List[string]
$errors = New-Object System.Collections.Generic.List[string]
$files = New-Object 'System.Collections.Generic.HashSet[string]'

foreach ($invariant in $invariants) {
    if (-not $invariant.id) {
        $errors.Add("ERROR: invariant is missing id")
        continue
    }
    if (-not $invariant.checks) {
        $errors.Add("ERROR: invariant '$($invariant.id)' has no checks")
        continue
    }

    foreach ($check in @($invariant.checks)) {
        if (-not $check.file -or -not $check.pattern) {
            $errors.Add("ERROR: invariant '$($invariant.id)' has a check missing file or pattern")
            continue
        }

        $path = if ([IO.Path]::IsPathRooted($check.file)) {
            [IO.Path]::GetFullPath($check.file)
        } else {
            [IO.Path]::GetFullPath((Join-Path $RepoRoot $check.file))
        }
        [void]$files.Add($check.file)

        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $errors.Add("ERROR: $($check.file) referenced by invariant '$($invariant.id)' does not exist")
            continue
        }

        try {
            $content = [IO.File]::ReadAllText($path)
            $content = ($content -replace "`r`n", "`n") -replace "`r", "`n"
            if (-not [Text.RegularExpressions.Regex]::IsMatch($content, [string]$check.pattern, $regexOptions)) {
                $missing.Add("MISSING: $($check.file) :: $($invariant.id) - $($invariant.description)")
            }
        } catch {
            $errors.Add("ERROR: failed checking $($check.file) for '$($invariant.id)': $($_.Exception.Message)")
        }
    }
}

if ($errors.Count -gt 0 -or $missing.Count -gt 0) {
    foreach ($line in $errors) { Write-Output $line }
    foreach ($line in $missing) { Write-Output $line }
    Write-Output "PARITY FAILED"
    exit 1
}

Write-Output "PARITY OK: $($invariants.Count) invariants across $($files.Count) files"
exit 0
