import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "integrate-preflight.py"
SPEC = importlib.util.spec_from_file_location("integrate_preflight", SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class PreflightProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name) / "profiles" / "factory-integrate"
        (self.root / "deploy").mkdir(parents=True)
        (self.root / "cron").mkdir()
        self.script_copy = self.root / "deploy" / "integrate-preflight.py"
        self.script_copy.write_text("# fixture path only\n", encoding="utf-8")
        self.session_id = "cron_abc123_20260808_120000"
        self.now = time.time()
        self._write_session(ended_at=None)
        self._write_job(enabled=True, state="scheduled")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_session(self, *, ended_at, source="cron", profile_name="factory-integrate"):
        db = self.root / "state.db"
        if db.exists():
            db.unlink()
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, profile_name TEXT, "
            "started_at REAL, ended_at REAL)"
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
            (self.session_id, source, profile_name, self.now - 30, ended_at),
        )
        conn.commit()
        conn.close()

    def _write_job(self, *, enabled, state, name="Wolftac Factory Integrate Worker"):
        payload = {
            "jobs": [
                {
                    "id": "abc123",
                    "name": name,
                    "enabled": enabled,
                    "state": state,
                    "skill": "wolftac-integrate",
                    "skills": ["wolftac-integrate"],
                }
            ]
        }
        (self.root / "cron" / "jobs.json").write_text(json.dumps(payload), encoding="utf-8")

    def _env(self, session_id=None):
        return mock.patch.dict(
            os.environ,
            {
                "HERMES_HOME": str(self.root),
                "HERMES_CRON_SESSION": "1",
                "HERMES_SESSION_ID": session_id or self.session_id,
                "LINEAR_API_KEY": "",
                "LINEAR_OAUTH_TOKEN": "",
                "COMPOSIO_API_KEY": "",
            },
            clear=False,
        )

    def test_deployed_root_is_derived_from_script_location(self):
        self.assertTrue(os.path.samefile(PREFLIGHT.deployed_profile_root(self.script_copy), self.root))

    def test_repo_copy_is_not_runtime_attestation(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            PREFLIGHT.deployed_profile_root(SCRIPT)
        self.assertEqual(raised.exception.code, 3)

    def test_active_profile_local_integrate_cron_session_passes(self):
        with self._env(), contextlib.redirect_stdout(io.StringIO()) as output:
            PREFLIGHT.check_executing_runtime(str(self.root))
            PREFLIGHT.check_session_provenance(str(self.root), now=self.now)
        self.assertIn("session-provenance check", output.getvalue())

    def test_foreign_session_cannot_pass_by_spoofing_hermes_home(self):
        with self._env("cron_foreign_20260808_120000"), self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()) as output:
            PREFLIGHT.check_executing_runtime(str(self.root))
            PREFLIGHT.check_session_provenance(str(self.root), now=self.now)
        self.assertEqual(raised.exception.code, 3)
        self.assertIn("not recorded in the isolated profile", output.getvalue())

    def test_full_cli_spoofed_home_fails_before_network(self):
        self.script_copy.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / ".env").write_text("LINEAR_FACTORY_BOT_TOKEN=not-a-real-token\n", encoding="utf-8")
        env = os.environ.copy()
        env.update(
            {
                "HERMES_HOME": str(self.script_copy.resolve().parent.parent),
                "HERMES_CRON_SESSION": "1",
                "HERMES_SESSION_ID": "cron_foreign_20260808_120000",
                "LINEAR_API_KEY": "",
                "LINEAR_OAUTH_TOKEN": "",
                "COMPOSIO_API_KEY": "",
                "LINEAR_FACTORY_BOT_TOKEN": "",
            }
        )
        result = subprocess.run(
            [sys.executable, str(self.script_copy)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("not recorded in the isolated profile", result.stdout)
        self.assertNotIn("viewer", result.stdout.lower())

    def test_ended_session_is_rejected(self):
        self._write_session(ended_at=self.now - 1)
        with self._env(), self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            PREFLIGHT.check_session_provenance(str(self.root), now=self.now)
        self.assertEqual(raised.exception.code, 3)

    def test_disabled_integrate_job_is_rejected(self):
        self._write_job(enabled=False, state="scheduled")
        with self._env(), self.assertRaises(SystemExit) as raised, contextlib.redirect_stdout(io.StringIO()):
            PREFLIGHT.check_session_provenance(str(self.root), now=self.now)
        self.assertEqual(raised.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
