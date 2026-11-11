<#
WD-584. Plan by default; only an elevated operator may apply after merge.
-TaskXmlOut and -TaskXmlIn are offline test seams, never credentials.
-LibraryOnly defines the pure assessor and the Apply/Verify cores for offline tests,
then returns before any plan, file, ACL or task work. It cannot be combined with
-Apply or -Verify.
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
    [string]$TaskXmlIn = '',
    [string]$ExpectedManifest = '',
    [string]$ManifestOut = '',
    [string]$ApprovedCommit = '',
    [switch]$LibraryOnly
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Apply -and $Verify) { throw 'Choose Apply or Verify, not both.' }
if ($TaskXmlIn -and -not $Verify) { throw 'TaskXmlIn requires Verify.' }
if ($ExpectedManifest -and -not $Verify) { throw 'ExpectedManifest requires Verify.' }
if ($ManifestOut -and ($Apply -or $Verify)) { throw 'ManifestOut is a plan-mode output only.' }
if ($LibraryOnly -and ($Apply -or $Verify)) { throw 'LibraryOnly cannot be combined with Apply or Verify.' }
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# Refuse before XML output, directory creation, source reads or ACL/task access.
if ($Apply -and -not $elevated) {
    Write-Output 'APPLY REFUSED: requires an elevated session'
    exit 2
}

# ── Path-security assessment (AC-15/AC-16) ─────────────────────────────────────
# One pure assessor is shared by Apply and Verify. It reads only the facts it is
# given: per component a security descriptor (SDDL), reparse flag, physical identity
# (volume serial + file id), final path and link count, plus the operator SID set.
# It returns SAFE, UNSAFE or UNKNOWN per component with named reasons. UNKNOWN never
# counts as SAFE.
$script:WdFileNames = @('factory-watchdog.ps1', 'deploy.ps1', 'junctions.json', 'approval-policy.json', 'junction-lib.ps1')
$script:WdProtectedRoles = @('InstallDir', 'Deploy', 'File')
# Rights that let the operator change, delete or re-ACL a component.
$script:WdDelete = 0x10000L
$script:WdDeleteChild = 0x40L
$script:WdWriteDacOwner = 0xC0000L               # WRITE_DAC | WRITE_OWNER
$script:WdWriteMask = 0x116L                     # write/add-file, append/add-subdir, write EA, write attributes
# Operator token model (non-elevated). Deny-only groups in a UAC filtered token never
# grant access, so they are excluded from the allow set.
$script:WdBaseOperatorSids = @(
    'S-1-1-0',      # Everyone
    'S-1-5-11',     # Authenticated Users
    'S-1-5-32-545', # Users
    'S-1-5-4',      # INTERACTIVE
    'S-1-5-3',      # BATCH (S4U task logon)
    'S-1-2-0',      # LOCAL
    'S-1-2-1',      # CONSOLE LOGON
    'S-1-5-15',     # This Organization
    'S-1-5-113'     # Local account
)
$script:WdDenyOnlySids = @('S-1-5-32-544', 'S-1-5-114')   # Administrators; local account and member of Administrators

function New-WdOperatorSidSet {
    param([string]$UserSid, [string[]]$LocalGroupSids = @(), [string[]]$Unresolved = @())
    $sids = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $excluded = [Collections.Generic.List[string]]::new()
    $problems = [Collections.Generic.List[string]]::new()
    foreach ($item in @($Unresolved)) { if ($item) { $problems.Add($item) } }
    if ($UserSid) { [void]$sids.Add($UserSid) } else { $problems.Add('operator-user-sid') }
    foreach ($sid in $script:WdBaseOperatorSids) { [void]$sids.Add($sid) }
    foreach ($sid in @($LocalGroupSids)) {
        if (-not $sid) { continue }
        if ($script:WdDenyOnlySids -contains $sid) { $excluded.Add($sid) } else { [void]$sids.Add($sid) }
    }
    [pscustomobject]@{ UserSid = $UserSid; Sids = $sids; ExcludedDenyOnly = @($excluded); Unresolved = @($problems) }
}

function Resolve-WdOperatorSidSet {
    param([Parameter(Mandatory)][string]$Account)
    try {
        $userSid = ([Security.Principal.NTAccount]::new($Account)).Translate([Security.Principal.SecurityIdentifier]).Value
    } catch { return New-WdOperatorSidSet -Unresolved @("account $Account") }
    $groups = [Collections.Generic.List[string]]::new()
    $unresolved = [Collections.Generic.List[string]]::new()
    $parts = $Account -split '\\'
    $domain = if ($parts.Count -gt 1) { $parts[0] } else { '.' }
    $name = $parts[-1]
    if ($domain -ne '.' -and $domain -ine [Environment]::MachineName) {
        $unresolved.Add("groups of non-local account $Account")
    } else {
        try {
            $user = [ADSI]"WinNT://$([Environment]::MachineName)/$name,user"
            foreach ($group in @($user.psbase.Invoke('Groups'))) {
                try {
                    $bytes = $group.GetType().InvokeMember('objectSid', 'GetProperty', $null, $group, $null)
                    $groups.Add([Security.Principal.SecurityIdentifier]::new([byte[]]$bytes, 0).Value)
                } catch { $unresolved.Add("a local group of $Account") }
            }
        } catch { $unresolved.Add("local groups of $Account") }
    }
    # When the operator itself runs Verify non-elevated, its real token's enabled groups
    # are added too. A union only widens the modeled rights, which is conservative.
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $currentElevated = ([Security.Principal.WindowsPrincipal]::new($current)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($current.User.Value -eq $userSid -and -not $currentElevated) {
        foreach ($sid in $current.Groups) { $groups.Add($sid.Value) }
    }
    New-WdOperatorSidSet -UserSid $userSid -LocalGroupSids @($groups) -Unresolved @($unresolved)
}

function ConvertTo-WdMappedMask([int]$Raw) {
    $mask = [int64]$Raw
    if ($mask -lt 0) { $mask += 4294967296L }
    $mapped = $mask -band 0x00FFFFFFL
    if ($mask -band 0x80000000L) { $mapped = $mapped -bor 0x120089L }   # GENERIC_READ
    if ($mask -band 0x40000000L) { $mapped = $mapped -bor 0x120116L }   # GENERIC_WRITE
    if ($mask -band 0x20000000L) { $mapped = $mapped -bor 0x1200A0L }   # GENERIC_EXECUTE
    if ($mask -band 0x10000000L) { $mapped = $mapped -bor 0x1F01FFL }   # GENERIC_ALL
    if ($mask -band 0x02000000L) { $mapped = $mapped -bor 0x1F01FFL }   # MAXIMUM_ALLOWED
    $mapped
}

function Get-WdOperatorRights {
    # Windows evaluates DACL entries in order; for each right the first matching
    # allow or deny decides. A canonical DACL therefore applies deny before allow.
    param([Parameter(Mandatory)]$Descriptor, [Parameter(Mandatory)]$SidSet, [bool]$Owned)
    $granted = 0L; $denied = 0L
    foreach ($ace in $Descriptor.DiscretionaryAcl) {
        if ($ace.AceFlags -band [Security.AccessControl.AceFlags]::InheritOnly) { continue }
        $isDeny = "$($ace.AceType)" -match 'Denied'
        if ($ace -isnot [Security.AccessControl.CommonAce] -or $ace.IsCallback) {
            if ($isDeny) { continue }   # ignoring a deny can only widen modeled rights
            return [pscustomobject]@{ Mask = $granted; Unknown = "unsupported-ace:$($ace.AceType)" }
        }
        $sid = $ace.SecurityIdentifier.Value
        $applies = $SidSet.Sids.Contains($sid) -or ($sid -eq 'S-1-3-4' -and $Owned)
        if (-not $applies) { continue }
        $mask = ConvertTo-WdMappedMask $ace.AccessMask
        switch ("$($ace.AceQualifier)") {
            'AccessAllowed' { $granted = $granted -bor ($mask -band (-bnot $denied)) }
            'AccessDenied' { $denied = $denied -bor ($mask -band (-bnot $granted)) }
            default { return [pscustomobject]@{ Mask = $granted; Unknown = "unsupported-ace:$($ace.AceQualifier)" } }
        }
    }
    [pscustomobject]@{ Mask = $granted; Unknown = '' }
}

function Get-WdChain {
    # Volume root -> system ancestors -> application parent -> InstallDir -> deploy -> files.
    param([Parameter(Mandatory)][string]$Path, [string[]]$FileNames = $script:WdFileNames)
    $install = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $dirs = [Collections.Generic.List[string]]::new()
    $cursor = $install
    while ($cursor) { $dirs.Insert(0, $cursor); $cursor = [IO.Path]::GetDirectoryName($cursor) }
    if ($dirs.Count -lt 3) { throw 'InstallDir must have an application parent below the volume root.' }
    $chain = [Collections.Generic.List[object]]::new()
    for ($i = 0; $i -lt $dirs.Count; $i++) {
        $role = if ($i -eq $dirs.Count - 1) { 'InstallDir' } elseif ($i -eq $dirs.Count - 2) { 'AppParent' } else { 'SystemAncestor' }
        $chain.Add(@{ Path = $dirs[$i]; Role = $role; Kind = 'Directory'; Parent = $i - 1; Name = '' })
    }
    $deploy = [IO.Path]::Combine($install, 'deploy')   # string-only: no provider/drive lookup
    $chain.Add(@{ Path = $deploy; Role = 'Deploy'; Kind = 'Directory'; Parent = $dirs.Count - 1; Name = '' })
    $deployIndex = $chain.Count - 1
    foreach ($name in $FileNames) {
        $chain.Add(@{ Path = [IO.Path]::Combine($deploy, $name); Role = 'File'; Kind = 'File'; Parent = $deployIndex; Name = $name })
    }
    , $chain.ToArray()
}

function Join-WdObservation {
    param([Parameter(Mandatory)][hashtable]$Spec, $Observation, [string]$ExpectedIdentity = '')
    $record = @{}
    foreach ($key in $Spec.Keys) { $record[$key] = $Spec[$key] }
    foreach ($key in @('Exists', 'Sddl', 'Reparse', 'Identity', 'FinalPath', 'Links', 'Token')) {
        $record[$key] = if ($Observation -and $Observation.ContainsKey($key)) { $Observation[$key] } else { $null }
    }
    $record.ExpectedIdentity = $ExpectedIdentity
    $record
}

function Test-WdPathChain {
    param([Parameter(Mandatory)][object[]]$Components, [Parameter(Mandatory)]$SidSet, [switch]$AllowMissing)
    $facts = @()
    foreach ($c in $Components) {
        $unsafe = [Collections.Generic.List[string]]::new()
        $unknown = [Collections.Generic.List[string]]::new()
        $state = 'present'; $owned = $false; $mask = 0L; $selfUnknown = $false
        if ($null -eq $c.Exists) { $state = 'unknown'; $unknown.Add('existence-unknown') }
        elseif (-not $c.Exists) {
            $state = 'absent'
            if (-not ($AllowMissing -and $c.Role -ne 'SystemAncestor')) { $unsafe.Add('missing') }
        } else {
            if (@($SidSet.Unresolved).Count) { $unknown.Add('operator-groups-unresolved:' + (@($SidSet.Unresolved) -join ';')) }
            if ($null -eq $c.Reparse) { $unknown.Add('reparse-unknown') } elseif ($c.Reparse) { $unsafe.Add('reparse-point') }
            if (-not $c.Identity) { $unknown.Add('identity-unreadable') }
            elseif ($c.ExpectedIdentity -and $c.ExpectedIdentity -ne $c.Identity) { $unsafe.Add('identity-changed') }
            if (-not $c.FinalPath) { $unknown.Add('final-path-unreadable') }
            elseif ($c.FinalPath.TrimEnd('\') -ine $c.Path.TrimEnd('\')) { $unsafe.Add('path-mismatch') }
            if ($c.Kind -eq 'File') {
                if ($null -eq $c.Links) { $unknown.Add('link-count-unknown') } elseif ($c.Links -gt 1) { $unsafe.Add('multiple-hard-links') }
            }
            $descriptor = $null
            if (-not $c.Sddl) { $unknown.Add('descriptor-unreadable') }
            else {
                try { $descriptor = [Security.AccessControl.RawSecurityDescriptor]::new([string]$c.Sddl) }
                catch { $unknown.Add('descriptor-unparseable') }
            }
            if ($descriptor) {
                if (-not $descriptor.Owner) { $unknown.Add('owner-unknown') }
                elseif ($SidSet.Sids.Contains($descriptor.Owner.Value)) { $owned = $true; $unsafe.Add('operator-owns-component') }
                if ($null -eq $descriptor.DiscretionaryAcl) { $mask = 0x1F01FFL; $unsafe.Add('null-dacl') }
                else {
                    $rights = Get-WdOperatorRights -Descriptor $descriptor -SidSet $SidSet -Owned $owned
                    $mask = $rights.Mask
                    if ($rights.Unknown) { $unknown.Add($rights.Unknown) }
                }
                if ($mask -band $script:WdDelete) { $unsafe.Add('delete-on-component') }
                if ($mask -band $script:WdWriteDacOwner) { $unsafe.Add('write-dac-or-owner-on-component') }
                if ($c.Kind -eq 'Directory' -and ($mask -band $script:WdDeleteChild)) { $unsafe.Add('delete-child-on-component') }
                if ($script:WdProtectedRoles -contains $c.Role -and ($mask -band $script:WdWriteMask)) { $unsafe.Add('operator-can-write') }
            }
            if ($unknown.Count) { $selfUnknown = $true }
        }
        $facts += , @{ C = $c; Unsafe = $unsafe; Unknown = $unknown; State = $state; Owned = $owned; Mask = $mask; SelfUnknown = $selfUnknown }
    }
    $results = @()
    for ($i = 0; $i -lt $facts.Count; $i++) {
        $f = $facts[$i]
        $p = if ($f.C.Parent -ge 0 -and $f.C.Parent -lt $facts.Count) { $facts[$f.C.Parent] } else { $null }
        if ($p -and $f.State -ne 'absent') {
            if ($p.State -eq 'absent') { $f.Unknown.Add('inconsistent-chain') }
            if ($p.Owned) { $f.Unsafe.Add('parent-owned-by-operator') }
            if ($p.Mask -band $script:WdDeleteChild) { $f.Unsafe.Add('parent-grants-delete-child') }
            if ($p.Mask -band $script:WdWriteDacOwner) { $f.Unsafe.Add('parent-write-dac-or-owner') }
            if ($p.SelfUnknown -or $p.State -eq 'unknown') { $f.Unknown.Add('parent-unknown') }
        }
        $verdict = if ($f.Unsafe.Count) { 'UNSAFE' } elseif ($f.Unknown.Count) { 'UNKNOWN' } else { 'SAFE' }
        $reason = if ($verdict -ne 'SAFE') { (@($f.Unsafe) + @($f.Unknown)) -join ',' } elseif ($f.State -eq 'absent') { 'absent' } else { 'trusted' }
        $results += [pscustomobject]@{ Path = $f.C.Path; Role = $f.C.Role; Kind = $f.C.Kind; Name = $f.C.Name; Verdict = $verdict; Reason = $reason }
    }
    [pscustomobject]@{ Ok = -not @($results | Where-Object { $_.Verdict -ne 'SAFE' }).Count; Results = $results }
}

function Format-WdAssessment([string]$Prefix, $Assessment) {
    foreach ($r in $Assessment.Results) {
        if ($r.Verdict -ne 'SAFE') { "$Prefix $($r.Role) $($r.Path): $($r.Verdict) $($r.Reason)" }
    }
}

function Get-WdSha256([byte[]]$Bytes) {
    [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($Bytes))
}

# ── Apply core (side effects only through $Io) ─────────────────────────────────
# Order: assess every existing component -> create missing application directories
# one level at a time, each held open without FILE_SHARE_DELETE and reassessed with
# identity pinning -> reassess files -> normalize trusted pre-existing directory ACLs
# -> write files through the held chain -> revalidate -> register the task.
function Invoke-WdApplyCore {
    param([Parameter(Mandatory)]$Io, [Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][object[]]$Copies,
          [Parameter(Mandatory)]$SidSet, [Parameter(Mandatory)][string]$OperatorSid, [Parameter(Mandatory)][string]$TaskXml)
    $lines = [Collections.Generic.List[string]]::new()
    $chain = Get-WdChain -Path $Path -FileNames @($Copies | ForEach-Object { $_.Name })
    $dirSddl = "O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;$OperatorSid)"
    $fileSddl = "O:BAD:PAI(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;$OperatorSid)"
    $refuse = {
        param([string]$Why, $Assessment)
        foreach ($line in Format-WdAssessment 'REFUSED' $Assessment) { $lines.Add($line) }
        $lines.Add("APPLY REFUSED: $Why (no file copied, no ACL set, task not registered)")
    }
    $first = @(foreach ($c in $chain) { Join-WdObservation $c ($Io.Read($c.Path, $c.Kind)) })
    $assessment = Test-WdPathChain -Components $first -SidSet $SidSet -AllowMissing
    $Io.Milestone("assess-existing ok=$($assessment.Ok)")
    if (-not $assessment.Ok) { & $refuse 'existing install path is not trusted' $assessment; return [pscustomobject]@{ Code = 3; Lines = $lines } }

    $deployIndex = [Array]::FindIndex($chain, [Predicate[object]] { param($c) $c.Role -eq 'Deploy' })
    $held = @()
    for ($i = 0; $i -le $deployIndex; $i++) {
        $c = $chain[$i]
        $existed = [bool]$first[$i].Exists
        if (-not $existed) {
            $created = $Io.CreateDirectory($c.Path, $dirSddl)
            if ($created -ne 'Created') { $Io.Milestone("raced-in $($c.Path)") }
        }
        $writable = $existed -and ($c.Role -in @('InstallDir', 'Deploy'))
        $expected = if ($existed) { [string]$first[$i].Identity } else { '' }
        $held += , (Join-WdObservation $c ($Io.Hold($c.Path, 'Directory', $writable)) $expected)
        $assessment = Test-WdPathChain -Components $held -SidSet $SidSet
        $Io.Milestone("assess-held $($c.Role) ok=$($assessment.Ok)")
        if (-not $assessment.Ok) {
            $Io.Release()
            & $refuse 'install path changed or is not trusted after the first check' $assessment
            return [pscustomobject]@{ Code = 3; Lines = $lines }
        }
    }
    $files = @(for ($i = $deployIndex + 1; $i -lt $chain.Count; $i++) {
        $expected = if ($first[$i].Exists) { [string]$first[$i].Identity } else { '' }
        Join-WdObservation $chain[$i] ($Io.Read($chain[$i].Path, 'File')) $expected
    })
    $assessment = Test-WdPathChain -Components (@($held) + @($files)) -SidSet $SidSet -AllowMissing
    $Io.Milestone("assess-files ok=$($assessment.Ok)")
    if (-not $assessment.Ok) {
        $Io.Release()
        & $refuse 'existing installed file is not trusted' $assessment
        return [pscustomobject]@{ Code = 3; Lines = $lines }
    }

    for ($i = 0; $i -le $deployIndex; $i++) {
        if ($first[$i].Exists -and $chain[$i].Role -in @('InstallDir', 'Deploy')) { $Io.SetSecurity($held[$i].Token, $dirSddl) }
    }
    $written = @{}
    foreach ($copy in $Copies) {
        if ((Get-WdSha256 $copy.Bytes) -ne $copy.Hash) {
            $Io.Release()
            $lines.Add("APPLY FAILED: source $($copy.Name) changed after the plan; task not registered")
            return [pscustomobject]@{ Code = 4; Lines = $lines }
        }
        $written[$copy.Name] = $Io.WriteFile([IO.Path]::Combine($held[$deployIndex].Path, $copy.Name), $copy.Bytes, $fileSddl)
    }

    $final = @()
    for ($i = 0; $i -lt $chain.Count; $i++) {
        $c = $chain[$i]
        $pinned = if ($i -le $deployIndex) { [string]$held[$i].Identity } else { [string]$written[$c.Name].Identity }
        $final += , (Join-WdObservation $c ($Io.Read($c.Path, $c.Kind)) $pinned)
    }
    $assessment = Test-WdPathChain -Components $final -SidSet $SidSet
    $mismatch = @(foreach ($copy in $Copies) { if ($Io.Hash($written[$copy.Name].Token) -ne $copy.Hash) { $copy.Name } })
    $Io.Milestone("revalidate ok=$($assessment.Ok) hash-mismatch=$($mismatch.Count)")
    if (-not $assessment.Ok -or $mismatch.Count) {
        $Io.Release()
        foreach ($line in Format-WdAssessment 'FAILED' $assessment) { $lines.Add($line) }
        foreach ($name in $mismatch) { $lines.Add("FAILED File ${name}: hash-mismatch") }
        $lines.Add('APPLY FAILED: post-write revalidation; task not registered')
        return [pscustomobject]@{ Code = 4; Lines = $lines }
    }
    $Io.RegisterTask($TaskXml)
    $Io.Release()
    $lines.Add('APPLIED: WolftacFactoryWatchdog')
    [pscustomobject]@{ Code = 0; Lines = $lines }
}

# ── Verify core (reads only through $Io) ───────────────────────────────────────
function Read-WdManifest([string]$Path) {
    $text = [IO.File]::ReadAllText($Path)
    $data = $text | ConvertFrom-Json -AsHashtable
    if ($data -isnot [hashtable] -or -not $data.ContainsKey('approvedCommit') -or -not $data.ContainsKey('files')) { throw 'needs approvedCommit and files' }
    if ("$($data.approvedCommit)" -notmatch '^[0-9a-f]{40}$') { throw 'approvedCommit must be a 40-hex commit id' }
    $files = $data.files
    if ($files -isnot [hashtable] -or $files.Count -ne $script:WdFileNames.Count) { throw 'files must list exactly the five installed files' }
    $hashes = @{}
    foreach ($name in $script:WdFileNames) {
        if (-not $files.ContainsKey($name) -or "$($files[$name])" -notmatch '^[0-9A-Fa-f]{64}$') { throw "missing or invalid SHA-256 for $name" }
        $hashes[$name] = "$($files[$name])".ToUpperInvariant()
    }
    [pscustomobject]@{ ApprovedCommit = "$($data.approvedCommit)"; Files = $hashes; Sha256 = (Get-WdSha256 ([IO.File]::ReadAllBytes($Path))) }
}

function Invoke-WdVerifyCore {
    param([Parameter(Mandatory)]$Io, [Parameter(Mandatory)][string]$Path, $Manifest, [string]$ManifestProblem = '', [Parameter(Mandatory)]$SidSet)
    $checks = [Collections.Generic.List[object]]::new()
    if ($Manifest -and -not $ManifestProblem) {
        $checks.Add([pscustomobject]@{ Name = 'expected manifest'; Ok = $true; Detail = "approvedCommit=$($Manifest.ApprovedCommit) sha256=$($Manifest.Sha256)" })
    } else {
        if (-not $ManifestProblem) { $ManifestProblem = 'not supplied (-ExpectedManifest is required)' }
        $checks.Add([pscustomobject]@{ Name = 'expected manifest'; Ok = $false; Detail = $ManifestProblem })
    }
    $identityOk = $SidSet.UserSid -and -not @($SidSet.Unresolved).Count
    $checks.Add([pscustomobject]@{ Name = 'operator identity'; Ok = [bool]$identityOk
        Detail = if ($identityOk) { "sids=$($SidSet.Sids.Count) excluded-deny-only=$(@($SidSet.ExcludedDenyOnly) -join ';')" } else { 'unresolved: ' + (@($SidSet.Unresolved) -join ';') } })
    $chain = Get-WdChain -Path $Path
    $hashes = @{}
    $records = @()
    try {
        $held = @(foreach ($c in $chain) { $Io.Hold($c.Path, $c.Kind, $false) })
        for ($i = 0; $i -lt $chain.Count; $i++) {
            $c = $chain[$i]
            if ($c.Kind -eq 'File' -and $held[$i].Exists) { $hashes[$c.Name] = $Io.Hash($held[$i].Token) }
            $pinned = if ($held[$i].Exists) { [string]$held[$i].Identity } else { '' }
            $fresh = if ($held[$i].Exists) { $Io.Read($c.Path, $c.Kind) } else { $held[$i] }
            $records += , (Join-WdObservation $c $fresh $pinned)
        }
    } finally { $Io.Release() }
    $assessment = Test-WdPathChain -Components $records -SidSet $SidSet
    foreach ($r in $assessment.Results) {
        $checks.Add([pscustomobject]@{ Name = "path $($r.Role) $($r.Path)"; Ok = ($r.Verdict -eq 'SAFE'); Detail = if ($r.Verdict -eq 'SAFE') { '' } else { "$($r.Verdict) $($r.Reason)" } })
    }
    foreach ($name in $script:WdFileNames) {
        $actual = if ($hashes.ContainsKey($name)) { $hashes[$name] } else { $null }
        $detail = if (-not $Manifest -or $ManifestProblem) { 'no-expected-hash' }
                  elseif ($null -eq $actual) { 'missing' }
                  elseif ($actual -ne $Manifest.Files[$name]) { "hash-mismatch actual=$actual" }
                  else { '' }
        $checks.Add([pscustomobject]@{ Name = "file $name sha256"; Ok = -not $detail; Detail = $detail })
    }
    $checks.ToArray()
}

# ── Native read/write seams (Windows handles; used only by real Apply/Verify) ───
function Initialize-WdNative {
    if ('WolftacWd584.PathNative' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace WolftacWd584 {
    public sealed class NodeInfo {
        public bool Exists; public int Error; public string Sddl; public bool Reparse;
        public string Identity; public string FinalPath; public uint Links;
    }
    public static class PathNative {
        const uint READ_CONTROL = 0x20000, FILE_READ_ATTRIBUTES = 0x80, FILE_READ_DATA = 0x1;
        const uint GENERIC_READ = 0x80000000, GENERIC_WRITE = 0x40000000, WRITE_DAC = 0x40000, WRITE_OWNER = 0x80000;
        const uint SHARE_READ = 1, SHARE_WRITE = 2, SHARE_DELETE = 4, OPEN_EXISTING = 3, CREATE_ALWAYS = 2;
        const uint BACKUP_SEMANTICS = 0x02000000, OPEN_REPARSE_POINT = 0x00200000, ATTR_REPARSE = 0x400;
        const int OWNER_INFO = 1, DACL_INFO = 4;

        [StructLayout(LayoutKind.Sequential)]
        struct ByHandleInfo { public uint Attributes, C1, C2, A1, A2, W1, W2, Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow; }
        [StructLayout(LayoutKind.Sequential)]
        struct FileIdInfo { public ulong Volume, IdLow, IdHigh; }
        [StructLayout(LayoutKind.Sequential)]
        struct SecurityAttributes { public int Length; public IntPtr Descriptor; public int Inherit; }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr sa, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, EntryPoint = "CreateFileW")]
        static extern SafeFileHandle CreateFileSa(string name, uint access, uint share, ref SecurityAttributes sa, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetFileInformationByHandle(SafeFileHandle h, out ByHandleInfo info);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetFileInformationByHandleEx(SafeFileHandle h, int infoClass, out FileIdInfo info, uint size);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern uint GetFinalPathNameByHandleW(SafeFileHandle h, StringBuilder path, uint size, uint flags);
        [DllImport("advapi32.dll", SetLastError = true)]
        static extern bool GetKernelObjectSecurity(SafeFileHandle h, int info, byte[] sd, uint length, out uint needed);
        [DllImport("advapi32.dll", SetLastError = true)]
        static extern bool SetKernelObjectSecurity(SafeFileHandle h, int info, byte[] sd);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        static extern bool CreateDirectoryW(string path, ref SecurityAttributes sa);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool FlushFileBuffers(SafeFileHandle h);

        // A held handle omits FILE_SHARE_DELETE, so the object cannot be renamed or deleted
        // while it is open. NTFS enforces share modes only for handles that carry data
        // access, so a held handle always includes FILE_READ_DATA (list-directory).
        public static SafeFileHandle Open(string path, bool file, bool shareDelete, bool writable, out int error) {
            uint access = READ_CONTROL | FILE_READ_ATTRIBUTES | ((file || !shareDelete) ? FILE_READ_DATA : 0) | (writable ? WRITE_DAC | WRITE_OWNER : 0);
            uint share = SHARE_READ | SHARE_WRITE | (shareDelete ? SHARE_DELETE : 0);
            SafeFileHandle h = CreateFileW(path, access, share, IntPtr.Zero, OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT, IntPtr.Zero);
            error = h.IsInvalid ? Marshal.GetLastWin32Error() : 0;
            if (h.IsInvalid) { h.Dispose(); return null; }
            return h;
        }
        public static NodeInfo Describe(SafeFileHandle h) {
            NodeInfo n = new NodeInfo(); n.Exists = true;
            ByHandleInfo bh;
            if (!GetFileInformationByHandle(h, out bh)) { n.Error = Marshal.GetLastWin32Error(); return n; }
            n.Reparse = (bh.Attributes & ATTR_REPARSE) != 0; n.Links = bh.Links;
            FileIdInfo id;
            if (GetFileInformationByHandleEx(h, 18, out id, (uint)Marshal.SizeOf(typeof(FileIdInfo))))
                n.Identity = id.Volume.ToString("X16") + ":" + id.IdHigh.ToString("X16") + id.IdLow.ToString("X16");
            StringBuilder sb = new StringBuilder(32768);
            uint len = GetFinalPathNameByHandleW(h, sb, (uint)sb.Capacity, 0);
            if (len > 0 && len < sb.Capacity) {
                string p = sb.ToString();
                if (p.StartsWith(@"\\?\") && !p.StartsWith(@"\\?\UNC\")) p = p.Substring(4);
                n.FinalPath = p;
            }
            uint needed;
            GetKernelObjectSecurity(h, OWNER_INFO | DACL_INFO, null, 0, out needed);
            if (needed > 0) {
                byte[] buf = new byte[needed];
                if (GetKernelObjectSecurity(h, OWNER_INFO | DACL_INFO, buf, needed, out needed))
                    n.Sddl = new RawSecurityDescriptor(buf, 0).GetSddlForm(AccessControlSections.Owner | AccessControlSections.Access);
            }
            return n;
        }
        public static NodeInfo ReadPath(string path, bool file) {
            int err;
            SafeFileHandle h = Open(path, file, true, false, out err);
            if (h == null) { NodeInfo n = new NodeInfo(); n.Exists = !(err == 2 || err == 3); n.Error = err; return n; }
            using (h) return Describe(h);
        }
        public static string Sha256(SafeFileHandle h) {
            using (IncrementalHash sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256)) {
                byte[] buf = new byte[81920]; long offset = 0; int read;
                while ((read = RandomAccess.Read(h, buf, offset)) > 0) { sha.AppendData(buf, 0, read); offset += read; }
                return Convert.ToHexString(sha.GetHashAndReset());
            }
        }
        static byte[] Binary(string sddl) {
            RawSecurityDescriptor sd = new RawSecurityDescriptor(sddl);
            byte[] bytes = new byte[sd.BinaryLength]; sd.GetBinaryForm(bytes, 0); return bytes;
        }
        // 0 = created by this call; 183 = already existed (raced in or pre-existing).
        public static int CreateDirectory(string path, string sddl) {
            GCHandle pin = GCHandle.Alloc(Binary(sddl), GCHandleType.Pinned);
            try {
                SecurityAttributes sa = new SecurityAttributes();
                sa.Length = Marshal.SizeOf(typeof(SecurityAttributes)); sa.Descriptor = pin.AddrOfPinnedObject();
                if (CreateDirectoryW(path, ref sa)) return 0;
                int e = Marshal.GetLastWin32Error();
                if (e == 183) return 183;
                throw new Win32Exception(e);
            } finally { pin.Free(); }
        }
        public static void SetSecurity(SafeFileHandle h, string sddl) {
            if (!SetKernelObjectSecurity(h, OWNER_INFO | DACL_INFO, Binary(sddl))) throw new Win32Exception(Marshal.GetLastWin32Error());
        }
        public static SafeFileHandle WriteProtected(string path, byte[] data, string sddl) {
            byte[] sd = Binary(sddl);
            GCHandle pin = GCHandle.Alloc(sd, GCHandleType.Pinned);
            SafeFileHandle h;
            try {
                SecurityAttributes sa = new SecurityAttributes();
                sa.Length = Marshal.SizeOf(typeof(SecurityAttributes)); sa.Descriptor = pin.AddrOfPinnedObject();
                h = CreateFileSa(path, GENERIC_READ | GENERIC_WRITE | READ_CONTROL | WRITE_DAC | WRITE_OWNER | FILE_READ_ATTRIBUTES,
                                 SHARE_READ, ref sa, CREATE_ALWAYS, OPEN_REPARSE_POINT, IntPtr.Zero);
            } finally { pin.Free(); }
            if (h.IsInvalid) { int e = Marshal.GetLastWin32Error(); h.Dispose(); throw new Win32Exception(e); }
            NodeInfo n = Describe(h);
            if (n.Error != 0 || n.Reparse) { h.Dispose(); throw new IOException("Refusing unverifiable or reparse-point destination: " + path); }
            if (!SetKernelObjectSecurity(h, OWNER_INFO | DACL_INFO, sd)) { int e = Marshal.GetLastWin32Error(); h.Dispose(); throw new Win32Exception(e); }
            RandomAccess.Write(h, data, 0);
            if (!FlushFileBuffers(h)) { int e = Marshal.GetLastWin32Error(); h.Dispose(); throw new Win32Exception(e); }
            return h;
        }
    }
}
'@
}

function ConvertFrom-WdNode($Node) {
    if (-not $Node.Exists) { return @{ Exists = $false } }
    if ($Node.Error) { return @{ Exists = $null } }
    @{ Exists = $true; Sddl = $Node.Sddl; Reparse = $Node.Reparse; Identity = $Node.Identity; FinalPath = $Node.FinalPath; Links = [int64]$Node.Links }
}

function New-WdReadIo {
    Initialize-WdNative
    $io = [pscustomobject]@{ Handles = [Collections.Generic.Dictionary[string, object]]::new(); Next = 0 }
    $io | Add-Member ScriptMethod Milestone { param([string]$Text) Write-Verbose $Text }
    $io | Add-Member ScriptMethod Read { param([string]$Path, [string]$Kind) ConvertFrom-WdNode ([WolftacWd584.PathNative]::ReadPath($Path, $Kind -eq 'File')) }
    $io | Add-Member ScriptMethod Hold {
        param([string]$Path, [string]$Kind, [bool]$Writable)
        $err = 0
        $handle = [WolftacWd584.PathNative]::Open($Path, $Kind -eq 'File', $false, $Writable, [ref]$err)
        if ($null -eq $handle) { return @{ Exists = $(if ($err -in @(2, 3)) { $false } else { $null }) } }
        $token = "h$($this.Next)"; $this.Next++
        $this.Handles[$token] = $handle
        $info = ConvertFrom-WdNode ([WolftacWd584.PathNative]::Describe($handle))
        $info.Token = $token
        $info
    }
    $io | Add-Member ScriptMethod Hash { param([string]$Token) [WolftacWd584.PathNative]::Sha256($this.Handles[$Token]) }
    $io | Add-Member ScriptMethod Release { foreach ($handle in $this.Handles.Values) { $handle.Dispose() }; $this.Handles.Clear() }
    $io
}

if ($LibraryOnly) { return }

$files = $script:WdFileNames
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
    function Result([string]$Name, [bool]$Ok, [string]$Detail = '') {
        Write-Output ("$(if ($Ok) { 'PASS' } else { 'FAIL' }) $Name" + $(if ($Detail) { ": $Detail" } else { '' }))
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
    # AC-16: the whole chain, every installed file and pinned hashes. Expected hashes
    # come only from the operator-supplied manifest, never from the current checkout.
    $manifest = $null; $manifestProblem = ''
    if ($ExpectedManifest) {
        try { $manifest = Read-WdManifest $ExpectedManifest } catch { $manifestProblem = "invalid: $($_.Exception.Message)" }
    }
    try {
        $sidSet = Resolve-WdOperatorSidSet -Account $OperatorUser
        foreach ($check in Invoke-WdVerifyCore -Io (New-WdReadIo) -Path $InstallDir -Manifest $manifest -ManifestProblem $manifestProblem -SidSet $sidSet) {
            Result $check.Name $check.Ok $check.Detail
        }
    } catch { Result 'path assessment' $false "UNKNOWN $($_.Exception.Message)" }
    exit ([int]$script:failed)
}

$copies = @()
foreach ($name in $files) {
    $source = Join-Path $SourceRoot "deploy\$name"
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        Write-Output "MISSING $source"
        exit 1
    }
    $copies += @{ Name = $name; Source = $source; Destination = (Join-Path $InstallDir "deploy\$name"); Hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash }
}
foreach ($copy in $copies) { Write-Output "PLAN COPY $($copy.Source) -> $($copy.Destination) SHA256=$($copy.Hash)" }
Write-Output 'PLAN ACL owner=BUILTIN\Administrators inheritance=protected'
Write-Output 'PLAN ACE NT AUTHORITY\SYSTEM FullControl ContainerInherit,ObjectInherit'
Write-Output 'PLAN ACE BUILTIN\Administrators FullControl ContainerInherit,ObjectInherit'
Write-Output "PLAN ACE $OperatorUser ReadAndExecute ContainerInherit,ObjectInherit"
Write-Output "PLAN TASK account=$OperatorUser logon=S4U runlevel=LeastPrivilege interval=PT15M"
Write-Output "PLAN ACTION $PwshPath $arguments"
Write-Output 'PLAN GUARD refuse unless every existing component from the volume root to the five files is trusted against the operator''s non-elevated token'
if ($ManifestOut) {
    if ($ApprovedCommit -notmatch '^[0-9a-f]{40}$') { throw 'ManifestOut requires -ApprovedCommit <40-hex approved commit id>.' }
    $manifestFiles = [ordered]@{}
    foreach ($copy in $copies) { $manifestFiles[$copy.Name] = $copy.Hash }
    $manifestText = ([ordered]@{ approvedCommit = $ApprovedCommit; files = $manifestFiles } | ConvertTo-Json -Depth 4) -replace "`r`n", "`n"
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($ManifestOut), $manifestText + "`n")
    Write-Output "PLAN MANIFEST $([IO.Path]::GetFullPath($ManifestOut)) approvedCommit=$ApprovedCommit"
}
if ($Apply) {
    # Elevated only (refused above otherwise). Every side effect goes through this Io,
    # after Invoke-WdApplyCore has assessed the existing chain.
    $operatorSid = ([Security.Principal.NTAccount]::new($OperatorUser)).Translate([Security.Principal.SecurityIdentifier]).Value
    $sidSet = Resolve-WdOperatorSidSet -Account $OperatorUser
    foreach ($copy in $copies) { $copy.Bytes = [IO.File]::ReadAllBytes($copy.Source) }
    $io = New-WdReadIo
    $io | Add-Member ScriptMethod CreateDirectory {
        param([string]$Path, [string]$Sddl)
        if ([WolftacWd584.PathNative]::CreateDirectory($Path, $Sddl) -eq 0) { 'Created' } else { 'Exists' }
    }
    $io | Add-Member ScriptMethod SetSecurity { param([string]$Token, [string]$Sddl) [WolftacWd584.PathNative]::SetSecurity($this.Handles[$Token], $Sddl) }
    $io | Add-Member ScriptMethod WriteFile {
        param([string]$Path, [byte[]]$Bytes, [string]$Sddl)
        $handle = [WolftacWd584.PathNative]::WriteProtected($Path, $Bytes, $Sddl)
        $token = "h$($this.Next)"; $this.Next++
        $this.Handles[$token] = $handle
        $info = ConvertFrom-WdNode ([WolftacWd584.PathNative]::Describe($handle))
        $info.Token = $token
        $info
    }
    $io | Add-Member ScriptMethod RegisterTask { param([string]$Xml) Register-ScheduledTask -TaskName 'WolftacFactoryWatchdog' -Xml $Xml -Force | Out-Null }
    try {
        $result = Invoke-WdApplyCore -Io $io -Path $InstallDir -Copies $copies -SidSet $sidSet -OperatorSid $operatorSid -TaskXml $xml.OuterXml
    } finally { $io.Release() }
    foreach ($line in $result.Lines) { Write-Output $line }
    if ($result.Code -ne 0) { exit $result.Code }
}
if ($TaskXmlOut) { $xml.Save([IO.Path]::GetFullPath($TaskXmlOut)) }
exit 0
