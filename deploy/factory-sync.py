#!/usr/bin/env python3
"""Fast-forward the Factory copies. Operator job, never a worker live action.

Tests must supply every path explicitly. No credentials, worker dispatch, service
control or protected-file writes. Only fixed status vocabulary is emitted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

EXPECTED_REMOTE = "git@github.com:WolftacDigital/wolftac-factory.git"
PROTECTED = ("factory-watchdog.ps1", "deploy.ps1", "junctions.json",
             "approval-policy.json", "junction-lib.ps1")


def git(repo, *args):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(["git", "-C", str(repo), *args], env=env,
                          capture_output=True, timeout=120)


def checked(repo, *args):
    result = git(repo, *args)
    if result.returncode:
        raise OSError("git failed")
    return result.stdout.decode("utf-8").strip()


def load_gatekeeper():
    # Import definitions only, never main(), read_token(), or any dispatch path.
    # Repository spelling differs from the operator-installed script spelling.
    path = Path(__file__).with_name("factory-gatekeeper.py")
    if not path.exists():
        path = Path(__file__).with_name("factory_gatekeeper.py")
    spec = importlib.util.spec_from_file_location(
        "factory_sync_gatekeeper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def inflight(state_path, home, gatekeeper):
    names = list(gatekeeper.DEFAULT_JOB_MAP)
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        markers = state["lanes"]
        if not isinstance(markers, dict):
            raise ValueError("invalid lanes")
    except (OSError, ValueError, KeyError, TypeError):
        return names
    held = []
    for lane, entry in gatekeeper.DEFAULT_JOB_MAP.items():
        try:
            _, job_id, profile = gatekeeper.lane_entry(entry)
            jobs_path = gatekeeper.profile_jobs_path(str(home), profile)
            jobs = json.loads(Path(jobs_path).read_text(encoding="utf-8"))["jobs"]
            matches = [job for job in jobs if job["id"] == job_id]
            if len(matches) != 1:
                raise ValueError("missing job")
            marker = markers.get(lane)
            if marker is None:
                continue
            stamp = gatekeeper.parse_ts(marker["dispatched_at"])
            if stamp is None:
                raise ValueError("invalid timestamp")
            age = (datetime.now(timezone.utc) - stamp).total_seconds()
            if (matches[0].get("last_run_at") == marker["job_last_run_at"]
                    and age < gatekeeper.INFLIGHT_TTL_MINUTES * 60):
                held.append(lane)
        except (OSError, ValueError, KeyError, TypeError):
            held.append(lane)
    # Unknown markers cannot establish that every lane is idle. Emit only the
    # trusted lane-map names, never keys from the input document.
    if set(markers) - set(names):
        return names
    return held


def checkout(name, repo, args, gatekeeper):
    if name == "live" and gatekeeper is None:
        # No lane definitions means no proof that live is idle. Use the existing
        # fixed failure vocabulary; never expose the import exception or path.
        return "ff-failed -> skipped"
    try:
        if checked(repo, "remote", "get-url", "origin") != args.expected_remote:
            return "remote-mismatch -> skipped"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "remote-mismatch -> skipped"
    try:
        if args.dry_run:
            remote = checked(repo, "ls-remote", "origin", "refs/heads/main").split()
            if len(remote) != 2 or remote[1] != "refs/heads/main":
                return "fetch-failed -> skipped"
            target = remote[0]
        else:
            checked(repo, "fetch", "origin")
            target = checked(repo, "rev-parse", "origin/main")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target):
            return "fetch-failed -> skipped"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "fetch-failed -> skipped"
    try:
        # NUL records count paths correctly even when a filename contains LF.
        status = git(repo, "status", "--porcelain", "-z")
        if status.returncode:
            return "ff-failed -> skipped"
        records = status.stdout.split(b"\0")[:-1]
        count, i = 0, 0
        while i < len(records):
            count += 1
            i += 2 if records[i][:1] in (b"R", b"C") else 1
        if count:
            return f"dirty ({count} files) -> skipped"
        head = checked(repo, "rev-parse", "HEAD")
        if head == target:
            return f"current {target[:7]}"
        if args.dry_run:
            # ls-remote never downloads objects. If absent locally, report intent
            # only; the real run still requires ancestry after its bounded fetch.
            present = git(repo, "cat-file", "-e", target + "^{commit}").returncode == 0
            if present and git(repo, "merge-base", "--is-ancestor", head, target).returncode:
                return "diverged -> skipped"
        elif git(repo, "merge-base", "--is-ancestor", head, target).returncode:
            return "diverged -> skipped"
        if name == "live":
            held = inflight(args.gatekeeper_state, args.hermes_home, gatekeeper)
            if held:
                return f"lanes-in-flight ({','.join(held)}) -> skipped"
        if args.dry_run:
            return f"would-update {target[:7]}"
        checked(repo, "merge", "--ff-only", "origin/main")
        if checked(repo, "rev-parse", "HEAD") != target:
            return "ff-failed -> skipped"
        return f"updated {target[:7]}"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "ff-failed -> skipped"


def install(source, target, dry_run):
    temp = None
    try:
        new = source.read_bytes()
        if target.is_symlink():
            raise OSError("symlink destination")
        old = target.read_bytes() if target.exists() else None
        if old == new:
            return "current"
        if dry_run:
            return "would-install"
        target.parent.mkdir(parents=True, exist_ok=True)
        if old is not None:
            # Exclusive creation never overwrites an earlier backup, including
            # two installs in the same UTC second.
            for attempt in range(3):
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup = target.with_name(target.name + ".bak-" + stamp)
                try:
                    with backup.open("xb") as stream:
                        stream.write(old)
                    break
                except FileExistsError:
                    if attempt == 2:
                        raise
                    time.sleep(1)
        fd, name = tempfile.mkstemp(prefix=target.name + ".tmp-", dir=target.parent)
        temp = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(new)
            stream.flush()
            os.fsync(stream.fileno())
        pattern = re.compile(re.escape(target.name) + r"\.bak-\d{8}T\d{6}Z")
        backups = sorted((p for p in target.parent.iterdir()
                          if pattern.fullmatch(p.name) and p.is_file() and not p.is_symlink()),
                         key=lambda p: p.name, reverse=True)
        # Do all potentially failing housekeeping before the atomic replacement.
        for backup in backups[5:]:
            backup.unlink()
        os.replace(temp, target)
        return "installed"
    except OSError as exc:
        return f"install-failed ({type(exc).__name__})"
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def protected_status(source, installed):
    stale = 0
    for name in PROTECTED:
        try:
            equal = (hashlib.sha256((source / name).read_bytes()).digest()
                     == hashlib.sha256((installed / name).read_bytes()).digest())
        except OSError:
            equal = False
        stale += not equal
    return f"stale ({stale} of 5)" if stale else "current"


def parser():
    home = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "hermes"
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--main-clone", type=Path, default=Path("D:/Wolftac/Factory"))
    ap.add_argument("--live", type=Path, default=Path("D:/Wolftac/.factory-live"))
    ap.add_argument("--scripts-dir", type=Path, default=home / "scripts")
    ap.add_argument("--watchdog-dir", type=Path,
                    default=Path("C:/ProgramData/WolftacFactory/watchdog/deploy"))
    ap.add_argument("--gatekeeper-state", type=Path)
    ap.add_argument("--hermes-home", type=Path, default=home)
    ap.add_argument("--expected-remote", default=EXPECTED_REMOTE)
    ap.add_argument("--dry-run", action="store_true")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.gatekeeper_state is None:
        args.gatekeeper_state = args.hermes_home / "cron/gatekeeper-state.json"
    try:
        gatekeeper = load_gatekeeper()
        # An incomplete installed module is also an unavailable safety guard.
        for attribute in ("DEFAULT_JOB_MAP", "INFLIGHT_TTL_MINUTES", "lane_entry",
                          "profile_jobs_path", "parse_ts"):
            getattr(gatekeeper, attribute)
    except Exception:
        gatekeeper = None
    primary = checkout("main-clone", args.main_clone, args, gatekeeper)
    live = checkout("live", args.live, args, gatekeeper)
    source = args.main_clone / "deploy"
    allowed = primary.startswith(("current ", "updated "))
    # A remote advance may change either installed file. Without fetching objects
    # a dry run can report intent only, never assert that remote bytes are current.
    preview = args.dry_run and primary.startswith("would-update ")
    statuses = []
    for original, installed in (("factory-gatekeeper.py", "factory_gatekeeper.py"),
                                ("approval-policy.json", "approval-policy.json")):
        statuses.append("would-install" if preview else
                        install(source / original, args.scripts_dir / installed,
                                args.dry_run) if allowed else "skipped")
    watchdog = protected_status(source, args.watchdog_dir)
    print(f"sync: main-clone: {primary}; live: {live}; gatekeeper: {statuses[0]}; "
          f"policy: {statuses[1]}; watchdog-files: {watchdog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
