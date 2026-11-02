<#
WD-584. Plan by default; only an elevated operator may apply after merge.
-TaskXmlOut and -TaskXmlIn are offline test seams, never credentials.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateNotNullOrEmpty()][string]$OperatorUser,
    [string]$InstallDir = "$env:ProgramData\WolftacFactory\watchdog",
    [string]$SourceRoot = (Split-Path $PSScriptRoot -Parent),
    [string]$RepoRoot = 'D:\Wolftac\Factory',
    [string[]]$CronDir = @(),
    [string]$EnvFile = '',
    [string]$PwshPath = 'C:\Program Files\PowerShell\7\pwsh.exe',
    [string]$TaskXmlOut = '',
    [switch]$Apply,
    [switch]$Verify,
    [string]$TaskXmlIn = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Apply -and $Verify) { throw 'Choose Apply or Verify, not both.' }
if ($TaskXmlIn -and -not $Verify) { throw 'TaskXmlIn requires Verify.' }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# Refuse before XML output, directory creation, source reads or ACL/task access.
if ($Apply -and -not $elevated) {
    Write-Output 'APPLY REFUSED: requires an elevated session'
    exit 2
}
$files = @('factory-watchdog.ps1', 'deploy.ps1', 'junctions.json', 'approval-policy.json', 'junction-lib.ps1')
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$actionFile = Join-Path $InstallDir 'deploy\factory-watchdog.ps1'
# Pure path construction, no profile registry or Task Scheduler lookup in plan mode.
# Override CronDir/EnvFile for an operator with a nonstandard profile location.
$profileName = ($OperatorUser -split '\\')[-1]
if ($profileName -match '[/\\:"<>|\r\n]' -or $profileName -in @('.', '..')) { throw 'Invalid operator account.' }
$operatorProfile = Join-Path (Split-Path $env:USERPROFILE -Parent) $profileName
$homeRoot = Join-Path $operatorProfile 'AppData\Local\hermes'
if (-not $CronDir.Count) {
    $CronDir = @((Join-Path $homeRoot 'cron'), (Join-Path $homeRoot 'profiles\builder\cron'),
                (Join-Path $homeRoot 'profiles\reviewer\cron'), (Join-Path $homeRoot 'profiles\integrator\cron'))
}
if (-not $EnvFile) { $EnvFile = Join-Path $homeRoot '.env' }
function Quote-Argument([string]$Value) {
    if ($Value -match '["\r\n\x00]') { throw 'Task argument contains a quote or control character.' }
    '"' + $Value + '"'
}
$arguments = '-NoProfile -NonInteractive -File ' + (Quote-Argument $actionFile) +
    ' -CronDir ' + (Quote-Argument ($CronDir -join ';')) + ' -EnvFile ' + (Quote-Argument $EnvFile) +
    ' -RepoRoot ' + (Quote-Argument $RepoRoot)
$xml = [xml]'<?xml version="1.0" encoding="utf-16"?><Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Triggers><TimeTrigger><Repetition><Interval>PT15M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>2020-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers><Principals><Principal id="Operator"><UserId/><LogonType>S4U</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals><Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><StartWhenAvailable>true</StartWhenAvailable></Settings><Actions Context="Operator"><Exec><Command/><Arguments/></Exec></Actions></Task>'
$xml.Task.Principals.Principal.UserId = $OperatorUser
$xml.Task.Actions.Exec.Command = $PwshPath
$xml.Task.Actions.Exec.Arguments = $arguments

if ($Verify) {
    $script:failed = $false
    function Result([string]$Name, [bool]$Ok) {
        Write-Output "$(if ($Ok) { 'PASS' } else { 'FAIL' }) $Name"
        if (-not $Ok) { $script:failed = $true }
    }
    try {
        $actual = if ($TaskXmlIn) { [xml][IO.File]::ReadAllText($TaskXmlIn) }
                  else { [xml](Export-ScheduledTask -TaskName 'WolftacFactoryWatchdog') }
        $ns = [Xml.XmlNamespaceManager]::new($actual.NameTable)
        $ns.AddNamespace('t', 'http://schemas.microsoft.com/windows/2004/02/mit/task')
        function Value([string]$Path) {
            $node = $actual.SelectSingleNode($Path, $ns)
            if ($node) { return $node.InnerText }; return ''
        }
        $users = $actual.SelectNodes('//t:Principal', $ns)
        $user = Value '//t:Principal/t:UserId'
        Result 'task principal' ($users.Count -eq 1 -and $user -ieq $OperatorUser -and $user -notmatch '^(SYSTEM|NT AUTHORITY\\SYSTEM|S-1-5-18)$')
        Result 'logon type' ((Value '//t:Principal/t:LogonType') -ceq 'S4U')
        Result 'run level' ((Value '//t:Principal/t:RunLevel') -ceq 'LeastPrivilege')
        Result 'no password' ($actual.SelectNodes('//*[local-name()="Password"]').Count -eq 0)
        Result 'action path' ($actual.SelectNodes('//t:Actions/*', $ns).Count -eq 1 -and
            (Value '//t:Exec/t:Command') -ieq $PwshPath -and
            (Value '//t:Exec/t:Arguments') -ceq $arguments)
    } catch { Result 'task definition readable' $false }
    try {
        $acl = Get-Acl -LiteralPath $InstallDir
        $adminSid = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
        Result 'install-dir owner' ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -eq $adminSid.Value)
        $operatorSid = ([Security.Principal.NTAccount]::new($OperatorUser)).Translate([Security.Principal.SecurityIdentifier]).Value
        $expected = @{'S-1-5-18' = 'FullControl'; 'S-1-5-32-544' = 'FullControl'}
        $expected[$operatorSid] = 'ReadAndExecute'
        $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
        $aclOk = $acl.AreAccessRulesProtected -and $rules.Count -eq 3
        $seen = @{}
        foreach ($rule in $rules) {
            $sid = $rule.IdentityReference.Value
            if ($seen.ContainsKey($sid) -or -not $expected.ContainsKey($sid) -or
                "$($rule.FileSystemRights)" -ne $expected[$sid] -or
                "$($rule.AccessControlType)" -ne 'Allow' -or $rule.IsInherited -or
                "$($rule.InheritanceFlags)" -ne 'ContainerInherit, ObjectInherit' -or
                "$($rule.PropagationFlags)" -ne 'None') { $aclOk = $false }
            $seen[$sid] = $true
        }
        Result 'install-dir ACL' $aclOk
    } catch { Result 'install-dir ACL' $false }
    # This tests the current non-elevated user, not an impersonated account.
    $probe = Join-Path $InstallDir ('.write-probe-' + [guid]::NewGuid().ToString('N'))
    $denied = $false
    if (-not $elevated -and (Test-Path -LiteralPath $InstallDir -PathType Container)) {
        try {
            $stream = [IO.File]::Open($probe, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
            [IO.File]::Delete($probe)
        } catch [UnauthorizedAccessException] { $denied = $true }
          catch { $denied = $false }
    }
    Result 'operator cannot write' $denied
    exit ([int]$script:failed)
}

$copies = @()
foreach ($name in $files) {
    $source = Join-Path $SourceRoot "deploy\$name"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        Write-Output "MISSING $source"
        exit 1
    }
    $copies += @{ Source = $source; Destination = (Join-Path $InstallDir "deploy\$name"); Hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash }
}
foreach ($copy in $copies) { Write-Output "PLAN COPY $($copy.Source) -> $($copy.Destination) SHA256=$($copy.Hash)" }
Write-Output 'PLAN ACL owner=BUILTIN\Administrators inheritance=protected'
Write-Output 'PLAN ACE NT AUTHORITY\SYSTEM FullControl ContainerInherit,ObjectInherit'
Write-Output 'PLAN ACE BUILTIN\Administrators FullControl ContainerInherit,ObjectInherit'
Write-Output "PLAN ACE $OperatorUser ReadAndExecute ContainerInherit,ObjectInherit"
Write-Output "PLAN TASK account=$OperatorUser logon=S4U runlevel=LeastPrivilege interval=PT15M"
Write-Output "PLAN ACTION $PwshPath $arguments"
if ($Apply) {
    # Refuse reparse-point destinations, including ancestors, before copying as admin.
    $cursor = $InstallDir
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and
            ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing reparse-point install path: $cursor"
        }
        $cursor = Split-Path $cursor -Parent
    }
    if (Test-Path -LiteralPath $InstallDir) {
        foreach ($item in Get-ChildItem -LiteralPath $InstallDir -Force -Recurse) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing reparse point within install directory.' }
        }
    }
    $admin = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    $system = [Security.Principal.SecurityIdentifier]::new('S-1-5-18')
    $operator = ([Security.Principal.NTAccount]::new($OperatorUser)).Translate([Security.Principal.SecurityIdentifier])
    $directoryAcl = [Security.AccessControl.DirectorySecurity]::new()
    $directoryAcl.SetOwner($admin)
    $directoryAcl.SetAccessRuleProtection($true, $false)
    foreach ($entry in @(@($system, 'FullControl'), @($admin, 'FullControl'), @($operator, 'ReadAndExecute'))) {
        $directoryAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($entry[0], $entry[1], 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Set-Acl -LiteralPath $InstallDir -AclObject $directoryAcl
    $deployDir = Join-Path $InstallDir 'deploy'
    New-Item -ItemType Directory -Path $deployDir -Force | Out-Null
    Set-Acl -LiteralPath $deployDir -AclObject $directoryAcl
    foreach ($copy in $copies) {
        Copy-Item -LiteralPath $copy.Source -Destination $copy.Destination -Force
        $fileAcl = [Security.AccessControl.FileSecurity]::new()
        $fileAcl.SetOwner($admin)
        $fileAcl.SetAccessRuleProtection($false, $false)
        Set-Acl -LiteralPath $copy.Destination -AclObject $fileAcl
        if ((Get-FileHash -LiteralPath $copy.Destination -Algorithm SHA256).Hash -ne $copy.Hash) { throw 'Installed hash differs from plan.' }
    }
    Register-ScheduledTask -TaskName 'WolftacFactoryWatchdog' -Xml $xml.OuterXml -Force | Out-Null
    Write-Output 'APPLIED: WolftacFactoryWatchdog'
}
if ($TaskXmlOut) { $xml.Save([IO.Path]::GetFullPath($TaskXmlOut)) }
exit 0
