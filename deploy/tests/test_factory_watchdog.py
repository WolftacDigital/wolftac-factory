"""WD-549 behavior checks. Every watchdog invocation is -DryRun with fake credentials.

Run: python -B -m unittest discover -s deploy/tests -p test_factory_watchdog.py -v
Optional TP4: set WD549_REAL_CRON to the real cron directory (read-only).
No live .env is read. TemporaryDirectory uses the caller's TMPDIR when set.
"""
import json
import os
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
        self.assertIn("DRY RUN: no Slack post, no snapshot, no state written.", text)
        self.assertEqual(before, self.envfile.read_bytes() if self.envfile.exists() else None)
        self.assertFalse((self.cron / "watchdog-state.json").exists())
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
                self.assertEqual(code, 1 if reason else 0)
                if reason:
                    self.assertIn("linear-token-expiry:", rows[0])
                    self.assertIn(reason, rows[0])
                    if reason == "expires":
                        self.assertIn(value[:10], rows[0])

    def test_offset_is_compared_in_utc(self):
        value = (datetime.now(timezone.utc) + timedelta(days=2)).astimezone(timezone(timedelta(hours=-5)))
        self.write_expiry(value.isoformat())
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
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

    def test_missing_env_is_unhealthy(self):
        self.envfile.unlink()
        code, text = self.run_watchdog()
        self.assertEqual(code, 1)
        self.assertIn("cannot read expiry date", text)
        self.assertIn("watchdog cannot read SLACK_FACTORY_BOT_TOKEN", text)

    def baseline(self):
        text = subprocess.check_output(["git", "show", "origin/main:deploy/factory-watchdog.ps1"],
                                       cwd=ROOT, encoding="utf-8")
        path = self.root / "main-watchdog.ps1"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path, text

    def test_existing_checks_and_alerting_are_unchanged(self):
        basepath, base = self.baseline()
        current = SCRIPT.read_text(encoding="utf-8")
        marker = "# ── transition-based alerting"
        self.assertEqual(base[base.index(marker):], current[current.index(marker):])
        for start, end in [("function Send-SlackAlert", "# ── run the checks"),
                           ("function Get-SlackToken", "function ")]:
            def section(text):
                pos = text.index(start)
                return text[pos:text.index(end, pos + len(start))]
            self.assertEqual(section(base), section(current))
        self.assertEqual(base.count("Send-SlackAlert"), current.count("Send-SlackAlert"))
        # Only the new check dispatch is inserted inside the existing cron checks.
        def checks(text):
            part = text[text.index("function Test-CronDirectory"):text.index("function Get-StatePath")]
            return part.replace("    Test-GatekeeperAuth $Dir $jobs\n", "")
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
            return text[text.index("DRIFT"):text.index("DRY RUN:")]
        self.assertEqual(diagnostics(before[1]), diagnostics(after[1]))
        self.assertIn("LEARN", diagnostics(after[1]))
        self.assertEqual(before_state, state.read_bytes() if state.exists() else None)
        print(f"TP4 real-cron comparison: identical findings ({len(findings(after[1]))}); state unchanged")


if __name__ == "__main__":
    unittest.main()
