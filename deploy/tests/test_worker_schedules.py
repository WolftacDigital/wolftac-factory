"""WD-576: run the real operator script only with scratch homes and fake Hermes."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh") or ""
SCRIPT = ROOT / "deploy/worker-schedules.ps1"
FAKE = ROOT / "deploy/tests/fixtures/fake-hermes.ps1"

# Import constants only. No credential loader, network probe or dispatch is called.
spec = importlib.util.spec_from_file_location("wd576_gatekeeper", ROOT / "deploy/factory-gatekeeper.py")
assert spec is not None and spec.loader is not None
gatekeeper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gatekeeper)
TARGETS = [(profile, job_id) for _, job_id, profile in gatekeeper.DEFAULT_JOB_MAP.values()]


@unittest.skipUnless(PWSH, "PowerShell 7 required")
class WorkerScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd576-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.log = self.home / "fake-argv.jsonl"
        self.env = os.environ.copy()
        self.env.update(FAKE_HERMES_HOME=str(self.home), FAKE_HERMES_LOG=str(self.log),
                        FAKE_HERMES_MODE="", LOCALAPPDATA=str(self.home))
        self.paths = {}
        self.docs = {}
        for profile, job_id in TARGETS:
            self.docs.setdefault(profile, {"jobs": [], "updated_at": "fixture"})["jobs"].append({
                "id": job_id, "name": f"Wolftac Factory {profile} {job_id}",
                "enabled": True, "state": "scheduled", "no_agent": False,
                "prompt": "DO_NOT_PRINT_FIXTURE_PROMPT", "next_run_at": "fixture",
                "schedule": {"kind": "interval", "minutes": 5}})
        for profile, doc in self.docs.items():
            path = self.home / "profiles" / profile / "cron/jobs.json"
            path.parent.mkdir(parents=True)
            self.paths[profile] = path
            self.save(profile)
        # Explicitly out-of-scope jobs must remain byte-identical.
        for relative in ("cron/jobs.json", "profiles/factory-integrate/cron/jobs.json"):
            path = self.home / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"jobs":[{"id":"untouched","name":"ROLLBACK (paused)","enabled":false}]}')
        self.original = self.snapshot()

    def save(self, profile):
        self.paths[profile].write_text(json.dumps(self.docs[profile]), encoding="utf-8", newline="\n")

    def snapshot(self):
        return {p.relative_to(self.home).as_posix(): p.read_bytes()
                for p in self.home.rglob("*") if p.is_file()}

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8-sig").splitlines()] if self.log.exists() else []

    def run_script(self, apply=False, extra=()):
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
                                 "-HermesHome", str(self.home), "-HermesCommand", str(FAKE),
                                 *(["-Apply"] if apply else []), *extra], env=self.env,
                                capture_output=True, encoding="utf-8", timeout=30)
        self.assertNotIn("DO_NOT_PRINT_FIXTURE_PROMPT", result.stdout + result.stderr)
        return result

    def assert_code(self, result, code):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def test_dry_run_and_exact_gatekeeper_map(self):
        result = self.run_script()
        self.assert_code(result, 0)
        rows = result.stdout.splitlines()
        self.assertEqual(len(rows), len(TARGETS))
        actual = [re.match(r"PLAN\s+(\S+) .*: every 5m -> every 120m$", row) for row in rows]
        self.assertTrue(all(actual), result.stdout)
        self.assertEqual({m[1] for m in actual if m is not None}, {f"{p}/{j}" for p, j in TARGETS})
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.snapshot(), self.original)

    def test_apply_backs_up_before_edits_and_second_apply_is_noop(self):
        result = self.run_script(apply=True)
        self.assert_code(result, 0)
        self.assertEqual(result.stdout.count("APPLIED "), 4)
        self.assertEqual(result.stdout.count("(verified)"), 4)
        self.assertEqual(self.calls(), [["-p", p, "cron", "edit", j, "--schedule", "every 120m"] for p, j in TARGETS])
        backups = sorted(self.home.rglob("jobs.json.bak-*-pre-safety-net"))
        self.assertEqual(len(backups), 3)
        for profile, path in self.paths.items():
            backup = next(path.parent.glob("jobs.json.bak-*-pre-safety-net"))
            self.assertEqual(backup.read_bytes(), self.original[path.relative_to(self.home).as_posix()])
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
            expected = json.loads(self.original[path.relative_to(self.home).as_posix()])
            for job in expected["jobs"]:
                job["schedule"] = {"kind": "interval", "minutes": 120, "display": "every 120m"}
            self.assertEqual(doc, expected)  # No unrelated field edits.
        for name in ("cron/jobs.json", "profiles/factory-integrate/cron/jobs.json"):
            self.assertEqual((self.home / name).read_bytes(), self.original[name])
        before = self.snapshot()
        result = self.run_script(apply=True)
        self.assert_code(result, 0)
        self.assertEqual(len(result.stdout.splitlines()), 4)
        self.assertEqual(result.stdout.count("OK      "), 4)
        self.assertEqual(self.snapshot(), before)  # No new calls OR backup.

    def test_validate_all_before_any_write(self):
        # The last target is invalid: earlier valid targets must not be applied.
        for mode in ("missing-id", "bad-name", "missing-file", "bad-json", "duplicate-id"):
            with self.subTest(mode=mode):
                path = self.paths["integrator"]
                original = path.read_bytes()
                doc = json.loads(original)
                if mode == "missing-id":
                    doc["jobs"] = []
                elif mode == "bad-name":
                    doc["jobs"][0]["name"] = "Something Else"
                elif mode == "duplicate-id":
                    doc["jobs"].append(doc["jobs"][0].copy())
                path.write_text(json.dumps(doc))
                if mode == "missing-file":
                    path.unlink()
                elif mode == "bad-json":
                    path.write_text("{invalid")
                before = self.snapshot()
                result = self.run_script(apply=True)
                self.assert_code(result, 1)
                self.assertIn("REFUSE  " if mode == "bad-name" else "MISSING ", result.stdout)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.calls(), [])
                path.write_bytes(original)

    def test_fail_exit_code_and_noop_readback_are_not_success(self):
        for mode, reason in (("fail", "hermes exit code 1"), ("noop", "read-back kind 'interval' minutes '5'")):
            with self.subTest(mode=mode):
                # Keep the main fixture pristine between modes; independent backup roots.
                with tempfile.TemporaryDirectory(prefix="wd576-mode-", dir=os.getenv("TMPDIR")) as tmp:
                    home = Path(tmp)
                    for name, data in self.original.items():
                        p = home / name
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_bytes(data)
                    env = self.env | {"FAKE_HERMES_HOME": str(home), "FAKE_HERMES_LOG": str(home / "calls"),
                                      "FAKE_HERMES_MODE": mode}
                    result = subprocess.run([PWSH, "-NoProfile", "-File", str(SCRIPT), "-Apply",
                                             "-HermesHome", str(home), "-HermesCommand", str(FAKE)],
                                            env=env, capture_output=True, encoding="utf-8", timeout=30)
                    self.assert_code(result, 1)
                    self.assertEqual(result.stdout.count("FAILED  "), 4)
                    self.assertIn(reason, result.stdout)
                    self.assertNotIn("APPLIED ", result.stdout)
                    for p in home.rglob("jobs.json"):
                        self.assertEqual(p.read_bytes(), self.original[p.relative_to(home).as_posix()])
                    for backup in home.rglob("jobs.json.bak-*-pre-safety-net"):
                        self.assertEqual(backup.read_bytes(), (backup.parent / "jobs.json").read_bytes())

    def test_noninterval_schedule_and_custom_minimum(self):
        self.docs["integrator"]["jobs"][0]["schedule"] = {"kind": "cron", "expr": "*/5 * * * *"}
        self.save("integrator")
        result = self.run_script(apply=True, extra=("-SafetyNetMinutes", "180"))
        self.assert_code(result, 0)
        self.assertIn("kind cron -> every 180m", result.stdout)
        self.assertEqual(result.stdout.count("APPLIED "), 4)
        self.assertTrue(all(call[-1] == "every 180m" for call in self.calls()))


if __name__ == "__main__":
    unittest.main()
