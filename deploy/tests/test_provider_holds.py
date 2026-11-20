"""WD-630: synthetic jobs/HTTP/dispatch and real watchdog CLI, never live state."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_factory_gatekeeper as f
import test_factory_watchdog as w


g = f.g


class ProviderGateTests(f.GatekeeperTests):
    def setUp(self):
        f.GatekeeperTests.setUp(self)
        self.clock = f.NOW
        patch.object(g, "now_utc", side_effect=lambda: self.clock).start()
        self.records = json.loads(self.jobs.read_text())["jobs"]
        for job in self.records:
            job.update(provider="provider-a", last_status="ok")
        self.issues["Todo"][0]["inverseRelations"]["nodes"] = []
        crm = f.issue("WD-2")
        crm["project"] = {"name": "Wolftac CRM v2"}
        crm["inverseRelations"]["nodes"] = []
        self.issues["Todo"].append(crm)
        self.issues["In Review"] = [f.issue("WD-3", "In Review")]
        self.issues["Approved"] = [f.issue("WD-4", "Approved")]
        self.persist()

    def persist(self):
        self.write(self.jobs, {"jobs": self.records})

    def job(self, lane):
        return next(j for j in self.records if j["id"] == g.DEFAULT_JOB_MAP[lane][1])

    def usage_failure(self, lane="build", reset=None, detail="RuntimeError: HTTP 429: The usage limit has been reached PRIVATE"):
        job = self.job(lane)
        job.update(last_status="error", last_run_at=self.clock.isoformat(),
                   last_failure={"at": self.clock.isoformat(), "detail": detail})
        if reset:
            job["last_failure"]["reset_at"] = reset.isoformat()
        self.persist()

    def hold(self):
        return json.loads(self.state.read_text())["provider_holds"]["provider-a"]

    def success(self, lane="integrate", status="ok"):
        # Intentionally keep the old failure: real jobs retain it after recovery.
        self.clock += timedelta(seconds=1)
        self.job(lane).update(last_run_at=self.clock.isoformat(), last_status=status)
        self.persist()

    def test_ac1_explicit_reset_all_lanes_then_one_integrate_probe(self):
        reset = self.clock + timedelta(minutes=40)
        self.usage_failure(reset=reset)
        out = self.tick()
        self.assertEqual(out.count("until 20:40Z) -> skipped"), 4)
        self.spawn.assert_not_called()
        self.assertNotIn("PRIVATE", out + self.state.read_text())
        self.clock = reset - timedelta(seconds=1)
        self.tick()
        self.spawn.assert_not_called()
        self.clock = reset
        out = self.tick()
        self.spawn.assert_called_once()
        self.assertIn("integrate: provider-probe -> dispatched", out)
        self.assertEqual(self.hold()["probe"]["lane"], "integrate")
        self.clock += timedelta(minutes=100)  # Even after ordinary in-flight TTL.
        self.tick()
        self.spawn.assert_not_called()
        self.success()
        self.tick()
        self.assertNotIn("until", self.hold())
        self.assertGreaterEqual(self.spawn.call_count, 1)

    def test_release_probe_delivery_failed_and_cancelled(self):
        for status in ("delivery_failed", "cancelled"):
            with self.subTest(status=status):
                self.clock += timedelta(seconds=1)
                reset = self.clock + timedelta(minutes=40)
                self.usage_failure("integrate", reset=reset)
                self.tick()
                self.clock = reset
                self.assertIn("integrate: provider-probe -> dispatched", self.tick())
                self.success(status=status)
                out = self.tick()
                self.assertIn("provider-holds: none", out)
                self.assertNotIn("until", self.hold())
                self.assertNotIn("probe", self.hold())
                self.assertEqual(self.hold()["step"], 60)
                self.assertGreaterEqual(self.spawn.call_count, 1)
                self.clock += timedelta(hours=3)
                self.assertNotIn("provider-hold (", self.tick())

    def test_idle_safety_net_outcome_releases_expired_hold(self):
        self.issues = {state: [] for state in self.issues}
        self.usage_failure()
        self.tick()
        until = g.parse_ts(self.hold()["until"])
        # A success before the deadline resets escalation, not the active hold.
        self.success("build")
        self.tick()
        self.assertEqual(g.parse_ts(self.hold()["until"]), until)
        self.clock = until + timedelta(minutes=1)
        self.success("build")
        # Process it late: the event time, not the observing tick, proves recovery.
        self.clock += timedelta(days=2)
        self.assertIn("provider-holds: none", self.tick())
        self.assertNotIn("until", self.hold())
        self.assertNotIn("since", self.hold())
        self.assertEqual(self.hold()["step"], 60)
        self.spawn.assert_not_called()
        self.assertIn("provider-holds: none", self.tick())  # Old failure stays consumed.

    def test_expiry_alone_or_preexpiry_success_does_not_release(self):
        self.issues = {state: [] for state in self.issues}
        self.usage_failure()
        self.tick()
        until = g.parse_ts(self.hold()["until"])
        self.success("build")
        self.clock = until + timedelta(days=2)
        self.tick()
        self.assertEqual(g.parse_ts(self.hold()["until"]), until)
        self.assertNotIn("probe", self.hold())
        self.spawn.assert_not_called()

    def test_sibling_success_does_not_release_reserved_probe(self):
        self.usage_failure(reset=self.clock)
        self.tick()
        self.assertEqual(self.hold()["probe"]["lane"], "integrate")
        self.success("build")
        self.assertIn("awaiting probe", self.tick())
        self.spawn.assert_not_called()
        self.clock += timedelta(seconds=1)
        self.usage_failure("integrate")
        self.tick()
        self.assertEqual(g.parse_ts(self.hold()["until"]) - self.clock, timedelta(minutes=60))
        self.spawn.assert_not_called()

    def test_newer_or_same_time_failure_wins_over_terminal_status(self):
        for status in ("delivery_failed", "cancelled"):
            self.usage_failure("integrate")
            self.job("integrate")["last_status"] = status
            self.assertTrue(g.provider_outcome(self.job("integrate"))[1])
            self.job("integrate")["last_failure"]["at"] = (self.clock + timedelta(seconds=1)).isoformat()
            self.assertTrue(g.provider_outcome(self.job("integrate"))[1])

    def test_ac2_ac4_escalation_cap_and_same_event_not_recounted(self):
        for minutes in (60, 120, 240, 480, 480):
            self.usage_failure("integrate")
            out = self.tick()
            self.spawn.assert_not_called()
            until = g.parse_ts(self.hold()["until"])
            self.assertEqual(until - self.clock, timedelta(minutes=minutes))
            before = self.hold()
            self.tick()
            self.assertEqual(self.hold(), before)
            self.clock = until
            self.tick()
            self.spawn.assert_called_once()
            self.assertEqual(self.hold()["probe"]["lane"], "integrate")
            self.clock += timedelta(seconds=1)
        self.assertNotIn("PRIVATE", "".join(self.output))

    def test_ac3_other_provider_continues_and_empty_integrate_yields_probe(self):
        self.job("review")["provider"] = "provider-b"
        self.issues["Approved"] = []
        self.usage_failure()
        out = self.tick()
        self.spawn.assert_called_once()
        self.assertIn("review: no-baseline -> dispatched", out)
        self.clock += timedelta(minutes=60)
        self.tick()
        self.spawn.assert_called_once()
        self.assertEqual(self.hold()["probe"]["lane"], "build")

    def test_ac5_other_errors_unreadable_and_invalid_provider(self):
        for detail in ("network timeout", "HTTP 401 unauthorized", "interrupted"):
            self.clock += timedelta(seconds=1)
            self.usage_failure(detail=detail)
            out = self.tick("--dry-run")
            self.assertNotIn("provider-hold (", out)
            self.assertFalse(self.state.exists())
        self.jobs.write_text("not json")
        self.assertNotIn("provider-hold (", self.tick("--dry-run"))
        self.job("build")["provider"] = "private; injected"
        self.usage_failure()
        out = self.tick("--dry-run")
        self.assertNotIn("private", out)
        self.assertNotIn("provider-hold (", out)

    def test_ac8_nonusage_outcome_resets_after_240(self):
        for minutes in (60, 120, 240):
            self.usage_failure("integrate")
            self.tick()
            self.clock += timedelta(minutes=minutes)
            self.tick()
            self.clock += timedelta(seconds=1)
        self.success(status="interrupted")
        self.tick()
        self.assertEqual(self.hold()["step"], 60)
        self.assertNotIn("until", self.hold())
        self.clock += timedelta(seconds=1)
        self.usage_failure("integrate")
        self.tick()
        self.assertEqual(g.parse_ts(self.hold()["until"]) - self.clock, timedelta(minutes=60))

    def test_ac9_explicit_timestamp_does_not_use_or_advance_step(self):
        for minutes in (60, 120):
            self.usage_failure("integrate")
            self.tick()
            self.clock += timedelta(minutes=minutes)
            self.tick()
            self.clock += timedelta(seconds=1)
        self.assertEqual(self.hold()["step"], 240)
        reset = self.clock + timedelta(minutes=30)
        self.usage_failure("integrate", detail="rate limit resets at " + reset.isoformat())
        self.tick()
        self.assertEqual(g.parse_ts(self.hold()["until"]), reset)
        self.assertEqual(self.hold()["step"], 240)

    def test_dry_run_single_probe_and_no_writes(self):
        self.usage_failure(reset=self.clock - timedelta(seconds=1))
        before = self.jobs.read_bytes()
        out = self.tick("--dry-run")
        self.assertEqual(out.count("dispatched(dry-run)"), 1)
        self.assertFalse(self.state.exists())
        self.assertEqual(before, self.jobs.read_bytes())
        self.spawn.assert_not_called()

    def test_probe_failure_and_no_result_do_not_release(self):
        self.usage_failure(reset=self.clock)
        self.spawn.side_effect = OSError("PRIVATE")
        self.tick()
        self.assertNotIn("probe", self.hold())
        self.spawn.side_effect = None
        self.tick()
        self.assertIn("probe", self.hold())
        self.clock += timedelta(hours=4)
        self.tick()
        self.spawn.assert_not_called()

    def test_profile_jobs_are_read_separately_and_old_failure_is_superseded(self):
        for lane, entry in g.DEFAULT_JOB_MAP.items():
            path = Path(g.profile_jobs_path(str(self.root), entry[2]))
            path.parent.mkdir(parents=True, exist_ok=True)
            siblings = [self.job(name) for name, e in g.DEFAULT_JOB_MAP.items() if e[2] == entry[2]]
            self.write(path, {"jobs": siblings})
        snapshots = g.provider_jobs(g.DEFAULT_JOB_MAP, str(self.root))
        self.assertEqual(set(snapshots), set(g.DEFAULT_JOB_MAP))
        self.usage_failure()
        self.success("build")
        outcome = g.provider_outcome(self.job("build"))
        self.assertFalse(outcome[1])


@unittest.skipUnless(w.PWSH, "PowerShell 7 required")
class ProviderWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd630-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cron = self.root / "cron"
        self.cron.mkdir()
        self.clock = datetime.now(timezone.utc).replace(microsecond=0)
        self.start = self.clock
        self.output = self.cron / "output/gk/tick.txt"
        self.output.parent.mkdir(parents=True)
        self.outbox = self.root / "outbox"
        self.envfile = self.root / "expiry-fixture.txt"
        self.envfile.write_text("LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=" + (self.clock + timedelta(days=10)).isoformat())
        (self.cron / "jobs.json").write_text(json.dumps({"jobs": [{
            "id": "gk", "name": "Wolftac Factory Gatekeeper", "enabled": True,
            "last_status": "ok", "schedule": {"minutes": 30},
            "next_run_at": (self.clock + timedelta(days=10)).isoformat()}]}))
        self.state = self.cron / "watchdog-state.json"
        self.wrapper = self.root / "clock.ps1"
        self.env = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in ("TOKEN", "SECRET", "API_KEY"))}
        self.env.update(LOCALAPPDATA=str(self.root), HERMES_HOME=str(self.root), TEMP=str(self.root), TMP=str(self.root))

    def inventory(self, value=None):
        if value is None:
            value = "provider-a until " + (self.start + timedelta(days=4)).isoformat()
        self.output.write_text("gatekeeper: build: in-flight (skip); provider-holds: " + value + "\n")

    def run_tick(self, dry=True):
        def quoted(value):
            return "'" + str(value).replace("'", "''") + "'"
        (self.cron / "ticker_heartbeat").write_text("synthetic")
        os.utime(self.cron / "ticker_heartbeat", (self.clock.timestamp(), self.clock.timestamp()))
        self.wrapper.write_text(
            "$global:fixtureNow = [datetimeoffset]::Parse(" + quoted(self.clock.isoformat()) + ").LocalDateTime\n"
            "function global:Get-Date { param([string]$Format) if ($Format) { return $global:fixtureNow.ToString($Format) }; return $global:fixtureNow }\n"
            "& " + quoted(w.SCRIPT) + (" -DryRun" if dry else "") +
            " -SkipDrift -SkipLearn -SkipAudit -CronDir " + quoted(self.cron) +
            " -HermesHome " + quoted(self.root) + " -EnvFile " + quoted(self.envfile) +
            " -AlertOutbox " + quoted(self.outbox) + "\nexit $LASTEXITCODE\n", encoding="utf-8")
        before = self.state.read_bytes() if self.state.exists() else None
        assert w.PWSH is not None
        result = subprocess.run([w.PWSH, "-NoProfile", "-NonInteractive", "-File", str(self.wrapper)],
                                env=self.env, capture_output=True, encoding="utf-8", timeout=30)
        text = result.stdout + result.stderr
        self.assertIn(result.returncode, (0, 1, 3), text)
        if dry:
            self.assertIn("DRY RUN: no Discord post", text)
            self.assertEqual(before, self.state.read_bytes() if self.state.exists() else None)
        return result.returncode, text

    def test_ac6_ac10_three_days_cadence_and_recovery(self):
        self.inventory()
        for hours, digest in ((0, True), (1, False), (12, False), (23.99, False),
                              (24, True), (25, False), (48, True), (49, False)):
            self.clock = self.start + timedelta(hours=hours)
            code, text = self.run_tick()
            self.assertEqual(code, 1, text)
            self.assertIn("UNHEALTHY: 1 finding(s)", text)
            self.assertEqual("Factory provider hold daily digest" in text, digest, text)
            self.assertEqual("new: " in text, hours == 0, text)
            self.assertNotIn("still unhealthy", text)
            self.run_tick(dry=False)  # Persist only synthetic state; sender is outbox.
        messages = [p.read_text(encoding="utf-8-sig") for p in self.outbox.iterdir()]
        self.assertEqual(sum("daily digest" in m for m in messages), 3)
        self.assertEqual(sum("new: " in m for m in messages), 1)
        self.inventory("none")
        code, text = self.run_tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("recovered: "), 1)
        self.run_tick(dry=False)
        self.assertNotIn("recovered:", self.run_tick()[1])

    def test_unknown_preserves_incident_and_timestamp_change_is_not_new(self):
        self.inventory()
        self.run_tick(dry=False)
        before = json.loads(self.state.read_text(encoding="utf-8-sig"))["open_findings"]
        for text in ("gatekeeper: missing inventory", "gatekeeper: provider-holds: PRIVATE_INVALID"):
            self.output.write_text(text)
            code, out = self.run_tick()
            self.assertEqual(code, 1)
            self.assertNotIn("recovered:", out)
            self.assertNotIn("PRIVATE", out)
            self.run_tick(dry=False)
            after = json.loads(self.state.read_text(encoding="utf-8-sig"))["open_findings"]
            self.assertEqual(set(after), set(before))
            self.assertEqual({k: g.parse_ts(v) for k, v in after.items()},
                             {k: g.parse_ts(v) for k, v in before.items()})
        self.inventory("provider-a until " + (self.start + timedelta(days=5)).isoformat())
        self.assertNotIn("new:", self.run_tick()[1])

    def test_expired_inventory_recovers_after_idle_safety_net_outcome(self):
        until = self.start - timedelta(days=2)
        failed_at = until - timedelta(hours=1)
        state = {"provider_holds": {"provider-a": {
            "until": until.isoformat(), "since": failed_at.isoformat(),
            "observed_at": failed_at.isoformat(), "step": 120}}}
        self.inventory("provider-a until " + until.isoformat())
        code, text = self.run_tick()
        self.assertEqual(code, 1, text)  # Expiry alone is not proven recovery.
        self.assertIn("new: ", text)
        self.assertNotIn("recovered:", text)
        self.run_tick(dry=False)
        # No queued work or reserved probe. Feed the same retained-failure shape
        # as a real safety-net completion through the production state reducer.
        jobs = {"build": {"provider": "provider-a", "last_status": "ok",
                          "last_run_at": (until + timedelta(minutes=1)).isoformat(),
                          "last_failure": {"at": failed_at.isoformat(),
                                           "detail": "HTTP 429 usage limit PRIVATE"}}}
        with patch.object(g, "now_utc", return_value=self.clock):
            holds = g.update_provider_holds(state, jobs)
        active = [provider + " until " + hold["until"]
                  for provider, hold in holds.items() if hold.get("until")]
        self.assertEqual(active, [])
        self.inventory(",".join(active) or "none")
        self.clock += timedelta(seconds=1)
        code, text = self.run_tick()
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("recovered: "), 1)
        self.assertNotIn("daily digest", text)
        self.run_tick(dry=False)
        self.clock += timedelta(days=1)
        code, text = self.run_tick()
        self.assertEqual(code, 0, text)
        self.assertNotIn("recovered:", text)
        self.assertNotIn("daily digest", text)

    def test_failed_digest_does_not_advance_cadence(self):
        self.inventory()
        self.outbox.write_text("not a directory")
        code, text = self.run_tick(dry=False)
        self.assertEqual(code, 3, text)
        state = json.loads(self.state.read_text(encoding="utf-8-sig"))
        self.assertIsNone(state["provider_hold_last_alert_at"])
        self.outbox.unlink()
        self.assertEqual(self.run_tick(dry=False)[0], 1)
        self.assertIsNotNone(json.loads(self.state.read_text(encoding="utf-8-sig"))["provider_hold_last_alert_at"])


def load_tests(loader, tests, pattern):
    # The original harness's tests are discovered in their own module unchanged.
    suite = unittest.TestSuite()
    for cls in (ProviderGateTests, ProviderWatchdogTests):
        suite.addTests(cls(name) for name in cls.__dict__ if name.startswith("test_"))
    return suite


if __name__ == "__main__":
    unittest.main()
