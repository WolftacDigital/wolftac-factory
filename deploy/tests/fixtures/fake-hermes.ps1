# Fixture only. No param block: preserve -p and --schedule as literal argv.
$ErrorActionPreference = 'Stop'
if (-not $env:FAKE_HERMES_HOME -or -not $env:FAKE_HERMES_LOG) { exit 2 }
ConvertTo-Json -InputObject @($args) -Compress | Add-Content -LiteralPath $env:FAKE_HERMES_LOG
if ($args.Count -ne 7 -or $args[0] -ne '-p' -or $args[2] -ne 'cron' -or
    $args[3] -ne 'edit' -or $args[5] -ne '--schedule' -or
    $args[6] -notmatch '^every ([1-9][0-9]*)m$' -or
    $args[1] -notin @('builder', 'reviewer', 'integrator')) { exit 2 }
$minutes = [int]$Matches[1]
if ($env:FAKE_HERMES_MODE -eq 'fail') { exit 1 }
if ($env:FAKE_HERMES_MODE -eq 'noop') { exit 0 }
$path = Join-Path $env:FAKE_HERMES_HOME "profiles/$($args[1])/cron/jobs.json"
$doc = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
$jobId = $args[4] # Pipeline scriptblocks have their own automatic $args.
$job = @($doc.jobs | Where-Object { $_.id -eq $jobId })
if ($job.Count -ne 1) { exit 2 }
$job[0].schedule = [pscustomobject]@{ kind = 'interval'; minutes = $minutes; display = "every ${minutes}m" }
$doc | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $path -Encoding utf8
exit 0
