"""WD-580: real PowerShell process, synthetic cron/env/policy, no live sends."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

PWSH = shutil.which("pwsh")
SCRIPT = Path(__file__).resolve().parents[1] / "factory-watchdog.ps1"


def stamp(days=1):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class WatchdogAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd580-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cron = self.make_cron("cron")
        self.envfile = self.root / "fixture.env"
        self.envfile.write_text("SLACK_FACTORY_BOT_TOKEN=fixture-only-slack-580\n"
                                f"LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT={stamp(10)}\n", encoding="utf-8")
        self.policy = self.root / "policy.json"
        self.policy.write_text(json.dumps({"slack_channel_id": "fixture", "approver_slack_user_id": "fixture",
            "fallback_identity": {"username": "fixture", "icon_emoji": ":test_tube:"}}))
        self.outbox = self.root / "outbox"
        # Allowlist runtime necessities; never pass inherited service credentials.
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "HOME",
            "TEMP", "TMP", "TMPDIR", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMDRIVE"}}
        self.env["LOCALAPPDATA"] = str(self.root / "appdata")

    def make_cron(self, name):
        cron = self.root / name
        cron.mkdir()
        (cron / "ticker_heartbeat").write_text("fixture")
        jobs = [{"id": ident, "name": title, "enabled": True, "paused": False,
                 "last_status": "ok", "schedule": {"minutes": 30}, "next_run_at": stamp()}
                for ident, title in [("gkfixture1", "Wolftac Factory Gatekeeper"),
                                      ("buildfixture1", "Wolftac Factory Build Worker")]]
        (cron / "jobs.json").write_text(json.dumps({"jobs": jobs}))
        return cron

    def output(self, text, cron=None, name="new.txt", age=0):
        folder = (cron or self.cron) / "output/gkfixture1"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_text(text, encoding="utf-8")
        at = time.time() - age
        os.utime(path, (at, at))

    def run_watchdog(self, dry=True, extra=(), cron=None, outbox=None, expected=0):
        args = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
                "-CronDir", str(cron or self.cron), "-EnvFile", str(self.envfile),
                "-PolicyPath", str(self.policy), "-UserProfile", str(self.root / "profile"),
                "-SkipDrift", "-SkipLearn",
                "-SlackOutbox", str(outbox or self.outbox), *extra]
        if dry:
            args.append("-DryRun")
        result = subprocess.run(args, env=self.env, capture_output=True, encoding="utf-8", timeout=30)
        text = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, text)
        self.assertNotIn("fixture-only-slack-580", text)
        if dry:
            self.assertIn("DRY RUN:", text)
        return text

    def messages(self):
        return [p.read_text(encoding="utf-8-sig") for p in self.outbox.glob("*.txt")]

    def state(self):
        return json.loads((self.cron / "watchdog-state.json").read_text(encoding="utf-8-sig"))

    def test_found_does_not_change_liveness_and_dry_run_writes_nothing(self):
        self.output("gatekeeper: build: idle")
        control = self.run_watchdog()
        self.output("gatekeeper: build: idle; audit: done-without-integration: WD-371,WD-413 (last audit 2026-10-29T00:00:00Z)")
        output = self.run_watchdog()
        self.assertIn("AUDIT: 2 Done issue(s) without an INTEGRATION REPORT: WD-371, WD-413", output)
        self.assertIn("HEALTHY: all checks passed", control)
        self.assertIn("HEALTHY: all checks passed", output)
        self.assertEqual([s for s in control.splitlines() if s.startswith("  - ")],
                         [s for s in output.splitlines() if s.startswith("  - ")])
        self.assertFalse((self.cron / "watchdog-state.json").exists())
        self.assertFalse(self.outbox.exists())

    def test_output_matrix_and_id_sanitization(self):
        cases = [("audit: clean (window x)", "AUDIT CLEAN:"),
                 ("audit: overdue (last ok never)", "AUDIT: CANNOT CHECK"),
                 ("build: idle", "AUDIT: not reported"), ("", "AUDIT: not reported"),
                 ("audit: done-without-integration: WD-371,<b>x</b>", "INTEGRATION REPORT: WD-371")]
        for value, expected in cases:
            with self.subTest(value=value):
                self.output(value)
                output = self.run_watchdog()
                self.assertIn(expected, output)
                self.assertNotIn("<b>", output)
        self.assertIn("AUDIT: skipped (-SkipAudit)", self.run_watchdog(extra=("-SkipAudit",)))

    def test_missing_output_and_invalid_job_id(self):
        self.assertIn("AUDIT: not reported", self.run_watchdog())
        self.output("audit: done-without-integration: WD-371")
        path = self.cron / "jobs.json"
        jobs = json.loads(path.read_text())
        jobs["jobs"][0]["id"] = "../gkfixture1"
        path.write_text(json.dumps(jobs))
        self.assertIn("AUDIT: not reported", self.run_watchdog())
        jobs["jobs"][0]["name"] = "Other"
        path.write_text(json.dumps(jobs))
        self.assertIn("AUDIT: not reported", self.run_watchdog())

    def test_newest_and_union_precedence(self):
        other = self.make_cron("other")
        third = self.make_cron("third")
        self.output("audit: done-without-integration: WD-99", name="old.txt", age=120)
        self.output("audit: clean")
        self.output("audit: overdue", cron=other)
        dirs = f"{self.cron};{other};{third}"
        self.assertIn("AUDIT: CANNOT CHECK", self.run_watchdog(cron=dirs))
        self.output("audit: done-without-integration: WD-413,WD-371; audit: overdue", cron=other)
        self.output("audit: done-without-integration: WD-371,WD-9", cron=third)
        output = self.run_watchdog(cron=dirs)
        self.assertIn("AUDIT: 3 Done issue(s) without an INTEGRATION REPORT: WD-9, WD-371, WD-413", output)
        self.assertNotIn("WD-99", output)

    def test_transition_cooldown_nag_changed_ids_and_recovery(self):
        self.output("audit: done-without-integration: WD-371,WD-413")
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("WD-371, WD-413", self.messages()[0])
        self.assertIn("Not a worker outage", self.messages()[0])
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 1)
        self.run_watchdog(dry=False, extra=("-NagHours", "0"))
        self.assertEqual(len(self.messages()), 2)
        self.assertTrue(any("still unresolved" in m for m in self.messages()))
        self.output("audit: done-without-integration: WD-9")
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 3)
        before = self.state()
        self.output("old gatekeeper, no audit")
        self.run_watchdog(dry=False)
        self.assertEqual(self.state()["audit_status"], "found")
        self.assertEqual(self.state()["audit_ids"], ["WD-9"])
        self.assertEqual(datetime.fromisoformat(self.state()["audit_last_alert_at"]),
                         datetime.fromisoformat(before["audit_last_alert_at"]))
        self.output("audit: clean")
        self.run_watchdog(dry=False, extra=("-SkipAudit",))
        self.assertEqual(self.state()["audit_status"], "found")
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 4)
        self.assertTrue(any("audit clean" in m for m in self.messages()))
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 4)
        self.assertEqual(self.state()["audit_status"], "clean")

    def test_overdue_cooldown_and_recovery(self):
        self.output("audit: overdue")
        self.run_watchdog(dry=False)
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 1)
        self.assertIn("has not completed for 48h", self.messages()[0])
        self.output("audit: clean")
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 2)

    def test_delivery_failure_retries_transition_and_recovery(self):
        broken = self.root / "not-a-directory"
        broken.write_text("fixture")
        self.output("audit: done-without-integration: WD-371")
        for _ in range(2):
            self.run_watchdog(dry=False, outbox=broken, expected=3)
            self.assertEqual(self.state()["audit_status"], "clean")
            self.assertIsNone(self.state()["audit_last_alert_at"])
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 1)
        alert = self.state()["audit_last_alert_at"]
        self.output("audit: clean")
        for _ in range(2):
            self.run_watchdog(dry=False, outbox=broken, expected=3)
            self.assertEqual(self.state()["audit_status"], "found")
            self.assertEqual(datetime.fromisoformat(self.state()["audit_last_alert_at"]),
                             datetime.fromisoformat(alert))
        self.run_watchdog(dry=False)
        self.run_watchdog(dry=False)
        self.assertEqual(len(self.messages()), 2)
        self.assertEqual(self.state()["audit_status"], "clean")


if __name__ == "__main__":
    unittest.main()
