"""WD-618 M1/M2a primitive tests. All state is synthetic harness-owned scratch.

M2 guardian/IPC, M3 observation, M4 recover/adopt and M5 integration are PENDING.
No test here asserts whole-criterion or deployed lease acceptance.
"""
import ctypes
import ast
import dataclasses
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from typing import Any
import uuid
import venv

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "deploy/tests/lease_fixtures/test_entry.py"
LEASE = ROOT / "deploy/lease.py"
FP = "a" * 64


def scratch_root(environ):
    """Require an explicit harness-owned long path, not Python's temp fallback."""
    value = next((environ[key] for key in ("TEMP", "TMP", "TMPDIR") if environ.get(key)), None)
    if not value or not Path(value).is_absolute() or re.search(r"~[0-9]", value):
        raise AssertionError("BLOCKED fixture root: explicit full-path TEMP/TMP/TMPDIR required")
    root = Path(value).resolve(strict=True)
    if not root.is_dir():
        raise AssertionError("BLOCKED fixture root: not a directory")
    return root


class OwnedSubst:
    """A test mapping receipt; cleanup never deletes an unverified definition."""
    def __init__(self, drive, target, env):
        self.drive, self.target, self.env = drive, Path(target).resolve(), env
        self.executable = str(Path(env["SYSTEMROOT"]) / "System32/subst.exe")
        self.created_definition = None

    @staticmethod
    def unused_drive():
        mask = ctypes.WinDLL("kernel32", use_last_error=True).GetLogicalDrives()
        if not mask:
            raise AssertionError("BLOCKED subst: GetLogicalDrives failed")
        for i in range(25, 3, -1):
            if not mask & (1 << i):
                return chr(65 + i) + ":"
        raise AssertionError("BLOCKED subst: no unused drive")

    def definition(self):
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        query = k.QueryDosDeviceW
        query.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        query.restype = ctypes.c_uint32
        buf = ctypes.create_unicode_buffer(32768)
        if not query(self.drive, buf, len(buf)):
            if ctypes.get_last_error() == 2:
                return None
            raise AssertionError("BLOCKED subst: cannot verify current target")
        return buf.value

    def create(self):
        if self.definition() is not None:
            raise AssertionError("BLOCKED subst: occupied letter")
        result = subprocess.run([self.executable, self.drive, str(self.target)], env=self.env,
                                capture_output=True, timeout=20)
        if result.returncode:
            raise AssertionError(f"BLOCKED subst: creation failed ({result.returncode})")
        expected = "\\??\\" + str(self.target)
        actual = self.definition()
        if actual != expected:
            raise AssertionError(f"BLOCKED subst: created target mismatch {actual!r} != {expected!r}")
        self.created_definition = actual
        print(f"SUBST CREATED {self.drive} => {actual}", flush=True)

    def cleanup(self):
        if self.created_definition is None:
            return "NOT-OWNED"
        actual = self.definition()
        if actual != self.created_definition:
            raise AssertionError(f"BLOCKED subst: target replaced; preserved {self.drive} => {actual!r}")
        result = subprocess.run([self.executable, self.drive, "/D"], env=self.env,
                                capture_output=True, timeout=20)
        if result.returncode or self.definition() is not None:
            raise AssertionError("BLOCKED subst: owned cleanup failed")
        self.created_definition = None
        return "REMOVED-OWNED"


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


@unittest.skipUnless(os.name == "nt", "Windows identity/locking/process fixtures; NOT RUN on non-Windows")
class M1Case(unittest.TestCase):
    def setUp(self):
        base = scratch_root(os.environ)
        self.temp = tempfile.TemporaryDirectory(prefix="wd618-m1-", dir=base)
        self.scratch = Path(self.temp.name).resolve()
        self.assertTrue(self.scratch.is_relative_to(base))
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
        self.assertTrue(self.scratch.is_relative_to(scratch_root(os.environ)))
        for path in self.scratch.rglob("*"):
            self.assertTrue(path.resolve().is_relative_to(self.scratch), str(path))
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
        mapping = OwnedSubst(OwnedSubst.unused_drive(), self.scratch, self.env)
        try:
            mapping.create()
            self.owner()
            self.probe("HELD", worktree=mapping.drive + "\\" + self.worktree.name)
        finally:
            self.assertEqual(mapping.cleanup(), "REMOVED-OWNED")
        self.assertIsNone(mapping.definition())

    def test_m1_subst_occupied_and_failed_create_remove_nothing(self):
        owner = OwnedSubst(OwnedSubst.unused_drive(), self.scratch, self.env)
        owner.create()
        try:
            occupied = OwnedSubst(owner.drive, self.worktree, self.env)
            with self.assertRaisesRegex(AssertionError, "occupied letter"):
                occupied.create()
            self.assertEqual(occupied.cleanup(), "NOT-OWNED")
            self.assertEqual(owner.definition(), owner.created_definition)
        finally:
            self.assertEqual(owner.cleanup(), "REMOVED-OWNED")
        failed = OwnedSubst(owner.drive, self.scratch / "does-not-exist", self.env)
        with self.assertRaisesRegex(AssertionError, "creation failed"):
            failed.create()
        self.assertEqual(failed.cleanup(), "NOT-OWNED")
        self.assertIsNone(failed.definition())

    def test_m1_subst_replaced_target_is_preserved_and_reported(self):
        original = OwnedSubst(OwnedSubst.unused_drive(), self.scratch, self.env)
        original.create()
        # A second test-owned actor removes only the verified original mapping.
        replacer = OwnedSubst(original.drive, self.scratch, self.env)
        replacer.created_definition = original.created_definition
        self.assertEqual(replacer.cleanup(), "REMOVED-OWNED")
        replacement = OwnedSubst(original.drive, self.worktree, self.env)
        replacement.create()
        try:
            with self.assertRaisesRegex(AssertionError, "target replaced; preserved") as caught:
                original.cleanup()
            print(str(caught.exception), flush=True)
            self.assertEqual(replacement.definition(), replacement.created_definition)
            self.assertTrue(Path(replacement.drive + "\\").is_dir())
        finally:
            self.assertEqual(replacement.cleanup(), "REMOVED-OWNED")
        self.assertIsNone(replacement.definition())

    def test_m1_fixture_root_is_explicit_and_confined(self):
        self.assertTrue(self.scratch.is_relative_to(scratch_root(os.environ)))
        for env in ({}, {"TEMP": "relative"}, {"TMP": "C:/SHORT~1/scratch"}):
            with self.subTest(env=env), self.assertRaisesRegex(AssertionError, "explicit full-path"):
                scratch_root(env)
        for key in ("TEMP", "TMP", "TMPDIR"):
            self.assertEqual(scratch_root({key: str(self.scratch)}), self.scratch)

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


class TestM1PlatformRefusal(unittest.TestCase):
    """Unskipped Linux proof; Windows simulation is explicitly not Linux proof."""
    def setUp(self):
        harness = dict(os.environ)
        # GitHub supplies its session-owned scratch as RUNNER_TEMP, not TMPDIR.
        # Make that explicit for this test harness, without a system-temp fallback.
        if os.name != "nt" and not any(harness.get(k) for k in ("TEMP", "TMP", "TMPDIR")):
            harness["TMPDIR"] = harness.get("RUNNER_TEMP", "")
        base = scratch_root(harness)
        self.temp = tempfile.TemporaryDirectory(prefix="wd618-platform-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name).resolve()
        self.assertTrue(self.scratch.is_relative_to(base))
        self.env = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT")
                    if k in os.environ}
        for key in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.env[key] = str(self.scratch)

    def invoke(self, path, override=False, instrument=False):
        env = dict(self.env)
        if override:
            env["LEASE_TEST_BOOT_ID"] = "99"
        args = [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B"]
        if instrument or os.name == "nt":
            # Installed in the actual child before main, never a parent-only trace.
            code = '''
import os, runpy, sys, types
module = runpy.run_path(sys.argv[1], run_name="lease_platform_probe")
if os.name == "nt":
    module["main"].__globals__["os"] = types.SimpleNamespace(name="posix", environ=os.environ)
effects = []
def audit(event, args):
    writing = event == "open" and ((isinstance(args[1], str) and any(c in args[1] for c in "wax+")) or
                                  (isinstance(args[2], int) and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)))
    if writing or event.startswith(("subprocess.", "winreg.", "msvcrt.", "os.spawn", "os.exec", "os.fork")) or event in (
            "os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.system", "ctypes.dlopen"):
        effects.append(event)
        raise AssertionError("protected effect: " + event)
sys.addaudithook(audit)
result = module["main"](["hold", "--resource", sys.argv[2]])
print("EFFECT-AUDIT loaded=true effects=" + str(len(effects)), file=sys.stderr)
sys.exit(result)
'''
            args += ["-c", code, str(path), str(self.scratch / "state")]
        else:
            args += [str(path), "hold", "--resource", str(self.scratch / "state")]
        before = {str(p.relative_to(self.scratch)): p.read_bytes() if p.is_file() else None
                  for p in self.scratch.rglob("*")}
        result = subprocess.run(args, env=env, cwd=self.scratch, capture_output=True, text=True, timeout=20)
        after = {str(p.relative_to(self.scratch)): p.read_bytes() if p.is_file() else None
                 for p in self.scratch.rglob("*")}
        self.assertEqual(before, after, "normal entry wrote protected state")
        self.assertEqual(result.returncode, 6, result.stderr)
        if instrument or os.name == "nt":
            self.assertEqual(result.stderr, "EFFECT-AUDIT loaded=true effects=0\n")
        else:
            self.assertEqual(result.stderr, "")
        return result

    def assert_unsupported(self, result):
        self.assertEqual(result.stdout, "UNSUPPORTED-CONTEXT\n")
        self.assertEqual(result.returncode, 6)

    def test_m1_non_windows_cli_exact_refusal_no_effects(self):
        self.assert_unsupported(self.invoke(LEASE))
        self.assert_unsupported(self.invoke(LEASE, instrument=True))
        self.assertFalse(any(self.scratch.iterdir()))

    def test_m1_non_windows_unknown6_negative_control(self):
        source = LEASE.read_text(encoding="utf-8")
        guard = '    if os.name != "nt":\n        return emit("UNSUPPORTED-CONTEXT")\n'
        self.assertEqual(source.count(guard), 1)
        mutant = self.scratch / "lease_without_platform_guard.py"
        mutant.write_text(source.replace(guard, ""), encoding="utf-8", newline="\n")
        result = self.invoke(mutant)
        self.assertEqual(result.stdout, "UNKNOWN\n")
        with self.assertRaisesRegex(AssertionError, "UNSUPPORTED-CONTEXT"):
            self.assert_unsupported(result)

    def test_m1_non_windows_override_refusal_precedes_platform(self):
        self.assertEqual(self.invoke(LEASE, override=True).stdout, "TEST-OVERRIDE-REJECTED\n")
        self.assertFalse(any(self.scratch.iterdir()))


@unittest.skipUnless(os.name == "nt", "Windows M2a fixtures; NOT RUN on non-Windows")
class M2aCase(unittest.TestCase):
    def setUp(self):
        from deploy import lease
        self.lease = lease
        self.temp = tempfile.TemporaryDirectory(prefix="wd618-m2a-", dir=scratch_root(os.environ))
        self.scratch = Path(self.temp.name).resolve()
        self.assertTrue(self.scratch.is_relative_to(scratch_root(os.environ)))
        self.addCleanup(self.temp.cleanup)
        self.boot = mock.Mock()
        self.boot.read.return_value = 10
        self.deps = lease.Deps(self.boot, lease.NoBarrier(), lease.NoFailure(),
                               lease.NoAudit(), lease.ProcessApi(), time.time)
        self.env = {key: os.environ[key] for key in
                    ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE")
                    if key in os.environ}
        for key in ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            self.env[key] = str(self.scratch)
        self.api: Any = lease.ProcApi()  # Methods gain fixture-only recording spies.
        self.deps = dataclasses.replace(self.deps, process=self.api)
        self.fixture_children = []
        self.addCleanup(self.drain_fixture_children)

    def drain_fixture_children(self):
        for api, child, pid in self.fixture_children:
            self.assertEqual(child.pid, pid)
            self.assertNotEqual(pid, os.getpid())
            if child.closed:
                continue
            if api.wait(child, 0) is None:
                # Test exception NG-8(a), not production D9. Original handles only.
                k = self.lease._kernel()
                k.GetProcessId.argtypes = [ctypes.c_void_p]
                k.GetProcessId.restype = ctypes.c_uint32
                self.assertEqual(k.GetProcessId(child.process_handle), pid)
                k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
                k.TerminateProcess.restype = ctypes.c_int
                self.assertTrue(k.TerminateProcess(child.process_handle, 91))
            self.assertIsNotNone(api.wait(child, 15000))
            api.close_child(child)
        self.assertTrue(all(child.closed for _, child, _ in self.fixture_children))

    def argv(self, marker="marker", release="release"):
        return [getattr(sys, "_base_executable"), "-B", str(ENTRY.with_name("m2a_child.py")),
                str(self.scratch), marker, release]

    def child(self, api=None):
        api = api or self.api
        child = api.create_suspended(self.argv(), str(self.scratch), self.env)
        self.fixture_children.append((api, child, child.pid))
        return child

    def job(self):
        job = self.lease.Job.create(self.deps)
        self.addCleanup(job.close)
        return job

    def await_file(self, name):
        path = self.scratch / name
        deadline = time.monotonic() + 15
        while not path.exists():
            self.assertLess(time.monotonic(), deadline, "fixture marker timeout: " + name)
            time.sleep(0.01)
        return path

    def ledger(self, deps=None, directory=None):
        ledger = self.lease.Ledger.create(directory or self.scratch, deps or self.deps)
        self.addCleanup(ledger.close)
        return ledger

    def unknown(self, function, *args):
        with self.assertRaises(self.lease.LeaseError) as caught:
            function(*args)
        self.assertEqual(caught.exception.result, "UNKNOWN")


class TestM2aLedger(M2aCase):
    def test_m2a_ledger_seq_fsync_strict_read(self):
        ledger = self.ledger()
        assert ledger._stream is not None
        with mock.patch.object(self.lease.os, "fsync", wraps=os.fsync) as sync:
            self.assertEqual(ledger.append("one", "exec", "ADMITTED"), 1)
            self.assertEqual(ledger.append("one", "exec", "LAUNCH-ATTEMPTED"), 2)
            self.assertEqual(sync.call_count, 2)
            self.assertEqual(sync.call_args.args, (ledger._stream.fileno(),))
        self.assertEqual(ledger.phase("one"), "LAUNCH-ATTEMPTED")
        reader = self.lease.Ledger.open_existing(ledger.path, self.deps)
        self.assertEqual(reader.phase("one"), "LAUNCH-ATTEMPTED")
        self.assertEqual([r["seq"] for r in reader.entries()], [1, 2])
        with self.assertRaisesRegex(self.lease.LeaseError, "FAILED-STORAGE"):
            reader.append("two", "exec", "ADMITTED")
        ledger.close()
        original = ledger.path.read_bytes()
        for bad in (b'{}\n', b'not-json\n', original.replace(b'LAUNCH-ATTEMPTED', b'BAD-PHASE')):
            with self.subTest(bad=bad):
                ledger.path.write_bytes(bad)
                self.unknown(reader.entries)

    def test_m2a_ledger_truncated_last_line_unknown(self):
        ledger = self.ledger()
        ledger.append("one", "exec", "ADMITTED")
        ledger.close()
        with ledger.path.open("ab") as stream:
            stream.write(b'{"seq":2,"phase":"RESUME-ATTEMPTED"')
        self.unknown(self.lease.Ledger.open_existing, ledger.path, self.deps)

    def test_m2a_ledger_nonincreasing_seq_unknown(self):
        ledger = self.ledger()
        ledger.append("one", "exec", "ADMITTED")
        ledger.close()
        row = json.loads(ledger.path.read_bytes())
        for seq in (1, 0, 3, None, True):
            with self.subTest(seq=seq):
                other = dict(row, seq=seq)
                ledger.path.write_text(json.dumps(row) + "\n" + json.dumps(other) + "\n", newline="\n")
                self.unknown(ledger.entries)
        del row["seq"]
        ledger.path.write_text(json.dumps(row) + "\n", newline="\n")
        self.unknown(ledger.entries)

    def test_m2a_ledger_append_failure_latches_failed_storage(self):
        for source in ("injection", "fsync", "flush", "write"):
            with self.subTest(source=source):
                directory = self.scratch / source
                directory.mkdir()
                fail = mock.Mock()
                fail.check.side_effect = OSError("injected") if source == "injection" else None
                ledger = self.ledger(dataclasses.replace(self.deps, fail=fail), directory)
                if source in ("flush", "write"):
                    stream = mock.Mock(wraps=ledger._stream)
                    getattr(stream, source).side_effect = OSError(source)
                    ledger._stream = stream
                with mock.patch.object(self.lease.os, "fsync", side_effect=OSError("fsync") if source == "fsync" else None):
                    with self.assertRaisesRegex(self.lease.LeaseError, "FAILED-STORAGE"):
                        ledger.append("one", "exec", "ADMITTED")
                self.assertTrue(ledger.failed)
                self.assertIsNone(ledger.phase("one"))
                self.assertEqual(ledger._seq, 0)
                with self.assertRaisesRegex(self.lease.LeaseError, "FAILED-STORAGE"):
                    ledger.append("one", "exec", "ADMITTED")

    def test_m2a_ledger_records_admission_boot_id(self):
        ledger = self.ledger()
        ledger.append("one", "exec", "ADMITTED")
        self.boot.read.return_value = 11
        ledger.append("one", "exec", "LAUNCH-ATTEMPTED")
        ledger.append("two", "exec", "ADMITTED")
        self.assertEqual([r["boot_id"] for r in ledger.entries()], [10, 10, 11])
        self.boot.read.side_effect = OSError("unreadable boot")
        before = ledger.path.read_bytes()
        self.unknown(ledger.append, "three", "exec", "ADMITTED")
        self.assertEqual(ledger.path.read_bytes(), before)
        self.assertIsNone(ledger.phase("three"))

    def test_m2a_ledger_never_reopens_existing(self):
        ledger = self.ledger()
        ledger.append("one", "exec", "ADMITTED")
        ledger.close()
        before = ledger.path.read_bytes()
        with self.assertRaisesRegex(self.lease.LeaseError, "FAILED-STORAGE"):
            self.lease.Ledger.create(self.scratch, self.deps)
        self.assertEqual(ledger.path.read_bytes(), before)


class TestM2aJob(M2aCase):
    def test_m2a_job_flags_no_breakaway_no_kill_on_close(self):
        job = self.job()
        self.assertEqual(job.flags() & 0x3800, 0)
        self.assertEqual(job.active(), 0)
        self.assertEqual(job.members(), [])
        child = self.child()
        self.assertGreater(child.ctime, 0)
        self.assertIsNone(self.api.wait(child, 0))
        self.assertFalse((self.scratch / "marker").exists())
        self.api.assign(job, child)
        self.assertTrue(job.contains(child))
        self.assertEqual(job.active(), 1)
        self.assertEqual(job.members(), [self.lease.ProcIdentity(child.pid, child.ctime)])
        self.assertEqual(self.api.resume(child), 1)
        self.assertEqual(json.loads(self.await_file("marker").read_text())["pid"], child.pid)
        (self.scratch / "release").touch()
        self.assertEqual(self.api.wait(child, 15000), 0)
        self.assertEqual(job.active(), 0)

    def test_m2a_job_close_terminates_nothing(self):
        job = self.job()
        child = self.child()
        self.api.assign(job, child)
        self.assertTrue(job.contains(child))
        self.assertEqual(self.api.resume(child), 1)
        self.assertEqual(json.loads(self.await_file("marker").read_text())["pid"], child.pid)
        job.close()
        self.assertIsNone(self.api.wait(child, 200))
        self.assertEqual(self.api.creation_time(child.pid), child.ctime)
        (self.scratch / "release").touch()
        self.assertEqual(self.api.wait(child, 15000), 0)


class PhaseBarrier:
    def __init__(self, phase):
        self.phase = phase
        self.entered, self.leave = threading.Event(), threading.Event()

    def reach(self, phase):
        if phase == self.phase:
            self.entered.set()
            if not self.leave.wait(30):
                raise AssertionError("fixture barrier timeout: " + phase)


class M2aExecutorCase(M2aCase):
    def executor(self, fail_at=(), barrier=None, argv=None):
        fail = mock.Mock()
        def check(name):
            if name in fail_at:
                raise OSError("injected " + name)
        fail.check.side_effect = check
        deps = dataclasses.replace(self.deps, fail=fail, barrier=barrier or self.lease.NoBarrier())
        original_create = self.api.create_suspended
        def create(*args):
            child = original_create(*args)
            self.fixture_children.append((self.api, child, child.pid))
            return child
        self.api.create_suspended = mock.Mock(side_effect=create)
        self.api.resume = mock.Mock(wraps=self.api.resume)
        self.api.terminate_unrun = mock.Mock(wraps=self.api.terminate_unrun)
        ledger = self.ledger(deps)
        executor = self.lease.Executor(ledger, self.job(), deps, threading.Lock())
        self.assertEqual(executor.admit("op", argv or self.argv(), str(self.scratch), self.env), "ADMITTED")
        return executor

    def start(self, executor, barrier):
        results = []
        def run():
            try:
                results.append(executor.run("op"))
            except BaseException as exc:
                results.append(exc)
        thread = threading.Thread(target=run)
        def cleanup():
            barrier.leave.set()
            (self.scratch / "release").touch()
            (self.scratch / "grand-release").touch()
            thread.join(20)
            self.assertFalse(thread.is_alive(), "fixture executor did not drain")
        self.addCleanup(cleanup)
        thread.start()
        self.assertTrue(barrier.entered.wait(15), results)
        return thread, results

    def finish(self, thread, results, barrier):
        barrier.leave.set()
        thread.join(20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], self.lease.Outcome)
        return results[0]

    def phases(self, executor):
        return [row["phase"] for row in executor.ledger.entries()]

class TestM2aExecutor(M2aExecutorCase):
    def test_m2a_failed_cancel_never_continues_to_resume(self):
        barrier = PhaseBarrier("LAUNCHED-SUSPENDED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        self.api.terminate_unrun.side_effect = self.lease.LeaseError("UNKNOWN")
        self.assertEqual(executor.cancel("op").result, "UNKNOWN")
        self.assertEqual(self.finish(thread, results, barrier).result, "UNKNOWN")
        self.api.resume.assert_not_called()
        self.assertEqual(executor.open_ops(), ["op"])
        self.assertIsNone(self.api.wait(self.fixture_children[0][1], 0))
        self.assertFalse((self.scratch / "marker").exists())

    def test_m2a_resume_attempted_refuses_d9_before_os_resume(self):
        barrier = PhaseBarrier("RESUME-ATTEMPTED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        record = executor._ops["op"]
        with executor.phase_mutex:
            record.cleanup_reason = "cancel"
            with self.assertRaisesRegex(self.lease.LeaseError, "REJECTED"):
                self.api.terminate_unrun(record.child, record)
        self.assertEqual(executor.cancel("op").result, "RUNNING")
        self.assertFalse((self.scratch / "marker").exists())
        self.api.resume.assert_not_called()
        (self.scratch / "release").touch()
        self.assertEqual(self.finish(thread, results, barrier).result, "DONE")

    def test_m2a_create_failure_launch_failed_nothing_ran(self):
        executor = self.executor(argv=[str(self.scratch / "missing.exe")])
        result = executor.run("op")
        self.assertEqual(result.phase, "LAUNCH-FAILED")
        self.assertEqual(executor.open_ops(), [])
        self.assertEqual(self.phases(executor), ["ADMITTED", "LAUNCH-ATTEMPTED", "LAUNCH-FAILED"])
        self.assertFalse((self.scratch / "marker").exists())
        self.api.resume.assert_not_called()
        self.api.terminate_unrun.assert_not_called()

    def test_m2a_assign_failed_d9_terminated_unrun_marker_absent(self):
        executor = self.executor(fail_at={"assign"})
        self.assertEqual(executor.run("op").result, "CANCELLED")
        self.assertEqual(self.phases(executor), ["ADMITTED", "LAUNCH-ATTEMPTED", "LAUNCHED-SUSPENDED",
                                                "ASSIGN-FAILED", "TERMINATED-UNRUN", "CANCELLED"])
        self.api.terminate_unrun.assert_called_once()
        child = self.fixture_children[0][1]
        self.assertEqual(self.api.wait(child, 0), 1)
        self.assertFalse((self.scratch / "marker").exists())
        self.api.resume.assert_not_called()
        self.assertEqual(executor.open_ops(), [])

    def test_m2a_resume_attempted_append_fail_no_resume_d9(self):
        executor = self.executor(fail_at={"append:RESUME-ATTEMPTED"})
        self.assertEqual(executor.run("op").result, "FAILED-STORAGE")
        self.api.resume.assert_not_called()
        self.api.terminate_unrun.assert_called_once()
        self.assertEqual(self.api.wait(self.fixture_children[0][1], 0), 1)
        self.assertFalse((self.scratch / "marker").exists())
        self.assertNotIn("RESUME-ATTEMPTED", self.phases(executor))
        self.assertEqual(executor.status("op").result, "UNKNOWN")
        self.assertEqual(executor.open_ops(), ["op"])
        self.assertTrue(executor.ledger.failed)
        with self.assertRaisesRegex(self.lease.LeaseError, "FAILED-STORAGE"):
            executor.admit("next", self.argv(), str(self.scratch), self.env)

    def test_m2a_terminated_unrun_append_fail_op_open_status_unknown(self):
        executor = self.executor(fail_at={"assign", "append:TERMINATED-UNRUN"})
        self.assertEqual(executor.run("op").result, "FAILED-STORAGE")
        self.api.terminate_unrun.assert_called_once()
        self.assertEqual(self.api.wait(self.fixture_children[0][1], 0), 1)
        self.assertEqual(executor.ledger.phase("op"), "ASSIGN-FAILED")
        self.assertEqual(executor.open_ops(), ["op"])
        self.assertEqual(executor.status("op").result, "UNKNOWN")
        self.assertFalse((self.scratch / "marker").exists())

    def test_m2a_truncated_resume_attempted_recovered_unknown_no_d9(self):
        barrier = PhaseBarrier("LAUNCHED-SUSPENDED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        recovered = self.scratch / "recovered.jsonl"
        recovered.write_bytes(executor.ledger.path.read_bytes() + b'{"phase":"RESUME-ATTEMPTED"')
        reader = self.lease.Ledger(recovered, self.deps)
        self.assertEqual(self.lease.classify_recovered(reader), "UNKNOWN")
        self.assertEqual(self.lease.classify_recovered(reader, executor), "UNKNOWN")
        self.api.terminate_unrun.assert_not_called()
        self.assertIsNone(self.api.wait(self.fixture_children[0][1], 0))
        # The original live executor, not recovery, drains normally after proof.
        (self.scratch / "release").touch()
        self.assertEqual(self.finish(thread, results, barrier).result, "DONE")
        self.api.terminate_unrun.assert_not_called()

    def test_m2a_after_os_resume_before_resumed_marker_appears_cancel_running(self):
        barrier = PhaseBarrier("after-os-resume")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        child = self.fixture_children[0][1]
        self.assertEqual(json.loads(self.await_file("marker").read_text())["pid"], child.pid)
        self.assertEqual(executor.ledger.phase("op"), "RESUME-ATTEMPTED")
        self.assertEqual(executor.cancel("op").result, "RUNNING")
        self.assertEqual(executor.status("op").result, "RUNNING")
        self.assertEqual(executor.open_ops(), ["op"])
        self.api.terminate_unrun.assert_not_called()
        (self.scratch / "release").touch()
        self.assertEqual(self.finish(thread, results, barrier).result, "DONE")
        self.assertEqual(executor.open_ops(), [])

    def test_m2a_resume_uncertain_no_cleanup_normal_drain(self):
        barrier = PhaseBarrier("RESUME-UNCERTAIN")
        executor = self.executor(barrier=barrier)
        original_resume = self.api.resume
        def uncertain(child):
            self.assertEqual(original_resume(child), 1)  # Real OS effect, uncertain reported result.
            return 0xffffffff
        self.api.resume = mock.Mock(side_effect=uncertain)
        thread, results = self.start(executor, barrier)
        self.await_file("marker")
        self.assertEqual(executor.cancel("op").result, "RUNNING")
        self.api.terminate_unrun.assert_not_called()
        (self.scratch / "release").touch()
        self.assertEqual(self.finish(thread, results, barrier).result, "DONE")
        self.assertEqual(self.phases(executor)[-3:], ["RESUME-UNCERTAIN", "EXITED", "DRAINED"])

    def test_m2a_old_generation_suspended_child_no_termination_operator_blocked(self):
        barrier = PhaseBarrier("LAUNCHED-SUSPENDED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        reader = self.lease.Ledger.open_existing(executor.ledger.path, self.deps)
        later = self.scratch / "later-generation"
        later.mkdir()
        new_executor = self.lease.Executor(self.ledger(directory=later), executor.job,
                                           self.deps, threading.Lock())
        self.assertEqual(self.lease.classify_recovered(reader, new_executor), "OPERATOR-BLOCKED")
        self.assertEqual(new_executor.cancel("op").result, "REJECTED")
        self.assertIsNone(self.api.wait(self.fixture_children[0][1], 0))
        self.assertFalse((self.scratch / "marker").exists())
        self.api.terminate_unrun.assert_not_called()
        (self.scratch / "release").touch()
        self.assertEqual(self.finish(thread, results, barrier).result, "DONE")

    def test_m2a_pre_create_cancel_launch_cancelled_no_terminate_call(self):
        barrier = PhaseBarrier("LAUNCH-ATTEMPTED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        self.assertEqual(executor.status("op").result, "RUNNING")
        self.assertEqual(executor.cancel("op").result, "CANCELLED")
        self.assertEqual(executor.status("op").result, "CANCELLED")
        self.assertEqual(self.finish(thread, results, barrier).result, "CANCELLED")
        self.assertEqual(self.phases(executor)[-2:], ["LAUNCH-CANCELLED", "CANCELLED"])
        self.api.create_suspended.assert_not_called()
        self.api.terminate_unrun.assert_not_called()
        self.assertFalse((self.scratch / "marker").exists())

    def test_m2a_d9_refuses_pid_and_foreign_child(self):
        barrier = PhaseBarrier("LAUNCHED-SUSPENDED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        child = self.fixture_children[0][1]
        foreign_api = self.lease.ProcApi()
        foreign = self.child(foreign_api)
        record = executor._ops["op"]
        with executor.phase_mutex:
            record.cleanup_reason = "cancel"
            for wrong in (child.pid, foreign, dataclasses.replace(child)):
                with self.assertRaisesRegex(self.lease.LeaseError, "REJECTED"):
                    self.api.terminate_unrun(wrong, record)
        self.assertIsNone(self.api.wait(child, 0))
        self.assertIsNone(foreign_api.wait(foreign, 0))
        self.assertEqual(executor.cancel("op").result, "CANCELLED")
        self.assertEqual(self.api.wait(child, 0), 1)
        self.assertFalse((self.scratch / "marker").exists())
        self.assertEqual(self.finish(thread, results, barrier).result, "CANCELLED")
        # Foreign child cleanup belongs solely to its test creator, never D9.

    def test_m2a_cancel_admitted_no_create(self):
        executor = self.executor()
        self.assertEqual(executor.cancel("op").result, "CANCELLED")
        self.assertEqual(executor.run("op").result, "CANCELLED")
        self.api.create_suspended.assert_not_called()

    def test_m2a_cancel_assigned_d9(self):
        barrier = PhaseBarrier("ASSIGNED")
        executor = self.executor(barrier=barrier)
        thread, results = self.start(executor, barrier)
        self.assertEqual(executor.cancel("op").result, "CANCELLED")
        self.assertEqual(self.finish(thread, results, barrier).result, "CANCELLED")
        self.api.resume.assert_not_called()
        self.api.terminate_unrun.assert_called_once()
        self.assertFalse((self.scratch / "marker").exists())

    def test_m2a_static_terminateprocess_only_in_d9_no_taskkill_os_kill(self):
        source = LEASE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        occurrences = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "TerminateProcess"]
        self.assertTrue(occurrences)
        for node in occurrences:
            owner = [f.name for f in functions if f.end_lineno is not None and f.lineno <= node.lineno <= f.end_lineno]
            self.assertEqual(owner, ["terminate_unrun"])
        self.assertNotIn("taskkill", source.lower())
        self.assertFalse(any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                             and n.value.id == "os" and n.attr == "kill" for n in ast.walk(tree)))
        d9 = next(n for n in functions if n.name == "terminate_unrun")
        self.assertEqual([a.arg for a in d9.args.args], ["self", "child", "proof"])
        segment = ast.get_source_segment(source, d9)
        assert segment is not None
        self.assertIn('"RESUME-ATTEMPTED" in proof.phases', segment)


class TestM2aContainment(M2aExecutorCase):
    def containment(self, mode, executable):
        barrier = PhaseBarrier("EXITED")
        executor = self.executor(barrier=barrier, argv=self.argv() + [mode, str(executable)])
        thread, results = self.start(executor, barrier)
        receipt = json.loads(self.await_file("marker").read_text())
        child = self.fixture_children[0][1]
        self.assertEqual(receipt["parent"], child.pid)
        self.assertEqual(self.api.wait(child, 0), 0)
        self.assertEqual(executor.ledger.phase("op"), "EXITED")
        if receipt["result"] == "REFUSED":
            self.assertEqual(mode, "breakaway")
            self.assertEqual(receipt["winerror"], 5)
            self.assertFalse((self.scratch / "grand-marker").exists())
            self.assertEqual(executor.job.active(), 0)
        else:
            self.assertEqual(receipt["result"], "SPAWNED")
            grand = json.loads(self.await_file("grand-marker").read_text())
            members = executor.job.members()
            pids = {m.pid for m in members}
            self.assertIn(receipt["pid"], pids)
            self.assertIn(grand["pid"], pids)  # Includes venv redirector's actual interpreter.
            self.assertNotEqual(grand["pid"], child.pid)
            for pid in {receipt["pid"], grand["pid"]}:
                k = self.lease._kernel()
                k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
                k.OpenProcess.restype = ctypes.c_void_p
                handle = k.OpenProcess(0x1000, False, pid)
                self.assertTrue(handle)
                try:
                    ctime = self.api.creation_time(pid)
                    assert ctime is not None
                    probe = self.lease.Child(handle, 0, pid, ctime)
                    self.assertTrue(executor.job.contains(probe))
                finally:
                    k.CloseHandle(handle)
            self.assertGreater(executor.job.active(), 0)
            barrier.leave.set()
            thread.join(0.2)
            self.assertTrue(thread.is_alive(), "DRAINED before descendant cessation")
            self.assertEqual(executor.status("op").phase, "EXITED")
            self.assertEqual(executor.open_ops(), ["op"])
            self.assertNotIn("DRAINED", self.phases(executor))
            (self.scratch / "grand-release").touch()
        outcome = self.finish(thread, results, barrier)
        self.assertEqual(outcome.result, "DONE")
        self.assertEqual(outcome.exit_code, 0)
        self.assertEqual(executor.job.active(), 0)
        self.assertEqual(executor.job.members(), [])
        self.api.terminate_unrun.assert_not_called()
        print(f"AC17 mode={mode} executable={executable} result={receipt['result']} drained=0", flush=True)

    def test_m2a_grandchild_breakaway_direct_refused_or_in_job(self):
        self.containment("breakaway", getattr(sys, "_base_executable"))

    def test_m2a_grandchild_breakaway_venv_refused_or_in_job(self):
        directory = self.scratch / "fixture-venv"
        venv.EnvBuilder(with_pip=False).create(directory)
        executable = directory / "Scripts/python.exe"
        self.assertTrue(executable.is_file())
        self.assertNotEqual(executable.resolve(), Path(getattr(sys, "_base_executable")).resolve())
        self.containment("breakaway", executable)

    def test_m2a_drained_waits_for_grandchild_exit(self):
        self.containment("descendant", getattr(sys, "_base_executable"))


if __name__ == "__main__":
    unittest.main()
