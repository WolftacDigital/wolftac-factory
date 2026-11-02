"""Only fixture entry constructs injected Deps. Never deployed or a real agent.

The owner is a non-agent process exercising M1 library calls, NOT a guardian.
Synthetic fingerprints/drain inputs exercise publication only, not M3/M4 proof.
"""
import dataclasses
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import lease

FP = "a" * 64
EVIDENCE = "b" * 64


def reply(**values):
    print(json.dumps(values, sort_keys=True), flush=True)


class FixtureBoot:
    def read(self):
        return 10


class FixtureBarrier:
    def __init__(self, name):
        self.name = name

    def reach(self, name):
        if name == self.name:
            reply(barrier=name, pid=os.getpid())
            if sys.stdin.readline().strip() != "continue":
                raise lease.LeaseError("UNKNOWN")


class FixtureProcess(lease.ProcessApi):
    def __init__(self, denied=False):
        self.denied = denied

    def creation_time(self, pid):
        if self.denied:
            raise PermissionError("synthetic access denied")
        return super().creation_time(pid)


class FixtureFail:
    def __init__(self, fail=None):
        self.fail = fail

    def check(self, name):
        if name == self.fail:
            raise OSError("fixture injected storage failure")


class FixtureAudit:
    def record(self, event, path):
        pass


def fixture_deps(config):
    return lease.Deps(FixtureBoot(), FixtureBarrier(config.get("barrier")),
                      FixtureFail(config.get("fail")), FixtureAudit(),
                      FixtureProcess(config.get("denied", False)),
                      lambda: time.time() + config.get("clock_shift", 0))


def make_state(gen, deps, branch, restriction="none"):
    return {"lifecycle": "GRANTED", "gen": gen,
            "guardian": deps.process.current().record(), "boot_id": deps.boot.read(),
            "token_hash": FP, "branch": branch, "correlation": {"fixture": True},
            "restriction": restriction, "adopted_fp": None}


def run(config):
    deps = fixture_deps(config)
    action = config["action"]
    if action == "identity":
        wt, br = lease.resource_ids(config["worktree"], config["branch"])
        reply(result="OK", wt=wt, br=br)
        return
    if action == "liveness":
        identity = lease.ProcIdentity(**config["identity"])
        reply(result=lease.liveness(identity, deps.process))
        return
    wt_id, br_id = lease.resource_ids(config["worktree"], config["branch"])
    wt = lease.ResourceDir(config["root"], wt_id)
    br = lease.ResourceDir(config["root"], br_id)
    pair = lease.acquire_pair(wt, br, deps)
    if pair == "HELD":
        reply(result="HELD")
        return
    try:
        store = lease.StateStore(pair)
        if action == "probe":
            decision = lease.inspect_acquire(pair, config["branch"], FP)
            reply(**dataclasses.asdict(decision))
            return
        if action == "lock-only":
            reply(result="OK")
            sys.stdin.readline()
            return
        if action == "prechecks":
            state = make_state(1, deps, config["branch"])
            state["lifecycle"] = config["lifecycle"]
            if config.get("restricted"):
                state["restriction"] = {"kind": "adoption-required", "gen": 1, "evidence_sha": EVIDENCE}
            store.write(wt.path / "state.json", state, "worktree")
            before = (wt.path / "state.json").read_bytes()
            recover = lease.decide_recover_precheck(state)
            adopt = lease.decide_adopt_precheck(state, config.get("evidence_sha", EVIDENCE),
                                                config.get("evidence_gen", 1), FP, FP)
            reply(recover=recover, adopt=adopt, unchanged=before == (wt.path / "state.json").read_bytes())
            return
        if action == "journal":
            gen = lease.next_gen(pair)
            path = lease.txn_open(pair, gen, config["branch"])
            if config.get("terminal"):
                lease.txn_abandoned_drained(pair, path, {"drained": True, "fixture_no_children": True})
            if config.get("op"):
                lease.txn_open(pair, gen, config["branch"], "run-one", config["op"])
            pair.close()  # Test journal precedence, not merely OS lock contention.
            reply(result="OK", gen=gen, identity=deps.process.current().record(), path=str(path))
            sys.stdin.readline()
            return
        if action == "generations":
            one = lease.next_gen(pair)
            journal = lease.txn_open(pair, one, config["branch"])
            lease.txn_abandoned_drained(pair, journal, {"drained": True, "fixture_no_children": True})
            two = lease.next_gen(pair)
            lease.txn_open(pair, two, config["branch"])
            try:
                lease.txn_open(pair, one, config["branch"])
            except lease.LeaseError as exc:
                refused = exc.result
            else:
                raise AssertionError("generation overwritten")
            reply(one=one, two=two, refused=refused, terminal=journal.read_text())
            return
        if action == "unlock-probe":
            pair.close()
            try:
                store.write(wt.path / "state.json", make_state(1, deps, config["branch"]), "worktree")
            except lease.LeaseError as exc:
                reply(result=exc.result)
                return
            raise AssertionError("write without locks")
        decision = lease.inspect_acquire(pair, config["branch"], FP)
        if decision.result != "GRANTED":
            reply(**dataclasses.asdict(decision))
            return
        gen = lease.next_gen(pair)
        lease.txn_open(pair, gen, config["branch"])
        previous = store.read(wt.path / "state.json", "worktree")
        restriction = previous["restriction"] if previous else "none"
        state = make_state(gen, deps, config["branch"], restriction)
        pub = lease.Publication(pair, gen, FP)
        lease.publish_A1(pub)
        if action == "abandoned-a1":
            lease.txn_abandoned_drained(pair, wt.path / f"gen-{gen}" / "txn.jsonl",
                                        {"drained": True, "fixture_no_children": True})
            reply(result="DONE", gen=gen)
            return
        lease.publish_A2(pub, state)
        lease.publish_A3(pub)
        if action in ("restrict", "adopt"):
            # M1 publication fixture, not recovery: synthetic ORPHANED authority
            # after a fully closed grant transaction; no admitted child exists.
            state["lifecycle"] = "ORPHANED"
            store.write(wt.path / "state.json", state, "worktree")
            lease.restrict_RC1(pub, EVIDENCE)
            lease.restrict_RC2(pub, EVIDENCE)
            lease.restrict_RC3(pub)
            if action == "adopt":
                lease.adopt_AD1(pub, EVIDENCE, gen, FP, FP)
                lease.adopt_AD2(pub)
            reply(result="DONE", gen=gen)
            return
        if action == "released":
            lease.publish_R1(pub)
            lease.publish_R2(pub)
            reply(result="DONE", gen=gen)
            return
        if config.get("old_heartbeat"):
            store.write(wt.path / f"gen-{gen}" / "heartbeat.json",
                        {"ts": deps.clock() - 86400, "boot_id": 10, "ledger_seq": 0,
                         "job_active": 0, "obs_active": 0, "members": [], "open_ops": []}, "heartbeat")
        if config.get("old_heartbeat"):
            pair.close()  # Fixture exposes live identity to a separate acquirer.
        reply(result="GRANTED", mode=decision.mode, gen=gen, identity=deps.process.current().record())
        while True:
            command = sys.stdin.readline().strip()
            if command == "release":
                lease.publish_R1(pub)
                lease.publish_R2(pub)
                reply(result="DONE")
                return
            if command == "stale-release":
                # Simulates delivery to the current lock-owning executor, not a
                # bypass of the OS lock by an old owner. M2 adds actual IPC.
                try:
                    lease.publish_R1(lease.Publication(pair, gen - 1, FP))
                except lease.LeaseError as exc:
                    reply(result=exc.result)
                else:
                    raise AssertionError("stale release accepted")
            elif command == "snapshot":
                records = {}
                for path in wt.root.rglob("*"):
                    if not path.is_file():
                        continue
                    resource = next((r for r in (wt, br) if path == r.path / "lock"), None)
                    if resource is not None:
                        assert resource.fd is not None
                        os.lseek(resource.fd, 0, os.SEEK_SET)
                        data = os.read(resource.fd, 1)
                    else:
                        data = path.read_bytes()
                    records[str(path.relative_to(wt.root))] = data.hex()
                reply(records=records)
            elif command == "inspect-live":
                reply(**dataclasses.asdict(lease.inspect_acquire(pair, config["branch"], FP)))
            elif command == "exit" or not command:
                return
    finally:
        pair.close()


def main():
    try:
        config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        if config["action"] == "emit-result":
            return lease.emit(config["result"])
        run(config)
        return 0
    except lease.LeaseError as exc:
        reply(result=exc.result)
        return lease.RESULTS[exc.result][0]


if __name__ == "__main__":
    sys.exit(main())
