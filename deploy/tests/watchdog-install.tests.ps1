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
Ok 'WD-641 AC-7 ten-minute limit in XML and plan' ($doc.Task.Settings.ExecutionTimeLimit -ceq 'PT10M' -and $r.Out -match '(?m)^PLAN TASK .* limit=PT10M\r?$')
foreach ($invalid in @(@('-FilesOnly'), @('-FilesOnly','-Verify'), @('-FilesOnly','-Apply','-Verify'))) {
    $invalidResult = Run $installer ($common + @('-InstallDir',$inst) + $invalid)
    Ok 'WD-641 AC-6 invalid FilesOnly combination refused' ($invalidResult.Code -eq 3 -and $invalidResult.Out.Contains('APPLY REFUSED: FilesOnly requires Apply without Verify')) $invalidResult.Out
}
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

# ── AC-15 / AC-16 (TP12 / TP13): offline path-security controls ─────────────────
# Synthetic security descriptors on a fictitious Q:\ volume, fed to the shared pure
# assessor, plus a stub Io whose side-effect seams only append milestones. Nothing
# here changes an ACL, registers a task or runs a real Apply; -LibraryOnly returns
# before any plan, file or task work. Real-handle checks below only read scratch.
$caseLog = Join-Path $Scratch 'tp12-tp13-cases.log'
function Stub-Io([hashtable]$State, [hashtable]$Hooks = @{}) {
    $io = [pscustomobject]@{ State = $State; Hooks = $Hooks; Log = [Collections.Generic.List[string]]::new(); Next = 0; TaskXml = '' }
    $io | Add-Member ScriptMethod Fire { param([string]$Key) if ($this.Hooks.ContainsKey($Key)) { & $this.Hooks[$Key] $this.State } }
    $io | Add-Member ScriptMethod Info {
        param([string]$Path)
        $n = $this.State[$Path]
        if ($null -eq $n) { return @{ Exists = $false } }
        if ($n.ContainsKey('Unreadable')) { return @{ Exists = $null } }
        @{ Exists = $true; Sddl = $n.Sddl; Reparse = $n.Reparse; Identity = $n.Identity
           FinalPath = $(if ($n.ContainsKey('FinalPath')) { $n.FinalPath } else { $Path })
           Links = $(if ($n.ContainsKey('Links')) { $n.Links } else { 1 }) }
    }
    $io | Add-Member ScriptMethod Milestone { param([string]$Text) $this.Log.Add("milestone $Text") }
    $io | Add-Member ScriptMethod Read { param([string]$Path, [string]$Kind) $this.Fire("Read:$Path"); $this.Log.Add("read $Path"); $this.Info($Path) }
    $io | Add-Member ScriptMethod Hold {
        param([string]$Path, [string]$Kind, [bool]$Writable)
        $this.Fire("Hold:$Path"); $this.Log.Add("hold $Path")
        $i = $this.Info($Path); if ($i.Exists) { $i.Token = $Path }; $i
    }
    $io | Add-Member ScriptMethod Hash { param([string]$Token) $this.Log.Add("hash $Token"); [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([byte[]]$this.State[$Token].Bytes)) }
    $io | Add-Member ScriptMethod Release { $this.Log.Add('release') }
    $io | Add-Member ScriptMethod CreateDirectory {
        param([string]$Path, [string]$Sddl)
        $this.Fire("Create:$Path"); $this.Log.Add("create $Path")
        if ($this.State.ContainsKey($Path)) { return 'Exists' }
        $this.Next++; $this.State[$Path] = @{ Sddl = $Sddl; Reparse = $false; Identity = "V1:new-$($this.Next)" }; 'Created'
    }
    $io | Add-Member ScriptMethod SetSecurity { param([string]$Token, [string]$Sddl) $this.Log.Add("setsecurity $Token"); $this.State[$Token].Sddl = $Sddl }
    $io | Add-Member ScriptMethod WriteFile {
        param([string]$Path, [byte[]]$Bytes, [string]$Sddl)
        $this.Log.Add("write $Path"); $this.Next++
        $this.State[$Path] = @{ Sddl = $Sddl; Reparse = $false; Identity = "V1:new-$($this.Next)"; Bytes = $Bytes; Links = 1 }
        $this.Fire("AfterWrite:$Path")
        $i = $this.Info($Path); $i.Token = $Path; $i
    }
    $io | Add-Member ScriptMethod RegisterTask {
        param([string]$Xml)
        $this.Log.Add('register'); $this.Fire('RegisterTask'); $this.TaskXml = $Xml
    }
    $io | Add-Member ScriptMethod ReadTaskXml {
        $this.Log.Add('read-task')
        if ($this.Hooks.ContainsKey('ReadTaskXml')) { return & $this.Hooks.ReadTaskXml $this.TaskXml }
        $this.TaskXml
    }
    $io
}
& {
    . $installer -OperatorUser 'FIXTURE\operator' -LibraryOnly
    $op = 'S-1-5-21-1111111111-2222222222-3333333333-1001'
    $profileIo = [pscustomobject]@{ Profile = 'C:\Users\op'; Exists = $true; SeenSid = '' }
    $profileIo | Add-Member ScriptMethod ReadOperatorProfile { param($Sid) $this.SeenSid = $Sid; $this.Profile }
    $profileIo | Add-Member ScriptMethod ProfileExists { param($Path) $this.Exists }
    $sidSet = New-WdOperatorSidSet -UserSid $op -LocalGroupSids @('S-1-5-32-544', 'S-1-5-32-545')
    Ok 'AC-15 operator SID set excludes deny-only Administrators' (-not $sidSet.Sids.Contains('S-1-5-32-544') -and $sidSet.ExcludedDenyOnly -contains 'S-1-5-32-544' -and $sidSet.Sids.Contains('S-1-5-3') -and $sidSet.Sids.Contains('S-1-5-4'))
    $vol = 'Q:\'; $pd = 'Q:\ProgramData'; $app = 'Q:\ProgramData\WolftacFactory'
    $inst = 'Q:\ProgramData\WolftacFactory\watchdog'; $dep = "$inst\deploy"
    $sd = @{
        # Shapes of the live C:\ and C:\ProgramData descriptors: Users may create
        # children (Users:Write on ProgramData, AU:AppendData on the root) but hold no
        # delete-child, DELETE, WRITE_DAC or WRITE_OWNER there.
        Root = 'O:S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464D:PAI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)(A;OICIIO;SDGXGWGR;;;AU)(A;;LC;;;AU)'
        ProgramData = 'O:SYD:PAI(A;OICIIO;GA;;;CO)(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)(A;CI;0x116;;;BU)'
        Dir = "O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;$op)"
        File = "O:BAD:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;$op)"
        # What a non-elevated operator gets by creating WolftacFactory first.
        OperatorCreated = "O:${op}D:AI(A;ID;FA;;;$op)(A;OICIIOID;GA;;;CO)(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;0x1200a9;;;BU)(A;CIID;0x116;;;BU)"
    }
    $names = @('factory-watchdog.ps1', 'deploy.ps1', 'junctions.json', 'approval-policy.json', 'junction-lib.ps1')
    $copies = @(foreach ($name in $names) { $b = [Text.Encoding]::UTF8.GetBytes("synthetic $name`n"); @{ Name = $name; Bytes = $b; Hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($b)) } })
    $hashes = @{}; foreach ($c in $copies) { $hashes[$c.Name] = $c.Hash }
    function State([switch]$Empty) {
        $s = @{ $vol = @{ Sddl = $sd.Root; Reparse = $false; Identity = 'V1:root' }; $pd = @{ Sddl = $sd.ProgramData; Reparse = $false; Identity = 'V1:pd' } }
        if (-not $Empty) {
            $s[$app] = @{ Sddl = $sd.Dir; Reparse = $false; Identity = 'V1:app' }
            $s[$inst] = @{ Sddl = $sd.Dir; Reparse = $false; Identity = 'V1:inst' }
            $s[$dep] = @{ Sddl = $sd.Dir; Reparse = $false; Identity = 'V1:deploy' }
            foreach ($c in $copies) { $s["$dep\$($c.Name)"] = @{ Sddl = $sd.File; Reparse = $false; Identity = "V1:$($c.Name)"; Links = 1; Bytes = $c.Bytes } }
        }
        $s
    }
    function Apply-Case([string]$Name, [hashtable]$State, [hashtable]$Hooks = @{}, $Sids = $sidSet, [switch]$FilesOnly) {
        $io = Stub-Io $State $Hooks
        $r = Invoke-WdApplyCore -Io $io -Path $inst -Copies $copies -SidSet $Sids -OperatorSid $op -TaskXml $doc.OuterXml -ProfileIo $profileIo -FilesOnly:$FilesOnly
        $out = $r.Lines -join "`n"
        $effects = @($io.Log | Where-Object { $_ -match '^(setsecurity|write|register)' })
        [IO.File]::AppendAllText($caseLog, "CASE $Name EXIT=$($r.Code) EFFECTS=$($effects.Count)`n$out`nLOG: $($io.Log -join ' | ')`n`n")
        Write-Host "  [$Name] exit=$($r.Code) effects=$($effects.Count) :: $(($r.Lines | Select-Object -First 3) -join ' || ')"
        @{ Code = $r.Code; Out = $out; Log = @($io.Log); Effects = $effects }
    }
    function Refused([string]$Name, [hashtable]$Case, [string]$Role, [string]$Reason) {
        Ok "$Name refused for $Role $Reason with zero protected effects" ($Case.Code -eq 3 -and
            ($Case.Out -match "(?m)^REFUSED $Role \S.*: (UNSAFE|UNKNOWN) .*\b$([regex]::Escape($Reason))\b") -and
            $Case.Out.Contains('APPLY REFUSED') -and $Case.Effects.Count -eq 0) $Case.Out
    }

    # Pure assessor: the live-shaped system ancestors are SAFE despite create-child rights.
    $chain = Get-WdChain -Path $inst
    $st = State
    $recs = @(foreach ($c in $chain) { Join-WdObservation $c ((Stub-Io $st).Info($c.Path)) })
    $a = Test-WdPathChain -Components $recs -SidSet $sidSet
    Ok 'AC-15 trusted chain with Users:Write on ProgramData is SAFE' ($a.Ok -and @($a.Results).Count -eq 10 -and ($a.Results | Where-Object Path -eq $pd).Reason -eq 'trusted') (($a.Results | Format-Table Role, Verdict, Reason -AutoSize | Out-String))
    function Assess([hashtable]$Changes, $Sids = $sidSet) {
        $s = State; foreach ($k in $Changes.Keys) { $s[$k] = $Changes[$k] }
        $rs = @(foreach ($c in $chain) { Join-WdObservation $c ((Stub-Io $s).Info($c.Path)) })
        Test-WdPathChain -Components $rs -SidSet $Sids
    }
    function Verdict($Assessment, [string]$Path) { $r = $Assessment.Results | Where-Object Path -eq $Path; "$($r.Verdict) $($r.Reason)" }
    $v = Verdict (Assess @{ $inst = @{ Sddl = ($sd.Dir + '(A;CI;0x116;;;BU)'); Reparse = $false; Identity = 'V1:inst' } }) $inst
    Ok 'AC-15 create-child on InstallDir itself is UNSAFE operator-can-write' ($v -match '^UNSAFE .*operator-can-write') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = ($sd.Dir.Replace('D:PAI', "D:PAI(D;;0xd0040;;;$op)") + '(A;;FA;;;BU)'); Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 canonical deny-before-allow removes replacement rights' ($v -eq 'SAFE trusted') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = ($sd.Dir.Replace('D:PAI', 'D:PAI(A;;FA;;;BU)') + "(D;;0xd0040;;;$op)"); Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 non-canonical allow-before-deny keeps replacement rights (ordered evaluation)' ($v -match '^UNSAFE .*delete-on-component') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = ($sd.Dir + '(A;;GA;;;BU)'); Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 GenericAll maps to replacement rights' ($v -match '^UNSAFE .*delete-on-component.*write-dac-or-owner-on-component.*delete-child-on-component') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = ($sd.Dir + '(A;OICIIO;GA;;;BU)'); Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 inherit-only ACE skipped on the object itself' ($v -eq 'SAFE trusted') $v
    $f = "$dep\deploy.ps1"
    $v = Verdict (Assess @{ $f = @{ Sddl = ($sd.File + '(A;;GW;;;AU)'); Reparse = $false; Identity = 'V1:x'; Links = 1 } }) $f
    Ok 'AC-15 GenericWrite on an installed file maps to operator-can-write' ($v -match '^UNSAFE .*operator-can-write') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = ($sd.Dir + '(XA;;FA;;;BU;(WIN://SYSAPPID Contains "x"))'); Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 callback ACE is UNKNOWN, never SAFE' ($v -match '^UNKNOWN .*unsupported-ace') $v
    $v = Verdict (Assess @{ $app = @{ Sddl = 'O:BAD:NO_ACCESS_CONTROL'; Reparse = $false; Identity = 'V1:app' } }) $app
    Ok 'AC-15 null DACL is UNSAFE' ($v -match '^UNSAFE .*null-dacl') $v
    $broken = New-WdOperatorSidSet -UserSid $op
    $broken.Sids.Add('S-1-5-32-544') | Out-Null
    $v = Verdict (Assess @{} $broken) $inst
    Ok 'AC-15 control: Administrators counted as an allow would make the trusted chain UNSAFE' ($v -match '^UNSAFE .*delete-on-component') $v

    # Apply ordering: safe fresh install and safe re-apply.
    $c = Apply-Case 'positive-fresh' (State -Empty)
    $log = $c.Log
    $firstWrite = [Array]::FindIndex([string[]]$log, [Predicate[string]] { param($x) $x -like 'write *' })
    $heldDeploy = [Array]::IndexOf([string[]]$log, 'milestone assess-held Deploy ok=True')
    $reval = [Array]::FindIndex([string[]]$log, [Predicate[string]] { param($x) $x -like 'milestone revalidate ok=True*' })
    Ok 'AC-15 positive fresh install: create, hold+assess, write, revalidate, register in order' ($c.Code -eq 0 -and $c.Out.Contains('APPLIED') -and
        @($log | Where-Object { $_ -like 'create *' }).Count -eq 3 -and @($log | Where-Object { $_ -like 'write *' }).Count -eq 5 -and
        $heldDeploy -ge 0 -and $firstWrite -gt $heldDeploy -and $reval -gt $firstWrite -and [Array]::IndexOf([string[]]$log, 'register') -gt $reval -and
        -not @($log | Where-Object { $_ -like 'setsecurity *' }).Count) ($log -join "`n")
    $c = Apply-Case 'positive-existing' (State)
    Ok 'AC-15 positive re-apply on trusted existing chain' ($c.Code -eq 0 -and @($c.Log | Where-Object { $_ -like 'setsecurity *' }).Count -eq 2 -and $c.Log -contains 'register' -and -not @($c.Log | Where-Object { $_ -like 'create *' }).Count) ($c.Log -join "`n")

    # WD-641 uses the real Apply core, including path assessment and final hashes.
    Ok 'WD-641 AC-2 equal readback required after registration' ($c.Out -ceq 'APPLIED: WolftacFactoryWatchdog' -and [Array]::IndexOf([string[]]$c.Log,'read-task') -gt [Array]::IndexOf([string[]]$c.Log,'register'))
    $sidReader = { param($text) $x = [xml]$text; $x.Task.Principals.Principal.UserId = $op; $x.OuterXml }.GetNewClosure()
    $c = Apply-Case 'principal-exported-as-sid' (State) @{ ReadTaskXml = $sidReader }
    Ok 'WD-641 AC-2 resolved operator SID is the same principal' ($c.Code -eq 0 -and $c.Out -ceq 'APPLIED: WolftacFactoryWatchdog') $c.Out
    foreach ($reader in @({ param($xml) 'not found' }, { param($xml) throw 'private read failure' }, { param($xml) '' })) {
        $c = Apply-Case 'task-absent-or-unreadable' (State) @{ ReadTaskXml = $reader }
        Ok 'WD-641 AC-1 missing/unreadable readback is exit 5 without APPLIED' ($c.Code -eq 5 -and $c.Out -ceq 'APPLY FAILED: task not found after registration' -and $c.Log[-1] -eq 'release') $c.Out
    }
    $changes = [ordered]@{
        principal = { param($x) $x.Task.Principals.Principal.UserId = 'OTHER\account' }
        logon = { param($x) $x.Task.Principals.Principal.LogonType = 'InteractiveToken' }
        runlevel = { param($x) $x.Task.Principals.Principal.RunLevel = 'HighestAvailable' }
        executable = { param($x) $x.Task.Actions.Exec.Command = 'other.exe' }
        arguments = { param($x) $x.Task.Actions.Exec.Arguments = 'changed-private-argument' }
        interval = { param($x) $x.Task.Triggers.TimeTrigger.Repetition.Interval = 'PT30M' }
        limit = { param($x) $x.Task.Settings.ExecutionTimeLimit = 'PT72H' }
    }
    foreach ($field in $changes.Keys) {
        $mutator = $changes[$field]
        $reader = { param($text) $x = [xml]$text; & $mutator $x; $x.OuterXml }.GetNewClosure()
        $c = Apply-Case "task-diff-$field" (State) @{ ReadTaskXml = $reader }
        Ok "WD-641 AC-2 readback mismatch names only $field" ($c.Code -eq 5 -and $c.Out -ceq "APPLY FAILED: registered task differs: $field" -and $c.Log[-1] -eq 'release') $c.Out
    }
    $c = Apply-Case 'registration-refused' (State) @{ RegisterTask = { throw [UnauthorizedAccessException]::new('RAW-SECRET-DO-NOT-PRINT') } }
    Ok 'WD-641 AC-3 registration exception sanitized, files retained' ($c.Code -eq 5 -and $c.Out -match '^APPLY FAILED: task registration refused \(UnauthorizedAccessException, HRESULT 0x[0-9A-F]{8}\)' -and $c.Out.EndsWith('FILES INSTALLED: 5 of 5 current; task NOT registered') -and -not $c.Out.Contains('RAW-SECRET') -and -not ($c.Log -contains 'read-task') -and $c.Log[-1] -eq 'release') $c.Out
    $resolved = Resolve-WdOperatorProfile -Io $profileIo -Sid $op
    $paths = Get-WdProfilePaths -Profile $resolved
    Ok 'WD-641 AC-4 SID profile ignores caller USERPROFILE and LOCALAPPDATA' ($resolved -ceq 'C:\Users\op' -and $profileIo.SeenSid -ceq $op -and $paths.CronDir.Count -eq 4 -and @(@($paths.CronDir) + @($paths.EnvFile) | Where-Object { -not $_.StartsWith('C:\Users\op\AppData\Local\hermes\') }).Count -eq 0)
    $overrides = Get-WdProfilePaths -Profile $resolved -Cron @('Q:\explicit\cron') -Env 'Q:\explicit\fixture.env'
    Ok 'WD-641 AC-4 explicit paths retained' ($overrides.CronDir[0] -ceq 'Q:\explicit\cron' -and $overrides.EnvFile -ceq 'Q:\explicit\fixture.env')
    foreach ($missing in @('missing-key','missing-directory')) {
        $profileIo.Profile = if ($missing -eq 'missing-key') { '' } else { 'C:\Users\op' }
        $profileIo.Exists = $false
        $c = Apply-Case $missing (State) @{ RegisterTask = { throw 'must not register' } }
        Ok 'WD-641 AC-5 absent profile refuses before any install I/O' ($c.Code -eq 3 -and $c.Out -ceq 'APPLY REFUSED: operator profile not found for FIXTURE\operator' -and $c.Log.Count -eq 0) $c.Out
    }
    $profileIo.Profile = 'C:\Users\op'; $profileIo.Exists = $true
    $c = Apply-Case 'files-only' (State) @{ RegisterTask = { throw 'must not register' }; ReadTaskXml = { throw 'must not read task' } } -FilesOnly
    Ok 'WD-641 AC-6 files-only revalidates five writes with zero task calls' ($c.Code -eq 0 -and $c.Out -ceq 'FILES APPLIED: WolftacFactoryWatchdog files only; task untouched' -and @($c.Log | Where-Object { $_ -like 'write *' }).Count -eq 5 -and @($c.Log | Where-Object { $_ -match '^(register|read-task)$' }).Count -eq 0 -and $c.Log -contains 'milestone revalidate ok=True hash-mismatch=0') $c.Out

    # Negative controls: each refused for its named security reason, zero effects.
    $s = State -Empty; $s[$app] = @{ Sddl = $sd.OperatorCreated; Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'operator-owned-parent' $s
    Refused 'AC-15 operator-owned parent' $c 'AppParent' 'operator-owns-component'
    Ok 'AC-15 operator-owned parent: no create, no takeover' (-not @($c.Log | Where-Object { $_ -like 'create *' }).Count)
    $s = State; $s[$app] = @{ Sddl = $sd.OperatorCreated; Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'operator-owned-parent-valid-child' $s
    Refused 'AC-15 valid child under operator-owned parent' $c 'InstallDir' 'parent-owned-by-operator'
    $s = State; $s[$app] = @{ Sddl = ($sd.Dir + '(A;;0x40;;;BU)'); Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'delete-child-on-parent' $s
    Refused 'AC-15 delete-child on parent' $c 'AppParent' 'delete-child-on-component'
    Refused 'AC-15 delete-child on parent (child view)' $c 'InstallDir' 'parent-grants-delete-child'
    $s = State; $s[$app] = @{ Sddl = ($sd.Dir + '(A;;WD;;;BU)'); Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'write-dac-on-parent' $s
    Refused 'AC-15 WRITE_DAC on parent' $c 'AppParent' 'write-dac-or-owner-on-component'
    $s = State; $s[$app] = @{ Sddl = ($sd.Dir + "(A;;WO;;;$op)"); Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'write-owner-on-parent' $s
    Refused 'AC-15 WRITE_OWNER on parent' $c 'AppParent' 'write-dac-or-owner-on-component'
    $s = State; $s[$app] = @{ Sddl = ($sd.Dir + '(A;;SD;;;AU)'); Reparse = $false; Identity = 'V1:app' }
    $c = Apply-Case 'delete-on-parent' $s
    Refused 'AC-15 DELETE on parent' $c 'AppParent' 'delete-on-component'
    $race = @{ "Create:$app" = { param($st) $st[$app] = @{ Sddl = $sd.OperatorCreated; Reparse = $false; Identity = 'V1:raced' } } }
    $c = Apply-Case 'parent-created-after-first-check' (State -Empty) $race
    Refused 'AC-15 parent raced in after the first check' $c 'AppParent' 'operator-owns-component'
    Ok 'AC-15 raced-in parent detected as already existing, child never created' ($c.Log -contains "milestone raced-in $app" -and -not @($c.Log | Where-Object { $_ -eq "create $inst" }).Count) ($c.Log -join "`n")
    $swap = @{ "Hold:$app" = { param($st) $st[$app] = @{ Sddl = $sd.Dir; Reparse = $false; Identity = 'V1:replacement' } } }
    $c = Apply-Case 'parent-replaced-after-first-check' (State) $swap
    Refused 'AC-15 parent replaced after the first check' $c 'AppParent' 'identity-changed'
    $s = State; $s[$dep] = @{ Sddl = $sd.Dir; Reparse = $true; Identity = 'V1:deploy' }
    $c = Apply-Case 'reparse-deploy' $s
    Refused 'AC-15 reparse point in the chain' $c 'Deploy' 'reparse-point'
    $drift = @{ "Hold:$inst" = { param($st) $st[$inst].Reparse = $true } }
    $c = Apply-Case 'reparse-drift-at-hold' (State) $drift
    Refused 'AC-15 reparse drift after the first check' $c 'InstallDir' 'reparse-point'
    $redirect = @{ "Hold:$dep" = { param($st) $st[$dep].FinalPath = 'Q:\elsewhere\deploy' } }
    $c = Apply-Case 'final-path-drift-at-hold' (State) $redirect
    Refused 'AC-15 final-path drift after the first check' $c 'Deploy' 'path-mismatch'
    $s = State; $s[$pd] = @{ Sddl = $null; Reparse = $false; Identity = 'V1:pd' }
    $c = Apply-Case 'unreadable-descriptor' $s
    Refused 'AC-15 unreadable descriptor' $c 'SystemAncestor' 'descriptor-unreadable'
    $s = State; $s[$pd] = @{ Sddl = 'not-an-sddl'; Reparse = $false; Identity = 'V1:pd' }
    $c = Apply-Case 'unparseable-descriptor' $s
    Refused 'AC-15 unparseable descriptor' $c 'SystemAncestor' 'descriptor-unparseable'
    $s = State; $s[$app] = @{ Unreadable = $true }
    $c = Apply-Case 'unknown-existence' $s
    Refused 'AC-15 unreadable component' $c 'AppParent' 'existence-unknown'
    $c = Apply-Case 'unresolved-group' (State) @{} (New-WdOperatorSidSet -UserSid $op -Unresolved @('local groups of FIXTURE\operator'))
    Refused 'AC-15 unresolved operator group' $c 'InstallDir' 'operator-groups-unresolved'
    $c = Apply-Case 'administrators-counted-as-allow' (State) @{} $broken
    Refused 'AC-15 control: Administrators as an allow' $c 'InstallDir' 'delete-on-component'
    $c = Apply-Case 'deny-only-administrators' (State)
    Ok 'AC-15 deny-only Administrators membership does not count as an allow' ($c.Code -eq 0 -and $sidSet.ExcludedDenyOnly -contains 'S-1-5-32-544') $c.Out
    $s = State; $s["$dep\deploy.ps1"] = @{ Sddl = ($sd.File + '(A;;0x116;;;BU)'); Reparse = $false; Identity = 'V1:deploy.ps1'; Links = 1; Bytes = $copies[1].Bytes }
    $c = Apply-Case 'untrusted-existing-file' $s
    Refused 'AC-15 operator-writable existing file' $c 'File' 'operator-can-write'
    $late = @{ "AfterWrite:$dep\junction-lib.ps1" = { param($st) $st[$dep].Identity = 'V1:late-swap' } }
    $c = Apply-Case 'post-write-revalidation' (State) $late
    Ok 'AC-15 post-write identity drift blocks task registration' ($c.Code -eq 4 -and $c.Out -match 'FAILED Deploy .*identity-changed' -and -not ($c.Log -contains 'register')) $c.Out

    # AC-16: Verify core with the same assessor, pinned manifest and synthetic chain.
    $manifest = [pscustomobject]@{ ApprovedCommit = ('a' * 40); Files = $hashes; Sha256 = 'synthetic' }
    function Verify-Case([string]$Name, [hashtable]$State, [hashtable]$Hooks = @{}, $Manifest = $manifest) {
        $io = Stub-Io $State $Hooks
        $checks = @(Invoke-WdVerifyCore -Io $io -Path $inst -Manifest $Manifest -SidSet $sidSet)
        $lines = @(foreach ($k in $checks) { "$(if ($k.Ok) { 'PASS' } else { 'FAIL' }) $($k.Name)$(if ($k.Detail) { ': ' + $k.Detail })" })
        $effects = @($io.Log | Where-Object { $_ -match '^(create|setsecurity|write|register)' })
        [IO.File]::AppendAllText($caseLog, "VERIFY $Name EFFECTS=$($effects.Count)`n$($lines -join "`n")`n`n")
        Write-Host "  [verify $Name] fails: $(@($lines | Where-Object { $_ -like 'FAIL*' }) -join ' || ')"
        @{ Lines = $lines; Out = ($lines -join "`n"); Fails = @($lines | Where-Object { $_ -like 'FAIL*' }); Effects = $effects }
    }
    $c = Verify-Case 'trusted-complete' (State)
    Ok 'AC-16 trusted complete fixture passes every chain, file and hash check' ($c.Fails.Count -eq 0 -and @($c.Lines | Where-Object { $_ -like 'PASS file * sha256' }).Count -eq 5 -and @($c.Lines | Where-Object { $_ -like 'PASS path *' }).Count -eq 10 -and $c.Effects.Count -eq 0) $c.Out
    $s = State; $s[$app] = @{ Sddl = $sd.OperatorCreated; Reparse = $false; Identity = 'V1:app' }
    $c = Verify-Case 'unsafe-parent-valid-child' $s
    Ok 'AC-16 valid child cannot mask an unsafe parent' ($c.Out -match "(?m)^FAIL path AppParent $([regex]::Escape($app)): UNSAFE .*operator-owns-component" -and $c.Out -match '(?m)^FAIL path InstallDir .*parent-owned-by-operator' -and @($c.Lines | Where-Object { $_ -like 'PASS file * sha256' }).Count -eq 5) $c.Out
    $s = State; $s["$dep\factory-watchdog.ps1"] = @{ Sddl = ($sd.File + '(A;;0x116;;;BU)'); Reparse = $false; Identity = 'V1:w'; Links = 1; Bytes = $copies[0].Bytes }
    $c = Verify-Case 'writable-file' $s
    Ok 'AC-16 one operator-writable installed file FAILs' ($c.Out -match '(?m)^FAIL path File .*factory-watchdog\.ps1: UNSAFE operator-can-write' -and $c.Fails.Count -eq 1) $c.Out
    $s = State; $s["$dep\junctions.json"] = @{ Sddl = "O:${op}D:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;$op)"; Reparse = $false; Identity = 'V1:j'; Links = 1; Bytes = $copies[2].Bytes }
    $c = Verify-Case 'operator-owned-file' $s
    Ok 'AC-16 one operator-owned installed file FAILs' ($c.Out -match '(?m)^FAIL path File .*junctions\.json: UNSAFE operator-owns-component' -and $c.Fails.Count -eq 1) $c.Out
    $s = State; $s["$dep\deploy.ps1"].Bytes = [Text.Encoding]::UTF8.GetBytes("tampered`n")
    $c = Verify-Case 'changed-file' $s
    Ok 'AC-16 changed file FAILs on the pinned hash' ($c.Out -match '(?m)^FAIL file deploy\.ps1 sha256: hash-mismatch' -and $c.Fails.Count -eq 1) $c.Out
    $s = State; $s.Remove("$dep\approval-policy.json")
    $c = Verify-Case 'missing-file' $s
    Ok 'AC-16 missing file FAILs' ($c.Out -match '(?m)^FAIL path File .*approval-policy\.json: UNSAFE missing' -and $c.Out -match '(?m)^FAIL file approval-policy\.json sha256: missing') $c.Out
    $swap = @{ "Read:$dep\junction-lib.ps1" = { param($st) $st["$dep\junction-lib.ps1"].Identity = 'V1:swapped' } }
    $c = Verify-Case 'identity-swap' (State) $swap
    Ok 'AC-16 identity swap FAILs' ($c.Out -match '(?m)^FAIL path File .*junction-lib\.ps1: UNSAFE identity-changed') $c.Out
    $s = State; $s["$dep\deploy.ps1"].Reparse = $true
    $c = Verify-Case 'reparse-file' $s
    Ok 'AC-16 reparse swap FAILs' ($c.Out -match '(?m)^FAIL path File .*deploy\.ps1: UNSAFE reparse-point') $c.Out
    $s = State; $s[$dep].Sddl = $null
    $c = Verify-Case 'unknown-deploy-descriptor' $s
    Ok 'AC-16 unknown descriptor FAILs as UNKNOWN' ($c.Out -match '(?m)^FAIL path Deploy .*: UNKNOWN descriptor-unreadable') $c.Out
    $c = Verify-Case 'no-manifest' (State) @{} $null
    Ok 'AC-16 no expected input is a named FAIL' ($c.Out -match '(?m)^FAIL expected manifest: not supplied' -and $c.Out -match '(?m)^FAIL file factory-watchdog\.ps1 sha256: no-expected-hash') $c.Out

    # WD-641 AC-8: execute the unchanged production Verify entry block in a child.
    # Only OS facts are stubbed: the three owner/ACL/write-probe facts and the
    # path reader. The real XML checks, manifest reader, assessor, output and exit
    # reducer run. No alternate production CLI option can bypass those checks.
    $verifyHarness = Join-Path $Scratch 'verify-entry-fixture.ps1'
    $statePath = Join-Path $Scratch 'verify-state.json'
    $manifestPath = Join-Path $Scratch 'verify-manifest.json'
    Put $statePath ((State) | ConvertTo-Json -Depth 12)
    Put $manifestPath (@{ approvedCommit = ('a' * 40); files = $hashes } | ConvertTo-Json)
    $stubDefinition = 'function Stub-Io {' + (Get-Command Stub-Io).ScriptBlock.ToString() + "}`n"
    $harness = @'
param($Installer, $XmlInput, $Baseline, $StatePath, $ManifestPath, $InstallPath,
      [string]$ExpectedLogon = 'S4U', [switch]$BadInstall)
. $Installer -OperatorUser 'FIXTURE\operator' -LibraryOnly -ExpectedLogon $ExpectedLogon
$OperatorUser = 'FIXTURE\operator'
$InstallDir = $InstallPath; $ExpectedManifest = $ManifestPath; $TaskXmlIn = $XmlInput
$baselineXml = [xml][IO.File]::ReadAllText($Baseline)
$PwshPath = $baselineXml.Task.Actions.Exec.Command
$arguments = $baselineXml.Task.Actions.Exec.Arguments
$state = Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json -AsHashtable
function New-WdReadIo { Stub-Io $state }
function Resolve-WdOperatorSidSet { New-WdOperatorSidSet -UserSid 'S-1-5-21-1111111111-2222222222-3333333333-1001' }
function Get-WdInstallChecks {
    foreach ($name in @('install-dir owner','install-dir ACL','operator cannot write')) {
        [pscustomobject]@{ Name = $name; Ok = -not ($BadInstall -and $name -eq 'install-dir ACL'); Detail = '' }
    }
}
function Export-ScheduledTask { throw 'NO LIVE TASK READ' }
function Register-ScheduledTask { throw 'NO LIVE TASK WRITE' }
function Get-Acl { throw 'NO LIVE ACL READ' }
function Set-Acl { throw 'NO LIVE ACL WRITE' }
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Installer, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'installer parse failed' }
$blocks = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -ceq '$Verify' }, $false))
if ($blocks.Count -ne 1) { throw 'expected one production Verify entry block' }
$body = $blocks[0].Clauses[0].Item2.Extent.Text
& ([scriptblock]::Create($body.Substring(1,$body.Length-2)))
'@
    # Put function after the param block, before the fixture body executes.
    $harness = $harness.Replace(". `$Installer -OperatorUser", $stubDefinition + ". `$Installer -OperatorUser")
    Put $verifyHarness $harness
    $verifyArgs = @('-Installer',$installer,'-Baseline',$xmlPath,'-StatePath',$statePath,'-ManifestPath',$manifestPath,'-InstallPath',$inst)
    $vr = Run $verifyHarness ($verifyArgs + @('-XmlInput',$xmlPath))
    Ok 'WD-641 AC-8 S4U production Verify entry passes with trusted fixture' ($vr.Code -eq 0 -and $vr.Out.Contains('PASS logon type') -and $vr.Out -notmatch '(?m)^(FAIL|DEGRADED) ') $vr.Out
    foreach ($mode in @('Interactive','InteractiveToken')) {
        $interactive = [xml]$doc.OuterXml; $interactive.Task.Principals.Principal.LogonType = $mode
        $interactivePath = Join-Path $Scratch "task-$mode.xml"; $interactive.Save($interactivePath)
        $vr = Run $verifyHarness ($verifyArgs + @('-XmlInput',$interactivePath,'-ExpectedLogon','Interactive'))
        Ok "WD-641 AC-8 $mode degraded-only exits 6 with no FAIL" ($vr.Code -eq 6 -and $vr.Out.Contains('DEGRADED task logon Interactive (signed-in only; does not run after a reboot until sign-in)') -and $vr.Out -notmatch '(?m)^FAIL ') $vr.Out
        $vr = Run $verifyHarness ($verifyArgs + @('-XmlInput',$interactivePath))
        Ok "WD-641 AC-8 $mode default remains FAIL" ($vr.Code -eq 1 -and $vr.Out.Contains('FAIL logon type') -and -not $vr.Out.Contains('DEGRADED')) $vr.Out
        $vr = Run $verifyHarness ($verifyArgs + @('-XmlInput',$interactivePath,'-ExpectedLogon','Interactive','-BadInstall'))
        Ok "WD-641 AC-8 $mode other failures dominate degradation" ($vr.Code -eq 1 -and $vr.Out.Contains('FAIL install-dir ACL') -and $vr.Out.Contains('DEGRADED')) $vr.Out
    }

    # Real-handle reader on scratch only (read-only; no ACL change): reparse, identity
    # swap and the no-FILE_SHARE_DELETE hold that blocks rename during a write.
    $real = Join-Path $Scratch 'tp12-real'
    $dirA = Join-Path $real 'a'; New-Item -ItemType Directory -Force $dirA | Out-Null
    $io = New-WdReadIo
    $before = $io.Read($dirA, 'Directory')
    $h = $io.Hold($dirA, 'Directory', $false)
    $renameBlocked = $false
    try { Rename-Item -LiteralPath $dirA -NewName 'a-moved' } catch { $renameBlocked = $true }
    $io.Release()
    Ok 'AC-15 held directory handle (no FILE_SHARE_DELETE) blocks rename' ($renameBlocked -and $h.Exists -and $h.Identity -eq $before.Identity -and (Test-Path -LiteralPath $dirA))
    Rename-Item -LiteralPath $dirA -NewName 'a-moved'
    New-Item -ItemType Directory $dirA | Out-Null
    $after = $io.Read($dirA, 'Directory')
    $pinned = Join-WdObservation @{ Path = $dirA; Role = 'InstallDir'; Kind = 'Directory'; Parent = -1; Name = '' } $after ([string]$before.Identity)
    $v = (Test-WdPathChain -Components @($pinned) -SidSet $sidSet).Results[0]
    Ok 'AC-15 real rename-and-replace changes physical identity' ($before.Identity -and $after.Identity -and $before.Identity -ne $after.Identity -and $v.Reason -match 'identity-changed') "$($before.Identity) -> $($after.Identity) $($v.Reason)"
    $junction = Join-Path $real 'j'
    New-Item -ItemType Junction -Path $junction -Target (Join-Path $real 'a-moved') | Out-Null
    $jr = $io.Read($junction, 'Directory')
    Ok 'AC-15 real junction is read as a reparse point' ($jr.Exists -and $jr.Reparse -eq $true)
}

# TP13: the real script's offline -Verify -TaskXmlIn with a pinned manifest, on a scratch
# install the test user owns. Chain/owner checks must FAIL by name; hash checks must
# follow the manifest, not the checkout.
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$inst3 = Join-Path $Scratch 'inst3'; $xml3 = Join-Path $Scratch 'task3.xml'; $manifest3 = Join-Path $Scratch 'manifest3.json'
$head = (& git -C $Repo rev-parse HEAD).Trim()
$common3 = @('-OperatorUser',$me,'-SourceRoot',$Repo,'-RepoRoot',$Repo,'-CronDir',$cron,'-EnvFile',$fixtureEnv,'-PwshPath',$pwsh,'-InstallDir',$inst3)
$r = Run $installer ($common3 + @('-TaskXmlOut',$xml3,'-ManifestOut',$manifest3,'-ApprovedCommit',$head))
Ok 'AC-16 plan writes a pinned manifest' ($r.Code -eq 0 -and (Test-Path $manifest3) -and -not (Test-Path $inst3) -and $r.Out.Contains("PLAN MANIFEST")) $r.Out
$m = Get-Content -Raw $manifest3 | ConvertFrom-Json
Ok 'AC-16 manifest pins approved commit and five plan hashes' ($m.approvedCommit -eq $head -and @($m.files.PSObject.Properties).Count -eq 5 -and $m.files.'deploy.ps1' -eq (Get-FileHash (Join-Path $Repo 'deploy\deploy.ps1') -Algorithm SHA256).Hash)
New-Item -ItemType Directory -Force (Join-Path $inst3 'deploy') | Out-Null
foreach ($name in $files) { Copy-Item -LiteralPath (Join-Path $Repo "deploy\$name") -Destination (Join-Path $inst3 "deploy\$name") }
$r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$xml3,'-ExpectedManifest',$manifest3))
Ok 'AC-16 real Verify: manifest and operator identity resolved' ($r.Out.Contains('PASS expected manifest: approvedCommit=' + $head) -and $r.Out.Contains('PASS operator identity')) $r.Out
Ok 'AC-16 real Verify: all five hashes match the manifest' (@([regex]::Matches($r.Out, '(?m)^PASS file \S+ sha256')).Count -eq 5) $r.Out
Ok 'AC-16 real Verify: user-owned scratch InstallDir is a named FAIL' ($r.Code -ne 0 -and $r.Out -match "(?m)^FAIL path InstallDir $([regex]::Escape($inst3)): UNSAFE .*operator-owns-component" -and $r.Out -match '(?m)^FAIL path File .*factory-watchdog\.ps1: UNSAFE .*operator-owns-component') $r.Out
foreach ($mode in @('Interactive','InteractiveToken')) {
    $interactive = [xml][IO.File]::ReadAllText($xml3)
    $interactive.Task.Principals.Principal.LogonType = $mode
    $interactivePath = Join-Path $Scratch "task3-$mode.xml"; $interactive.Save($interactivePath)
    $r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$interactivePath,'-ExpectedManifest',$manifest3,'-ExpectedLogon','Interactive'))
    Ok "WD-641 TP2 real CLI $mode emits DEGRADED but preserves scratch-security failure" ($r.Code -eq 1 -and $r.Out.Contains('DEGRADED task logon Interactive (signed-in only; does not run after a reboot until sign-in)') -and -not $r.Out.Contains('FAIL logon type') -and $r.Out.Contains('operator-owns-component')) $r.Out
    $r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$interactivePath,'-ExpectedManifest',$manifest3))
    Ok "WD-641 TP2 real CLI $mode defaults to FAIL" ($r.Code -eq 1 -and $r.Out.Contains('FAIL logon type') -and -not $r.Out.Contains('DEGRADED')) $r.Out
}
[IO.File]::AppendAllText((Join-Path $inst3 'deploy\deploy.ps1'), "`n# scratch modification`n")
Remove-Item -LiteralPath (Join-Path $inst3 'deploy\junctions.json')
$r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$xml3,'-ExpectedManifest',$manifest3))
Ok 'AC-16 real Verify: changed and missing files are named FAILs' ($r.Code -ne 0 -and $r.Out -match '(?m)^FAIL file deploy\.ps1 sha256: hash-mismatch' -and $r.Out -match '(?m)^FAIL file junctions\.json sha256: missing' -and $r.Out -match '(?m)^FAIL path File .*junctions\.json: UNSAFE missing') $r.Out
$r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$xml3))
Ok 'AC-16 real Verify without -ExpectedManifest is a named FAIL' ($r.Code -ne 0 -and $r.Out -match '(?m)^FAIL expected manifest: not supplied') $r.Out
$badManifest = Join-Path $Scratch 'manifest-bad.json'
Put $badManifest '{"approvedCommit":"HEAD","files":{}}'
$r = Run $installer ($common3 + @('-Verify','-TaskXmlIn',$xml3,'-ExpectedManifest',$badManifest))
Ok 'AC-16 real Verify rejects a malformed manifest by name' ($r.Code -ne 0 -and $r.Out -match '(?m)^FAIL expected manifest: invalid') $r.Out
$r = Run $installer ($common + @('-InstallDir',$inst2,'-Verify','-TaskXmlIn',$xml2))
Ok 'AC-16 unresolvable operator makes the chain UNKNOWN, never PASS' ($r.Code -ne 0 -and $r.Out -match '(?m)^FAIL operator identity: unresolved' -and $r.Out -match '(?m)^FAIL path InstallDir .*UNKNOWN .*operator-groups-unresolved' -and $r.Out.Contains('PASS task principal')) $r.Out
Write-Host "RESULT pass=$script:passed fail=0"
Write-Host "PROOF $Scratch"
# Keep synthetic proof outside the worktree. No deployment cleanup is performed.
exit 0
