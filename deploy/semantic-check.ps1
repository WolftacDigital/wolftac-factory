<# Deterministic workflow checks that regex parity alone cannot express. #>
param([string]$RepoRoot = (Split-Path $PSScriptRoot -Parent))

$ErrorActionPreference = "Stop"
$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$errors = New-Object System.Collections.Generic.List[string]

function Read-RepoFile([string]$RelativePath) {
    $path = Join-Path $RepoRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $script:errors.Add("missing required file: $RelativePath")
        return ""
    }
    return [IO.File]::ReadAllText($path)
}

function Require-Pattern([string]$RelativePath, [string]$Pattern, [string]$Message) {
    $content = Read-RepoFile $RelativePath
    if ($content -and -not [regex]::IsMatch($content, $Pattern, "IgnoreCase,Singleline")) {
        $script:errors.Add("$RelativePath - $Message")
    }
}

$specFiles = @(
    "canonical\wolftac-spec\spec-template.md",
    "ports\codex\wolftac-spec.md",
    "ports\claude-desktop\wolftac-spec\SKILL.md",
    "ports\hermes\wolftac-spec\SKILL.md",
    "plugins\wolftac-factory\skills\wolftac-spec\references\spec-template.md"
)
foreach ($file in $specFiles) {
    $content = Read-RepoFile $file
    if ($content -match "pure markdown edit") {
        $errors.Add("$file - contains task-specific data/migration boilerplate")
    }
    Require-Pattern $file "Data / migration" "missing the data/migration quality gate"
}

$buildFiles = @(
    "canonical\wolftac-build\SKILL.md",
    "ports\codex\wolftac-build.md",
    "ports\claude-desktop\wolftac-build\SKILL.md",
    "ports\hermes\wolftac-build\SKILL.md",
    "plugins\wolftac-factory\skills\wolftac-build\SKILL.md"
)
foreach ($file in $buildFiles) {
    Require-Pattern $file "dedicated (issue )?worktree" "does not require an isolated build worktree"
    Require-Pattern $file "git push -u origin" "does not require publishing the branch"
    Require-Pattern $file "Published head" "build report does not record the published SHA"
}

$reviewFiles = @(
    "canonical\wolftac-review\SKILL.md",
    "ports\codex\wolftac-review.md",
    "ports\claude-desktop\wolftac-review\SKILL.md",
    "ports\hermes\wolftac-review\SKILL.md",
    "plugins\wolftac-factory\skills\wolftac-review\SKILL.md"
)
foreach ($file in $reviewFiles) {
    $content = Read-RepoFile $file
    Require-Pattern $file "detached review\s+worktree" "does not require an isolated review worktree"
    Require-Pattern $file "Reviewed head" "review report does not record the verified SHA"
    Require-Pattern $file "keep.{0,40}In Review" "stale reviews do not explicitly remain In Review"
    if ([regex]::IsMatch($content, "SWEPT:[^\r\n]{0,120}(reset|return)[^\r\n]{0,40}`?Todo`?", "IgnoreCase")) {
        $errors.Add("$file - stale review recovery incorrectly returns built work to Todo")
    }
}

# Review's passing verdict is Approved; a review surface that still sets Done is a defect.
foreach ($file in $reviewFiles) {
    $content = Read-RepoFile $file
    if ($content -and [regex]::IsMatch($content, "(set|state)\s+``Done``", "IgnoreCase")) {
        $errors.Add("$file - review surface still sets Done; passing verdicts must set Approved (only integrate sets Done)")
    }
}

$integrateFiles = @(
    "canonical\wolftac-integrate\SKILL.md",
    "ports\hermes\wolftac-integrate\SKILL.md",
    "plugins\wolftac-factory\skills\wolftac-integrate\SKILL.md"
)
foreach ($file in $integrateFiles) {
    $content = Read-RepoFile $file
    Require-Pattern $file "MERGE PROOF PACK" "missing the MERGE PROOF PACK contract"
    Require-Pattern $file "APPROVE MERGE" "missing the APPROVE MERGE human approval gate"
    Require-Pattern $file "merge-base --is-ancestor" "does not verify merge ancestry before Done"
    Require-Pattern $file "detached integrate\s+worktree" "does not require an isolated integrate worktree"
    Require-Pattern $file "[Nn]ever force-push" "missing the force-push prohibition"
    Require-Pattern $file "older than the\s+newest\s+proof pack" "missing the approval staleness guard"
    Require-Pattern $file "differs\s+from\s+your\s+own\s+Linear\s+viewer\s+id" "missing the human-approval viewer-identity check"
    Require-Pattern $file "[Nn]ever trust a SHA from a prior session" "merge path does not rebuild the rebased head at merge time"
    Require-Pattern $file "Default\s+head" "proof pack does not record the default-branch SHA it verified against"
    Require-Pattern $file "EQUALITY" "merge path does not enforce default-head SHA equality against the proof pack"
    Require-Pattern $file "LINEAR_FACTORY_BOT_TOKEN" "stage does not mandate bot-credentialed Linear I/O"
    Require-Pattern $file "fail\s+closed" "stage does not fail closed when bot credentials are missing"
    Require-Pattern $file "(notify-only|notification-only|notification lanes are notify)" "notification lanes are not restricted to notify-only"
    Require-Pattern $file "[Dd]o not load or use.{0,100}default\s+Linear" "stage does not prohibit loading or using the default Linear transport"
    if ([regex]::IsMatch($content, '(ToolSearch|`(?:list_issues|get_issue|save_issue|list_comments|save_comment)`|Confirm Linear tools|use this profile''s Linear toolset equivalents)', "IgnoreCase")) {
        $errors.Add("$file - integrate procedure references the default/human Linear tool path instead of bot-authenticated GraphQL")
    }
    Require-Pattern $file "integrate-preflight" "missing the REQUIRED runtime preflight step"
}

# WD-281: the preflight and runtime recipe must ship with the repo and keep their contracts.
Require-Pattern "deploy\integrate-preflight.py" "PREFLIGHT FAIL \(3\)" "preflight lost the dual-capable-runtime (human Linear path) violation check"
Require-Pattern "deploy\integrate-preflight.py" "client_credentials" "preflight lost the token re-mint path"
Require-Pattern "deploy\integrate-preflight.py" "composio" "preflight capability scan lost Composio-proxied transport detection"
Require-Pattern "deploy\integrate-preflight.py" "executing process environment" "preflight lost the executing-runtime credential check"
Require-Pattern "deploy\bot-runtime.md" "blank\s+overrides" "runtime recipe lost the inherited-credential blank-override step"
Require-Pattern "deploy\integrate-preflight.py" "EXPECTED_BOT_ID\s*=" "preflight lost the pinned (non-overridable) bot id"
Require-Pattern "deploy\integrate-preflight.py" "(?s)viewer\(fresh\).{0,700}persist_token" "re-mint must viewer-verify a fresh token BEFORE persisting it"
Require-Pattern "deploy\bot-runtime.md" "cron create" "bot-runtime recipe lost the verified worker-creation command"
Require-Pattern "deploy\bot-runtime.md" "factory-integrate" "bot-runtime recipe lost the isolated profile"
Require-Pattern "deploy\bot-runtime.md" "pin-cron-model" "bot-runtime recipe lost the supported model-pin flow"
Require-Pattern "deploy\deploy.ps1" "profiles\\factory-integrate\\deploy\\integrate-preflight\.py" "deploy lost the profile-deployed preflight pair (stable cron path)"
Require-Pattern "deploy\bot-runtime.md" "profile-deployed copy" "bot-runtime recipe lost the stable profile-deployed preflight path doctrine"
Require-Pattern "deploy\bot-runtime.md" "cron edit" "bot-runtime recipe lost the supported prompt/workdir repair route"
Require-Pattern "deploy\integrate-preflight.py" "os\.replace" "token persistence lost its atomic write"
Require-Pattern "deploy\pin-cron-model.py" "(?s)os\.replace.{0,600}readback" "pin tool lost atomic write or readback verification"

$pluginManifest = "plugins\wolftac-factory\.codex-plugin\plugin.json"
try {
    $manifest = Read-RepoFile $pluginManifest | ConvertFrom-Json
    if ($manifest.name -ne "wolftac-factory" -or $manifest.skills -ne "./skills/") {
        $errors.Add("$pluginManifest - invalid plugin identity or skills path")
    }
} catch {
    $errors.Add("$pluginManifest - invalid JSON: $($_.Exception.Message)")
}

# Every directly linked Markdown reference in a skill must ship beside it.
$skillFiles = Get-ChildItem -LiteralPath $RepoRoot -Recurse -Filter SKILL.md -File | Where-Object {
    $_.FullName -notmatch "[\\/]\.git[\\/]"
}
foreach ($skill in $skillFiles) {
    $content = [IO.File]::ReadAllText($skill.FullName)
    foreach ($match in [regex]::Matches($content, '(?:references/)?[A-Za-z0-9._-]+\.md')) {
        $relative = $match.Value -replace '/', [IO.Path]::DirectorySeparatorChar
        $target = Join-Path $skill.DirectoryName $relative
        if ($relative -match '^references' -and -not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $errors.Add("$($skill.FullName) - missing linked reference $($match.Value)")
        }
    }
}

$scanRoots = @("canonical", "ports", "plugins", "README.md", "AGENTS.md", "deploy\hermes-moa-presets.yaml.example")
$staleModelPattern = "gpt-5" + [regex]::Escape(".5")
foreach ($root in $scanRoots) {
    $path = Join-Path $RepoRoot $root
    $files = if (Test-Path -LiteralPath $path -PathType Container) {
        Get-ChildItem -LiteralPath $path -Recurse -File
    } elseif (Test-Path -LiteralPath $path -PathType Leaf) { @(Get-Item $path) } else { @() }
    foreach ($file in $files) {
        if ([regex]::IsMatch([IO.File]::ReadAllText($file.FullName), $staleModelPattern, "IgnoreCase")) {
            $errors.Add("$($file.FullName) - stale GPT model reference")
        }
    }
}

if ($errors.Count) {
    foreach ($item in $errors) { Write-Output "SEMANTIC ERROR: $item" }
    Write-Output "SEMANTIC FAILED"
    exit 1
}

Write-Output "SEMANTIC OK: workflow state, artifact, model, plugin, and reference checks passed"
exit 0
