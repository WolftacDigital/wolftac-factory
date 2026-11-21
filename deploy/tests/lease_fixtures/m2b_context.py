"""Credential-free startup fixtures, not operational lease clients.

Only test_entry constructs injected Deps. Every child has a test-owned launch
receipt. The guardian pauses before the incomplete grant boundary, never grants,
and exits naturally when the test releases it. No production lease root is used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import lease


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class FileBarrier:
    def __init__(self, config):
        self.config = config

    def reach(self, name):
        if name != "guardian-started":
            return
        root = Path(self.config["fixture_root"])
        identity = lease.ProcessApi().current()
        write(root / "ready.json", dict(identity.record(), barrier=name))
        deadline = time.monotonic() + 60
        while not (root / "continue").exists():
            if time.monotonic() > deadline:
                raise AssertionError("guardian startup fixture not released")
            time.sleep(0.01)


def run(config, deps):
    root = Path(config["fixture_root"]).resolve(strict=True)
    harness = Path(os.environ["TEMP"]).resolve(strict=True)
    assert root.is_relative_to(harness)
    entry = Path(__file__).with_name("test_entry.py")
    action = config["action"]
    if action == "m2b-guardian":
        try:
            result = lease.guardian_main(deps)
        except lease.LeaseError as exc:
            result = exc.result
        write(root / "guardian-result.json", {"result": result, "pid": os.getpid()})
        return lease.RESULTS[result][0]
    if action == "m2b-caller":
        try:
            child = lease.spawn_guardian([str(root / "guardian-config.json")],
                                         cwd=root, env=dict(os.environ), entry=entry)
        except lease.LeaseError as exc:
            write(root / "caller-result.json", {"result": exc.result, "pid": os.getpid()})
            return lease.RESULTS[exc.result][0]
        receipt = {"caller": deps.process.current().record(),
                   "guardian": {"pid": child.pid, "ctime": deps.process.creation_time(child.pid)}}
        write(root / "caller-result.json", receipt)
        return child.wait(timeout=65)
    if action == "m2b-parent":
        child = subprocess.Popen([getattr(sys, "_base_executable"), "-I", "-S", "-B", str(entry),
                                  str(root / "caller-config.json")], cwd=root,
                                 env=dict(os.environ), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        write(root / "parent-result.json", {"parent": deps.process.current().record(),
                                            "caller": {"pid": child.pid,
                                                       "ctime": deps.process.creation_time(child.pid)}})
        return child.wait(timeout=70)
    raise AssertionError("unknown fixture action")
