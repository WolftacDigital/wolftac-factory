"""WD-672: real Git, synthetic homes, explicit paths, no network or live install."""
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/factory-sync.py"
spec = importlib.util.spec_from_file_location("factory_sync", SCRIPT)
assert spec is not None and spec.loader is not None
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd672-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "gitconfig"
        self.config.write_text("")
        self.env = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(self.config),
            "GIT_CONFIG_COUNT": "0", "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.primary = self.root / "main-clone"
        self.live = self.root / "live"
        self.scripts = self.root / "scripts"
        self.protected = self.root / "protected"
        self.home = self.root / "home"
        self.state = self.root / "state.json"
        self.cmd(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.cmd(self.root, "clone", str(self.remote), str(self.seed))
        self.cmd(self.seed, "config", "core.autocrlf", "false")
        deploy = self.seed / "deploy"
        deploy.mkdir()
        for name in set(sync.PROTECTED) | {"factory-gatekeeper.py"}:
            (deploy / name).write_bytes(b"synthetic initial bytes\n")
        self.cmd(self.seed, "add", ".")
        self.cmd(self.seed, "commit", "-m", "synthetic baseline")
        self.cmd(self.seed, "push", "origin", "main")
        self.base = self.cmd(self.seed, "rev-parse", "HEAD")
        for clone in (self.primary, self.live):
            self.cmd(self.root, "clone", str(self.remote), str(clone))
            self.cmd(clone, "config", "core.autocrlf", "false")
        self.scripts.mkdir()
        self.protected.mkdir()
        for name in sync.PROTECTED:
            shutil.copyfile(deploy / name, self.protected / name)
        (self.scripts / "factory_gatekeeper.py").write_bytes(b"old installed bytes\n")
        (self.scripts / "approval-policy.json").write_bytes(b"old policy bytes\n")
        self.gk = sync.load_gatekeeper()
        jobs_by_path = {}
        for lane, entry in self.gk.DEFAULT_JOB_MAP.items():
            _, job_id, profile = self.gk.lane_entry(entry)
            path = Path(self.gk.profile_jobs_path(str(self.home), profile))
            jobs_by_path.setdefault(path, []).append({"id": job_id, "last_run_at": "fixture-last-run"})
        for path, jobs in jobs_by_path.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"jobs": jobs}))
        self.state.write_text(json.dumps({"lanes": {}}))
        self.argv = ["--main-clone", str(self.primary), "--live", str(self.live),
                     "--scripts-dir", str(self.scripts), "--watchdog-dir", str(self.protected),
                     "--gatekeeper-state", str(self.state), "--hermes-home", str(self.home),
                     "--expected-remote", str(self.remote)]
        (self.seed / "advance.txt").write_text("synthetic new main commit\n")
        self.cmd(self.seed, "add", ".")
        self.cmd(self.seed, "commit", "-m", "synthetic advance")
        self.cmd(self.seed, "push", "origin", "main")
        self.head = self.cmd(self.seed, "rev-parse", "HEAD")

    def cmd(self, cwd, *args):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def run_sync(self, *extra, child=False):
        if child:
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), *self.argv, *extra],
                                    capture_output=True, encoding="utf-8", timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            text = result.stdout
        else:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(sync.main([*self.argv, *extra]), 0)
            text = output.getvalue()
        self.assertEqual(len(text.splitlines()), 1, text)
        self.assertTrue(text.startswith("sync: main-clone: "), text)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("synthetic", text)
        self.assertNotIn("installed bytes", text)
        return text

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def marker(self, minutes=0):
        self.state.write_text(json.dumps({"lanes": {"build": {
            "dispatched_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
            "job_last_run_at": "fixture-last-run"}}}))

    def test_ac1_updates_and_idempotent_cli(self):
        text = self.run_sync(child=True)
        for target in ("main-clone", "live"):
            self.assertIn(f"{target}: updated {self.head[:7]}", text)
        for clone in (self.primary, self.live):
            self.assertEqual(self.cmd(clone, "rev-parse", "HEAD"), self.head)
        text = self.run_sync(child=True)
        self.assertIn(f"main-clone: current {self.head[:7]}", text)
        self.assertIn(f"live: current {self.head[:7]}", text)

    def test_ac2_dirty_untouched_primary_updates(self):
        file = self.live / "deploy/deploy.ps1"
        file.write_bytes(b"preserve synthetic user edit\n")
        before = file.read_bytes(), self.cmd(self.live, "status", "--porcelain")
        text = self.run_sync()
        self.assertIn("live: dirty (1 files) -> skipped", text)
        self.assertIn("main-clone: updated", text)
        self.assertEqual(before, (file.read_bytes(), self.cmd(self.live, "status", "--porcelain")))
        self.assertEqual(self.cmd(self.live, "rev-parse", "HEAD"), self.base)

    def test_ac3_divergence_and_remote_mismatch_no_fetch(self):
        (self.live / "local.txt").write_text("unmerged fixture")
        self.cmd(self.live, "add", ".")
        self.cmd(self.live, "commit", "-m", "local fixture")
        before = self.cmd(self.live, "rev-parse", "HEAD")
        self.assertIn("live: diverged -> skipped", self.run_sync())
        self.assertEqual(self.cmd(self.live, "rev-parse", "HEAD"), before)
        self.cmd(self.live, "remote", "set-url", "origin", "synthetic-wrong-remote")
        with patch.object(sync, "git", wraps=sync.git) as calls:
            self.assertIn("live: remote-mismatch -> skipped", self.run_sync())
        self.assertFalse(any(c.args[0] == self.live and c.args[1] == "fetch" for c in calls.call_args_list))

    def test_ac4_inflight_then_completed(self):
        self.marker()
        self.assertIn("live: lanes-in-flight (build) -> skipped", self.run_sync())
        self.assertEqual(self.cmd(self.primary, "rev-parse", "HEAD"), self.head)
        _, job_id, profile = self.gk.lane_entry(self.gk.DEFAULT_JOB_MAP["build"])
        path = Path(self.gk.profile_jobs_path(str(self.home), profile))
        jobs = json.loads(path.read_text())
        for job in jobs["jobs"]:
            if job["id"] == job_id:
                job["last_run_at"] = "fixture-next-run"
        path.write_text(json.dumps(jobs))
        self.assertIn("live: updated", self.run_sync())

    def test_ac4_expired_and_unreadable_state_and_jobs(self):
        self.state.write_text("not json")
        self.assertIn("live: lanes-in-flight", self.run_sync())
        self.marker(self.gk.INFLIGHT_TTL_MINUTES + 1)
        _, _, profile = self.gk.lane_entry(self.gk.DEFAULT_JOB_MAP["build"])
        path = Path(self.gk.profile_jobs_path(str(self.home), profile))
        original = path.read_bytes()
        path.unlink()
        self.assertIn("live: lanes-in-flight", self.run_sync())
        path.write_bytes(original)
        self.assertIn("live: updated", self.run_sync())

    def test_ac5_install_backup_and_idempotence(self):
        self.assertIn("gatekeeper: installed", self.run_sync())
        installed = self.scripts / "factory_gatekeeper.py"
        self.assertEqual(installed.read_bytes(), (self.primary / "deploy/factory-gatekeeper.py").read_bytes())
        backups = list(self.scripts.glob("factory_gatekeeper.py.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"old installed bytes\n")
        self.assertIn("gatekeeper: current", self.run_sync())
        self.assertEqual(backups, list(self.scripts.glob("factory_gatekeeper.py.bak-*")))

    def test_ac5_replace_failure_cleanup(self):
        with patch.object(sync.os, "replace", side_effect=PermissionError("synthetic secret not for output")):
            text = self.run_sync()
        self.assertIn("gatekeeper: install-failed (PermissionError)", text)
        self.assertEqual((self.scripts / "factory_gatekeeper.py").read_bytes(), b"old installed bytes\n")
        self.assertEqual(list(self.scripts.glob("*.tmp-*")), [])

    def test_ac6_retention_seven_installs(self):
        target = self.scripts / "factory_gatekeeper.py"
        source = self.root / "source.py"
        foreign = target.with_name(target.name + ".bak-manual")
        foreign.write_bytes(b"not owned")
        clock = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for i in range(7):
            source.write_bytes(f"version {i}".encode())
            with patch.object(sync, "datetime") as date:
                date.now.return_value = clock + timedelta(seconds=i)
                self.assertEqual(sync.install(source, target, False), "installed")
        backups = sorted(p for p in self.scripts.iterdir() if p != foreign and ".bak-" in p.name)
        self.assertEqual(len(backups), 5)
        self.assertEqual([p.read_bytes() for p in backups], [f"version {i}".encode() for i in range(1, 6)])
        self.assertEqual(foreign.read_bytes(), b"not owned")

    def test_ac7_protected_no_write(self):
        (self.protected / "junctions.json").write_bytes(b"different fixture")
        before = {p.name: p.read_bytes() for p in self.protected.iterdir()}
        original = io.open
        def guarded(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and self.protected in Path(file).parents:
                self.assertFalse(set(mode) & set("wax+"), "protected file opened for writing")
            return original(file, mode, *args, **kwargs)
        with patch("io.open", side_effect=guarded):
            self.assertIn("watchdog-files: stale (1 of 5)", self.run_sync())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.protected.iterdir()})

    def test_ac8_dry_run_entire_tree_immutable(self):
        before = self.snapshot()
        text = self.run_sync("--dry-run", child=True)
        self.assertIn("main-clone: would-update", text)
        self.assertIn("live: would-update", text)
        self.assertIn("gatekeeper: would-install", text)
        self.assertEqual(before, self.snapshot())

    def test_dry_run_remote_advance_is_not_false_current_install(self):
        for original, installed in (("factory-gatekeeper.py", "factory_gatekeeper.py"),
                                    ("approval-policy.json", "approval-policy.json")):
            shutil.copyfile(self.primary / "deploy" / original, self.scripts / installed)
        before = self.snapshot()
        self.assertIn("gatekeeper: would-install", self.run_sync("--dry-run"))
        self.assertEqual(before, self.snapshot())

    def test_import_definitions_never_writes_bytecode_or_calls_credentials(self):
        adjacent = self.root / "factory-gatekeeper.py"
        adjacent.write_text("DEFAULT_JOB_MAP = {}\nINFLIGHT_TTL_MINUTES = 90\n")
        before = self.snapshot()
        with patch.object(sync, "__file__", str(self.root / "factory-sync.py")), patch.object(sys, "dont_write_bytecode", False):
            module = sync.load_gatekeeper()
            self.assertEqual(module.INFLIGHT_TTL_MINUTES, 90)
            self.assertFalse(sys.dont_write_bytecode)
        self.assertEqual(before, self.snapshot())

    def test_ac9_fetch_failure_and_main_skip_no_install(self):
        original = sync.git
        def failing(repo, *args):
            if args == ("fetch", "origin"):
                return subprocess.CompletedProcess([], 1, b"", b"synthetic-secret")
            return original(repo, *args)
        with patch.object(sync, "git", side_effect=failing):
            text = self.run_sync()
        self.assertIn("main-clone: fetch-failed -> skipped", text)
        self.assertIn("gatekeeper: skipped", text)
        self.assertEqual((self.scripts / "factory_gatekeeper.py").read_bytes(), b"old installed bytes\n")


if __name__ == "__main__":
    unittest.main()
