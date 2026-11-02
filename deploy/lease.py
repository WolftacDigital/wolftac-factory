"""WD-618 M1: Windows identity, exclusion and durable publication primitives.

The public entry is deliberately inert until the guardian and observation stages
exist. These library primitives do not establish observation/drain evidence.
Publication callers must own BOTH locks for the entire transaction and supply
validated evidence from the later stages. Wall clock values never grant authority.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import sys
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
    return Deps(RegistryBoot(), NoBarrier(), NoFailure(), NoAudit(), ProcessApi(), time.time)


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


def main(argv=None):
    if any(name.startswith("LEASE_TEST_") for name in os.environ):
        return emit("TEST-OVERRIDE-REJECTED")
    return emit("UNKNOWN")  # Every CLI verb remains unavailable in M1.


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
