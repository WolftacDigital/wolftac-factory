"""Test-owned marker child. All files stay under the explicit fixture root."""
import json
import os
from pathlib import Path
import sys
import subprocess
import time


def main():
    root = Path(sys.argv[1]).resolve(strict=True)
    harness = Path(os.environ["TEMP"]).resolve(strict=True)
    assert root == harness
    marker = root / sys.argv[2]
    release = root / sys.argv[3]
    assert marker.resolve().is_relative_to(root)
    assert release.resolve().is_relative_to(root)
    if len(sys.argv) > 4:
        mode, executable = sys.argv[4:6]
        assert mode in ("breakaway", "descendant")
        flags = subprocess.CREATE_BREAKAWAY_FROM_JOB if mode == "breakaway" else 0
        try:
            child = subprocess.Popen([executable, "-B", __file__, str(root), "grand-marker", "grand-release"],
                                     cwd=root, env=dict(os.environ), creationflags=flags)
        except OSError as exc:
            if mode != "breakaway" or exc.winerror != 5:
                raise
            receipt = {"result": "REFUSED", "winerror": exc.winerror, "parent": os.getpid()}
        else:
            receipt = {"result": "SPAWNED", "pid": child.pid, "parent": os.getpid()}
        marker.write_text(json.dumps(receipt), encoding="utf-8")
        return 0  # Deliberately exit before the surviving descendant.
    marker.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    deadline = time.monotonic() + 60
    while not release.exists():
        if time.monotonic() > deadline:
            return 90
        time.sleep(0.01)
    return 0


if __name__ == "__main__":
    sys.exit(main())
