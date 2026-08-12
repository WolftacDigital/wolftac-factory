#!/usr/bin/env python3
"""Factory gatekeeper — free eligibility pre-check for the Wolftac worker loops.

Runs as a Hermes ``no_agent`` cron script (zero model tokens). Probes Linear
with cheap GraphQL counts and triggers a lane's agent job — via ``hermes cron
run <job-id>`` — only when that lane has eligible work. The agent jobs keep
slow interval schedules as a safety net, so the system self-heals even if this
script dies (the out-of-band watchdog also monitors this job's health).

Fail-open contract (WD-357 AC-4): eligibility filtering must be a SUPERSET
trigger. On ANY doubt — unreadable .env, network failure, unparseable
response — trigger every lane and say why. A false positive costs one agent
tick; a false negative strands the queue behind a silent gatekeeper.

The cron scheduler sanitizes the subprocess environment (no provider secrets
are inherited), so the Linear bot token is read from the Hermes ``.env`` FILE.
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

# Lane -> (Linear state name, Hermes cron job id). Job ids are the live
# worker jobs on this machine; override with --job-map for scratch testing.
DEFAULT_JOB_MAP = {
    "build": ("Todo", "a1ca59d16be5"),
    "review": ("In Review", "a8004c5f3bab"),
    "integrate": ("Approved", "a5d6e054f3fa"),
}

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


def read_token(env_path: str) -> str:
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("LINEAR_FACTORY_BOT_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise KeyError("LINEAR_FACTORY_BOT_TOKEN not present in " + env_path)


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


def trigger(job_id: str, dry_run: bool) -> str:
    if dry_run:
        return "would-trigger"
    result = subprocess.run(
        hermes_cli() + ["cron", "run", job_id],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return "triggered" if result.returncode == 0 else f"trigger-failed({result.returncode})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wolftac factory gatekeeper")
    parser.add_argument("--dry-run", action="store_true", help="report eligibility, trigger nothing")
    parser.add_argument("--env-path", default=None, help="override .env location (scratch testing)")
    parser.add_argument("--job-map", default=None, help="JSON lane->[state,jobId] override (scratch testing)")
    args = parser.parse_args()

    job_map = DEFAULT_JOB_MAP
    if args.job_map:
        raw = json.loads(args.job_map)
        job_map = {lane: (pair[0], pair[1]) for lane, pair in raw.items()}

    env_path = args.env_path or os.path.join(hermes_home(), ".env")

    lines: list[str] = []
    fail_open_reason: str | None = None
    token = None
    try:
        token = read_token(env_path)
    except Exception as exc:  # noqa: BLE001 — any failure here means fail open
        fail_open_reason = f"env unreadable: {exc}"

    for lane, (state, job_id) in job_map.items():
        if fail_open_reason is None:
            try:
                eligible = lane_has_work(token, state)
            except Exception as exc:  # noqa: BLE001 — probe failure -> fail open for this lane
                lines.append(f"{lane}: probe failed ({exc}) -> FAIL-OPEN {trigger(job_id, args.dry_run)}")
                continue
            if eligible:
                lines.append(f"{lane}: eligible -> {trigger(job_id, args.dry_run)}")
            else:
                lines.append(f"{lane}: idle")
        else:
            lines.append(f"{lane}: FAIL-OPEN ({fail_open_reason}) -> {trigger(job_id, args.dry_run)}")

    print("gatekeeper: " + "; ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
