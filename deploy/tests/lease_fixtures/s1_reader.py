"""S1 component reader only, not a guardian/recover/adopt acceptance fixture.

The audit hook is installed BEFORE importing lease and catches attempted opens,
including exceptions swallowed by the mutation. It is not kernel tracing.
"""
import functools
import json
import os
from pathlib import Path
import sys
import threading
import time


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    root = Path(os.environ["TEMP"]).resolve(strict=True)
    repo, state = Path(request["repo"]), Path(request["state"])
    assert repo.resolve().is_relative_to(root) and state.resolve().is_relative_to(root)
    forbidden = {os.path.normcase(os.path.abspath(p)) for p in request["forbidden"]}
    assert forbidden and all(Path(p).is_relative_to(root) for p in forbidden)
    mode = request["mode"]
    assert mode in ("normal", "mutation", "missing", "unloaded")
    count, caught = 0, 0
    assert "lease" not in sys.modules and "deploy.lease" not in sys.modules

    def audit(event, args):
        nonlocal count
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
            if path in forbidden:
                count += 1
                raise PermissionError("synthetic forbidden-open audit")

    sys.addaudithook(audit)
    result = {"pid": os.getpid(), "role": "component", "loaded": False,
              "hook_before_import": True, "count": 0, "mutation_caught": 0}
    if mode not in ("missing", "unloaded"):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import lease
        result["loaded"] = True
        if mode == "mutation":
            original = lease._open_content
            def mutant(path):
                nonlocal caught
                try:
                    with open(sorted(forbidden)[0], "rb") as stream:
                        stream.read(1)
                except PermissionError:
                    caught += 1
                return original(path)
            lease._open_content = mutant

        class Boot:
            def read(self):
                return 10

        proc = lease.ProcApi()
        deps = lease.Deps(Boot(), lease.NoBarrier(), lease.NoFailure(), lease.NoAudit(), proc, time.time)
        ledger = lease.Ledger.create(state, deps)
        job = lease.Job.create(deps)
        runner = lease.ObservationRunner(ledger, job, proc, deps, threading.Lock())
        metadata = functools.partial(lease.run_git, runner=runner, parent="fixture-reader",
                                     git=request["git"], env=dict(os.environ), scratch=state)
        try:
            fingerprint = lease.compute_fingerprint(repo, "fixture-branch", metadata)
            result["digest"] = fingerprint.digest
            result["observations"] = sum(row["phase"] == "OBSERVE" for row in ledger.entries())
            assert not runner.open_entries() and job.active() == 0
            assert not proc._children and not runner._streams
        finally:
            assert job.active() == 0
            job.close()
            ledger.close()
        result["count"], result["mutation_caught"] = count, caught
    if mode != "missing":
        (state / f"audit-component-{os.getpid()}.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
