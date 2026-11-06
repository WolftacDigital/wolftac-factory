# Linux host readiness

WD-634 prepares a dry-run boundary, not a host migration. No host was selected,
provisioned, or changed. No live job, schedule, credential, or repository moved.
Build/review/integrate skills and `AGENTS.md` remain unchanged. The Windows lane
continues to own Windows-native checks. Effort below is relative engineering
scope, not a purchase decision or an approval to execute: **S** = local path or
command adaptation, **M** = deployment/process integration and tests, **L** =
cross-host scheduling, ownership and recovery design.

## What is portable now

- Gatekeeper already resolves `HERMES_HOME`, then `LOCALAPPDATA/hermes`, then
  `~/hermes`; it already chooses Windows process flags versus POSIX
  `start_new_session`. No Gatekeeper production code was changed.
- Watchdog now accepts `-HermesHome`, defaulting to the same environment precedence.
  Its cron and fixture-env defaults derive from that home; explicit `-CronDir` and
  `-EnvFile` still override them. Unset variables retain the Windows defaults.
  `DriftProfile` falls back to `$HOME` only when `USERPROFILE` is missing.
  Windows learn backups keep their existing path; the non-Windows fallback is
  `<HermesHome>/backups/learn-edits`.
- Linux smoke explicitly uses `-DryRun -SkipDrift -SkipLearn`. This does **not**
  claim Linux support for deployment/junction drift, learning-backup discovery,
  live delivery, service management, or every factory skill.
- Other hard-coded paths in skill deployment, allowlists and integration helpers
  are inventory items, not ports: changing those would cross NG-2/NG-3. Existing
  parameter/environment seams are listed rather than moving live installations.

## Reproduce the proof

Prerequisites: Python 3.10+ and PowerShell 7 on PATH. CI uses the existing Ubuntu
runner tools; it does not install Hermes, credentials or a live scheduler. The
smoke fails, rather than skips, if `pwsh` is missing. Use a disposable scratch
output directory, never a live Hermes home:

```sh
python -B deploy/tests/linux_host_smoke.py --output <scratch>/windows.json
python3 -B deploy/tests/linux_host_smoke.py --output <scratch>/linux.json
# Copy both proof files into one scratch directory, then:
python -c "from pathlib import Path; assert Path('<scratch>/windows.json').read_bytes() == Path('<scratch>/linux.json').read_bytes(); print('identical')"
```

`linux_host_smoke.py` executes the real Gatekeeper script via `runpy` as `__main__`
in a child Python process with argv equivalent to:

```text
python deploy/factory-gatekeeper.py --dry-run --no-slack --env-path <scratch>/fixture.env --state-path <scratch>/state.json
pwsh -NoProfile -NonInteractive -File deploy/factory-watchdog.ps1 -DryRun -SkipDrift -SkipLearn -HermesHome <scratch> -PolicyPath <scratch>/policy.json -SlackOutbox <scratch>/outbox
```

Only Gatekeeper's clock and HTTP transport are replaced. A synthetic Linear
response exercises the real parsing, lane selection, authentication-error handling
and main entry point. Real socket connections and child dispatch are blocked.
No real API token is needed: all token-shaped inputs are unmistakably synthetic.
The child receives an allowlisted environment, scratch HOME/profile/temp/cache
paths and scratch `HERMES_HOME`. No parent credentials are inherited.

Four Gatekeeper fixtures cover idle, all four ready lanes, an in-flight build,
and HTTP 401. Ten watchdog fixtures cover healthy, disabled, paused, last error,
Gatekeeper auth failure, malformed expiry, environment-home resolution without
Windows path variables, `HERMES_HOME` alone with USERPROFILE, LOCALAPPDATA and
HOME all unset, the legacy LOCALAPPDATA default, and the HOME fallback.
Explicit parameter precedence is checked with a deliberately wrong environment
home. File hashes before/after ensure no dry-run state/outbox/fixture write.

Gatekeeper lane lines are compared in full. Watchdog findings and exit codes are
compared exactly after replacing only the scratch cron-root spelling with
`<scratch>/cron`; timestamps in the surrounding summary are not comparison data.
The fixed expected contract is `deploy/tests/fixtures/linux-host-expected.json`.
Both hosts must independently match it, preventing two identically broken runs
from being mistaken for success. The error fixture deliberately has no last-run
timestamp: PowerShell releases differ in automatic ISO-date deserialization.
This proof is not an exhaustive locale/date-format compatibility claim.

The `linux-host-portability` job in `.github/workflows/factory-checks.yml` runs
the same fixture runner on `ubuntu-latest` and uploads its JSON. Existing Windows
steps are unchanged; unittest discovery includes the smoke regression. Review
must check `gh pr checks` for the published head rather than infer remote CI from
local output. If Actions cannot start, the issue's build report records that
blocker and the separately executed Linux proof, not a fabricated green check.

Local Linux fallback used a pre-existing Linux container with Python 3.12.11 and
PowerShell 7.4.20, network disabled, read-only root/repo, and only a scratch proof
mount writable. WSL's installed Python was available, but `pwsh` was not. Supplying
scratch `HOME` and `TMPDIR` is necessary for PowerShell under a read-only root.
The image is a local verification convenience, not a production host selection
or a new CI dependency. Fixture command output and host versions belong in the
Linear build report; the committed expected JSON is reproducible on either host.

## Keep a Windows lane

| Repository / surface | Keep on Windows | Linux-side handoff (proposed, not implemented) |
| --- | --- | --- |
| `WolftacDigital/atlas_raw_only` | ATLAS installer install/upgrade and Windows service/desktop integration checks named by the approved issue. A Linux API/unit pass does not replace these. | Route the approved issue and exact published commit to a Windows worker; return native installer logs, exit codes and rollback proof before review can pass the Windows criteria. |
| `WolftacDigital/wolftac-factory` | `test_junctions.py`, junction setup/teardown, Windows deployment layout, Windows process-group/`taskkill` branches, watchdog drift/learn checks against that layout, Task Scheduler integration. | Retain the existing Windows test job; Linux carries the isolated Gatekeeper/watchdog smoke only. Keep OS-specific proof tied to the same SHA. |
| `WolftacDigital/logan-hermes-config` | Existing PC profile/service/schedule wiring stays on its owning machine until a separate approved migration work order. | Inventory and route host-specific checks; no copying credentials or rewriting live configuration as part of this issue. |
| CRM, Factory Console, Mission Control and other allowlisted web repos | No Windows-only requirement is inferred merely from a drive-letter checkout. Any client/installer/OS-specific criterion still requires Windows. | Classify by the approved issue's test plan. Port absolute allowlist entries through a separately approved host-local allowlist, not by weakening repository identity checks. |

A future handoff must retain one Linear work order, its SHA, exclusive ownership
of a native Windows worktree, and an explicit Windows-required check list. The
Linux scheduler should enqueue that bounded request and wait for verified native
proof, never translate a skipped Windows test into PASS. Windows workers fetch
the published branch into their own filesystem, not a shared cross-OS worktree.
Transport, leases/timeouts and recovery need a separate approved spec (M/L).
Approval and merge authority stay unchanged. No host, remote execution channel,
service unit or schedule is created by this document.

## Dependency decisions

| Key | Purpose / risk | Linux equivalent or existing seam | Effort |
| --- | --- | --- | --- |
| LOCALAPPDATA | Hermes homes, profile deployment targets, secret-file lookup and fixture layout. | Explicit `HERMES_HOME` / `-HermesHome` for runtime; host-native paths for deployment. Keep secrets local, never copy them through a task payload. | S runtime; M deployment |
| USERPROFILE | User skill roots, drift/learn lookup and test setup. | `$HOME` / `Path.home()` or explicit profile-root parameter; drift/learn deployment port remains deferred. | S/M |
| D-drive | Repo/worktree/live-checkout defaults and repository allowlists. | Explicit absolute Linux repo/worktree paths and `FACTORY_REPO_ALLOWLIST`; preserve identity and writable-root checks. | S per path; M validation |
| C-drive | Windows system tools, user checkouts and documentation examples. | Native Linux home/worktree paths; POSIX process-group termination for taskkill. Never mechanically rewrite foreign repo identities. | S/M |
| detached-process | Detached Gatekeeper workers and bounded test-runner process groups. | `start_new_session` and process groups already exist in Gatekeeper and `run_gate.py`; run native lifecycle tests before live use. | S verification |
| npm-cmd | Windows executable resolution at npm call sites. | `npm` on Linux; preserve Windows argv resolution where needed. No `npm.cmd` match exists in this repo at this revision; referenced incidents are in other repos and were not scanned here. | S per external call site |
| process-stop | Bounded child-tree termination in integration gates and review instructions. | Existing POSIX `os.killpg` branch; keep Windows `taskkill` for native workers. Do not broaden termination to unrelated processes. | S verification |
| scheduler | Independent watchdog schedule / installation guidance and alert wording. | An approved systemd timer/service with explicit user, paths, environment and restart policy; no systemd files are installed here. | M |
| junction | Shared live skill roots, deploy exclusions, drift/learn discovery and regression fixtures. | POSIX symlinks plus link-aware setup/teardown, repository identity and drift tests. A textual Junction-to-SymbolicLink substitution is not sufficient. | M |
| WSL | Windows-to-Linux execution, UNC path rejection, platform-specific instructions. | Native Linux commands and filesystem paths; keep WSL guidance for Windows workers and preserve path identity checks. | S/M |
| Windows-cmdlets | Scheduled-task commands and NTFS Junction creation; other Windows API searches included even when absent. | systemd for scheduling; POSIX symlinks for links; avoid Windows service/registry APIs. Inventory is not an execution recipe. | M |
| PowerShell-runtime | `pwsh` invocation is a runtime dependency, not intrinsically Windows-only. | PowerShell 7 on Linux; standard cmdlets remain available, but Junction/scheduler features need the replacements above. Do not substitute Windows PowerShell 5.1. | S runtime; M OS-specific features |

## Search method and exhaustive match ledger

The ledger below lists **every matching line**, grouped by file and category.
The category table above supplies the purpose, Linux equivalent and effort for
each entry; each ledger row additionally distinguishes runtime code, tests and
references. Repeated ports are intentionally not deduplicated. A line matching
two patterns appears under both categories. Counts mean matching **lines**, not
substring occurrences. Zero-hit searches are included. This document is excluded
to avoid self-matching; dotenv/auth/token stores are excluded and were not read.

Run from the repository root. The Python snippet constructs only the file list;
every content search is an actual `rg -n -i` call. For machine counting use
`rg --json -n -i` with the same patterns and files, count `type == 'match'` records,
and deduplicate `(path, line_number)` only for the unique-line total.

```python
import pathlib, subprocess
patterns = {
    'LOCALAPPDATA': 'LOCALAPPDATA',
    'USERPROFILE': 'USERPROFILE',
    'D-drive': 'D:[\\\\/]',
    'C-drive': 'C:[\\\\/]',
    'detached-process': 'DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|0x00000008|0x00000200|start_new_session',
    'npm-cmd': 'npm\\.cmd',
    'process-stop': 'taskkill',
    'scheduler': 'Task Scheduler|ScheduledTask|schtasks',
    'junction': 'junction|ItemType\\s+(?:Junction|SymbolicLink)',
    'WSL': '\\bwsl(?:\\.exe)?\\b',
    'Windows-cmdlets': '\\b(?:Get-WmiObject|Get-CimInstance|Invoke-CimMethod|Get-Acl|Set-Acl|Get-Service|New-Service|Start-Service|Stop-Service|Restart-Service|Get-ScheduledTask|Register-ScheduledTask|New-ScheduledTask\\w*|Set-ScheduledTask|Start-ScheduledTask|Stop-ScheduledTask|Unregister-ScheduledTask)\\b|HKLM:|HKCU:|ItemType\\s+Junction|cmd(?:\\.exe)?\\s+/c|icacls',
    'PowerShell-runtime': '\\bpwsh\\b',
}
paths = subprocess.check_output([
    'git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'
]).decode().split('\0')
files = sorted({p for p in paths if p and p != 'docs/linux-host.md'
    and not any(part.startswith('.env') or part in {'auth.json', 'tokens.json'}
                for part in pathlib.PurePosixPath(p).parts)})
for name, pattern in patterns.items():
    print(name, flush=True)
    subprocess.run(['rg', '-n', '-i', '--', pattern, *files], check=False)
```

Scanned **135 files**. **776 category/line pairs**, **702 unique file/line pairs**.

| Search key | Matching lines |
| --- | ---: |
| LOCALAPPDATA | 118 |
| USERPROFILE | 80 |
| D-drive | 67 |
| C-drive | 37 |
| detached-process | 17 |
| npm-cmd | 0 |
| process-stop | 7 |
| scheduler | 16 |
| junction | 239 |
| WSL | 100 |
| Windows-cmdlets | 13 |
| PowerShell-runtime | 82 |

### LOCALAPPDATA

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `AGENTS.md` | 207 | Operator guidance / example (not executed); LOCALAPPDATA |
| `README.md` | 17 | Operator guidance / example (not executed); LOCALAPPDATA |
| `canonical/wolftac-integrate/SKILL.md` | 64 | Operator guidance / example (not executed); LOCALAPPDATA |
| `canonical/wolftac-integrate/scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `canonical/wolftac-integrate/scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `deploy/deploy.ps1` | 50, 51, 52, 53, 54 | Runtime helper / host path; LOCALAPPDATA |
| `deploy/factory-gatekeeper.py` | 163 | Runtime helper / host path; LOCALAPPDATA |
| `deploy/factory-watchdog.ps1` | 64, 88, 522, 525, 533, 630, 633, 640 | Runtime helper / host path; LOCALAPPDATA |
| `deploy/junctions.json` | 4, 5, 6, 7 | Configuration / guard invariant; LOCALAPPDATA |
| `deploy/tests/factory-watchdog-drift.tests.ps1` | 297, 298, 302 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/linux_host_smoke.py` | 45, 165, 172, 177, 180 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/test_factory_watchdog.py` | 330 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/test_factory_watchdog_audit.py` | 38 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/test_junctions.py` | 66, 67, 114, 261, 285 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/test_run_gate.py` | 46, 248, 251, 254 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/tests/test_worker_schedules.py` | 34 | Regression fixture / assertion; LOCALAPPDATA |
| `deploy/worker-schedules.ps1` | 9 | Runtime helper / host path; LOCALAPPDATA |
| `docs/factory-gatekeeper.md` | 13, 269, 288, 289 | Operator guidance / example (not executed); LOCALAPPDATA |
| `docs/hermes-skill-junctions.md` | 53, 54, 97 | Operator guidance / example (not executed); LOCALAPPDATA |
| `docs/watchdog.md` | 96, 149, 230, 252, 253, 290, 349, 362, 385 | Operator guidance / example (not executed); LOCALAPPDATA |
| `plugins/wolftac-factory/skills/wolftac-integrate/SKILL.md` | 54 | Operator guidance / example (not executed); LOCALAPPDATA |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 66 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/claude-desktop/wolftac-integrate/scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/claude-desktop/wolftac-integrate/scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `ports/codex/wolftac-integrate-scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/codex/wolftac-integrate-scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `ports/codex/wolftac-integrate.md` | 60 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes-factory-integrate/deploy/bot-runtime.md` | 14 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes-factory-integrate/deploy/integrate-preflight.py` | 174 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL.md` | 54, 65, 116, 119, 120, 121, 122, 123, 124, 125, 126, 127, 148, 168 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes/wolftac-build/references/powershell-delivery-tests.md` | 5 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes/wolftac-integrate/SKILL.md` | 54, 65, 116, 119, 120, 121, 122, 123, 124, 125, 126, 127, 148 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes/wolftac-integrate/scripts/linear_bot.py` | 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 23, 63 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes/wolftac-integrate/scripts/slack_bot.py` | 68 | Runtime helper / host path; LOCALAPPDATA |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 121, 123 | Operator guidance / example (not executed); LOCALAPPDATA |
| `ports/hermes/wolftac-spec/SKILL.md` | 81 | Operator guidance / example (not executed); LOCALAPPDATA |

### USERPROFILE

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `AGENTS.md` | 169 | Operator guidance / example (not executed); USERPROFILE |
| `canonical/wolftac-integrate/SKILL.md` | 152 | Operator guidance / example (not executed); USERPROFILE |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `deploy/deploy.ps1` | 24, 25, 26, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 41, 42, 43, 56, 57, 58, 59, 290, 316 | Runtime helper / host path; USERPROFILE |
| `deploy/factory-watchdog.ps1` | 88, 92, 96, 125, 127, 128, 521, 524, 532, 629, 632, 639 | Runtime helper / host path; USERPROFILE |
| `deploy/tests/factory-watchdog-drift.tests.ps1` | 283, 284, 293, 297, 298, 301, 302, 304 | Regression fixture / assertion; USERPROFILE |
| `deploy/tests/linux_host_smoke.py` | 45, 166, 168, 172, 181 | Regression fixture / assertion; USERPROFILE |
| `deploy/tests/test_factory_watchdog.py` | 95, 327, 329 | Regression fixture / assertion; USERPROFILE |
| `deploy/tests/test_factory_watchdog_audit.py` | 36, 62 | Regression fixture / assertion; USERPROFILE |
| `deploy/tests/test_junctions.py` | 113 | Regression fixture / assertion; USERPROFILE |
| `deploy/tests/test_run_gate.py` | 45 | Regression fixture / assertion; USERPROFILE |
| `docs/watchdog.md` | 96, 99, 329 | Operator guidance / example (not executed); USERPROFILE |
| `plugins/wolftac-factory/skills/wolftac-integrate/SKILL.md` | 132 | Operator guidance / example (not executed); USERPROFILE |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 119, 120, 121, 122, 123, 124, 125, 126, 127 | Operator guidance / example (not executed); USERPROFILE |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `ports/codex/wolftac-integrate.md` | 144 | Operator guidance / example (not executed); USERPROFILE |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `ports/hermes/wolftac-build/references/existing-feature-branch-main-integration.md` | 27 | Operator guidance / example (not executed); USERPROFILE |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 23 | Runtime helper / host path; USERPROFILE |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 121, 123 | Operator guidance / example (not executed); USERPROFILE |

### D-drive

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `canonical/wolftac-build/SKILL.md` | 202 | Operator guidance / example (not executed); D-drive |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `canonical/wolftac-integrate/scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `canonical/wolftac-review/SKILL.md` | 121 | Operator guidance / example (not executed); D-drive |
| `deploy/junction-setup.ps1` | 70, 71 | Runtime helper / host path; D-drive |
| `deploy/repo-allowlist.json` | 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15 | Configuration / guard invariant; D-drive |
| `deploy/tests/factory-watchdog-learn.tests.ps1` | 10 | Regression fixture / assertion; D-drive |
| `deploy/tests/test_junctions.py` | 83 | Regression fixture / assertion; D-drive |
| `deploy/tests/test_run_gate.py` | 267 | Regression fixture / assertion; D-drive |
| `docs/hermes-skill-junctions.md` | 4, 38, 40, 41, 42, 164, 264, 267, 272, 273, 278, 281, 284, 286 | Operator guidance / example (not executed); D-drive |
| `docs/watchdog.md` | 134, 249 | Operator guidance / example (not executed); D-drive |
| `plugins/wolftac-factory/skills/wolftac-build/SKILL.md` | 169 | Operator guidance / example (not executed); D-drive |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `plugins/wolftac-factory/skills/wolftac-review/SKILL.md` | 93 | Operator guidance / example (not executed); D-drive |
| `ports/claude-desktop/wolftac-build/SKILL.md` | 196 | Operator guidance / example (not executed); D-drive |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 285 | Operator guidance / example (not executed); D-drive |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `ports/claude-desktop/wolftac-integrate/scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `ports/claude-desktop/wolftac-review/SKILL.md` | 116 | Operator guidance / example (not executed); D-drive |
| `ports/codex/wolftac-build.md` | 194 | Operator guidance / example (not executed); D-drive |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `ports/codex/wolftac-integrate-scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `ports/codex/wolftac-review.md` | 117 | Operator guidance / example (not executed); D-drive |
| `ports/hermes-factory-integrate/deploy/bot-runtime.md` | 44, 105, 120, 141 | Operator guidance / example (not executed); D-drive |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `ports/hermes/wolftac-build/SKILL.md` | 270, 271, 372 | Operator guidance / example (not executed); D-drive |
| `ports/hermes/wolftac-build/references/build-execution-budget-and-batching.md` | 75 | Operator guidance / example (not executed); D-drive |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 107 | Runtime helper / host path; D-drive |
| `ports/hermes/wolftac-integrate/scripts/slack_bot.py` | 43 | Runtime helper / host path; D-drive |
| `ports/hermes/wolftac-review/SKILL.md` | 84, 175 | Operator guidance / example (not executed); D-drive |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 15, 16, 25 | Operator guidance / example (not executed); D-drive |
| `ports/hermes/wolftac-review/references/wsl-linear-factory-review-proof.md` | 12 | Operator guidance / example (not executed); D-drive |

### C-drive

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `canonical/wolftac-build/SKILL.md` | 205 | Operator guidance / example (not executed); C-drive |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `canonical/wolftac-review/SKILL.md` | 124 | Operator guidance / example (not executed); C-drive |
| `deploy/factory-watchdog.ps1` | 13, 89 | Runtime helper / host path; C-drive |
| `deploy/repo-allowlist.json` | 6 | Configuration / guard invariant; C-drive |
| `deploy/tests/test_build_skip_report.py` | 121 | Regression fixture / assertion; C-drive |
| `deploy/tests/test_run_gate.py` | 268 | Regression fixture / assertion; C-drive |
| `docs/watchdog.md` | 97, 99, 250, 273, 277 | Operator guidance / example (not executed); C-drive |
| `plugins/wolftac-factory/skills/wolftac-build/SKILL.md` | 170 | Operator guidance / example (not executed); C-drive |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `plugins/wolftac-factory/skills/wolftac-review/SKILL.md` | 94 | Operator guidance / example (not executed); C-drive |
| `ports/claude-desktop/wolftac-build/SKILL.md` | 198 | Operator guidance / example (not executed); C-drive |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 286 | Operator guidance / example (not executed); C-drive |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `ports/claude-desktop/wolftac-review/SKILL.md` | 117 | Operator guidance / example (not executed); C-drive |
| `ports/codex/wolftac-build.md` | 196 | Operator guidance / example (not executed); C-drive |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `ports/codex/wolftac-review.md` | 119 | Operator guidance / example (not executed); C-drive |
| `ports/hermes-factory-integrate/deploy/bot-runtime.md` | 108 | Operator guidance / example (not executed); C-drive |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `ports/hermes/wolftac-build/SKILL.md` | 277 | Operator guidance / example (not executed); C-drive |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; C-drive |
| `ports/hermes/wolftac-review/SKILL.md` | 84, 86, 176 | Operator guidance / example (not executed); C-drive |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 3, 14, 15, 18, 23, 77 | Operator guidance / example (not executed); C-drive |
| `ports/hermes/wolftac-spec/references/spec-review-hard-cases.md` | 31 | Operator guidance / example (not executed); C-drive |

### detached-process

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |
| `deploy/factory-gatekeeper.py` | 537, 539 | Runtime helper / host path; detached-process |
| `deploy/tests/test_factory_gatekeeper.py` | 205 | Regression fixture / assertion; detached-process |
| `docs/factory-gatekeeper.md` | 176, 177 | Operator guidance / example (not executed); detached-process |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 174, 175 | Runtime helper / host path; detached-process |

### npm-cmd

No matches. See the dependency decision above for the external-call-site boundary.

### process-stop

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `canonical/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `ports/claude-desktop/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `ports/codex/wolftac-integrate-scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `ports/hermes/wolftac-integrate/scripts/run_gate.py` | 155 | Runtime helper / host path; process-stop |
| `ports/hermes/wolftac-review/SKILL.md` | 92 | Operator guidance / example (not executed); process-stop |

### scheduler

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `deploy/factory-watchdog.ps1` | 112, 762, 1094 | Runtime helper / host path; scheduler |
| `deploy/tests/README.md` | 47 | Regression fixture / assertion; scheduler |
| `docs/watchdog.md` | 17, 104, 243, 254, 255, 257, 259, 261, 307, 310, 348, 384 | Operator guidance / example (not executed); scheduler |

### junction

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `canonical/wolftac-integrate/scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `deploy/deploy.ps1` | 16, 18, 63, 64, 66, 67, 68, 70, 76, 138, 139, 140, 143, 144, 145, 146, 163, 165, 174, 180, 181, 192, 193, 203, 207, 209, 230, 232, 238, 239, 240, 244, 261, 263, 266, 286, 287, 288 | Runtime helper / host path; junction |
| `deploy/factory-watchdog.ps1` | 37, 38, 93, 162, 168, 537, 538, 546, 548, 621, 622, 623, 636, 642, 644, 645, 646, 651, 654, 789 | Runtime helper / host path; junction |
| `deploy/invariants.json` | 487, 488, 490, 491, 492, 493, 494 | Configuration / guard invariant; junction |
| `deploy/junction-lib.ps1` | 2, 4, 15, 27, 50, 57, 86, 91, 92, 102, 109, 144, 149, 155, 163, 187 | Runtime helper / host path; junction |
| `deploy/junction-setup.ps1` | 2, 5, 6, 7, 11, 13, 14, 20, 27, 29, 34, 37, 43, 59, 60, 63, 71, 76, 94, 100, 103, 107, 122, 137, 144, 155, 182, 184, 191 | Runtime helper / host path; junction |
| `deploy/junction-teardown.ps1` | 2, 6, 7, 8, 10, 34, 43, 46, 68, 85, 100, 139 | Runtime helper / host path; junction |
| `deploy/junctions.json` | 2 | Configuration / guard invariant; junction |
| `deploy/tests/README.md` | 13, 14, 16, 23, 26 | Regression fixture / assertion; junction |
| `deploy/tests/factory-watchdog-drift.tests.ps1` | 65, 66 | Regression fixture / assertion; junction |
| `deploy/tests/factory-watchdog-learn.tests.ps1` | 7, 8, 31, 41, 42, 43, 44, 45, 48, 62, 65, 76, 199, 203, 204 | Regression fixture / assertion; junction |
| `deploy/tests/test_factory_watchdog.py` | 51, 324 | Regression fixture / assertion; junction |
| `deploy/tests/test_junctions.py` | 1, 4, 31, 32, 56, 65, 70, 82, 86, 89, 90, 107, 121, 151, 212, 221, 235, 242, 246, 252, 253 | Regression fixture / assertion; junction |
| `docs/drift-reconciliation.md` | 16 | Operator guidance / example (not executed); junction |
| `docs/hermes-skill-junctions.md` | 1, 3, 20, 27, 28, 29, 30, 31, 34, 40, 41, 42, 47, 62, 65, 68, 70, 78, 81, 91, 93, 106, 128, 130, 136, 141, 144, 149, 158, 171, 174, 177, 179, 184, 187, 190, 192, 194, 197, 198, 200, 210, 222, 225, 231, 237, 241, 263, 272, 273, 279, 286 | Operator guidance / example (not executed); junction |
| `docs/watchdog.md` | 92, 134, 135, 140, 160, 165, 331, 333 | Operator guidance / example (not executed); junction |
| `plugins/wolftac-factory/skills/wolftac-integrate/scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `ports/claude-desktop/wolftac-integrate/scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `ports/codex/wolftac-integrate-scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `ports/hermes/wolftac-integrate/scripts/slack_bot.py` | 106 | Runtime helper / host path; junction |
| `ports/hermes/wolftac-review/SKILL.md` | 88 | Operator guidance / example (not executed); junction |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 46, 47 | Operator guidance / example (not executed); junction |
| `ports/hermes/wolftac-spec/references/spec-review-hard-cases.md` | 14 | Operator guidance / example (not executed); junction |

### WSL

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `.gitattributes` | 1 | Operator guidance / example (not executed); WSL |
| `AGENTS.md` | 79, 170 | Operator guidance / example (not executed); WSL |
| `README.md` | 35, 108, 109, 196 | Operator guidance / example (not executed); WSL |
| `canonical/wolftac-build/SKILL.md` | 152, 204, 206 | Operator guidance / example (not executed); WSL |
| `canonical/wolftac-review/SKILL.md` | 123 | Operator guidance / example (not executed); WSL |
| `deploy/deploy.ps1` | 11, 93, 94, 97, 98, 99, 100, 101, 107, 120, 126, 243 | Runtime helper / host path; WSL |
| `deploy/junction-lib.ps1` | 27 | Runtime helper / host path; WSL |
| `docs/drift-reconciliation.md` | 19 | Operator guidance / example (not executed); WSL |
| `docs/hermes-skill-junctions.md` | 6 | Operator guidance / example (not executed); WSL |
| `plugins/wolftac-factory/skills/wolftac-build/SKILL.md` | 170 | Operator guidance / example (not executed); WSL |
| `plugins/wolftac-factory/skills/wolftac-review/SKILL.md` | 93 | Operator guidance / example (not executed); WSL |
| `ports/claude-desktop/wolftac-build/SKILL.md` | 197 | Operator guidance / example (not executed); WSL |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 142, 285 | Operator guidance / example (not executed); WSL |
| `ports/claude-desktop/wolftac-review/SKILL.md` | 117 | Operator guidance / example (not executed); WSL |
| `ports/codex/wolftac-build.md` | 195 | Operator guidance / example (not executed); WSL |
| `ports/codex/wolftac-review.md` | 118 | Operator guidance / example (not executed); WSL |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL.md` | 190, 191, 192, 193, 194, 195 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-build/SKILL.md` | 194, 195, 254, 255, 276, 278, 299, 300 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-build/references/wd180-hermes-kanban-surface-integration.md` | 14, 28, 30 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-build/references/wsl-linear-build-worktree-and-proof.md` | 1, 3, 7, 11, 21, 25, 32, 34, 36, 42, 45, 48 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-build/references/wsl-webgl-build-pitfalls.md` | 1, 3, 7, 8, 9 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-integrate/SKILL.md` | 172, 173, 174, 175, 176, 177 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/SKILL.md` | 68, 70, 72, 74, 76, 176 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/hermes-kanban-bridge-review.md` | 15 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/memory-galaxy-vault-review.md` | 7 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/wd177-setup-wizard-review-hydration.md` | 20 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/windows-linear-factory-review-proof.md` | 121 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/wsl-linear-factory-review-proof.md` | 1, 3, 7, 10, 12 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/wsl-live-maintenance-proxy-review.md` | 1, 3, 8 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/wsl-router-status-health-review.md` | 1, 11, 22 | Operator guidance / example (not executed); WSL |
| `ports/hermes/wolftac-review/references/wsl-token-gated-live-review.md` | 1, 3, 7 | Operator guidance / example (not executed); WSL |
| `share/build-your-own-agent-factory.html` | 528, 602 | Operator guidance / example (not executed); WSL |

### Windows-cmdlets

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `deploy/invariants.json` | 490 | Configuration / guard invariant; Windows-cmdlets |
| `deploy/junction-setup.ps1` | 182 | Runtime helper / host path; Windows-cmdlets |
| `deploy/tests/factory-watchdog-learn.tests.ps1` | 43 | Regression fixture / assertion; Windows-cmdlets |
| `deploy/tests/test_junctions.py` | 246 | Regression fixture / assertion; Windows-cmdlets |
| `docs/watchdog.md` | 254, 255, 257, 259, 261, 307, 384 | Operator guidance / example (not executed); Windows-cmdlets |
| `ports/hermes/wolftac-review/SKILL.md` | 92 | Operator guidance / example (not executed); Windows-cmdlets |
| `ports/hermes/wolftac-spec/references/spec-review-hard-cases.md` | 36 | Operator guidance / example (not executed); Windows-cmdlets |

### PowerShell-runtime

| File | Every matching line | Purpose at this location (decision key above) |
| --- | --- | --- |
| `.github/workflows/factory-checks.yml` | 13 | Configuration / guard invariant; PowerShell-runtime |
| `README.md` | 83, 104, 105 | Operator guidance / example (not executed); PowerShell-runtime |
| `canonical/wolftac-integrate/SKILL.md` | 332 | Operator guidance / example (not executed); PowerShell-runtime |
| `deploy/deploy.ps1` | 4, 5, 7, 8 | Runtime helper / host path; PowerShell-runtime |
| `deploy/factory-watchdog.ps1` | 11, 12, 13, 110 | Runtime helper / host path; PowerShell-runtime |
| `deploy/junction-setup.ps1` | 5, 6, 7, 71 | Runtime helper / host path; PowerShell-runtime |
| `deploy/junction-teardown.ps1` | 6, 7, 8, 142 | Runtime helper / host path; PowerShell-runtime |
| `deploy/tests/README.md` | 6, 7, 98 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/factory-watchdog-drift.tests.ps1` | 4, 110, 212, 277, 300, 301 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/factory-watchdog-learn.tests.ps1` | 4, 99, 230 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/linux_host_smoke.py` | 155, 198 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_factory_watchdog.py` | 20, 33, 92 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_factory_watchdog_audit.py` | 12, 20, 60 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_junctions.py` | 4, 17, 89, 138, 142 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_linux_host.py` | 12 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_run_gate.py` | 282, 283, 291 | Regression fixture / assertion; PowerShell-runtime |
| `deploy/tests/test_worker_schedules.py` | 13, 25, 66, 150 | Regression fixture / assertion; PowerShell-runtime |
| `docs/ci-bootstrap.md` | 23 | Operator guidance / example (not executed); PowerShell-runtime |
| `docs/drift-reconciliation.md` | 31 | Operator guidance / example (not executed); PowerShell-runtime |
| `docs/factory-gatekeeper.md` | 302, 303 | Operator guidance / example (not executed); PowerShell-runtime |
| `docs/hermes-skill-junctions.md` | 40, 41, 42, 263, 272, 273, 278, 286 | Operator guidance / example (not executed); PowerShell-runtime |
| `docs/watchdog.md` | 43, 250, 251, 254, 270, 272, 276, 277, 284, 368 | Operator guidance / example (not executed); PowerShell-runtime |
| `plugins/wolftac-factory/skills/wolftac-integrate/SKILL.md` | 272 | Operator guidance / example (not executed); PowerShell-runtime |
| `ports/claude-desktop/wolftac-integrate/SKILL.md` | 329 | Operator guidance / example (not executed); PowerShell-runtime |
| `ports/codex/wolftac-integrate.md` | 313 | Operator guidance / example (not executed); PowerShell-runtime |
| `ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL.md` | 322 | Operator guidance / example (not executed); PowerShell-runtime |
| `ports/hermes/wolftac-build/references/powershell-delivery-tests.md` | 15 | Operator guidance / example (not executed); PowerShell-runtime |
| `ports/hermes/wolftac-integrate/SKILL.md` | 319 | Operator guidance / example (not executed); PowerShell-runtime |
