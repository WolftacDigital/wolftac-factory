#!/usr/bin/env python3
"""Factory gatekeeper — free eligibility pre-check for the Wolftac worker loops.

Runs as a Hermes ``no_agent`` cron script (zero model tokens). Probes Linear
with cheap GraphQL counts and dispatches a lane's agent job — via ``hermes cron
run <job-id>`` — only when that lane has eligible work. The agent jobs keep slow
interval schedules as a safety net, so the conveyor self-heals even if this
script dies (the out-of-band watchdog also monitors this job's health).

Design constraints learned the hard way (WD-357 review, 2026-08-12):

* ``hermes cron run`` executes **synchronously** — ``_try_dispatch_background_run``
  returns ``None`` for a nested cron run and falls back to inline execution. A
  worker pass legitimately takes many minutes, so the dispatch is launched
  DETACHED and never waited on. Blocking a 5-minute tick on a multi-minute agent
  run, or capping it with a timeout, would SIGKILL live workers mid-agent and
  strand issues in In Progress.
* Every lane is isolated: one lane's failure must never prevent the remaining
  lanes from being probed (a busy build lane previously starved review and
  integrate when an exception escaped the loop).
* Hermes' cross-process re-entry guard is a persisted ``fire_claim`` with a 300 s
  TTL that Hermes' own docstring calls "routinely outlived by real jobs", and the
  in-memory guard is process-local. This script therefore keeps its OWN in-flight
  marker per lane, cleared only when the job's ``last_run_at`` advances past the
  dispatch (or after a generous TTL), so a slow worker is never double-fired.
* Reporting is honest: a detached dispatch is reported as ``dispatched``, never
  ``triggered``; the FOLLOWING tick reports ``confirmed-ran`` or
  ``no-run-observed`` by comparing ``last_run_at``. Counting dispatches as runs
  would corrupt exactly the numbers the soak measurement depends on.

Fail-open contract: eligibility filtering is a SUPERSET trigger. On any doubt —
unreadable ``.env``, network failure, unparseable response — dispatch the lane
and say why. A false positive costs one agent tick; a false negative strands the
queue behind a silent gatekeeper. The in-flight guard still applies, so
fail-open cannot stack dispatches on a lane that is already working.

Read-only against Linear: this script never claims, comments, or mutates.

Canonical copy: deploy/factory-gatekeeper.py in the wolftac-factory repo.
Installed copy:  %LOCALAPPDATA%/hermes/scripts/factory_gatekeeper.py
Wiring + rollback: docs/factory-gatekeeper.md
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# Lane -> (Linear state name, Hermes cron job id). Job ids are the live worker
# jobs on this machine; override with --job-map for scratch testing. A stale id
# fails loudly (dispatch returns non-zero and is logged), never silently.
DEFAULT_JOB_MAP = {
    "build": ("Todo", "a1ca59d16be5"),
    "review": ("In Review", "a8004c5f3bab"),
    "integrate": ("Approved", "a5d6e054f3fa"),
}

# A lane stays marked in-flight until its job's last_run_at advances, or this
# long — comfortably longer than any real worker pass, comfortably shorter than
# the 2 h safety-net schedule, so a lost dispatch cannot wedge a lane for a day.
INFLIGHT_TTL_MINUTES = 90

QUERY = """
query($state: String!) {
  issues(
    filter: {
      team: { key: { eq: "WD" } }
      labels: { name: { eq: "Factory Queue" } }
      state: { name: { eq: $state } }
    }
    first: 1
  ) {
    nodes { identifier }
  }
}
"""


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hermes"
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_token(env_path: str) -> str:
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("LINEAR_FACTORY_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise KeyError("LINEAR_FACTORY_BOT_TOKEN not present in " + env_path)


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {"lanes": {}}
    except (OSError, ValueError):
        return {"lanes": {}}


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass  # state is an optimisation; losing it degrades to fail-open


def job_last_run(jobs_path: str, job_id: str):
    """Return the job's recorded last_run_at, or None if unknown/unreadable."""
    try:
        with open(jobs_path, encoding="utf-8") as fh:
            jobs = json.load(fh).get("jobs", [])
    except (OSError, ValueError):
        return None
    for job in jobs:
        if job.get("id") == job_id:
            return job.get("last_run_at")
    return None


def lane_has_work(token: str, state: str) -> bool:
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": QUERY, "variables": {"state": state}}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL error"))
    return bool(payload["data"]["issues"]["nodes"])


def hermes_cli() -> list[str]:
    """Locate the hermes CLI; prefer the install-dir binary, fall back to PATH."""
    for candidate in (
        os.path.join(hermes_home(), "bin", "hermes.exe"),
        os.path.join(hermes_home(), "bin", "hermes"),
    ):
        if os.path.exists(candidate):
            return [candidate]
    return ["hermes"]


def dispatch(job_id: str, lane: str, dry_run: bool, exec_override=None) -> str:
    """Launch the worker DETACHED and return immediately.

    ``hermes cron run`` blocks for the whole agent pass, so waiting here — with
    or without a timeout — either stalls the tick or kills a live worker. We
    fire and forget; the next tick reports whether the run actually happened.
    """
    if dry_run:
        return "would-dispatch"
    cmd = list(exec_override) if exec_override else hermes_cli() + ["cron", "run", job_id]
    log_dir = os.path.join(hermes_home(), "cron", "gatekeeper-logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
        log_path = os.path.join(log_dir, f"{lane}-{stamp}.log")
        log_handle = open(log_path, "w", encoding="utf-8")
    except OSError:
        log_handle = subprocess.DEVNULL

    kwargs = {"stdout": log_handle, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives this tick's exit.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed command shape
    except OSError as exc:
        return f"dispatch-failed({exc.__class__.__name__})"
    return f"dispatched(pid={proc.pid})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wolftac factory gatekeeper")
    parser.add_argument("--dry-run", action="store_true", help="report decisions, dispatch nothing")
    parser.add_argument("--env-path", default=None, help="override .env location (scratch testing)")
    parser.add_argument("--jobs-path", default=None, help="override cron jobs.json (scratch testing)")
    parser.add_argument("--state-path", default=None, help="override in-flight state file (scratch testing)")
    parser.add_argument("--job-map", default=None, help='JSON lane->[state,jobId] override (scratch testing)')
    parser.add_argument("--exec-override", default=None,
                        help="TEST ONLY: JSON argv list to launch instead of the hermes CLI")
    args = parser.parse_args()

    job_map = DEFAULT_JOB_MAP
    if args.job_map:
        job_map = {lane: (pair[0], pair[1]) for lane, pair in json.loads(args.job_map).items()}
    exec_override = json.loads(args.exec_override) if args.exec_override else None

    home = hermes_home()
    env_path = args.env_path or os.path.join(home, ".env")
    jobs_path = args.jobs_path or os.path.join(home, "cron", "jobs.json")
    state_path = args.state_path or os.path.join(home, "cron", "gatekeeper-state.json")

    state = load_state(state_path)
    lanes_state = state.setdefault("lanes", {})

    token = None
    fail_open_reason = None
    try:
        token = read_token(env_path)
    except Exception as exc:  # noqa: BLE001 — any failure here means fail open
        fail_open_reason = f"env unreadable: {exc}"

    lines: list[str] = []
    for lane, (linear_state, job_id) in job_map.items():
        # Per-lane isolation: nothing raised in here may prevent later lanes
        # from being probed (WD-357 D1b).
        try:
            observed_last_run = job_last_run(jobs_path, job_id)
            marker = lanes_state.get(lane)

            if marker:
                dispatched_at = parse_ts(marker.get("dispatched_at"))
                if observed_last_run and observed_last_run != marker.get("job_last_run_at"):
                    lanes_state.pop(lane, None)
                    lines.append(f"{lane}: confirmed-ran")
                elif dispatched_at and now_utc() - dispatched_at > timedelta(minutes=INFLIGHT_TTL_MINUTES):
                    lanes_state.pop(lane, None)
                    lines.append(f"{lane}: no-run-observed (stale in-flight cleared after {INFLIGHT_TTL_MINUTES}m)")
                else:
                    lines.append(f"{lane}: in-flight (skip)")
                    continue

            if fail_open_reason is not None:
                result = dispatch(job_id, lane, args.dry_run, exec_override)
                lines.append(f"{lane}: FAIL-OPEN ({fail_open_reason}) -> {result}")
            else:
                try:
                    eligible = lane_has_work(token, linear_state)
                except Exception as exc:  # noqa: BLE001 — probe failure -> fail open for this lane
                    result = dispatch(job_id, lane, args.dry_run, exec_override)
                    lines.append(f"{lane}: probe failed ({exc}) -> FAIL-OPEN {result}")
                    eligible = None
                else:
                    if eligible:
                        result = dispatch(job_id, lane, args.dry_run, exec_override)
                        lines.append(f"{lane}: eligible -> {result}")
                    else:
                        lines.append(f"{lane}: idle")
                        continue

            if not args.dry_run:
                lanes_state[lane] = {
                    "dispatched_at": now_utc().isoformat(),
                    "job_last_run_at": observed_last_run,
                }
        except Exception as exc:  # noqa: BLE001 — lane isolation is the whole point
            lines.append(f"{lane}: LANE-ERROR ({exc.__class__.__name__}: {exc})")

    if not args.dry_run:
        save_state(state_path, state)
    print("gatekeeper: " + "; ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
