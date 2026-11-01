<#
WD-576: operator-only, dry-run-first worker safety-net reconciliation.
Never write jobs.json here: Hermes owns its lock, atomic save and next-run time.
Build/review must pass a scratch -HermesHome and fake -HermesCommand.
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$HermesHome = "$env:LOCALAPPDATA\hermes",
    [string]$HermesCommand = "$HermesHome\bin\hermes.exe",
    [ValidateRange(1, 2147483647)][int]$SafetyNetMinutes = 120
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-Target([string]$Path, [string]$Id) {
    try {
        $doc = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json
        $matches = @($doc.jobs | Where-Object { $_.id -eq $Id })
        if ($matches.Count -ne 1) { return $null }
        return $matches[0]
    } catch { return $null }
}
function Value($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}

$targets = @(
    @{ Profile = 'builder'; Id = '9b7b5987bcd6' },
    @{ Profile = 'builder'; Id = '1dd429fe03b2' },
    @{ Profile = 'reviewer'; Id = '8e5c37a23b91' },
    @{ Profile = 'integrator'; Id = '4e7a4d9c5fa4' }
)
$plans = @()
$invalid = $false
# Validate the ENTIRE target set before any backup or command invocation.
foreach ($target in $targets) {
    $key = "$($target.Profile)/$($target.Id)"
    $path = Join-Path $HermesHome "profiles/$($target.Profile)/cron/jobs.json"
    $job = Read-Target $path $target.Id
    if ($null -eq $job) {
        Write-Output "MISSING ${key}: not found in $path"
        $invalid = $true
        continue
    }
    $name = Value $job 'name'
    if ($name -notlike 'Wolftac Factory *') {
        Write-Output "REFUSE  ${key}: name '$name' does not match 'Wolftac Factory *'"
        $invalid = $true
        continue
    }
    $schedule = Value $job 'schedule'
    $kind = Value $schedule 'kind'
    $minutes = Value $schedule 'minutes'
    if ($kind -eq 'interval' -and $minutes -eq $SafetyNetMinutes) {
        Write-Output "OK      $key ${name}: already every ${SafetyNetMinutes}m"
        continue
    }
    $current = if ($kind -eq 'interval') { "every ${minutes}m" } else { "kind $kind" }
    Write-Output "PLAN    $key ${name}: $current -> every ${SafetyNetMinutes}m"
    $plans += @{ Profile = $target.Profile; Id = $target.Id; Key = $key; Name = $name; Path = $path }
}
if ($invalid) { exit 1 }
if (-not $Apply) { exit 0 }

$backedUp = @{}
$failed = $false
foreach ($plan in $plans) {
    $key = $plan.Key
    $name = $plan.Name
    try {
        if (-not $backedUp.ContainsKey($plan.Profile)) {
            $backup = "$($plan.Path).bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')-pre-safety-net"
            # Never overwrite a previous rollback artifact on a same-second retry.
            [IO.File]::Copy($plan.Path, $backup, $false)
            $backedUp[$plan.Profile] = $true
        }
        $LASTEXITCODE = 0
        # Do not forward CLI output, which could include full job data/prompts.
        $null = & $HermesCommand -p $plan.Profile cron edit $plan.Id --schedule "every ${SafetyNetMinutes}m" 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0) {
            Write-Output "FAILED  $key ${name}: hermes exit code $code"
            $failed = $true
            continue
        }
        $job = Read-Target $plan.Path $plan.Id
        $schedule = Value $job 'schedule'
        $kind = Value $schedule 'kind'
        $minutes = Value $schedule 'minutes'
        if ($kind -ne 'interval' -or $minutes -ne $SafetyNetMinutes) {
            Write-Output "FAILED  $key ${name}: read-back kind '$kind' minutes '$minutes'"
            $failed = $true
            continue
        }
        Write-Output "APPLIED $key ${name}: every ${SafetyNetMinutes}m (verified)"
    } catch {
        # Do not echo exception text from a CLI or malformed job document.
        Write-Output "FAILED  $key ${name}: backup or hermes invocation failed"
        $failed = $true
    }
}
exit ($failed ? 1 : 0)
