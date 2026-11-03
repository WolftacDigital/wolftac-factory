"""WD-618: Windows identity, publication and execution primitives.

The public entry is deliberately inert until the guardian and observation stages
exist. These library primitives do not establish observation/drain evidence.
Publication callers must own BOTH locks for the entire transaction and supply
validated evidence from the later stages. Wall clock values never grant authority.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass, field
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Protocol
import uuid

RESULTS = {name: (code, name) for code, names in (
    (0, "OK GRANTED DONE CANCELLED NOT-ADMITTED"), (2, "HELD"),
    (3, "LEASE-LOST REJECTED"), (4, "FOREIGN-WRITE"),
    (5, "ORPHANED OPERATOR-BLOCKED BUSY BATCH-OPEN RUNNING"),
    (6, "UNKNOWN UNKNOWN-EFFECT UNSUPPORTED-CONTEXT TEST-OVERRIDE-REJECTED FAILED-STORAGE"),
    (7, "ADOPTION-REQUIRED"),
) for name in names.split()}


class LeaseError(Exception):
    def __init__(self, result="UNKNOWN"):
        super().__init__(result)
        self.result = result


def emit(result):
    code, line = RESULTS[result]
    print(line, flush=True)
    return code


class BootSource(Protocol):
    def read(self) -> int: ...


class Barrier(Protocol):
    def reach(self, name: str) -> None: ...


class FailInjector(Protocol):
    def check(self, name: str) -> None: ...


class AuditProbe(Protocol):
    def record(self, event: str, path: Path) -> None: ...


class RegistryBoot:
    def read(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters",
                                0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, "BootId")
            if kind != winreg.REG_DWORD or type(value) is not int or not 0 <= value <= 0xffffffff:
                raise LeaseError()
            return value
        except OSError as exc:
            raise LeaseError() from exc


class NoBarrier:
    def reach(self, name):
        pass


class NoFailure:
    def check(self, name):
        pass


class NoAudit:
    def record(self, event, path):
        pass


@dataclass(frozen=True)
class ProcIdentity:
    pid: int
    ctime: int

    def record(self):
        return {"pid": self.pid, "ctime": self.ctime}


class ProcessApi:
    def creation_time(self, pid):
        k = _kernel()
        k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        k.OpenProcess.restype = W.HANDLE
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
        k.GetProcessTimes.restype = W.BOOL
        k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
        k.GetExitCodeProcess.restype = W.BOOL
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists
                return None
            raise LeaseError()
        try:
            exit_code = W.DWORD()
            if not k.GetExitCodeProcess(h, ctypes.byref(exit_code)):
                raise LeaseError()
            if exit_code.value != 259:
                return None
            times = [W.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(h, *(ctypes.byref(t) for t in times)):
                raise LeaseError()
            return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            k.CloseHandle(h)

    def current(self):
        pid = os.getpid()
        ctime = self.creation_time(pid)
        if ctime is None:
            raise LeaseError()
        return ProcIdentity(pid, ctime)


@dataclass(frozen=True)
class Deps:
    boot: BootSource
    barrier: Barrier
    fail: FailInjector
    audit: AuditProbe
    process: ProcessApi
    clock: Callable[[], float]


def default_deps():
    return Deps(RegistryBoot(), NoBarrier(), NoFailure(), NoAudit(), ProcApi(), time.time)


def _kernel():
    if os.name != "nt":
        raise LeaseError("UNSUPPORTED-CONTEXT")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CloseHandle.argtypes = [W.HANDLE]
    k.CloseHandle.restype = W.BOOL
    return k


@dataclass(frozen=True)
class DirIdentity:
    serial: int
    index: int
    final_path: str


def dir_identity(path):
    """The sole directory-handle open. Resolve ancestors, not the final reparse."""
    class Info(ctypes.Structure):
        _fields_ = [("attrs", W.DWORD), ("created", W.FILETIME),
                    ("accessed", W.FILETIME), ("written", W.FILETIME),
                    ("serial", W.DWORD), ("size_hi", W.DWORD),
                    ("size_lo", W.DWORD), ("links", W.DWORD),
                    ("index_hi", W.DWORD), ("index_lo", W.DWORD)]
    k = _kernel()
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID,
                              W.DWORD, W.DWORD, W.HANDLE]
    k.CreateFileW.restype = W.HANDLE
    k.GetFileInformationByHandle.argtypes = [W.HANDLE, ctypes.POINTER(Info)]
    k.GetFileInformationByHandle.restype = W.BOOL
    k.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
    k.GetFinalPathNameByHandleW.restype = W.DWORD
    h = k.CreateFileW(str(Path(path).absolute()), 0, 7, None, 3, 0x02000000 | 0x00200000, None)
    if h == ctypes.c_void_p(-1).value:
        raise LeaseError()
    try:
        info = Info()
        if not k.GetFileInformationByHandle(h, ctypes.byref(info)):
            raise LeaseError()
        if not info.attrs & 0x10 or info.attrs & 0x400:
            raise LeaseError()
        size = k.GetFinalPathNameByHandleW(h, None, 0, 0)
        if not size:
            raise LeaseError()
        buf = ctypes.create_unicode_buffer(size + 1)
        got = k.GetFinalPathNameByHandleW(h, buf, len(buf), 0)
        if not 0 < got < len(buf):
            raise LeaseError()
        return DirIdentity(info.serial, (info.index_hi << 32) | info.index_lo, buf.value)
    finally:
        k.CloseHandle(h)


def git_common_dir(worktree):
    """Read Git's administrative pointers directly, without executing Git."""
    try:
        dot = Path(worktree) / ".git"
        if dot.is_dir():
            admin = dot
        else:
            text = dot.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir: ") or "\n" in text:
                raise LeaseError()
            target = text[len("gitdir: "):]
            if not target:
                raise LeaseError()
            admin = dot.parent / target
        common = admin / "commondir"
        if common.exists():
            target = common.read_text(encoding="utf-8").strip()
            if not target or "\n" in target:
                raise LeaseError()
            admin = admin / target
        # Validate the final directory even if callers only need the path.
        dir_identity(admin)
        return admin
    except (OSError, UnicodeError) as exc:
        raise LeaseError() from exc


def resource_ids(worktree, branch):
    if (not isinstance(branch, str) or not branch or branch.startswith(("/", "-"))
            or branch.endswith(("/", ".")) or ".." in branch or "@{" in branch
            or any(c.isspace() or ord(c) < 32 or c in "~^:?*[\\" for c in branch)
            or any(not p or p.startswith(".") or p.endswith(".lock") for p in branch.split("/"))):
        raise LeaseError()
    wt = dir_identity(worktree)
    common = dir_identity(git_common_dir(worktree))
    digest = hashlib.sha256((common.final_path.lower() + "\nrefs/heads/" + branch).encode()).hexdigest()
    return f"wt-{wt.serial:x}-{wt.index:x}", "br-" + digest


class ResourceDir:
    def __init__(self, root, resource):
        if not re.fullmatch(r"wt-[0-9a-f]+-[0-9a-f]+|br-[0-9a-f]{64}", resource):
            raise LeaseError()
        self.root = Path(root)
        self.resource = resource
        self.path = self.root / resource
        self.fd = None
        self.owner_pid = None

    def lock_nb(self):
        import msvcrt
        if self.fd is not None:
            raise LeaseError()
        self.path.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path / "lock", os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600)
        try:
            # Locking beyond EOF is supported. Only the lock owner initializes it.
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return "HELD"
            raise LeaseError() from exc
        self.fd, self.owner_pid = fd, os.getpid()
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.fsync(fd)
            if os.fstat(fd).st_size != 1:
                raise LeaseError()
        except BaseException:
            self.unlock()
            raise
        return "OK"

    def unlock(self):
        import msvcrt
        if self.fd is not None:
            fd, self.fd = self.fd, None
            self.owner_pid = None
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(fd)


@dataclass
class Pair:
    wt: ResourceDir
    br: ResourceDir
    deps: Deps

    def require(self):
        if any(r.fd is None or r.owner_pid != os.getpid() for r in (self.wt, self.br)):
            raise LeaseError("REJECTED")

    def close(self):
        self.br.unlock()
        self.wt.unlock()


def acquire_pair(wt, br, deps):
    if wt.root != br.root or not wt.resource.startswith("wt-") or not br.resource.startswith("br-"):
        raise LeaseError()
    pair = Pair(wt, br, deps)
    try:
        if wt.lock_nb() == "HELD" or br.lock_nb() == "HELD":
            pair.close()
            return "HELD"
        deps.barrier.reach("LOCKED")
        return pair
    except BaseException:
        pair.close()
        raise


def _keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise LeaseError()


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise LeaseError()


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value):
        raise LeaseError()


def _proc(value):
    _keys(value, "pid ctime")
    _integer(value["pid"], 1)
    _integer(value["ctime"], 1)


def validate_state(value, kind):
    if kind == "worktree":
        _keys(value, "lifecycle gen guardian boot_id token_hash branch correlation restriction adopted_fp")
        if value["lifecycle"] not in ("FREE", "GRANTED", "RELEASED", "ORPHANED", "UNKNOWN"):
            raise LeaseError()
        _integer(value["gen"])
        _proc(value["guardian"])
        _integer(value["boot_id"])
        _sha(value["token_hash"])
        if not isinstance(value["branch"], str) or not value["branch"]:
            raise LeaseError()
        if not isinstance(value["correlation"], dict):
            raise LeaseError()
        r = value["restriction"]
        if r != "none":
            _keys(r, "kind evidence_sha gen")
            if r["kind"] != "adoption-required":
                raise LeaseError()
            _sha(r["evidence_sha"])
            _integer(r["gen"], 1)
            if r["gen"] > value["gen"]:
                raise LeaseError()
        if value["adopted_fp"] is not None:
            _sha(value["adopted_fp"])
    elif kind == "branch":
        _keys(value, "bound_to restricted_by")
        for key in ("bound_to", "restricted_by"):
            v = value[key]
            if v == {}:
                continue
            _keys(v, "wt gen phase" + (" evidence_sha" if key == "restricted_by" else ""))
            if not isinstance(v["wt"], str) or not re.fullmatch(r"wt-[0-9a-f]+-[0-9a-f]+", v["wt"]):
                raise LeaseError()
            _integer(v["gen"], 1)
            if v["phase"] not in ("PENDING", "BOUND"):
                raise LeaseError()
            if key == "restricted_by":
                _sha(v["evidence_sha"])
    elif kind == "heartbeat":
        _keys(value, "ts boot_id ledger_seq job_active obs_active members open_ops")
        if type(value["ts"]) not in (int, float):
            raise LeaseError()
        for key in ("boot_id", "ledger_seq", "job_active", "obs_active"):
            _integer(value[key])
        if not isinstance(value["members"], list) or not isinstance(value["open_ops"], list):
            raise LeaseError()
        for member in value["members"]:
            _proc(member)
        if any(not isinstance(op, str) for op in value["open_ops"]):
            raise LeaseError()
    else:
        raise LeaseError()
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LeaseError()
        result[key] = value
    return result


def _decode(text):
    try:
        return json.loads(text, object_pairs_hook=_unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(LeaseError()))
    except (ValueError, UnicodeError) as exc:
        raise LeaseError() from exc


class StateStore:
    def __init__(self, pair):
        self.pair = pair

    def read(self, path, kind):
        try:
            return validate_state(_decode(Path(path).read_text(encoding="utf-8")), kind)
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            raise LeaseError() from exc

    def write(self, path, value, kind):
        self.pair.require()
        validate_state(value, kind)
        path = Path(path)
        # Only the locked resources may be written through this store.
        if not any(path.is_relative_to(r.path) for r in (self.pair.wt, self.pair.br)):
            raise LeaseError("REJECTED")
        temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            self.pair.deps.fail.check("state-write")
            with temp.open("xb") as f:
                f.write((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
                f.flush()
                os.fsync(f.fileno())
            self.pair.deps.audit.record("replace", path)
            os.replace(temp, path)
        except OSError as exc:
            raise LeaseError("FAILED-STORAGE") from exc


def liveness(identity, process):
    try:
        _proc(identity.record())
        actual = process.creation_time(identity.pid)
        return "alive" if actual == identity.ctime else "dead"
    except (LeaseError, OSError):
        return "UNKNOWN"


def _live(record, deps):
    return liveness(ProcIdentity(**record["guardian"]), deps.process)


def _children(path, prefix="", suffix=""):
    """Unlike glob, never suppress a directory enumeration failure."""
    return [p for p in Path(path).iterdir()
            if p.name.startswith(prefix) and p.name.endswith(suffix)]


def next_gen(pair):
    pair.require()
    state = StateStore(pair).read(pair.wt.path / "state.json", "worktree")
    maximum = state["gen"] if state else 0
    for path in _children(pair.wt.path, "gen-"):
        if not re.fullmatch(r"gen-[1-9][0-9]*", path.name) or not path.is_dir():
            raise LeaseError()
        maximum = max(maximum, int(path.name[4:]))
    # Reserve by exclusive mkdir. A crash here leaves a detectable partial txn.
    gen = maximum + 1
    (pair.wt.path / f"gen-{gen}").mkdir()
    return gen


def _append(path, line, pair, exclusive=False):
    pair.require()
    try:
        pair.deps.fail.check("append")
        with Path(path).open("x" if exclusive else "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        raise LeaseError("FAILED-STORAGE") from exc


def txn_open(pair, gen, branch, run_id=None, kind="grant"):
    pair.require()
    _integer(gen, 1)
    if kind not in ("grant", "recover", "adopt") or (kind == "grant") != (run_id is None):
        raise LeaseError("REJECTED")
    directory = pair.wt.path / f"gen-{gen}"
    if not directory.is_dir():
        raise LeaseError()
    if run_id is None:
        path = directory / "txn.jsonl"
    else:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", run_id):
            raise LeaseError("REJECTED")
        (directory / "ops").mkdir(exist_ok=True)
        path = directory / "ops" / (run_id + ".jsonl")
    boot = pair.deps.boot.read()
    _integer(boot)
    _append(path, {"event": "TXN-OPEN", "wt": pair.wt.resource, "br": pair.br.resource,
                   "branch": branch, "gen": gen, "run_id": run_id, "kind": kind,
                   "owner": pair.deps.process.current().record(), "boot_id": boot}, pair, True)
    return path


def _journal(path):
    try:
        text = path.read_text(encoding="utf-8")
        if not text.endswith("\n"):
            raise LeaseError()
        rows = [_decode(line) for line in text.splitlines()]
        if not rows:
            raise LeaseError()
        first = rows[0]
        _keys(first, "event wt br branch gen run_id kind owner boot_id")
        if first["event"] != "TXN-OPEN" or first["kind"] not in ("grant", "recover", "adopt"):
            raise LeaseError()
        _proc(first["owner"])
        _integer(first["gen"], 1)
        _integer(first["boot_id"])
        if not isinstance(first["branch"], str) or not first["branch"]:
            raise LeaseError()
        if not re.fullmatch(r"wt-[0-9a-f]+-[0-9a-f]+", first["wt"]) or not re.fullmatch(r"br-[0-9a-f]{64}", first["br"]):
            raise LeaseError()
        if len(rows) > 2:
            raise LeaseError()  # M2 extends this to observation/ledger records.
        terminal = None
        if len(rows) == 2:
            _keys(rows[1], "event evidence")
            terminal = rows[1]["event"]
            expected = {"grant": "GRANT-PUBLISHED", "recover": "RECOVERED", "adopt": "ADOPTED"}[first["kind"]]
            if terminal not in (expected, "TXN-ABANDONED-DRAINED") or not isinstance(rows[1]["evidence"], dict) or not rows[1]["evidence"]:
                raise LeaseError()
        return first, terminal
    except (OSError, TypeError, UnicodeError) as exc:
        raise LeaseError() from exc


def txn_abandoned_drained(pair, path, evidence):
    """Caller supplies positive cessation evidence; not a recovery shortcut."""
    pair.require()
    if not Path(path).resolve().is_relative_to(pair.wt.path.resolve()):
        raise LeaseError("REJECTED")
    first, terminal = _journal(Path(path))
    if terminal or first["wt"] != pair.wt.resource or first["br"] != pair.br.resource:
        raise LeaseError("REJECTED")
    if not isinstance(evidence, dict) or evidence.get("drained") is not True:
        raise LeaseError("OPERATOR-BLOCKED")
    _append(path, {"event": "TXN-ABANDONED-DRAINED", "evidence": evidence}, pair)


def pending_transactions(pair, branch):
    """Inspect every generation, including authorities not yet published."""
    pair.require()
    outcome = None
    for wt in _children(pair.wt.root, "wt-"):
        for directory in _children(wt, "gen-"):
            if not directory.is_dir() or not re.fullmatch(r"gen-[1-9][0-9]*", directory.name):
                return "UNKNOWN"
            journal = directory / "txn.jsonl"
            if not journal.is_file():
                return "UNKNOWN"  # Could have claimed this branch before crashing.
            ops = directory / "ops"
            operations = _children(ops, suffix=".jsonl") if ops.exists() else []
            for path in [journal, *sorted(operations)]:
                try:
                    first, terminal = _journal(path)
                    if first["wt"] != wt.name or first["gen"] != int(directory.name[4:]):
                        return "UNKNOWN"
                    if path == journal and (first["kind"] != "grant" or first["run_id"] is not None):
                        return "UNKNOWN"
                    if path != journal and (first["kind"] == "grant" or first["run_id"] != path.stem):
                        return "UNKNOWN"
                except LeaseError:
                    return "UNKNOWN"
                if terminal == "GRANT-PUBLISHED" and StateStore(pair).read(wt / "state.json", "worktree") is None:
                    return "UNKNOWN"
                if terminal or (wt != pair.wt.path and first["br"] != pair.br.resource):
                    continue
                if first["br"] == pair.br.resource and first["branch"] != branch:
                    return "UNKNOWN"
                live = liveness(ProcIdentity(**first["owner"]), pair.deps.process)
                if live == "UNKNOWN":
                    return "UNKNOWN"
                if live == "alive":
                    outcome = outcome or "HELD"
                else:
                    outcome = "OPERATOR-BLOCKED"
    return outcome


@dataclass(frozen=True)
class Claims:
    result: str | None = None
    restricted: bool = False


def scan_branch_claims(pair, branch):
    pair.require()
    store = StateStore(pair)
    restricted = False
    outcome = None
    for wt in _children(pair.wt.root, "wt-"):
        try:
            state = store.read(wt / "state.json", "worktree")
            if state is None:
                continue  # Unpublished generations are handled by pending_transactions.
            if state["branch"] != branch and wt != pair.wt.path:
                continue
            heartbeat = wt / f"gen-{state['gen']}" / "heartbeat.json"
            store.read(heartbeat, "heartbeat")  # If present, malformed is not health evidence.
            restricted |= state["restriction"] != "none"
            if state["lifecycle"] == "UNKNOWN":
                return Claims("UNKNOWN", restricted)
            if state["lifecycle"] == "ORPHANED":
                outcome = "ORPHANED"
            elif state["lifecycle"] == "GRANTED":
                live = _live(state, pair.deps)
                if live == "UNKNOWN":
                    return Claims("UNKNOWN", restricted)
                candidate = "HELD" if live == "alive" else "ORPHANED"
                outcome = "ORPHANED" if "ORPHANED" in (candidate, outcome) else "HELD"
        except LeaseError:
            return Claims("UNKNOWN", restricted)
    return Claims(outcome, restricted)


@dataclass(frozen=True)
class Decision:
    result: str
    mode: str | None = None


def decide_acquire(state, live, claims=Claims(), pending=None, mirror_restricted=False, current_fp=None):
    """Pure policy. GRANTED/ORPHANED never reach a free-resource decision."""
    if claims.result == "UNKNOWN" or pending == "UNKNOWN" or live == "UNKNOWN":
        return Decision("UNKNOWN")
    if state and state["lifecycle"] == "UNKNOWN":
        return Decision("UNKNOWN")
    if state and state["lifecycle"] == "GRANTED":
        return Decision("HELD" if live == "alive" else "ORPHANED")
    if state and state["lifecycle"] == "ORPHANED":
        return Decision("ORPHANED")
    if claims.result:
        return Decision(claims.result)
    if pending:
        return Decision(pending)
    restricted = mirror_restricted or claims.restricted or (state and state["restriction"] != "none")
    # Fingerprint-dependent adoption checks are M3/M4. Never authorize an
    # unchecked first writable acquire after adoption in this incomplete core.
    if state and state["adopted_fp"] is not None and current_fp != state["adopted_fp"]:
        return Decision("UNKNOWN")
    return Decision("GRANTED", "read-only" if restricted else "write")


def decide_recover_precheck(state):
    if not state or state["lifecycle"] != "ORPHANED":
        return "UNKNOWN" if state and state["lifecycle"] == "UNKNOWN" else "REJECTED"
    return "OK"  # Eligibility only, never cessation or recovery authorization.


def decide_adopt_precheck(state, evidence_sha, evidence_gen, current_fp, evidence_fp):
    if not state or state["lifecycle"] not in ("FREE", "RELEASED"):
        return "HELD" if state and state["lifecycle"] == "GRANTED" else "REJECTED"
    r = state["restriction"]
    if r == "none" or r["evidence_sha"] != evidence_sha or r["gen"] != evidence_gen or current_fp != evidence_fp:
        return "REJECTED"
    return "OK"


def inspect_acquire(pair, branch, current_fp=None):
    """Reconcile authority under both locks; never publish a grant here.

    M1 fixtures supply synthetic fingerprints to test pure adoption decisions;
    production callers must obtain them from M3 observation.
    """
    pair.require()
    store = StateStore(pair)
    try:
        state = store.read(pair.wt.path / "state.json", "worktree")
        mirror = store.read(pair.br.path / "state.json", "branch")
        claims = scan_branch_claims(pair, branch)
        pending = pending_transactions(pair, branch)
        restricted = False
        stale = []
        if mirror:
            for key in ("bound_to", "restricted_by"):
                ref = mirror[key]
                if not ref:
                    continue
                authority = store.read(pair.wt.root / ref["wt"] / "state.json", "worktree")
                if key == "bound_to" and (authority is None or authority["gen"] < ref["gen"]):
                    journal = pair.wt.root / ref["wt"] / f"gen-{ref['gen']}" / "txn.jsonl"
                    first, terminal = _journal(journal)
                    if (terminal == "TXN-ABANDONED-DRAINED" and first["wt"] == ref["wt"]
                            and first["br"] == pair.br.resource and first["gen"] == ref["gen"]):
                        stale.append(key)
                        continue
                if authority is None:
                    if pending is None:
                        return Decision("UNKNOWN")
                    continue
                if authority["gen"] < ref["gen"]:
                    return Decision("UNKNOWN")
                if key == "restricted_by":
                    restricted |= authority["restriction"] != "none"
                    if authority["restriction"] == "none":
                        stale.append(key)
                elif authority["lifecycle"] in ("FREE", "RELEASED"):
                    stale.append(key)
        live = _live(state, pair.deps) if state and state["lifecycle"] == "GRANTED" else None
        decision = decide_acquire(state, live, claims, pending, restricted, current_fp)
        if decision.result == "ORPHANED" and state and state["lifecycle"] == "GRANTED" and live == "dead":
            state["lifecycle"] = "ORPHANED"
            store.write(pair.wt.path / "state.json", state, "worktree")
        if decision.result == "GRANTED" and pending is None and mirror and stale:
            for key in stale:
                mirror[key] = {}
            store.write(pair.br.path / "state.json", mirror, "branch")
        return decision
    except (LeaseError, OSError):
        return Decision("UNKNOWN")


class Publication:
    """Bound low-level writers; no CLI or drain/fingerprint implementation here."""
    def __init__(self, pair, gen, token_hash):
        self.pair, self.gen, self.token_hash = pair, gen, token_hash
        self.store = StateStore(pair)

    def state(self):
        self.pair.require()
        return self.store.read(self.pair.wt.path / "state.json", "worktree")

    def bound(self, lifecycle):
        state = self.state()
        if not state or state["gen"] != self.gen or state["token_hash"] != self.token_hash or state["lifecycle"] not in lifecycle:
            raise LeaseError("REJECTED")
        return state

    def branch(self):
        return self.store.read(self.pair.br.path / "state.json", "branch") or {"bound_to": {}, "restricted_by": {}}

    def wt_write(self, state, hook):
        self.store.write(self.pair.wt.path / "state.json", state, "worktree")
        self.pair.deps.barrier.reach(hook)

    def br_write(self, state, hook):
        self.store.write(self.pair.br.path / "state.json", state, "branch")
        self.pair.deps.barrier.reach(hook)


def publish_A1(pub):
    pub.pair.require()
    state = pub.state()
    if state and (state["gen"] >= pub.gen or state["lifecycle"] not in ("FREE", "RELEASED")):
        raise LeaseError("REJECTED")
    first, terminal = _journal(pub.pair.wt.path / f"gen-{pub.gen}" / "txn.jsonl")
    if terminal or first["wt"] != pub.pair.wt.resource or first["br"] != pub.pair.br.resource:
        raise LeaseError("REJECTED")
    branch = pub.branch()
    branch["bound_to"] = {"wt": pub.pair.wt.resource, "gen": pub.gen, "phase": "PENDING"}
    pub.br_write(branch, "A1")


def publish_A2(pub, state):
    pub.pair.require()
    prior = pub.state()
    pointer = pub.branch()["bound_to"]
    if pointer != {"wt": pub.pair.wt.resource, "gen": pub.gen, "phase": "PENDING"}:
        raise LeaseError("REJECTED")
    if state["gen"] != pub.gen or state["token_hash"] != pub.token_hash or state["lifecycle"] != "GRANTED":
        raise LeaseError("REJECTED")
    if prior and (prior["gen"] >= pub.gen or prior["restriction"] != state["restriction"]):
        raise LeaseError("REJECTED")
    pub.wt_write(state, "A2")


def publish_A3(pub):
    pub.bound(("GRANTED",))
    branch = pub.branch()
    if branch["bound_to"] != {"wt": pub.pair.wt.resource, "gen": pub.gen, "phase": "PENDING"}:
        raise LeaseError("REJECTED")
    branch["bound_to"]["phase"] = "BOUND"
    pub.br_write(branch, "A3")
    _append(pub.pair.wt.path / f"gen-{pub.gen}" / "txn.jsonl",
            {"event": "GRANT-PUBLISHED", "evidence": {"gen": pub.gen}}, pub.pair)


def publish_R1(pub):
    state = pub.bound(("GRANTED",))
    state["lifecycle"] = "RELEASED"
    pub.wt_write(state, "R1")


def publish_R2(pub):
    pub.bound(("RELEASED",))
    branch = pub.branch()
    pointer = branch["bound_to"]
    if pointer and (pointer["wt"] != pub.pair.wt.resource or pointer["gen"] != pub.gen):
        raise LeaseError("REJECTED")
    branch["bound_to"] = {}
    pub.br_write(branch, "R2")


def restrict_RC1(pub, evidence_sha):
    pub.bound(("ORPHANED",))
    _sha(evidence_sha)
    branch = pub.branch()
    branch["restricted_by"] = {"wt": pub.pair.wt.resource, "gen": pub.gen,
                               "evidence_sha": evidence_sha, "phase": "PENDING"}
    pub.br_write(branch, "RC1")


def restrict_RC2(pub, evidence_sha):
    state = pub.bound(("ORPHANED",))
    expected = {"wt": pub.pair.wt.resource, "gen": pub.gen,
                "evidence_sha": evidence_sha, "phase": "PENDING"}
    if pub.branch()["restricted_by"] != expected:
        raise LeaseError("REJECTED")
    state["lifecycle"] = "RELEASED"
    state["restriction"] = {"kind": "adoption-required", "evidence_sha": evidence_sha, "gen": pub.gen}
    pub.wt_write(state, "RC2")


def restrict_RC3(pub):
    state = pub.bound(("RELEASED",))
    branch = pub.branch()
    if state["restriction"] == "none" or branch["restricted_by"] != {
            "wt": pub.pair.wt.resource, "gen": pub.gen,
            "evidence_sha": state["restriction"]["evidence_sha"], "phase": "PENDING"}:
        raise LeaseError("REJECTED")
    branch["restricted_by"]["phase"] = "BOUND"
    pub.br_write(branch, "RC3")


def adopt_AD1(pub, evidence_sha, evidence_gen, current_fp, evidence_fp):
    state = pub.bound(("FREE", "RELEASED"))
    if decide_adopt_precheck(state, evidence_sha, evidence_gen, current_fp, evidence_fp) != "OK":
        raise LeaseError("REJECTED")
    _sha(current_fp)
    state["restriction"], state["adopted_fp"] = "none", current_fp
    pub.wt_write(state, "AD1")


def adopt_AD2(pub):
    state = pub.bound(("FREE", "RELEASED"))
    if state["restriction"] != "none" or state["adopted_fp"] is None:
        raise LeaseError("REJECTED")
    branch = pub.branch()
    ref = branch["restricted_by"]
    if ref and (ref["wt"] != pub.pair.wt.resource or ref["gen"] > pub.gen):
        raise LeaseError("REJECTED")
    branch["restricted_by"] = {}
    pub.br_write(branch, "AD2")


# M2a / M2b interface: Ledger owns persistence, Job owns only its job handle,
# ProcApi owns original Child handles, and Executor owns a per-instance op table.
# The caller supplies the shared phase mutex. Barriers run OUTSIDE that mutex.
# Admission captures BootSource once per op; every subsequent line binds to it.
# No library operation below publishes authority or enables the public CLI.
LEDGER_PHASES = frozenset((
    "ADMITTED LAUNCH-ATTEMPTED LAUNCH-CANCELLED LAUNCHED-SUSPENDED LAUNCH-FAILED "
    "ASSIGNED ASSIGN-FAILED RESUME-ATTEMPTED RESUMED RESUME-UNCERTAIN EXITED "
    "DRAINED TERMINATED-UNRUN CANCELLED NOT-ADMITTED NATIVE-OPEN NATIVE-CLOSED OBSERVE"
).split())
TERMINAL_PHASES = frozenset((
    "LAUNCH-CANCELLED LAUNCH-FAILED DRAINED TERMINATED-UNRUN CANCELLED "
    "NOT-ADMITTED NATIVE-CLOSED"
).split())


class Ledger:
    """One exclusive writer; open_existing is classification-only, never resume.

    Caller serializes appends with its phase mutex. A failed write/fsync poisons
    this writer permanently, even if the bytes happen to look complete later.
    """
    def __init__(self, path, deps, stream=None):
        self.path, self.deps, self._stream = Path(path), deps, stream
        self.failed = False
        self._seq = 0
        self._phases = {}
        self._boots = {}
        self._kinds = {}
        self._writer = stream is not None

    @classmethod
    def create(cls, gen_dir, deps):
        path = Path(gen_dir) / "ledger.jsonl"
        try:
            stream = path.open("xb")
        except OSError as exc:
            raise LeaseError("FAILED-STORAGE") from exc
        return cls(path, deps, stream)

    @classmethod
    def open_existing(cls, path, deps):
        ledger = cls(path, deps)
        ledger.entries()  # Fail closed immediately, including a missing file.
        return ledger

    @staticmethod
    def validate(rows):
        seq, bindings = 0, {}
        for row in rows:
            if not isinstance(row, dict) or not {"seq", "op_id", "kind", "phase", "boot_id"} <= row.keys():
                raise LeaseError()
            _integer(row["seq"], 1)
            if row["seq"] != seq + 1:
                raise LeaseError()
            seq = row["seq"]
            if (not isinstance(row["op_id"], str) or not row["op_id"]
                    or row["kind"] not in ("exec", "observe", "native")
                    or row["phase"] not in LEDGER_PHASES):
                raise LeaseError()
            _integer(row["boot_id"])
            if row["boot_id"] > 0xffffffff:
                raise LeaseError()
            binding = (row["kind"], row["boot_id"])
            if bindings.setdefault(row["op_id"], binding) != binding:
                raise LeaseError()
        return rows

    def entries(self):
        try:
            data = self.path.read_bytes()
            if data and not data.endswith(b"\n"):
                raise LeaseError()
            return self.validate([_decode(line.decode("utf-8")) for line in data.splitlines()])
        except (OSError, UnicodeError, TypeError) as exc:
            raise LeaseError() from exc

    def append(self, op_id, kind, phase, **fields):
        if self.failed or self._stream is None:
            raise LeaseError("FAILED-STORAGE")
        # An unreadable admission boot is UNKNOWN, not an admitted operation.
        boot = self._boots.get(op_id)
        if boot is None:
            try:
                boot = self.deps.boot.read()
                _integer(boot)
                if boot > 0xffffffff:
                    raise LeaseError()
            except Exception as exc:
                raise LeaseError() from exc
        try:
            if {"seq", "op_id", "kind", "phase", "boot_id"} & fields.keys():
                raise LeaseError()
            row = dict(fields, seq=self._seq + 1, op_id=op_id, kind=kind,
                       phase=phase, boot_id=boot)
            self.validate([dict(row, seq=1)])
            if op_id in self._kinds and self._kinds[op_id] != kind:
                raise LeaseError()
            self.deps.fail.check("append:" + phase)
            data = (json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            if self._stream.write(data) != len(data):
                raise OSError("short ledger write")
            self._stream.flush()
            os.fsync(self._stream.fileno())
        except Exception as exc:
            self.failed = True
            raise LeaseError("FAILED-STORAGE") from exc
        self._seq = row["seq"]
        self._boots[op_id] = boot
        self._kinds[op_id] = kind
        self._phases[op_id] = phase
        return self._seq

    def phase(self, op_id):
        if self._writer:
            return self._phases.get(op_id)
        return next((r["phase"] for r in reversed(self.entries()) if r["op_id"] == op_id), None)

    def close(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None


class _JobBasicLimit(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", W.DWORD), ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t), ("active_limit", W.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", W.DWORD),
                ("scheduling", W.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _JobExtendedLimit(ctypes.Structure):
    _fields_ = [("basic", _JobBasicLimit), ("io", _IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _JobAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in
                ("user", "kernel", "period_user", "period_kernel")] + [
                (name, W.DWORD) for name in ("faults", "total", "active", "terminated")]


class Job:
    """Unnamed containment, never kill-on-close or breakaway. Close is not drain."""
    def __init__(self, handle, deps):
        self.handle, self.deps = handle, deps

    @classmethod
    def create(cls, deps):
        k = _kernel()
        k.CreateJobObjectW.argtypes = [W.LPVOID, W.LPCWSTR]
        k.CreateJobObjectW.restype = W.HANDLE
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            raise LeaseError()
        job = cls(handle, deps)
        try:
            job.flags()
            return job
        except BaseException:
            job.close()
            raise

    def _query(self, kind, value):
        if self.handle is None:
            raise LeaseError()
        k = _kernel()
        k.QueryInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, W.LPVOID,
                                                W.DWORD, ctypes.POINTER(W.DWORD)]
        k.QueryInformationJobObject.restype = W.BOOL
        if not k.QueryInformationJobObject(self.handle, kind, ctypes.byref(value), ctypes.sizeof(value), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def flags(self):
        flags = self._query(9, _JobExtendedLimit()).basic.flags
        if flags & (0x800 | 0x1000 | 0x2000):
            raise LeaseError()  # BREAKAWAY_OK, SILENT_BREAKAWAY_OK, KILL_ON_JOB_CLOSE
        return flags

    def active(self):
        return self._query(1, _JobAccounting()).active

    def total(self):
        # Cumulative admission count also catches descendants that exit between
        # polls. Observation owns a distinct, serialized job.
        return self._query(1, _JobAccounting()).total

    def members(self):
        capacity = 16
        while capacity <= 1048576:
            class Pids(ctypes.Structure):
                _fields_ = [("assigned", W.DWORD), ("count", W.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]
            try:
                data = self._query(3, Pids())
            except OSError as exc:
                if exc.winerror != 234:  # ERROR_MORE_DATA
                    raise LeaseError() from exc
                capacity *= 2
                continue
            result = []
            for pid in data.pids[:data.count]:
                ctime = self.deps.process.creation_time(pid)
                if ctime is None:
                    raise LeaseError()  # Snapshot raced exit: never guess an identity.
                result.append(ProcIdentity(pid, ctime))
            return result
        raise LeaseError()

    def contains(self, child):
        if self.handle is None or child.closed:
            raise LeaseError()
        k = _kernel()
        k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
        k.IsProcessInJob.restype = W.BOOL
        inside = W.BOOL()
        if not k.IsProcessInJob(child.process_handle, self.handle, ctypes.byref(inside)):
            raise LeaseError()
        return bool(inside.value)

    def close(self):
        if self.handle is not None:
            if not _kernel().CloseHandle(self.handle):
                raise LeaseError()
            self.handle = None


@dataclass(eq=False)
class Child:
    process_handle: int
    thread_handle: int
    pid: int
    ctime: int
    closed: bool = False


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR),
                ("title", W.LPWSTR)] + [(name, W.DWORD) for name in
                ("x", "y", "xsize", "ysize", "xchars", "ychars", "fill", "flags")] + [
                ("show", W.WORD), ("reserved_size", W.WORD), ("reserved_bytes", W.LPVOID),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", W.DWORD), ("tid", W.DWORD)]


class ProcApi(ProcessApi):
    """Creation-handle ownership is local to this API AND the executor record.

    The original handle tuple is retained independently of mutable Child fields.
    A PID, reconstructed Child, foreign executor, or resumed child is never D9.
    """
    def __init__(self):
        self._children = {}
        self._resume_calls = set()
        self._owner_pid = os.getpid()

    def _owned(self, child):
        if (not isinstance(child, Child) or child.closed or self._owner_pid != os.getpid()
                or self._children.get(id(child)) != (child, child.process_handle,
                                                    child.thread_handle, child.pid, child.ctime)):
            raise LeaseError("REJECTED")

    def create_suspended(self, argv, cwd, env, *, stdio=None):
        if (not isinstance(argv, (list, tuple)) or not argv
                or any(not isinstance(a, str) or "\0" in a for a in argv)
                or not Path(argv[0]).is_absolute() or not Path(cwd).is_absolute()
                or not isinstance(env, dict)
                or any(not isinstance(k, str) or not k or "=" in k or "\0" in k
                       or not isinstance(v, str) or "\0" in v for k, v in env.items())):
            raise LeaseError("REJECTED")
        k = _kernel()
        k.CreateProcessW.argtypes = [W.LPCWSTR, W.LPWSTR, W.LPVOID, W.LPVOID, W.BOOL,
                                     W.DWORD, W.LPVOID, W.LPCWSTR,
                                     ctypes.POINTER(_StartupInfo), ctypes.POINTER(_ProcessInfo)]
        k.CreateProcessW.restype = W.BOOL
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        environment = ctypes.create_unicode_buffer("\0".join(k + "=" + v for k, v in
                                                   sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
        startup, info = _StartupInfo(), _ProcessInfo()
        startup.cb = ctypes.sizeof(startup)
        if stdio is None:
            if not k.CreateProcessW(argv[0], command, None, None, False, 0x4 | 0x400,
                                    environment, str(cwd), ctypes.byref(startup), ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
        else:
            # CPython constructs STARTUPINFOEX with this explicit handle list.
            # Only our three scratch streams cross the boundary, not locks/jobs.
            import _winapi
            import msvcrt
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in stdio]
            if len(handles) != 3 or len(set(handles)) != 3:
                raise LeaseError()
            si = subprocess.STARTUPINFO()
            si.dwFlags = subprocess.STARTF_USESTDHANDLES
            si.hStdInput, si.hStdOutput, si.hStdError = handles
            si.lpAttributeList = {"handle_list": handles}
            try:
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                ph, th, pid, tid = _winapi.CreateProcess(
                    argv[0], subprocess.list2cmdline(argv), None, None, True,
                    0x4 | 0x400, env, str(cwd), si)
                info = _ProcessInfo(ph, th, pid, tid)
            finally:
                for handle in handles:
                    os.set_handle_inheritable(handle, False)
        times = [W.FILETIME() for _ in range(4)]
        k.GetProcessTimes.argtypes = [W.HANDLE] + [ctypes.POINTER(W.FILETIME)] * 4
        k.GetProcessTimes.restype = W.BOOL
        child = Child(info.process, info.thread, info.pid, 0)
        # Preserve handles even on a metadata failure; caller must not infer that
        # CreateProcess failed after it returned a real child.
        self._children[id(child)] = (child, child.process_handle, child.thread_handle, child.pid, 0)
        if k.GetProcessTimes(info.process, *(ctypes.byref(t) for t in times)):
            child.ctime = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            self._children[id(child)] = (child, child.process_handle, child.thread_handle, child.pid, child.ctime)
        return child

    def assign(self, job, child):
        self._owned(child)
        k = _kernel()
        k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        k.AssignProcessToJobObject.restype = W.BOOL
        if not k.AssignProcessToJobObject(job.handle, child.process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, child):
        self._owned(child)
        self._resume_calls.add(id(child))
        k = _kernel()
        k.ResumeThread.argtypes = [W.HANDLE]
        k.ResumeThread.restype = W.DWORD
        return k.ResumeThread(child.thread_handle)

    def wait(self, child, timeout_ms):
        self._owned(child)
        k = _kernel()
        k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        k.WaitForSingleObject.restype = W.DWORD
        value = k.WaitForSingleObject(child.process_handle, timeout_ms)
        if value == 258:
            return None
        if value != 0:
            raise LeaseError()
        code = W.DWORD()
        k.GetExitCodeProcess.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
        k.GetExitCodeProcess.restype = W.BOOL
        if not k.GetExitCodeProcess(child.process_handle, ctypes.byref(code)):
            raise LeaseError()
        return code.value

    def terminate_unrun(self, child, proof):
        """D9 only. Called with the same live executor's mutex held and record.

        Persistence failure before RESUME-ATTEMPTED is a permitted D9 trigger;
        recovered bytes never constitute this proof. Confirmation uses the SAME
        process handle, never PID lookup, and precedes the terminal ledger line.
        """
        self._owned(child)
        if (not isinstance(proof, _ExecRecord) or proof.child is not child
                or proof.executor.deps.process is not self
                or proof.executor._ops.get(proof.op_id) is not proof
                or proof.executor.owner_pid != os.getpid()
                or not proof.executor.phase_mutex.locked()
                or "LAUNCHED-SUSPENDED" not in proof.phases
                or "RESUME-ATTEMPTED" in proof.phases
                or id(child) in self._resume_calls
                or proof.cleanup_reason not in ("ASSIGN-FAILED", "cancel", "resume-storage")):
            raise LeaseError("REJECTED")
        k = _kernel()
        k.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        k.TerminateProcess.restype = W.BOOL
        if not k.TerminateProcess(child.process_handle, 1):
            raise LeaseError()
        if self.wait(child, 30000) is None:
            raise LeaseError()

    def close_child(self, child):
        self._owned(child)
        k = _kernel()
        if not k.CloseHandle(child.thread_handle) or not k.CloseHandle(child.process_handle):
            raise LeaseError()
        child.closed = True
        self._children.pop(id(child))
        self._resume_calls.discard(id(child))


@dataclass(frozen=True)
class Outcome:
    result: str
    phase: str | None = None
    exit_code: int | None = None


@dataclass(eq=False)
class _ExecRecord:
    executor: Executor
    op_id: str
    argv: tuple
    cwd: str
    env: dict
    phase: str = "ADMITTED"
    phases: list = field(default_factory=lambda: ["ADMITTED"])
    child: Child | None = None
    cancelled: bool = False
    cleanup_reason: str | None = None
    exit_code: int | None = None
    error: str | None = None
    started: bool = False
    stdio: tuple | None = None
    observation_unknown: bool = False


class Executor:
    """Shared exec/observation launch mechanics, not a guardian or authority.

    run is the worker-thread entry and waits for positive job drain. status and
    cancel never run the target, and barriers never hold the caller's mutex.
    Handles remain available for evidence until close_child, even after D9.
    M2b owns checkpoint/observation-before-parent-close and release integration.
    """
    def __init__(self, ledger, job, deps, phase_mutex):
        self.ledger, self.job, self.deps = ledger, job, deps
        self.phase_mutex = phase_mutex
        self.owner_pid = os.getpid()
        self._ops = {}
        self._runner = threading.Lock()

    def _write(self, record, phase, **fields):
        self.ledger.append(record.op_id, "exec", phase, **fields)
        record.phase = phase
        record.phases.append(phase)

    def _outcome(self, record):
        if self.ledger.failed or record.error:
            return Outcome("UNKNOWN", record.phase, record.exit_code)
        if record.phase in ("CANCELLED", "LAUNCH-CANCELLED", "TERMINATED-UNRUN"):
            return Outcome("CANCELLED", record.phase)
        if record.phase in TERMINAL_PHASES:
            return Outcome("DONE", record.phase, record.exit_code)
        return Outcome("RUNNING", record.phase, record.exit_code)

    def _open(self, record):
        return bool(record.error) or record.phase not in TERMINAL_PHASES

    def admit(self, op_id, argv, cwd, env):
        with self.phase_mutex:
            if self.ledger.failed:
                raise LeaseError("FAILED-STORAGE")
            if (not isinstance(op_id, str) or not op_id or not isinstance(argv, (list, tuple))
                    or not argv or any(not isinstance(a, str) or "\0" in a for a in argv)
                    or not isinstance(env, dict)):
                raise LeaseError("REJECTED")
            if op_id in self._ops:
                old = self._ops[op_id]
                if (old.argv, old.cwd, old.env) != (tuple(argv), str(cwd), env):
                    raise LeaseError("REJECTED")
                return "ADMITTED"  # Creation replay only; run never executes twice.
            if any(self._open(r) for r in self._ops.values()):
                raise LeaseError("BATCH-OPEN")
            record = _ExecRecord(self, op_id, tuple(argv), str(cwd), dict(env))
            self.ledger.append(op_id, "exec", "ADMITTED", argv=list(argv), cwd=str(cwd))
            self._ops[op_id] = record
        self.deps.barrier.reach("ADMITTED")
        return "ADMITTED"

    def status(self, op_id):
        with self.phase_mutex:
            record = self._ops.get(op_id)
            return self._outcome(record) if record else Outcome("REJECTED")

    def open_ops(self):
        with self.phase_mutex:
            return [op_id for op_id, record in self._ops.items() if self._open(record)]

    def _cleanup(self, record, reason):
        record.cleanup_reason = reason
        self.deps.process.terminate_unrun(record.child, record)
        self._write(record, "TERMINATED-UNRUN")
        self._write(record, "CANCELLED")

    def cancel(self, op_id):
        with self.phase_mutex:
            record = self._ops.get(op_id)
            if record is None:
                return Outcome("REJECTED")  # IPC tombstones are M2b.
            if not self._open(record):
                return self._outcome(record)
            if "RESUME-ATTEMPTED" in record.phases:
                return Outcome("RUNNING", record.phase, record.exit_code)
            record.cancelled = True
            try:
                if record.child is not None:
                    self._cleanup(record, "cancel")
                elif record.phase == "LAUNCH-ATTEMPTED":
                    self._write(record, "LAUNCH-CANCELLED")
                    self._write(record, "CANCELLED")
                else:
                    self._write(record, "CANCELLED")
            except (LeaseError, OSError) as exc:
                record.error = str(exc)
            return self._outcome(record)

    def run(self, op_id):
        if not self._runner.acquire(blocking=False):
            return self.status(op_id)
        try:
            with self.phase_mutex:
                record = self._ops.get(op_id)
                if record is None:
                    return Outcome("REJECTED")
                if record.started or not self._open(record):
                    return self._outcome(record)
                record.started = True
            try:
                return self._run(record)
            except (LeaseError, OSError) as exc:
                with self.phase_mutex:
                    record.error = str(exc)
                    return Outcome("FAILED-STORAGE" if self.ledger.failed else "UNKNOWN", record.phase)
        finally:
            self._runner.release()

    def _run(self, record):
        with self.phase_mutex:
            if record.cancelled:
                return self._outcome(record)
            self._write(record, "LAUNCH-ATTEMPTED")
        self.deps.barrier.reach("LAUNCH-ATTEMPTED")
        with self.phase_mutex:
            # This is the last check before CreateProcess, serialized with cancel.
            if record.cancelled:
                return self._outcome(record)
            try:
                if record.stdio is None:
                    record.child = self.deps.process.create_suspended(record.argv, record.cwd, record.env)
                else:
                    record.child = self.deps.process.create_suspended(
                        record.argv, record.cwd, record.env, stdio=record.stdio)
            except (LeaseError, OSError):
                self._write(record, "LAUNCH-FAILED")
            else:
                self._write(record, "LAUNCHED-SUSPENDED", pid=record.child.pid, ctime=record.child.ctime)
        self.deps.barrier.reach(record.phase)
        with self.phase_mutex:
            if record.cancelled or record.error or not self._open(record):
                return self._outcome(record)
            child = record.child
            if child is None or child.ctime <= 0:
                raise LeaseError()  # Preserve an uncertain created child, no resume.
            try:
                self.deps.fail.check("assign")
                self.deps.process.assign(self.job, child)
                if not self.job.contains(child):
                    raise LeaseError()
            except (LeaseError, OSError):
                try:
                    self._write(record, "ASSIGN-FAILED")
                finally:
                    self._cleanup(record, "ASSIGN-FAILED")
                return self._outcome(record)
            self._write(record, "ASSIGNED")
        self.deps.barrier.reach("ASSIGNED")
        with self.phase_mutex:
            if record.cancelled or record.error or not self._open(record):
                return self._outcome(record)
            try:
                self._write(record, "RESUME-ATTEMPTED")
            except LeaseError:
                # The ledger latch prevents terminal persistence too; D9 confirms
                # cessation but the op remains open/UNKNOWN for later recovery.
                self._cleanup(record, "resume-storage")
                raise
        self.deps.barrier.reach("RESUME-ATTEMPTED")
        try:
            self.deps.fail.check("resume")
            resumed = self.deps.process.resume(child)
        except (LeaseError, OSError):
            resumed = 0xffffffff
        if resumed == 1:
            self.deps.barrier.reach("after-os-resume")
        with self.phase_mutex:
            self._write(record, "RESUMED" if resumed == 1 else "RESUME-UNCERTAIN")
        self.deps.barrier.reach(record.phase)
        return self._wait_and_drain(record)

    def _wait_and_drain(self, record):
        child = record.child
        code = None
        while code is None:
            code = self.deps.process.wait(child, 50)
        with self.phase_mutex:
            self._write(record, "EXITED", code=code)
            record.exit_code = code
        self.deps.barrier.reach("EXITED")
        while self.job.active() != 0:
            time.sleep(0.01)
        with self.phase_mutex:
            self._write(record, "DRAINED")
        self.deps.barrier.reach("DRAINED")
        return self.status(record.op_id)


def classify_recovered(entries, current_executor=None):
    """Read-only conservative classification, never a D9 or drain procedure.

    current_executor cannot turn persisted bytes into live ownership proof.
    Even with that optional context, only terminal ledger entries classify DONE.
    Boot-boundary validation and guardian recovery remain M4 responsibilities.
    """
    try:
        rows = entries.entries() if isinstance(entries, Ledger) else Ledger.validate(entries)
        phases = {row["op_id"]: row["phase"] for row in rows}
        return "OPERATOR-BLOCKED" if any(p not in TERMINAL_PHASES for p in phases.values()) else "DONE"
    except (LeaseError, OSError, TypeError, ValueError):
        return "UNKNOWN"


@dataclass(frozen=True)
class ObsResult:
    exit_code: int
    stdout: bytes
    obs_id: str


class _ObservationExecutor(Executor):
    """Internal admission, with the SAME M2a launch/D9 state machine."""
    _baseline: int = 0

    def _write(self, record, phase, **fields):
        self.ledger.append(record.op_id, "observe", phase, **fields)
        record.phase = phase
        record.phases.append(phase)

    def _outcome(self, record):
        if record.observation_unknown:
            return Outcome("UNKNOWN", record.phase, record.exit_code)
        return super()._outcome(record)

    def _wait_and_drain(self, record):
        child = record.child
        code = None
        while code is None:
            code = self.deps.process.wait(child, 50)
            with self.phase_mutex:
                if self.job.total() != self._baseline + 1:
                    record.observation_unknown = True
        with self.phase_mutex:
            self._write(record, "EXITED", code=code)
            record.exit_code = code
            if self.job.active() != 0:
                record.observation_unknown = True
        self.deps.barrier.reach("EXITED")
        while self.job.active() != 0:
            time.sleep(0.01)
        with self.phase_mutex:
            if self.job.total() != self._baseline + 1:
                record.observation_unknown = True
            self._write(record, "DRAINED", obs_active=0,
                        unexpected_descendant=record.observation_unknown)
        self.deps.barrier.reach("DRAINED")
        return self.status(record.op_id)


class ObservationRunner:
    """Serialized internal child work, never a public op or recursive gate.

    Failure is LeaseError(UNKNOWN/FAILED-STORAGE), not partial stdout. While a
    descendant remains, status is UNKNOWN and open_entries is nonempty (BUSY).
    run waits for positive drain before returning or surfacing that descendant
    failure. Storage/uncertain-launch failures retain original handles/streams
    and open evidence for the owner; closing a job is never a drain substitute.
    """
    def __init__(self, journal, job, proc, deps, phase_mutex):
        if proc is not deps.process:
            raise LeaseError()
        self.journal, self.job, self.proc, self.deps = journal, job, proc, deps
        self.phase_mutex = phase_mutex
        self.executor = _ObservationExecutor(journal, job, deps, phase_mutex)
        self._serial = threading.Lock()
        self._streams = {}

    def open_entries(self):
        return self.executor.open_ops()

    def status(self, obs_id):
        return self.executor.status(obs_id)

    def run(self, parent, argv, cwd, env, *, stdin=b""):
        if not isinstance(parent, str) or not parent or not isinstance(stdin, bytes):
            raise LeaseError()
        if not self._serial.acquire(blocking=False):
            raise LeaseError("BUSY")
        obs_id = uuid.uuid4().hex
        record = None
        try:
            with self.phase_mutex:
                if self.job.active() != 0 or any(self.executor._open(r) for r in self.executor._ops.values()):
                    raise LeaseError("BUSY")
                self.executor._baseline = self.job.total()
                record = _ExecRecord(self.executor, obs_id, tuple(argv), str(cwd), dict(env),
                                     phase="OBSERVE", phases=["OBSERVE"])
                self.journal.append(obs_id, "observe", "OBSERVE", obs_id=obs_id, parent=parent)
                self.executor._ops[obs_id] = record
            self.deps.barrier.reach("OBSERVE")  # durable journal, no process exists
            streams = []
            self._streams[obs_id] = streams
            for _ in range(3):
                streams.append(tempfile.TemporaryFile(mode="w+b", dir=self.journal.path.parent))
            streams[0].write(stdin)
            streams[0].seek(0)
            record.stdio = tuple(streams)
            result = self.executor.run(obs_id)
            if result.result != "DONE" or result.phase != "DRAINED" or result.exit_code is None:
                raise LeaseError("FAILED-STORAGE" if self.journal.failed else "UNKNOWN")
            if self.job.active() != 0:
                raise LeaseError()
            streams[1].seek(0)
            return ObsResult(result.exit_code, streams[1].read(), obs_id)
        except OSError as exc:
            raise LeaseError() from exc
        finally:
            # Only positively terminal work may discard owned capture/handles.
            if record is not None and not self.executor._open(record) and self.job.active() == 0:
                if record.child is not None:
                    self.proc.close_child(record.child)
                for stream in self._streams.pop(obs_id, []):
                    stream.close()
            self._serial.release()


SAFE_GIT_ENV = {
    "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0", "GIT_ATTR_NOSYSTEM": "1",
}


def _git_policy(argv, env, hooks, config):
    """Allow metadata only; do not inherit Git overrides, transports or config.

    hooks/config are new, empty, caller-owned paths outside the worktree.
    check-ignore alone accepts exit 1. Parsing its output is a separate duty.
    """
    args = tuple(argv)
    if args == ("check-ignore", "-z", "--stdin", "--no-index"):
        valid = (0, 1)
    elif args in (("rev-parse", "HEAD"), ("symbolic-ref", "HEAD"),
                  ("ls-files", "--stage", "-z")):
        valid = (0,)
    elif (len(args) == 2 and args[0] == "rev-parse"
          and re.fullmatch(r"refs/heads/[^\s\x00]+", args[1])
          and not any(x in args[1] for x in ("..", "@{", "\\", "~", "^", ":", "?", "*", "["))):
        valid = (0,)
    else:
        raise LeaseError()
    # In particular drop GIT_CONFIG_COUNT/PARAMETERS, GIT_DIR, GIT_WORK_TREE,
    # GIT_INDEX_FILE, tracing, alternate objects and inherited command helpers.
    safe = {k: v for k, v in env.items() if k.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE",
        "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    }}
    safe.update(SAFE_GIT_ENV, GIT_CONFIG_GLOBAL=str(config))
    command = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + str(hooks),
               "-c", "core.untrackedCache=false", *args]
    return command, safe, valid


def run_git(argv, cwd, *, runner, parent, git, env, scratch, stdin=b""):
    """Metadata adapter, exclusively through the bound observation runner.

    Bind the keyword context once (e.g. functools.partial) for the fingerprint's
    (argv, cwd) callable. stdin is bytes for check-ignore's NUL input. Never
    fall back to a direct subprocess. Safe configuration lives in owned scratch.
    """
    if (runner is None or not Path(git).is_absolute() or not Path(cwd).is_absolute()
            or not Path(scratch).is_absolute() or not isinstance(stdin, bytes)):
        raise LeaseError()
    directory = Path(tempfile.mkdtemp(prefix="lease-git-", dir=scratch))
    hooks, config = directory / "hooks", directory / "global"
    try:
        hooks.mkdir()
        config.touch(exist_ok=False)
        command, safe, valid = _git_policy(argv, env, hooks, config)
        result = runner.run(parent, [str(git), *command], cwd, safe, stdin=stdin)
        if result.exit_code not in valid:
            raise LeaseError()
        return result.exit_code, result.stdout
    finally:
        # Do not remove observation configuration from a still-running child.
        # Failed launches/storage leave their own open ledger evidence intact.
        if not runner.open_entries() and runner.job.active() == 0:
            if config.exists():
                config.unlink()
            if hooks.exists():
                hooks.rmdir()
            directory.rmdir()


GUARDIAN_FLAGS = 0x00000008 | 0x00000200 | 0x01000000
HEARTBEAT_SECONDS = 60


def guardian_self_check():
    """Reject job membership before opening a resource or creating any child.

    Launch flags alone are not proof: a venv redirector can place its interpreter
    in a new job even when the redirector successfully broke away from its caller.
    """
    k = _kernel()
    k.GetCurrentProcess.argtypes = []
    k.GetCurrentProcess.restype = W.HANDLE
    k.IsProcessInJob.argtypes = [W.HANDLE, W.HANDLE, ctypes.POINTER(W.BOOL)]
    k.IsProcessInJob.restype = W.BOOL
    inside = W.BOOL()
    if not k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(inside)):
        raise LeaseError("UNSUPPORTED-CONTEXT")
    if inside.value:
        raise LeaseError("UNSUPPORTED-CONTEXT")


def spawn_guardian(argv, *, cwd, env, entry=None):
    """Start only the base interpreter, detached and with explicit breakaway.

    No protected file is written by the caller. The returned Popen is a launch
    receipt, NOT a grant. The guardian must pass its own self-check and complete
    the grant transaction before any client may write. entry is a constructor
    seam for the fixture-only Python entry, never an environment/CLI override.
    """
    if os.name != "nt":
        raise LeaseError("UNSUPPORTED-CONTEXT")
    base = getattr(sys, "_base_executable", None)
    entry = Path(__file__).resolve() if entry is None else Path(entry)
    if (not base or not Path(base).is_absolute() or not entry.is_absolute()
            or not Path(cwd).is_absolute() or not isinstance(env, dict)
            or not isinstance(argv, (list, tuple))
            or any(not isinstance(a, str) or "\0" in a for a in argv)):
        raise LeaseError("UNSUPPORTED-CONTEXT")
    try:
        return subprocess.Popen([base, "-I", "-S", "-B", str(entry), *argv],
                                cwd=str(cwd), env=env, close_fds=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=GUARDIAN_FLAGS)
    except (OSError, ValueError) as exc:
        raise LeaseError("UNSUPPORTED-CONTEXT") from exc


class GuardianBootstrap:
    """Validated process context and empty containment jobs, not lease authority.

    No resource path is touched here. Operational startup must attach the locked
    transaction, ledger and observation runner before publication. Keeping this
    boundary separate makes unsupported contexts refuse before protected setup.
    """
    def __init__(self, deps):
        guardian_self_check()
        self.deps = deps
        self.boot_id = deps.boot.read()
        _integer(self.boot_id)
        if self.boot_id > 0xffffffff:
            raise LeaseError()
        self.identity = deps.process.current()
        _proc(self.identity.record())
        self.phase_mutex = threading.Lock()
        self.job = Job.create(deps)
        try:
            self.observation_job = Job.create(deps)
        except BaseException:
            self.job.close()
            raise

    def close_empty(self):
        # Never confuse closing a handle with positive cessation. This bootstrap
        # owns no child handles and consequently has no termination authority.
        if self.job.active() or self.observation_job.active():
            raise LeaseError("BUSY")
        self.observation_job.close()
        self.job.close()


class GuardianHeartbeat:
    """Health-only snapshot under the SAME mutex as ledger phase transitions.

    Caller owns both resource locks and invokes tick from its nonblocking loop.
    Scheduling uses monotonic time; the recorded wall clock never grants, frees
    or expires a resource. Persistence/inspection failure latches UNKNOWN health.
    The request loop must treat failed as a refusal to admit or release.
    """
    def __init__(self, pair: Pair, gen, boot_id, ledger, job, observation_job, phase_mutex):
        pair.require()
        _integer(gen, 1)
        _integer(boot_id)
        if boot_id > 0xffffffff:
            raise LeaseError()
        directory = pair.wt.path / f"gen-{gen}"
        if ledger.path != directory / "ledger.jsonl":
            raise LeaseError("REJECTED")
        self.pair, self.ledger = pair, ledger
        self.job, self.observation_job = job, observation_job
        self.phase_mutex, self.boot_id = phase_mutex, boot_id
        self.path = directory / "heartbeat.json"
        self.failed = False
        self._next = None

    def tick(self):
        now = time.monotonic()
        if self.failed or self.ledger.failed:
            self.failed = True
            raise LeaseError("UNKNOWN")
        if self._next is not None and now < self._next:
            return False
        try:
            with self.phase_mutex:
                self.pair.require()
                if self.ledger.failed or self.ledger._stream is None:
                    raise LeaseError()
                rows = self.ledger.entries()
                phases = {row["op_id"]: row["phase"] for row in rows}
                # A truncated or externally altered ledger cannot become a
                # healthy heartbeat merely because its writer has cached phases.
                if len(rows) != self.ledger._seq or phases != self.ledger._phases:
                    raise LeaseError()
                job_active = self.job.active()
                obs_active = self.observation_job.active()
                members = self.job.members() + self.observation_job.members()
                if (len(members) != job_active + obs_active
                        or job_active != self.job.active()
                        or obs_active != self.observation_job.active()):
                    raise LeaseError()
                value = {"ts": self.pair.deps.clock(), "boot_id": self.boot_id,
                         "ledger_seq": self.ledger._seq, "job_active": job_active,
                         "obs_active": obs_active,
                         "members": [member.record() for member in members],
                         "open_ops": sorted(op for op, phase in phases.items()
                                            if phase not in TERMINAL_PHASES)}
                StateStore(self.pair).write(self.path, value, "heartbeat")
            self._next = now + HEARTBEAT_SECONDS
            return True
        except (LeaseError, OSError):
            self.failed = True
            raise LeaseError("UNKNOWN")


def guardian_main(deps=None):
    """Bootstrap entry; never publish through missing operational prerequisites.

    R7 unit 1 ends at this boundary. The request loop, transaction/checkpoint and
    IPC wiring are still incomplete, so even valid startup returns UNKNOWN.
    """
    bootstrap = GuardianBootstrap(deps or default_deps())
    try:
        bootstrap.deps.barrier.reach("guardian-started")
        return "UNKNOWN"
    finally:
        bootstrap.close_empty()


def main(argv=None):
    if any(name.startswith("LEASE_TEST_") for name in os.environ):
        return emit("TEST-OVERRIDE-REJECTED")
    if os.name != "nt":
        return emit("UNSUPPORTED-CONTEXT")
    if list(argv or []) == ["--guardian"]:
        try:
            return emit(guardian_main())
        except (LeaseError, OSError) as exc:
            return emit(exc.result if isinstance(exc, LeaseError) else "UNKNOWN")
    return emit("UNKNOWN")  # Missing IPC/checkpoint prerequisites never grant.


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
