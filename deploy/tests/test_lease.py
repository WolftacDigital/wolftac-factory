"""WD-618 M1 real-process primitive tests. All state is synthetic C: scratch.

M2 guardian/IPC, M3 observation, M4 recover/adopt and M5 integration are PENDING.
No test here asserts whole-criterion or deployed lease acceptance.
"""
import ctypes
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "deploy/tests/lease_fixtures/test_entry.py"
LEASE = ROOT / "deploy/lease.py"
FP = "a" * 64


class FixtureProcess:
    def __init__(self, config, env, directory):
        self.config = directory / (uuid.uuid4().hex + ".json")
        self.config.write_text(json.dumps(config), encoding="utf-8")
        self.process = subprocess.Popen([getattr(sys, "_base_executable"), "-B", str(ENTRY), str(self.config)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                        env=env, cwd=directory)
        self.spawned_pid = self.process.pid
        self.messages = queue.Queue()
        self.stderr = []
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.errors = threading.Thread(target=self.read_errors, daemon=True)
        self.reader.start()
        self.errors.start()

    def read(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def read_errors(self):
        assert self.process.stderr is not None
        self.stderr.append(self.process.stderr.read())

    def receive(self):
        try:
            line = self.messages.get(timeout=30)
        except queue.Empty as exc:
            raise AssertionError("fixture response timeout") from exc
        if line is None:
            self.process.wait(timeout=10)
            self.errors.join(timeout=10)
            raise AssertionError(f"fixture {self.spawned_pid} exited {self.process.returncode}: {''.join(self.stderr)}")
        return json.loads(line)

    def send(self, value):
        assert self.process.stdin is not None
        self.process.stdin.write(value + "\n")
        self.process.stdin.flush()

    def terminate_owned(self):
        assert self.process.pid == self.spawned_pid
        assert self.spawned_pid != os.getpid()
        if self.process.poll() is None:
            self.process.terminate()  # Only this exact Popen-owned non-agent fixture.
        self.process.wait(timeout=15)

    def close(self):
        if self.process.poll() is None:
            try:
                self.send("exit")
                self.process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.terminate_owned()
        self.reader.join(timeout=5)
        self.errors.join(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            assert stream is not None
            stream.close()
        assert self.process.poll() is not None


class M1Case(unittest.TestCase):
    def setUp(self):
        # A full long path on C:, not the inherited LOGAN_~1 temporary path.
        base = Path(os.environ["USERPROFILE"]) / "AppData/Local/hermes/profiles/builder/cache/scratch"
        self.temp = tempfile.TemporaryDirectory(prefix="wd618-m1-", dir=base)
        self.scratch = Path(self.temp.name)
        self.state = self.scratch / "state"
        self.worktree = self.scratch / "Worktree Long Identity Name"
        self.worktree.mkdir()
        (self.worktree / ".git").mkdir()
        self.branch = "fixture-branch"
        self.children = []
        self.env = {key: os.environ[key] for key in
                    ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE")
                    if key in os.environ}
        for name in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.env[name] = str(self.scratch)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for child in reversed(self.children):
            child.close()
        self.assertTrue(all(c.process.poll() is not None for c in self.children))
        self.temp.cleanup()

    def spawn(self, action="owner", **changes):
        config = dict(action=action, root=str(self.state), worktree=str(self.worktree), branch=self.branch)
        config.update(changes)
        child = FixtureProcess(config, self.env, self.scratch)
        self.children.append(child)
        return child

    def call(self, action, **changes):
        child = self.spawn(action, **changes)
        value = child.receive()
        code = child.process.wait(timeout=30)
        self.assertIn(code, (0, 2, 3, 5, 6, 7), ''.join(child.stderr))
        return value

    def probe(self, result, mode=None, **changes):
        value = self.call("probe", **changes)
        self.assertEqual(value["result"], result, value)
        if mode is not None:
            self.assertEqual(value["mode"], mode)
        return value

    def paths(self):
        identities = self.call("identity")
        return self.state / identities["wt"], self.state / identities["br"]

    def second_worktree(self):
        second = self.scratch / "Second worktree"
        second.mkdir()
        admin = self.worktree / ".git/worktrees/second"
        admin.mkdir(parents=True)
        (second / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        return str(second)

    def snapshot(self):
        return {str(p.relative_to(self.state)): p.read_bytes()
                for p in self.state.rglob("*") if p.is_file()}


class TestM1Locks(M1Case):
    def test_m1_ac1_barrier_lock(self):
        self.race("LOCKED")

    def test_m1_ac1_barrier_a2(self):
        self.race("A2")

    def race(self, barrier):
        first = self.spawn(barrier=barrier)
        self.assertEqual(first.receive()["barrier"], barrier)
        self.assertEqual(self.call("owner")["result"], "HELD")
        first.send("continue")
        self.assertEqual(first.receive()["result"], "GRANTED")
        first.send("release")
        self.assertEqual(first.receive()["result"], "DONE")
        self.assertEqual(first.process.wait(timeout=10), 0)

    def test_m1_ac1_fifty_stress_races(self):
        for iteration in range(50):
            with self.subTest(iteration=iteration):
                root = str(self.scratch / f"race-{iteration}")
                a, b = self.spawn(root=root), self.spawn(root=root)
                results = [a.receive(), b.receive()]
                self.assertEqual(sorted(r["result"] for r in results), ["GRANTED", "HELD"])
                owner = (a, b)[next(i for i, r in enumerate(results) if r["result"] == "GRANTED")]
                owner.send("release")
                self.assertEqual(owner.receive()["result"], "DONE")
                for child in (a, b):
                    self.assertEqual(child.process.wait(timeout=10), 0)
                    child.close()
                self.children = [c for c in self.children if c not in (a, b)]

    def test_m1_ac2_delayed_old_release(self):
        first = self.spawn()
        one = first.receive()
        first.send("release")
        self.assertEqual(first.receive()["result"], "DONE")
        self.assertEqual(first.process.wait(timeout=10), 0)
        second = self.spawn()
        two = second.receive()
        self.assertEqual(two["gen"], one["gen"] + 1)
        second.send("snapshot")
        before = second.receive()["records"]
        second.send("stale-release")
        self.assertEqual(second.receive()["result"], "REJECTED")
        second.send("snapshot")
        self.assertEqual(second.receive()["records"], before)
        self.probe("HELD")
        second.send("snapshot")
        self.assertEqual(second.receive()["records"], before)

    def test_m1_branch_busy_releases_worktree_lock(self):
        first = self.spawn()
        self.assertEqual(first.receive()["result"], "GRANTED")
        second = self.second_worktree()
        self.probe("HELD", worktree=second)
        other = self.spawn("lock-only", worktree=second, branch="different")
        self.assertEqual(other.receive()["result"], "OK")

    def test_m1_publication_requires_both_locks(self):
        self.assertEqual(self.call("unlock-probe")["result"], "REJECTED")
        self.assertFalse(any(self.state.rglob("state.json")))


class TestM1Publication(M1Case):
    def crash(self, step, action, expected, mode=None):
        owner = self.spawn(action, barrier=step)
        row = owner.receive()
        self.assertEqual(row["barrier"], step)
        self.assertEqual(row["pid"], owner.spawned_pid)
        owner.terminate_owned()
        self.probe(expected, mode)

    def test_m1_ac10_crash_a1(self):
        self.crash("A1", "owner", "OPERATOR-BLOCKED")

    def test_m1_ac10_crash_a2(self):
        self.crash("A2", "owner", "ORPHANED")

    def test_m1_ac10_crash_a3(self):
        self.crash("A3", "owner", "ORPHANED")

    def test_m1_ac10_crash_r1(self):
        self.crash("R1", "released", "GRANTED", "write")

    def test_m1_ac10_crash_rc1(self):
        self.crash("RC1", "restrict", "ORPHANED")

    def test_m1_ac10_crash_rc2(self):
        self.crash("RC2", "restrict", "GRANTED", "read-only")

    def test_m1_ac10_crash_ad1(self):
        self.crash("AD1", "adopt", "GRANTED", "write")

    def test_m1_ac10_one_branch_two_worktrees(self):
        other = self.second_worktree()
        owner = self.spawn()
        self.assertEqual(owner.receive()["result"], "GRANTED")
        self.probe("HELD", worktree=other)
        owner.terminate_owned()
        self.probe("ORPHANED", worktree=other)

    def test_m1_ac10_restriction_across_worktrees(self):
        other = self.second_worktree()
        self.assertEqual(self.call("restrict")["result"], "DONE")
        self.probe("GRANTED", "read-only", worktree=other)
        wt, br = self.paths()
        (br / "state.json").unlink()
        self.probe("GRANTED", "read-only", worktree=other)
        self.assertEqual(json.loads((wt / "state.json").read_text())["restriction"]["kind"], "adoption-required")

    def test_m1_ac10_deleted_branch_record_orphan(self):
        owner = self.spawn()
        self.assertEqual(owner.receive()["result"], "GRANTED")
        owner.terminate_owned()
        _, br = self.paths()
        (br / "state.json").unlink()
        self.probe("ORPHANED", worktree=self.second_worktree())

    def test_m1_ac9_forged_lifecycles(self):
        for lifecycle, recover, adopt in (
                ("FREE", "REJECTED", "REJECTED"),
                ("RELEASED", "REJECTED", "REJECTED"),
                ("GRANTED", "REJECTED", "HELD"),
                ("ORPHANED", "OK", "REJECTED"),
                ("UNKNOWN", "UNKNOWN", "REJECTED")):
            with self.subTest(lifecycle=lifecycle):
                result = self.call("prechecks", lifecycle=lifecycle)
                self.assertEqual(result, dict(recover=recover, adopt=adopt, unchanged=True))

    def test_m1_ac9_earlier_evidence(self):
        for values in ({"evidence_sha": "c" * 64}, {"evidence_gen": 0}):
            with self.subTest(values=values):
                result = self.call("prechecks", lifecycle="RELEASED", restricted=True, **values)
                self.assertEqual(result, dict(recover="REJECTED", adopt="REJECTED", unchanged=True))


class TestM1Pending(M1Case):
    def test_m1_abandoned_a1_pointer_cleared_only_after_drain(self):
        result = self.call("abandoned-a1")
        self.assertEqual(result["result"], "DONE")
        self.probe("GRANTED", "write")
        _, br = self.paths()
        self.assertEqual(json.loads((br / "state.json").read_text())["bound_to"], {})

    def test_m1_pending_live_owner(self):
        owner = self.spawn("journal")
        self.assertEqual(owner.receive()["result"], "OK")
        self.probe("HELD")

    def test_m1_pending_dead_owner_same_and_other_worktree(self):
        other = self.second_worktree()
        owner = self.spawn("journal")
        self.assertEqual(owner.receive()["result"], "OK")
        owner.terminate_owned()
        self.probe("OPERATOR-BLOCKED")
        self.probe("OPERATOR-BLOCKED", worktree=other)

    def test_m1_pending_recover_and_adopt_journals(self):
        for kind in ("recover", "adopt"):
            with self.subTest(kind=kind):
                root = str(self.scratch / kind)
                owner = self.spawn("journal", root=root, op=kind, terminal=True)
                owner.receive()
                self.probe("HELD", root=root)
                owner.terminate_owned()
                self.probe("OPERATOR-BLOCKED", root=root)

    def test_m1_pending_partial_journal(self):
        owner = self.spawn("journal")
        journal = Path(owner.receive()["path"])
        owner.terminate_owned()
        journal.write_bytes(b'{"event":')
        self.probe("UNKNOWN")

    def test_m1_pending_unreadable_journal(self):
        owner = self.spawn("journal")
        journal = Path(owner.receive()["path"])
        owner.terminate_owned()
        journal.unlink()
        journal.mkdir()  # A real unreadable-as-file path, not a mocked JSON parse.
        self.probe("UNKNOWN")

    def test_m1_generation_never_reused_and_explicit_terminal(self):
        result = self.call("generations")
        self.assertEqual((result["one"], result["two"]), (1, 2))
        self.assertEqual(result["refused"], "FAILED-STORAGE")
        lines = [json.loads(line) for line in result["terminal"].splitlines()]
        self.assertEqual(lines[-1]["event"], "TXN-ABANDONED-DRAINED")
        self.assertTrue(lines[-1]["evidence"]["drained"])


class TestM1Storage(M1Case):
    def test_m1_deleted_published_authority_is_unknown(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        wt, _ = self.paths()
        (wt / "state.json").unlink()
        self.probe("UNKNOWN")

    def test_m1_ac18_late_live_owner_clock_shifts(self):
        for shift in (0, -86400, 86400):
            with self.subTest(shift=shift):
                owner = self.spawn(root=str(self.scratch / str(shift)), old_heartbeat=True, clock_shift=shift)
                self.assertEqual(owner.receive()["result"], "GRANTED")
                self.probe("HELD", root=str(self.scratch / str(shift)), clock_shift=-shift)

    def test_m1_ac19_truncated_state(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        wt, _ = self.paths()
        (wt / "state.json").write_bytes(b'{"lifecycle":')
        self.probe("UNKNOWN")

    def test_m1_ac19_truncated_branch(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        _, br = self.paths()
        (br / "state.json").write_bytes(b'{"bound_to":')
        self.probe("UNKNOWN")

    def test_m1_ac19_truncated_heartbeat(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        wt, _ = self.paths()
        (wt / "gen-1/heartbeat.json").write_bytes(b'{"ts":')
        self.probe("UNKNOWN")

    def test_m1_ac19_stray_temp_ignored(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        wt, _ = self.paths()
        (wt / "state.json.tmp-stray").write_bytes(b'not JSON')
        self.probe("GRANTED", "write")

    def test_m1_ac19_pid_creation_and_access_denied(self):
        owner = self.spawn()
        identity = owner.receive()["identity"]
        self.assertEqual(self.call("liveness", identity=identity)["result"], "alive")
        reused = dict(identity, ctime=identity["ctime"] + 1)
        self.assertEqual(self.call("liveness", identity=reused)["result"], "dead")
        self.assertEqual(self.call("liveness", identity=identity, denied=True)["result"], "UNKNOWN")
        owner.terminate_owned()
        self.assertEqual(self.call("liveness", identity=identity)["result"], "dead")

    def test_m1_strict_schema_and_duplicate_keys(self):
        self.assertEqual(self.call("released")["result"], "DONE")
        wt, _ = self.paths()
        path = wt / "state.json"
        original = path.read_text()
        parsed = json.loads(original)
        for malformed in (dict(parsed, surprise=1), dict(parsed, gen=True),
                          dict(parsed, guardian={"pid": 1}), dict(parsed, lifecycle="RECOVERED")):
            path.write_text(json.dumps(malformed), encoding="utf-8")
            self.probe("UNKNOWN")
        path.write_text(original.replace('"gen":1', '"gen":1,"gen":1'), encoding="utf-8")
        self.probe("UNKNOWN")

    def test_m1_failed_storage_never_grants(self):
        result = self.call("owner", fail="state-write")
        self.assertEqual(result["result"], "FAILED-STORAGE")
        self.probe("OPERATOR-BLOCKED")


class TestM1Identity(M1Case):
    def junction(self, path, target):
        result = subprocess.run([self.env["COMSPEC"], "/d", "/c", "mklink", "/J", str(path), str(target)],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.addCleanup(lambda: os.rmdir(path) if os.path.lexists(path) else None)

    def owner(self):
        owner = self.spawn()
        self.assertEqual(owner.receive()["result"], "GRANTED")
        return owner

    def test_m1_ac15_alias_ancestor_junction(self):
        alias = self.scratch / "alias-ancestor-junction"
        self.junction(alias, self.scratch)
        self.owner()
        self.probe("HELD", worktree=str(alias / self.worktree.name))

    def test_m1_ac15_alias_8dot3(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        f = k.GetShortPathNameW
        f.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        f.restype = ctypes.c_uint32
        buf = ctypes.create_unicode_buffer(32768)
        self.assertGreater(f(str(self.worktree), buf, len(buf)), 0, "BLOCKED alias-8dot3: API failed")
        self.assertNotEqual(Path(buf.value).name.lower(), self.worktree.name.lower(),
                            "BLOCKED alias-8dot3: short names unavailable on C:")
        self.owner()
        self.probe("HELD", worktree=buf.value)

    def test_m1_ac15_alias_drive_subst(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        mask = k.GetLogicalDrives()
        self.assertNotEqual(mask, 0)
        letter = next((chr(65 + i) for i in range(25, 3, -1) if not mask & (1 << i)), None)
        self.assertIsNotNone(letter, "BLOCKED alias-drive-subst: no unused drive")
        assert letter is not None
        drive = letter + ":"
        executable = str(Path(self.env["SYSTEMROOT"]) / "System32/subst.exe")
        added = False
        try:
            result = subprocess.run([executable, drive, str(self.scratch)], env=self.env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            added = True
            self.owner()
            self.probe("HELD", worktree=drive + "\\" + self.worktree.name)
        finally:
            if added:
                result = subprocess.run([executable, drive, "/D"], env=self.env, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(Path(drive + "\\").exists())

    def test_m1_ac15_root_is_reparse(self):
        alias = self.scratch / "root-is-reparse"
        self.junction(alias, self.worktree)
        self.owner()
        self.probe("UNKNOWN", worktree=str(alias))

    def test_m1_ac15_different_branch_same_worktree(self):
        self.owner()
        self.probe("HELD", branch="different-branch")


class TestM1Entry(M1Case):
    def test_m1_fixed_result_lines_and_codes(self):
        rows = {
            0: "OK GRANTED DONE CANCELLED NOT-ADMITTED", 2: "HELD",
            3: "LEASE-LOST REJECTED", 4: "FOREIGN-WRITE",
            5: "ORPHANED OPERATOR-BLOCKED BUSY BATCH-OPEN RUNNING",
            6: "UNKNOWN UNKNOWN-EFFECT UNSUPPORTED-CONTEXT TEST-OVERRIDE-REJECTED FAILED-STORAGE",
            7: "ADOPTION-REQUIRED",
        }
        for code, names in rows.items():
            for name in names.split():
                with self.subTest(result=name):
                    config = self.scratch / "emit.json"
                    config.write_text(json.dumps({"action": "emit-result", "result": name}))
                    result = subprocess.run([getattr(sys, "_base_executable"), "-B", str(ENTRY), str(config)],
                                            env=self.env, cwd=self.scratch, capture_output=True,
                                            text=True, timeout=20)
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertEqual(result.stdout, name + "\n")

    def invoke(self, verb, extra=None):
        env = dict(self.env)
        env.update(extra or {})
        before = {str(p): p.read_bytes() for p in self.scratch.rglob("*") if p.is_file()}
        result = subprocess.run([sys.executable, "-B", str(LEASE), verb, "--resource", str(self.state)],
                                cwd=self.scratch, env=env, capture_output=True, text=True, timeout=20)
        after = {str(p): p.read_bytes() for p in self.scratch.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(result.returncode, 6, result.stderr)
        return result.stdout.strip()

    def test_m1_inert_incomplete_all_verbs(self):
        for verb in ("hold", "acquire", "gate", "begin-native", "end-native", "exec", "release", "status", "cancel", "recover", "adopt", "unknown"):
            with self.subTest(verb=verb):
                self.assertEqual(self.invoke(verb), "UNKNOWN")
        self.assertFalse(self.state.exists())

    def test_m1_normal_entry_refuses_overrides(self):
        for name in ("LEASE_TEST_BOOT_ID", "LEASE_TEST_FAIL", "LEASE_TEST_BARRIERS", "LEASE_TEST_UNKNOWN"):
            with self.subTest(name=name):
                self.assertEqual(self.invoke("recover", {name: ""}), "TEST-OVERRIDE-REJECTED")
        self.assertFalse(self.state.exists())

    def test_m1_normal_entry_fake_boot_preserves_orphan(self):
        owner = self.spawn()
        self.assertEqual(owner.receive()["result"], "GRANTED")
        owner.terminate_owned()
        self.probe("ORPHANED")
        self.assertEqual(self.invoke("recover", {"LEASE_TEST_BOOT_ID": "99"}), "TEST-OVERRIDE-REJECTED")
        self.probe("ORPHANED")


if __name__ == "__main__":
    unittest.main()
