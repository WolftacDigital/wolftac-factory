# WD-584 offline acceptance suite. No live tasks, credentials, ACL writes or Apply.
# The sole Apply call proves refusal AFTER verifying this process is non-elevated.
[CmdletBinding()]
param([string]$Repo = (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent),
      [string]$Scratch = (Join-Path ([IO.Path]::GetTempPath()) ('watchdog-install-' + [guid]::NewGuid().ToString('N'))))
$ErrorActionPreference = 'Stop'
$pwsh = Join-Path $PSHOME 'pwsh.exe'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'FAIL: test requires a non-elevated session' }
Write-Host 'PASS non-elevated precondition'
New-Item -ItemType Directory -Path $Scratch | Out-Null
$script:passed = 0
function Ok([string]$Name, [bool]$Condition, [string]$Detail = '') {
    if (-not $Condition) { throw "FAIL $Name $Detail" }
    $script:passed++; Write-Host "PASS $Name"
}
function Run([string]$Script, [object[]]$Arguments) {
    $out = & $pwsh -NoProfile -NonInteractive -File $Script @Arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    [IO.File]::AppendAllText((Join-Path $Scratch 'commands.log'), "SCRIPT=$Script ARGS=$Arguments EXIT=$code`n$out`n")
    @{ Code = $code; Out = $out }
}
function Put([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Force (Split-Path $Path -Parent) | Out-Null
    [IO.File]::WriteAllText($Path, $Text.Replace("`r`n", "`n"))
}
$profile = Join-Path $Scratch 'profile'
$env:USERPROFILE = $profile; $env:HOME = $profile
$env:LOCALAPPDATA = Join-Path $profile 'AppData\Local'
$env:APPDATA = Join-Path $profile 'AppData\Roaming'
$env:HERMES_HOME = Join-Path $env:LOCALAPPDATA 'hermes'
$env:TEMP = Join-Path $Scratch 'temp'; $env:TMP = $env:TEMP; $env:TMPDIR = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP, $env:LOCALAPPDATA, $env:APPDATA | Out-Null
$fixtureEnv = Join-Path $Scratch 'fixture.env'
Put $fixtureEnv "SLACK_FACTORY_BOT_TOKEN=fixture-only-584`nLINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=$([datetimeoffset]::UtcNow.AddDays(10).ToString('o'))`n"
$cron = Join-Path $Scratch 'cron'
Put (Join-Path $cron 'ticker_heartbeat') "tick`n"
Put (Join-Path $cron 'jobs.json') (@{jobs=@(@{name='Wolftac Factory Build Worker';enabled=$true;paused=$false;last_status='ok';schedule=@{minutes=120};next_run_at=[datetimeoffset]::UtcNow.AddDays(1).ToString('o')})} | ConvertTo-Json -Depth 8)
$installer = Join-Path $Repo 'deploy\install-watchdog-task.ps1'
$inst = Join-Path $Scratch 'inst'; $inst2 = Join-Path $Scratch 'inst2'
$xmlPath = Join-Path $Scratch 'task.xml'; $xml2 = Join-Path $Scratch 'task2.xml'
$common = @('-OperatorUser','FIXTURE\operator','-SourceRoot',$Repo,'-RepoRoot',$Repo,
            '-CronDir',$cron,'-EnvFile',$fixtureEnv,'-PwshPath',$pwsh)
$r = Run $installer ($common + @('-InstallDir',$inst,'-TaskXmlOut',$xmlPath))
Ok 'AC-1 plan succeeds' ($r.Code -eq 0) $r.Out
foreach ($term in @('PLAN','FIXTURE\operator','S4U','LeastPrivilege',(Join-Path $inst 'deploy\factory-watchdog.ps1'))) {
    Ok "AC-1 plan contains $term" ($r.Out.Contains($term))
}
Ok 'AC-1 install directory absent' (-not (Test-Path $inst))
[xml]$doc = [IO.File]::ReadAllText($xmlPath)
Ok 'AC-2 principal' ($doc.Task.Principals.Principal.UserId -ceq 'FIXTURE\operator')
Ok 'AC-2 S4U' ($doc.Task.Principals.Principal.LogonType -ceq 'S4U')
Ok 'AC-2 LeastPrivilege' ($doc.Task.Principals.Principal.RunLevel -ceq 'LeastPrivilege')
Ok 'AC-2 no password or SYSTEM' ($doc.OuterXml -notmatch 'Password|SYSTEM')
Ok 'AC-2 command' ($doc.Task.Actions.Exec.Command -eq $pwsh)
Ok 'AC-2 arguments' ($doc.Task.Actions.Exec.Arguments.Contains('-File "' + (Join-Path $inst 'deploy\factory-watchdog.ps1') + '"') -and $doc.Task.Actions.Exec.Arguments.Contains('-RepoRoot "' + $Repo + '"') -and $doc.Task.Actions.Exec.Arguments.Contains('-CronDir') -and $doc.Task.Actions.Exec.Arguments.Contains('-EnvFile'))
Ok 'AC-2 interval/settings' ($doc.Task.Triggers.TimeTrigger.Repetition.Interval -eq 'PT15M' -and $doc.Task.Settings.MultipleInstancesPolicy -eq 'IgnoreNew' -and $doc.Task.Settings.DisallowStartIfOnBatteries -eq 'false' -and $doc.Task.Settings.StopIfGoingOnBatteries -eq 'false' -and $doc.Task.Settings.StartWhenAvailable -eq 'true')
$files = @('factory-watchdog.ps1','deploy.ps1','junctions.json','approval-policy.json','junction-lib.ps1')
Ok 'AC-3 exactly five copy lines' ([regex]::Matches($r.Out,'(?m)^PLAN COPY ').Count -eq 5)
foreach ($name in $files) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $Repo "deploy\$name") -Algorithm SHA256).Hash
    Ok "AC-3 hash $name" ($r.Out.Contains("SHA256=$hash"))
}
Ok 'AC-3 owner/inheritance/three ACEs' ($r.Out.Contains('owner=BUILTIN\Administrators inheritance=protected') -and [regex]::Matches($r.Out,'(?m)^PLAN ACE ').Count -eq 3 -and $r.Out.Contains('FIXTURE\operator ReadAndExecute'))
$stubLog = Join-Path $Scratch 'stub-called.log'
$global:WD584StubLog = $stubLog
function Register-ScheduledTask { Add-Content $global:WD584StubLog 'Register-ScheduledTask'; throw 'forbidden' }
function Get-ScheduledTask { Add-Content $global:WD584StubLog 'Get-ScheduledTask'; throw 'forbidden' }
function Unregister-ScheduledTask { Add-Content $global:WD584StubLog 'Unregister-ScheduledTask'; throw 'forbidden' }
function New-ScheduledTaskPrincipal { Add-Content $global:WD584StubLog 'New-ScheduledTaskPrincipal'; throw 'forbidden' }
function Set-Acl { Add-Content $global:WD584StubLog 'Set-Acl'; throw 'forbidden' }
& $installer -OperatorUser 'FIXTURE\operator' -SourceRoot $Repo -RepoRoot $Repo `
    -CronDir $cron -EnvFile $fixtureEnv -PwshPath $pwsh -InstallDir $inst -TaskXmlOut $xmlPath | Out-Null
Ok 'AC-4 in-session default succeeds with throwing stubs' ($LASTEXITCODE -eq 0 -and -not (Test-Path $stubLog))
$r = Run $installer ($common + @('-InstallDir',$inst,'-Apply','-TaskXmlOut',(Join-Path $Scratch 'refused.xml')))
Ok 'AC-5 refusal before every write' ($r.Code -eq 2 -and $r.Out.Contains('APPLY REFUSED: requires an elevated session') -and -not (Test-Path $inst) -and -not (Test-Path (Join-Path $Scratch 'refused.xml'))) $r.Out
$r = Run $installer ($common + @('-InstallDir',$inst2,'-TaskXmlOut',$xml2))
Ok 'AC-6 matching second plan' ($r.Code -eq 0 -and -not (Test-Path $inst2))
New-Item -ItemType Directory -Path $inst2 | Out-Null
$r = Run $installer ($common + @('-InstallDir',$inst2,'-Verify','-TaskXmlIn',$xml2))
foreach ($line in @('PASS task principal','PASS logon type','PASS action path','FAIL install-dir owner','FAIL operator cannot write')) { Ok "AC-6 $line" ($r.Out.Contains($line)) $r.Out }
Ok 'AC-6 nonzero for ordinary writable folder' ($r.Code -ne 0)
[xml]$tampered = [IO.File]::ReadAllText($xml2)
$tampered.Task.Principals.Principal.UserId = 'SYSTEM'
$bad = Join-Path $Scratch 'system.xml'; $tampered.Save($bad)
$r = Run $installer ($common + @('-InstallDir',$inst2,'-Verify','-TaskXmlIn',$bad))
Ok 'AC-6 rejects SYSTEM principal' ($r.Code -ne 0 -and $r.Out.Contains('FAIL task principal'))
[xml]$tampered = [IO.File]::ReadAllText($xml2)
$tampered.Task.Actions.Exec.Arguments = $tampered.Task.Actions.Exec.Arguments.Replace((Join-Path $inst2 'deploy\factory-watchdog.ps1'),(Join-Path $Repo 'deploy\factory-watchdog.ps1'))
$bad = Join-Path $Scratch 'checkout-action.xml'; $tampered.Save($bad)
$r = Run $installer ($common + @('-InstallDir',$inst2,'-Verify','-TaskXmlIn',$bad))
Ok 'AC-6 rejects checkout action' ($r.Out.Contains('FAIL action path'))
$r = Run $installer ($common + @('-InstallDir',$inst2,'-Verify','-TaskXmlIn',$xmlPath))
Ok 'AC-6 rejects mismatched inst/inst2 XML' ($r.Out.Contains('FAIL action path'))

# Full source fixture, not the real deployed tree. Archive committed source and overlay
# only the five installed files under test. No env/config/auth store is copied.
$checkout = Join-Path $Scratch 'checkout'
$zip = Join-Path $Scratch 'source.zip'
& git -C $Repo archive --format=zip --output=$zip HEAD
if ($LASTEXITCODE) { throw 'git archive failed' }
Expand-Archive -LiteralPath $zip -DestinationPath $checkout
foreach ($name in $files) { Copy-Item -LiteralPath (Join-Path $Repo "deploy\$name") -Destination (Join-Path $checkout "deploy\$name") -Force }
& git -C $checkout init -q -b main
& git -C $checkout -c core.autocrlf=false add -A
& git -C $checkout -c user.name=Fixture -c user.email=fixture@example.invalid commit -q -m fixture
if ($LASTEXITCODE) { throw 'fixture commit failed' }
# One actual builder-profile junction proves worker coverage; another copied worker
# target gives a non-vacuous drift row without touching live profile files.
$link = Join-Path $env:LOCALAPPDATA 'hermes\profiles\builder\skills\wolftac-build'
New-Item -ItemType Directory -Force (Split-Path $link -Parent) | Out-Null
New-Item -ItemType Junction -Path $link -Target (Join-Path $checkout 'ports\hermes\wolftac-build') | Out-Null
Put (Join-Path $checkout 'ports\hermes\wolftac-build\pending-proof.txt') "pending fixture`n"
$driftTarget = Join-Path $env:LOCALAPPDATA 'hermes\profiles\reviewer\skills\wolftac-review\SKILL.md'
Put $driftTarget "synthetic deployed difference`n"
New-Item -ItemType Directory -Force (Join-Path $inst 'deploy') | Out-Null
foreach ($name in $files) { Copy-Item -LiteralPath (Join-Path $checkout "deploy\$name") -Destination (Join-Path $inst "deploy\$name") }
$wdArgs = @('-DryRun','-CronDir',$cron,'-EnvFile',$fixtureEnv,'-UserProfile',$profile,'-LearnBackupRoot',(Join-Path $Scratch 'backups'))
$native = Run (Join-Path $checkout 'deploy\factory-watchdog.ps1') $wdArgs
$installed = Run (Join-Path $inst 'deploy\factory-watchdog.ps1') ($wdArgs + @('-RepoRoot',$checkout))
function Rows([string]$Text) { (($Text -split "`r?`n" | Where-Object { $_ -match '^  - |^DRIFT|^LEARN' }) -join "`n") }
Ok 'AC-7 identical finding/drift/learn rows' ((Rows $native.Out) -ceq (Rows $installed.Out)) ($native.Out + $installed.Out)
Ok 'AC-7 same exit code' ($native.Code -eq $installed.Code)
Ok 'AC-13c non-vacuous worker junction PENDING' ($installed.Out.Contains('PENDING  ports/hermes/wolftac-build/pending-proof.txt')) $installed.Out
Ok 'AC-13c non-vacuous worker copy drift' ($installed.Out -match 'DRIFT\s+.*profiles.reviewer.skills.wolftac-review.SKILL.md')
Ok 'AC-8 checkout has no stale rows' (-not $native.Out.Contains('WATCHDOG-STALE'))
$installedScript = Join-Path $inst 'deploy\factory-watchdog.ps1'
[IO.File]::AppendAllText($installedScript,"`n# scratch modification`n")
$r = Run $installedScript ($wdArgs + @('-RepoRoot',$checkout))
Ok 'AC-8 exactly one stale watchdog row' ([regex]::Matches($r.Out,'WATCHDOG-STALE deploy/factory-watchdog.ps1').Count -eq 1 -and $r.Code -eq $native.Code) $r.Out
Copy-Item -LiteralPath (Join-Path $checkout 'deploy\factory-watchdog.ps1') -Destination $installedScript -Force
$helper = Join-Path $inst 'deploy\junction-lib.ps1'
Remove-Item -LiteralPath $helper
$r = Run $installedScript ($wdArgs + @('-RepoRoot',$checkout))
Ok 'AC-13a missing helper cannot compare' ($r.Out.Contains('cannot compare deploy/junction-lib.ps1')) $r.Out
Copy-Item -LiteralPath (Join-Path $checkout 'deploy\junction-lib.ps1') -Destination $helper
[IO.File]::AppendAllText($helper,"`n# scratch modification`n")
$r = Run $installedScript ($wdArgs + @('-RepoRoot',$checkout))
Ok 'AC-13b exactly one stale helper row' ([regex]::Matches($r.Out,'WATCHDOG-STALE deploy/junction-lib.ps1').Count -eq 1)
Copy-Item -LiteralPath (Join-Path $checkout 'deploy\junction-lib.ps1') -Destination $helper -Force
$repoHelper = Join-Path $checkout 'deploy\junction-lib.ps1'
$helperBytes = [IO.File]::ReadAllBytes($repoHelper)
$marker = Join-Path $Scratch 'untrusted-helper-loaded'
Put $repoHelper "[IO.File]::WriteAllText('$($marker.Replace("'","''"))','loaded')`n"
$r = Run $installedScript ($wdArgs + @('-RepoRoot',$checkout))
Ok 'AC-13d repo helper never loaded' (-not (Test-Path $marker) -and $r.Out.Contains('WATCHDOG-STALE deploy/junction-lib.ps1')) $r.Out
[IO.File]::WriteAllBytes($repoHelper,$helperBytes)
$r = Run $installedScript ($wdArgs + @('-RepoRoot',$checkout,'-SkipDrift'))
Ok 'AC-8 SkipDrift skips stale comparison' (-not $r.Out.Contains('WATCHDOG-STALE') -and $r.Out.Contains('PENDING'))

# AC-9 positive control plus production Git hardening on an independent fixture.
$hookRepo = Join-Path $Scratch 'hookrepo'
New-Item -ItemType Directory $hookRepo | Out-Null
Put (Join-Path $hookRepo 'tracked.txt') "one`n"
& git -C $hookRepo init -q -b main
& git -C $hookRepo add tracked.txt
& git -C $hookRepo -c user.name=Fixture -c user.email=fixture@example.invalid commit -q -m fixture
Put (Join-Path $hookRepo 'tracked.txt') "one`ntwo`n"
$hookMarker = Join-Path $Scratch 'fsmonitor-ran'
$hook = Join-Path $Scratch 'hook.sh'
Put $hook ("#!/bin/sh`nprintf hit > '" + ($hookMarker -replace '\\','/') + "'`nprintf '\\0'`n")
& git -C $hookRepo config core.fsmonitor ($hook -replace '\\','/')
& git -C $hookRepo status --porcelain | Out-Null
Ok 'AC-9 positive control executes fsmonitor' (Test-Path $hookMarker)
Remove-Item -LiteralPath $hookMarker
$index = Join-Path $hookRepo '.git\index'
$beforeIndex = (Get-Item $index).LastWriteTimeUtc.Ticks
$standin = Join-Path $Scratch 'standin.ps1'
Put $standin ("`$JunctionedRoots = @('" + $hookRepo.Replace("'","''") + "')`n")
$r = Run (Join-Path $Repo 'deploy\factory-watchdog.ps1') ($wdArgs + @('-SkipDrift','-DeployScript',$standin))
Ok 'AC-9 PENDING remains observed' ($r.Out.Contains('PENDING  tracked.txt')) $r.Out
Ok 'AC-9 hook suppressed' (-not (Test-Path $hookMarker))
Ok 'AC-9 index timestamp unchanged' ((Get-Item $index).LastWriteTimeUtc.Ticks -eq $beforeIndex)
# Literal dot-source behavior, under redirected profile roots and only fixture source.
$dot = Join-Path $Scratch 'dot.ps1'
Put $dot "param(`$Script,`$Root)`nif (`$Root) { . `$Script -RepoRoot `$Root } else { . `$Script }; Write-Output ('RESOLVED=' + `$Repo)`n"
$r = Run $dot @((Join-Path $checkout 'deploy\deploy.ps1'))
Ok 'AC-10 no-argument Repo default' ($r.Out.Contains('RESOLVED=' + $checkout)) $r.Out
$repoRootProbe = Join-Path $Scratch 'repo-root-probe'
New-Item -ItemType Directory -Path $repoRootProbe | Out-Null
$r = Run $dot @((Join-Path $checkout 'deploy\deploy.ps1'),$repoRootProbe)
Ok 'AC-10 explicit RepoRoot' ($r.Out.Contains('RESOLVED=' + $repoRootProbe)) $r.Out
Write-Host "RESULT pass=$script:passed fail=0"
Write-Host "PROOF $Scratch"
# Keep synthetic proof outside the worktree. No deployment cleanup is performed.
exit 0
