"""WD-551: execute the watchdog with a synthetic Hermes command, never a live send."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/factory-watchdog.ps1"
PWSH = shutil.which("pwsh")


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class DiscordDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd551-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cron = self.root / "cron"
        self.cron.mkdir()
        self.envfile = self.root / "expiry-fixture.txt"
        self.envfile.write_text("LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=2099-01-01T00:00:00Z\n")
        self.state = self.cron / "watchdog-state.json"
        self.calls = self.root / "calls.jsonl"
        self.wrapper = self.root / "run.ps1"
        quote = lambda p: str(p).replace("'", "''")
        self.wrapper.write_text(r'''$ErrorActionPreference = 'Stop'
function global:hermes {
    $a = @($args)
    $path = $a[[array]::IndexOf($a, '--file') + 1]
    $bytes = [IO.File]::ReadAllBytes($path)
    $body = [IO.File]::ReadAllText($path)
    @{ argv = $a; path = $path; body = $body;
       bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) } |
        ConvertTo-Json -Compress | Add-Content -LiteralPath $env:WD551_CALLS
    $global:LASTEXITCODE = 0
    switch ($env:WD551_MODE) {
        'exit' { $global:LASTEXITCODE = 9; 'private-diagnostic-marker'; return }
        'missing' { '{"success":true}'; return }
        'false' { '{"success":false,"message_id":"123"}'; return }
        'empty' { '{"success":true,"message_id":""}'; return }
        'malformed' { 'private-diagnostic-marker'; return }
        'throw' { throw 'private-diagnostic-marker' }
        default { '{"success":true,"message_id":"123456789012345678"}' }
    }
}
$options = @{}
if ($env:WD551_DRY -eq '1') { $options.DryRun = $true }
if ($env:WD551_PROFILE) { $options.HermesProfile = $env:WD551_PROFILE }
if ($env:WD551_TARGET) { $options.DiscordTarget = $env:WD551_TARGET }
''' + f"& '{quote(SCRIPT)}' -CronDir '{quote(self.cron)}' -EnvFile '{quote(self.envfile)}' "
            f"-UserProfile '{quote(self.root)}' -SkipDrift -SkipLearn -AlertPrefix '[DRILL]' @options\nexit $LASTEXITCODE\n",
            encoding="utf-8", newline="\n")
        self.env = os.environ.copy()
        self.env.update(LOCALAPPDATA=str(self.root / "local"), TMPDIR=str(self.root),
                        WD551_CALLS=str(self.calls), WD551_MODE="ok", WD551_DRY="0",
                        WD551_PROFILE="", WD551_TARGET="")
        self.jobs(paused=True)

    def jobs(self, paused):
        (self.cron / "ticker_heartbeat").write_text("fixture")
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [{
            "id": "fixture", "name": "Wolftac Factory Build Worker", "enabled": True,
            "paused": paused, "last_status": "ok", "schedule": {"minutes": 30},
            "next_run_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }]}))

    def run_watchdog(self, mode="ok", dry=False, profile="", target=""):
        self.env.update(WD551_MODE=mode, WD551_DRY="1" if dry else "0",
                        WD551_PROFILE=profile, WD551_TARGET=target)
        assert PWSH is not None
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(self.wrapper)],
                                env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertNotIn("private-diagnostic-marker", result.stdout + result.stderr)
        self.assertEqual(list(self.root.glob("watchdog-*.txt")), [], "temporary payload leaked")
        return result

    def posted(self):
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8-sig").splitlines()] if self.calls.exists() else []

    def saved(self):
        return json.loads(self.state.read_text(encoding="utf-8-sig"))

    def test_transition_cooldown_recovery_and_command_contract(self):
        self.assertEqual(self.run_watchdog().returncode, 1)
        first = self.posted()
        self.assertEqual(len(first), 1)
        call = first[0]
        self.assertEqual(call["argv"], ["-p", "integrator", "send", "--to",
                                      "discord:1562236246158221323", "--file", call["path"], "--json"])
        self.assertFalse(call["bom"])
        self.assertTrue(call["body"].isascii())
        self.assertIn("[DRILL]", call["body"])
        self.assertIn("<@1022999767753691186>", call["body"])
        self.assertIn("PAUSED", call["body"])
        self.assertFalse(Path(call["path"]).exists())
        self.assertEqual(self.run_watchdog().returncode, 1)
        self.assertEqual(len(self.posted()), 1)
        self.jobs(paused=False)
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 2)
        self.assertIn("recovered", self.posted()[-1]["body"])
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 2)
        self.assertEqual(self.saved()["delivery_failure"], {})

    def test_failures_are_persisted_and_retried_without_duplicate(self):
        for mode in ("exit", "missing", "false", "empty", "malformed", "throw"):
            with self.subTest(mode=mode):
                if self.state.exists():
                    self.state.unlink()
                before = len(self.posted())
                self.assertEqual(self.run_watchdog(mode).returncode, 3)
                state = self.saved()
                self.assertIsNone(state["last_alert_at"])
                self.assertIn("liveness", state["delivery_failure"])
                self.assertNotIn("private-diagnostic-marker", self.state.read_text())
                self.assertEqual(self.run_watchdog().returncode, 1)
                self.assertEqual(len(self.posted()) - before, 2)
                self.assertEqual(self.posted()[-1]["body"], self.posted()[-2]["body"])
                self.assertTrue(self.saved()["last_alert_at"])
                self.assertEqual(self.saved()["delivery_failure"], {})
                self.assertEqual(self.run_watchdog().returncode, 1)
                self.assertEqual(len(self.posted()) - before, 2)

    def test_pending_alert_is_delivered_even_after_checks_recover(self):
        self.assertEqual(self.run_watchdog("exit").returncode, 3)
        pending = self.saved()["delivery_failure"]["liveness"]["message"]
        self.jobs(paused=False)
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 3)
        self.assertEqual(self.posted()[1]["body"], pending)
        self.assertIn("recovered", self.posted()[2]["body"])
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 3)

    def test_repeated_failure_across_recovery_still_delivers_both_transitions(self):
        self.assertEqual(self.run_watchdog("exit").returncode, 3)
        pending = self.saved()["delivery_failure"]["liveness"]["message"]
        self.jobs(paused=False)
        self.assertEqual(self.run_watchdog("exit").returncode, 3)
        self.assertEqual(self.run_watchdog("exit").returncode, 3)
        self.assertIsNone(self.saved()["last_alert_at"])
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 5)
        self.assertEqual(self.posted()[3]["body"], pending)
        self.assertIn("recovered", self.posted()[4]["body"])
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 5)

    def test_failed_recovery_is_retried_on_a_healthy_tick(self):
        self.assertEqual(self.run_watchdog().returncode, 1)
        previous_alert = self.saved()["last_alert_at"]
        self.jobs(paused=False)
        self.assertEqual(self.run_watchdog("missing").returncode, 3)
        self.assertEqual(self.saved()["last_alert_at"], previous_alert)
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 3)
        self.assertEqual(self.posted()[1]["body"], self.posted()[2]["body"])
        self.assertEqual(self.run_watchdog().returncode, 0)
        self.assertEqual(len(self.posted()), 3)

    def test_nag_retains_twelve_hour_discipline(self):
        self.run_watchdog()
        state = self.saved()
        state["last_alert_at"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat()
        self.state.write_text(json.dumps(state))
        self.assertEqual(self.run_watchdog().returncode, 1)
        self.assertEqual(len(self.posted()), 2)
        self.assertIn("still unhealthy after 13h", self.posted()[-1]["body"])
        self.run_watchdog()
        self.assertEqual(len(self.posted()), 2)

    def test_dry_run_does_not_send_or_write_even_with_pending_failure(self):
        self.assertEqual(self.run_watchdog(dry=True).returncode, 1)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.state.exists())
        self.run_watchdog("exit")
        before = self.state.read_bytes(), self.calls.read_bytes()
        self.assertEqual(self.run_watchdog(dry=True).returncode, 1)
        self.assertEqual(before, (self.state.read_bytes(), self.calls.read_bytes()))

    def test_old_missing_or_invalid_state_and_healthy_silence(self):
        self.jobs(paused=False)
        for old in (None, "bad-json", '{"status":"healthy"}'):
            with self.subTest(old=old):
                if old is not None:
                    self.state.write_text(old)
                self.assertEqual(self.run_watchdog().returncode, 0)
                self.assertEqual(self.posted(), [])

    def test_explicit_target_and_profile(self):
        self.assertEqual(self.run_watchdog(profile="fixture-profile", target="discord:9876").returncode, 1)
        args = self.posted()[0]["argv"]
        self.assertEqual(args[1], "fixture-profile")
        self.assertEqual(args[4], "discord:9876")


if __name__ == "__main__":
    unittest.main()
