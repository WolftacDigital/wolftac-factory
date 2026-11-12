"""WD-549 behavior checks. Every watchdog invocation is -DryRun with fake credentials.

Run: python -B -m unittest discover -s deploy/tests -p test_factory_watchdog.py -v
Optional TP4: set WD549_REAL_CRON to the real cron directory (read-only).
No live .env is read. TemporaryDirectory uses the caller's TMPDIR when set.
"""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/factory-watchdog.ps1"
PWSH = shutil.which("pwsh")
KEY = "LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT"
FAKES = ("fixture-only-bot-549", "fixture-only-secret-549", "fixture-only-app-549")


def without_wd584(text):
    """Allow only the three reviewed WD-584 forms, not arbitrary changed code."""
    begin = '    # WD-584 BEGIN: stale-copy\n'
    end = '    # WD-584 END: stale-copy\n'
    modified = 'WD-584' in text or 'if ($RepoRoot) { . $DeployScript' in text
    if modified:
        assert text.count('WD-584') == 2, 'WD-584 delimiter count'
        assert text.count(begin) == text.count(end) == 1, 'WD-584 malformed delimiters'
        left, right = text.index(begin), text.index(end) + len(end)
        assert left < right, 'WD-584 delimiter order'
        assert text[:left].endswith('    $script:driftAvailable = $true\n'), 'WD-584 block location'
        assert text[right:].startswith('    foreach ($p in $Pairs) {\n'), 'WD-584 block extent'
        text = text[:left] + text[right:]
    for function, following, indent in [('Measure-Drift', 'Add-LearnUnreadable', '        '),
                                         ('Measure-Learn', 'Format-LearnLine', '            ')]:
        left = text.index('function ' + function + ' {')
        right = text.index('function ' + following, left)
        old = indent + '. $DeployScript *> $null\n'
        new = (indent + 'if ($RepoRoot) { . $DeployScript -RepoRoot $RepoRoot *> $null }\n' +
               indent + 'else { . $DeployScript *> $null }\n')
        part = text[left:right]
        expected = new if modified else old
        assert part.count(expected) == 1, 'WD-584 call-site shape: ' + function
        assert part.count('. $DeployScript') == (2 if modified else 1), 'WD-584 call-site count'
        text = text[:left] + part.replace(expected, old) + text[right:]
    assert text.count('. $DeployScript') == 2, 'WD-584 unexpected call-site'
    old = '                $ns = & git -C $wt diff --numstat HEAD -- $path 2>$null\n'
    new = old.replace('diff --numstat', 'diff --no-ext-diff --no-textconv --numstat')
    expected = new if modified else old
    left = text.index('function Measure-Learn {')
    right = text.index('function Format-LearnLine', left)
    assert text[left:right].count(expected) == 1, 'WD-584 numstat location/shape'
    assert text.count('--no-ext-diff') == text.count('--no-textconv') == int(modified), 'WD-584 unexpected flags'
    return text.replace(expected, old)


def timestamp(days=10, seconds=0):
    return (datetime.now(timezone.utc) + timedelta(days=days, seconds=seconds)).isoformat()


def findings(text):
    return [line.strip() for line in text.splitlines() if line.startswith("  - ")]


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd549-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cron = self.make_cron("cron")
        self.envfile = self.root / "fixture.env"
        self.write_expiry(timestamp())
        # Both the extracted main script and the branch use the SAME drift input.
        # A default relative DeployScript would differ with the script's location.
        self.deploy = self.root / "fixture-deploy.ps1"
        # An actual clean scratch repo keeps /learn enabled, rather than suppressing
        # the new current-main check or treating an unknown worktree as clean.
        self.live = self.root / "live-skills"
        subprocess.run(["git", "init", "-q", str(self.live)], check=True,
                       capture_output=True, timeout=10)
        live_path = self.live.as_posix().replace("'", "''")
        self.deploy.write_text(f"$Pairs = @()\n$JunctionedRoots = @('{live_path}')\n",
                               encoding="utf-8", newline="\n")
        self.backups = self.root / "learn-backups"
        self.outbox = self.root / "slack-outbox"
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps({"slack_channel_id": "fixture", "approver_slack_user_id": "fixture",
                                          "fallback_identity": {"username": "fixture", "icon_emoji": ":test_tube:"}}))
        self.env = os.environ.copy()
        for key in ("SLACK_FACTORY_BOT_TOKEN", "LINEAR_FACTORY_BOT_TOKEN", "LINEAR_FACTORY_APP_ID",
                    "LINEAR_FACTORY_APP_SECRET", KEY):
            self.env.pop(key, None)

    def make_cron(self, name):
        cron = self.root / name
        cron.mkdir()
        (cron / "ticker_heartbeat").write_text("fixture\n")
        jobs = [{"id": "gk-549", "name": "Wolftac Factory Gatekeeper", "enabled": True,
                 "paused": False, "last_status": "ok", "schedule": {"minutes": 30},
                 "next_run_at": timestamp(1)}]
        (cron / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        return cron

    def write_expiry(self, value):
        lines = ["SLACK_FACTORY_BOT_TOKEN=fixture-only-slack-549",
                 f"LINEAR_FACTORY_BOT_TOKEN={FAKES[0]}", f"LINEAR_FACTORY_APP_SECRET={FAKES[1]}",
                 f"LINEAR_FACTORY_APP_ID={FAKES[2]}"]
        if value is not None:
            lines.append(f"{KEY}={value}")
        self.envfile.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    def output(self, name, text, age=0, cron=None):
        folder = (cron or self.cron) / "output/gk-549"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(text, encoding="utf-8")
        at = time.time() - age
        os.utime(path, (at, at))
        return path

    def run_watchdog(self, script=SCRIPT, cron=None, extra=(), deploy=None):
        before = self.envfile.read_bytes() if self.envfile.exists() else None
        state = self.cron / 'watchdog-state.json'
        before_state = state.read_bytes() if state.exists() else None
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script),
                                 "-DryRun", "-CronDir", str(cron or self.cron),
                                 "-EnvFile", str(self.envfile), "-PolicyPath", str(self.policy),
                                 "-UserProfile", str(Path.home()),
                                 "-LearnBackupRoot", str(self.backups),
                                 "-SlackOutbox", str(self.outbox),
                                 "-DeployScript", str(deploy or self.deploy), *extra],
                                env=self.env, capture_output=True, encoding="utf-8", timeout=30)
        text = result.stdout + result.stderr
        for fake in (*FAKES, "fixture-only-slack-549"):
            self.assertNotIn(fake, text)
        self.assertRegex(text, r"DRY RUN: no (?:Slack|Discord) post, no snapshot, no state written\.")
        self.assertEqual(before, self.envfile.read_bytes() if self.envfile.exists() else None)
        self.assertEqual(before_state, state.read_bytes() if state.exists() else None)
        self.assertFalse((self.cron / 'watchdog-drift-latest.txt').exists())
        self.assertFalse((self.cron / 'watchdog-learn-latest.txt').exists())
        self.assertFalse(self.backups.exists(), "DryRun must not create snapshots")
        self.assertFalse(self.outbox.exists(), "DryRun must not attempt alert delivery")
        return result.returncode, text

    def test_expiry_matrix(self):
        cases = [(timestamp(10), None), (timestamp(2), "expires"), (None, "missing"),
                 ("not-a-date", "unparseable"), ("", "missing"), (timestamp(-1), "expires"),
                 (timestamp(3, 60), None), (timestamp(3, -60), "expires"),
                 ('"' + timestamp() + '"', None), ("'" + timestamp() + "'", None),
                 ("2026-02-30T12:00:00Z", "unparseable"), ("11/01/2030", "unparseable"),
                 ("2030-01-01T12:00:00", "unparseable"), (FAKES[1], "unparseable")]
        for value, reason in cases:
            with self.subTest(reason=reason, index=cases.index((value, reason))):
                self.write_expiry(value)
                code, text = self.run_watchdog()
                rows = findings(text)
                self.assertEqual(len(rows), 1 if reason else 0)
                self.assertEqual(code, 0)  # WD-624: configuration, not liveness.
                self.assertIn('CONFIG: warning' if reason else 'CONFIG: clean', text)
                if reason:
                    self.assertIn("linear-token-expiry:", rows[0])
                    self.assertIn(reason, rows[0])
                    if reason == "expires":
                        self.assertIn(value[:10], rows[0])

    def test_offset_is_compared_in_utc(self):
        value = (datetime.now(timezone.utc) + timedelta(days=2)).astimezone(timezone(timedelta(hours=-5)))
        self.write_expiry(value.isoformat())
        code, text = self.run_watchdog()
        self.assertEqual(code, 0)
        self.assertIn(value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), findings(text)[0])
        self.assertIn("Z (expired", findings(text)[0])

    def test_only_newest_output_controls_rejection(self):
        self.output("z-old.txt", "linear-auth-failed " + FAKES[0], age=120)
        self.output("a-new.txt", "healthy " + FAKES[1])
        self.assertEqual(self.run_watchdog()[0], 0)
        self.output("a-new.txt", "linear-auth-failed " + " ".join(FAKES))
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
        self.assertEqual(len(findings(text)), 1)
        self.assertIn("gatekeeper-linear-auth:", findings(text)[0])
        # A newest empty file is not a rejection and must not resurrect an older one.
        self.output("a-new.txt", "")
        self.assertEqual(self.run_watchdog()[0], 0)

    def test_missing_job_directory_and_file_do_not_alert(self):
        self.assertEqual(self.run_watchdog()[0], 0)
        (self.cron / "output/gk-549").mkdir(parents=True)
        self.assertEqual(self.run_watchdog()[0], 0)
        self.output("orphan.txt", "linear-auth-failed")
        jobs = json.loads((self.cron / "jobs.json").read_text())
        jobs["jobs"][0]["name"] = "Wolftac Factory Build Worker"
        (self.cron / "jobs.json").write_text(json.dumps(jobs))
        self.assertEqual(self.run_watchdog()[0], 0)

    def test_multiple_cron_directories_one_expiry_finding(self):
        second = self.make_cron("cron2")
        self.output("tick.txt", "linear-auth-failed", cron=second)
        self.write_expiry(None)
        code, text = self.run_watchdog(cron=f"{self.cron};{second}")
        self.assertEqual(code, 1)
        rows = findings(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum("linear-token-expiry:" in row for row in rows), 1)
        self.assertEqual(sum("gatekeeper-linear-auth:" in row for row in rows), 1)
        self.assertIn(str(second), next(row for row in rows if "gatekeeper-linear-auth:" in row))
        self.assertFalse((second / "watchdog-state.json").exists())

    def test_gatekeeper_check_not_filtered_by_worker_pattern(self):
        self.output("tick.txt", "linear-auth-failed")
        _, text = self.run_watchdog(extra=("-JobNamePattern", "*Build Worker*"))
        self.assertEqual(sum("gatekeeper-linear-auth:" in row for row in findings(text)), 1)

    def test_missing_env_is_config_warning(self):
        self.envfile.unlink()
        code, text = self.run_watchdog()
        self.assertEqual(code, 0)
        self.assertIn('CONFIG: warning', text)
        self.assertIn("cannot read expiry date", text)
        self.assertNotIn("watchdog cannot read SLACK_FACTORY_BOT_TOKEN", text)

    def safety_worker(self, schedule, **changes):
        job = {"id": "worker-576", "name": "Wolftac Factory Build Worker - agentic (Mason)",
               "enabled": True, "paused": False, "no_agent": False, "last_status": "ok",
               "next_run_at": timestamp(1), "schedule": schedule}
        job.update(changes)
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [job]}))
        return job

    def test_worker_safety_net_boundaries(self):
        for minutes in (5, 119, 120, 240):
            with self.subTest(minutes=minutes):
                job = self.safety_worker({"kind": "interval", "minutes": minutes})
                code, text = self.run_watchdog()
                rows = [r for r in findings(text) if "worker-safety-net" in r]
                self.assertEqual(len(rows), int(minutes < 120))
                self.assertEqual(code, 0)  # Schedule-only check is configuration.
                if rows:
                    self.assertIn(job["name"], rows[0])
                    self.assertIn(f"every {minutes}m", rows[0])
                    self.assertIn("120m safety net", rows[0])
                # One report row as well as the separate findings summary.
                reports = [r for r in text.splitlines() if "worker-safety-net" in r
                           and r.lstrip().startswith(("ok ", "FAIL "))]
                self.assertEqual(len(reports), 1, text)
                self.assertIn("FAIL" if minutes < 120 else "ok", reports[0])

    def test_worker_safety_net_exclusions(self):
        cases = [({"kind": "interval", "minutes": 30}, {"name": "Wolftac Factory Gatekeeper"}),
                 ({"kind": "interval", "minutes": 5}, {"no_agent": True}),
                 ({"kind": "interval", "minutes": 5}, {"enabled": False}),
                 ({"kind": "interval", "minutes": 5}, {"paused": True}),
                 ({"minutes": 5}, {}),
                 ({"kind": "interval", "minutes": 5}, {"name": "Unrelated worker"})]
        for schedule, changes in cases:
            with self.subTest(schedule=schedule, changes=changes):
                self.safety_worker(schedule, **changes)
                _, text = self.run_watchdog()
                self.assertNotIn("worker-safety-net", text)

    def test_worker_safety_net_noninterval(self):
        self.safety_worker({"kind": "cron", "expr": "*/5 * * * *"})
        code, text = self.run_watchdog()
        rows = [r for r in findings(text) if "worker-safety-net" in r]
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 1)
        self.assertIn("kind 'cron' is not an interval", rows[0])

    def test_worker_safety_net_configured_minimum(self):
        self.safety_worker({"kind": "interval", "minutes": 5})
        code, text = self.run_watchdog(extra=("-WorkerMinIntervalMinutes", "5"))
        self.assertEqual(code, 0)
        self.assertEqual(findings(text), [])
        self.assertIn("minimum 5m", text)

    def baseline(self):
        text = subprocess.check_output(["git", "show", "origin/main:deploy/factory-watchdog.ps1"],
                                       cwd=ROOT, encoding="utf-8")
        path = self.root / "main-watchdog.ps1"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path, text

    def test_existing_checks_and_alerting_are_unchanged(self):
        basepath, base = self.baseline()
        current = SCRIPT.read_text(encoding="utf-8")
        # WD-580 adds an independent fourth concern. Remove only its explicitly
        # delimited additions when comparing the earlier checks byte-for-byte.
        def without_audit(text):
            text = re.sub(r'^[ \t]*# AUDIT BEGIN: [^\n]+\n.*?^[ \t]*# AUDIT END: [^\n]+\n\n?',
                          '', text, flags=re.MULTILINE | re.DOTALL)
            return text.replace(' -or -not $auditDelivered', '')
        base, current = without_audit(base), without_audit(current)
        base, current = without_wd584(base), without_wd584(current)
        # WD-551 intentionally replaces delivery/routing and adds pending retries.
        # Preserve the checks, rather than freezing the retired Slack transport.
        for start, end in [("function Test-LinearTokenExpiry", "function "),
                           ("function Test-GatekeeperAuth", "function Test-CronDirectory"),
                           ("function Measure-Drift", "# ── run the checks")]:
            def section(text):
                pos = text.index(start)
                return text[pos:text.index(end, pos + len(start))]
            self.assertEqual(section(base).replace("Slack", "Discord"), section(current).replace("Slack", "Discord"))
        # Only the new check dispatch is inserted inside the existing cron checks.
        def checks(text):
            part = text[text.index("function Test-CronDirectory"):text.index("function Get-StatePath")]
            part = part.replace("    Test-GatekeeperAuth $Dir $jobs\n", "").replace(
                "    Test-GatekeeperAudit $Dir $jobs\n", "")
            # The recent-failure check is a deliberate later addition to the cron checks;
            # drop its block on both sides so everything else stays pinned to main.
            start = part.find("        # Hermes records a failed run in last_failure")
            if start >= 0:
                part = part[:start] + part[part.index("        # Overdue is measured", start):]
            return part
        self.assertEqual(checks(base), checks(current))
        jobs = json.loads((self.cron / "jobs.json").read_text())
        jobs["jobs"][0].update(enabled=False, paused=True, last_status="error",
                              last_error="fixture failure", next_run_at=timestamp(-1))
        (self.cron / "jobs.json").write_text(json.dumps(jobs))
        before = self.run_watchdog(script=basepath)
        after = self.run_watchdog()
        self.assertEqual(before[0], after[0])
        self.assertEqual(findings(before[1]), findings(after[1]))
        self.assertEqual(len(findings(after[1])), 4)

    def test_wd584_normalizer_negative_controls(self):
        from unittest.mock import patch
        current = SCRIPT.read_text(encoding='utf-8')
        cases = [
            # WD-551 deliberately unfroze delivery, transitions and the delivery exit code, so
            # these three controls mutate checks the guard still pins to main instead.
            ('expiry check', current.replace('expiry date missing', 'expiry date absent'), None),
            ('threshold', current.replace('$ageMin -gt $HeartbeatMaxAgeMinutes', '$ageMin -ge $HeartbeatMaxAgeMinutes'), None),
            ('drift check', current.replace('cannot compare: deploy script not found', 'cannot compare: deploy script missing'), None),
            ('duplicate block', current.replace('    # WD-584 END: stale-copy\n',
                '    # WD-584 END: stale-copy\n    # WD-584 BEGIN: stale-copy\n    # WD-584 END: stale-copy\n'), 'delimiter count'),
            ('malformed block', current.replace('# WD-584 END: stale-copy', '# WD-584 END: wrong'), 'malformed delimiters'),
            ('call shape', current.replace('if ($RepoRoot) { . $DeployScript', 'if ($RepoRoot -ne "") { . $DeployScript'), 'call-site shape'),
            ('flag shape', current.replace('--no-ext-diff --no-textconv', '--no-textconv --no-ext-diff'), 'numstat location/shape'),
        ]
        for name, mutated, message in cases:
            with self.subTest(name=name):
                self.assertNotEqual(current, mutated, 'mutation must be non-vacuous')
                path = self.root / 'mutated-watchdog.ps1'
                path.write_text(mutated, encoding='utf-8', newline='\n')
                parser = self.root / 'parse.ps1'
                parser.write_text('param($Path)\n$t=$null;$e=$null;'
                    '[void][Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e);'
                    'if($e.Count){exit 1};exit 0\n', encoding='utf-8')
                result = subprocess.run([PWSH, '-NoProfile', '-File', str(parser), str(path)],
                                        env=self.env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, 'negative control must remain valid PowerShell')
                # Exercise the actual existing guard and its assertions, not a facsimile.
                with patch(__name__ + '.SCRIPT', path):
                    with self.assertRaises(AssertionError) as failure:
                        self.test_existing_checks_and_alerting_are_unchanged()
                if message:
                    self.assertIn(message, str(failure.exception))
                else:
                    self.assertNotIn('WD-584', str(failure.exception).split('\n')[0])

    def test_drift_check_still_runs_with_linear_checks(self):
        basepath, _ = self.baseline()
        missing = self.root / "missing-deploy.ps1"
        before = self.run_watchdog(script=basepath, deploy=missing)
        after = self.run_watchdog(deploy=missing)
        self.assertEqual(before[0], after[0])
        self.assertEqual(findings(before[1]), findings(after[1]))
        self.assertIn("DRIFT: CANNOT COMPARE", after[1])
        self.assertIn("LEARN: CANNOT CHECK", after[1])
        self.assertEqual(len(findings(after[1])), 2)
        for row in findings(after[1]):
            self.assertIn("deploy script not found", row)

    def test_pending_learn_edits_match_main_without_snapshotting(self):
        basepath, _ = self.baseline()
        pending = self.live / "pending.txt"
        pending.write_text("fixture line one\nfixture line two\n", encoding="utf-8", newline="\n")
        before_bytes = pending.read_bytes()
        before = self.run_watchdog(script=basepath)
        after = self.run_watchdog()
        self.assertEqual(before[0], 0)
        self.assertEqual(before[0], after[0])
        self.assertEqual(findings(before[1]), findings(after[1]))
        self.assertEqual(findings(after[1]), ["- PENDING  pending.txt (+2/-0, untracked)"])
        self.assertIn("LEARN: 1 uncommitted file(s)", after[1])
        self.assertEqual(before_bytes, pending.read_bytes())

    def test_unknown_learn_worktree_is_not_silenced(self):
        basepath, _ = self.baseline()
        self.deploy.write_text("$Pairs = @()\n", encoding="utf-8", newline="\n")
        before = self.run_watchdog(script=basepath)
        after = self.run_watchdog()
        self.assertEqual(before[0], after[0])
        self.assertEqual(findings(before[1]), findings(after[1]))
        self.assertEqual(len(findings(after[1])), 1)
        self.assertIn("LEARN: CANNOT CHECK", after[1])
        self.assertIn("resolved no junctioned skill roots", findings(after[1])[0])

    def test_fixture_drift_does_not_require_windows_profile_environment(self):
        # Ubuntu Actions has HOME, not USERPROFILE. Supply the same explicit
        # operator profile to both scripts instead of depending on host defaults.
        self.env.pop("USERPROFILE", None)
        self.env.pop("LOCALAPPDATA", None)
        basepath, _ = self.baseline()
        for script in (basepath, SCRIPT):
            with self.subTest(script=script.name):
                code, text = self.run_watchdog(script=script)
                self.assertEqual(code, 0)
                self.assertEqual(findings(text), [])
                self.assertIn("DRIFT CLEAN:", text)
                self.assertIn("LEARN CLEAN:", text)

    def seed_state(self, **fields):
        (self.cron / 'watchdog-state.json').write_text(json.dumps(fields), encoding='utf-8')

    def change_gatekeeper(self, **fields):
        path = self.cron / 'jobs.json'
        data = json.loads(path.read_text())
        data['jobs'][0].update(fields)
        path.write_text(json.dumps(data))

    def test_new_outage_is_not_hidden_by_old_config_state(self):
        self.write_expiry(None)
        self.seed_state(status='unhealthy', findings=['linear-token-expiry: missing'],
                        last_alert_at=timestamp(0))
        self.change_gatekeeper(enabled=False)
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
        self.assertEqual(text.count('DRY RUN POST: :warning:'), 1)
        self.assertIn('new: [' + str(self.cron) + '] Wolftac Factory Gatekeeper: DISABLED', text)
        self.assertNotIn('still unhealthy', text)
        self.assertIn('1 liveness finding(s) still open', text)

    def test_partial_recovery_and_new_key_during_existing_outage(self):
        prefix = f'[{self.cron}] Wolftac Factory Gatekeeper'
        keys = {prefix + '|DISABLED': timestamp(-1), prefix + '|PAUSED': timestamp(-1)}
        self.seed_state(status='unhealthy', open_findings=keys, last_alert_at=timestamp(0))
        self.change_gatekeeper(enabled=False, paused=False)
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
        self.assertIn('recovered: ' + prefix + '|PAUSED', text)
        self.assertNotIn('new:', text)
        self.assertIn('1 liveness finding(s) still open', text)
        self.change_gatekeeper(enabled=False, paused=True, last_status='error', last_error='synthetic')
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
        self.assertIn('new: ' + prefix + ': last_status=error', text)
        self.assertNotIn('new: ' + prefix + ': DISABLED', text)

    def test_full_recovery_reads_as_recovery(self):
        prefix = f'[{self.cron}] Wolftac Factory Gatekeeper'
        self.seed_state(status='unhealthy', open_findings={prefix + '|DISABLED': timestamp(-1)},
                        finding_texts={prefix + '|DISABLED': prefix + ': DISABLED (enabled: false)'},
                        last_alert_at=timestamp(0))
        code, text = self.run_watchdog()
        self.assertEqual(code, 0)
        self.assertEqual(text.count('DRY RUN POST:'), 1)
        self.assertIn('DRY RUN POST: :white_check_mark: *Factory watchdog: recovered*', text)
        self.assertIn('recovered: ' + prefix + ': DISABLED (enabled: false)', text)
        self.assertNotIn('need attention', text)

    def test_number_only_changes_keep_key_and_nag_window(self):
        prefix = f'[{self.cron}] Wolftac Factory Gatekeeper'
        self.seed_state(status='unhealthy', open_findings={prefix + '|OVERDUE': timestamp(-1)},
                        finding_texts={prefix + '|OVERDUE': prefix + ': OVERDUE - next_run_at was 338m ago'},
                        last_alert_at=timestamp(0))
        for minutes in (398, 4398):
            self.change_gatekeeper(next_run_at=timestamp(0, -minutes * 60))
            code, text = self.run_watchdog()
            self.assertEqual(code, 1)
            self.assertIn('OVERDUE', text)
            self.assertNotIn('DRY RUN POST:', text)
        self.seed_state(status='unhealthy', open_findings={prefix + '|OVERDUE': timestamp(-1)},
                        last_alert_at=timestamp(0, -13 * 3600))
        self.assertIn('still unhealthy after 13h', self.run_watchdog()[1])

    def test_config_daily_nag_recovery_and_no_liveness_failure(self):
        self.write_expiry(None)
        code, text = self.run_watchdog()
        self.assertEqual(code, 0)
        self.assertIn('HEALTHY:', text)
        self.assertEqual(text.count('DRY RUN POST:'), 1)
        self.assertIn(KEY, text)
        for hours, posts in ((0, 0), (23, 0), (24, 1), (25, 1)):
            self.seed_state(status='healthy', open_findings={}, config_status='warning',
                            config_last_alert_at=timestamp(0, -hours * 3600))
            code, text = self.run_watchdog()
            self.assertEqual(code, 0)
            self.assertEqual(text.count('DRY RUN POST:'), posts)
        self.write_expiry(timestamp())
        self.assertIn('Factory configuration recovered', self.run_watchdog()[1])

    def test_old_and_malformed_state_has_no_prior_keys(self):
        self.change_gatekeeper(paused=True)
        for state in ({}, {'status': 'unhealthy', 'findings': ['old']}, 'broken'):
            if isinstance(state, dict):
                self.seed_state(**state)
            else:
                (self.cron / 'watchdog-state.json').write_text(state)
            code, text = self.run_watchdog()
            self.assertEqual(code, 1)
            self.assertIn('new:', text)
            self.assertNotIn('recovered:', text)

    def test_key_preserves_numbered_job_and_directory_identity(self):
        second = self.make_cron('cron2')
        a = json.loads((self.cron / 'jobs.json').read_text())
        a['jobs'][0].update(name='Wolftac Factory Worker 1', paused=True)
        b = json.loads(json.dumps(a))
        b['jobs'][0]['name'] = 'Wolftac Factory Worker 2'
        (self.cron / 'jobs.json').write_text(json.dumps({'jobs': a['jobs'] + b['jobs']}))
        (second / 'jobs.json').write_text(json.dumps(a))
        code, text = self.run_watchdog(cron=f'{self.cron};{second}')
        self.assertEqual(code, 1)
        self.assertEqual(sum(line.startswith('new:') for line in text.splitlines()), 3)
        self.assertIn('3 liveness finding(s) still open', text)

    @unittest.skipUnless(os.getenv("WD549_REAL_CRON"), "TP4 opt-in real cron read-only comparison")
    def test_real_cron_findings_match_main(self):
        basepath, _ = self.baseline()
        cron = Path(os.environ["WD549_REAL_CRON"])
        state = cron / "watchdog-state.json"
        before_state = state.read_bytes() if state.exists() else None
        # No live env/credential reads. Full output is kept in memory, not echoed.
        deploy = ROOT / "deploy/deploy.ps1"
        before = self.run_watchdog(script=basepath, cron=cron, deploy=deploy)
        after = self.run_watchdog(cron=cron, deploy=deploy)
        self.assertEqual(before[0], after[0])
        self.assertEqual(findings(before[1]), findings(after[1]))
        # Compare entire drift/learn diagnostics too, including clean/unknown states.
        # Do not filter PENDING rows away to make current-main preservation pass.
        def diagnostics(text):
            return "\n".join(line for line in text[text.index("DRIFT"):text.index("DRY RUN:")].splitlines()
                             if not line.startswith("AUDIT"))
        self.assertEqual(diagnostics(before[1]), diagnostics(after[1]))
        self.assertIn("LEARN", diagnostics(after[1]))
        self.assertEqual(before_state, state.read_bytes() if state.exists() else None)
        print(f"TP4 real-cron comparison: identical findings ({len(findings(after[1]))}); state unchanged")


if __name__ == "__main__":
    unittest.main()
