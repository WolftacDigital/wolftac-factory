#!/usr/bin/env python3
"""Factory gatekeeper — zero-model-cost change detection for worker lanes.

Runs as a Hermes ``no_agent`` cron script. Fetches each lane's Factory Queue
issues and dispatches its worker only when the queue fingerprint changes.
Worker eligibility rules remain in the skills; the two-hour safety net covers
time-based eligibility and repositories becoming reachable.

Design constraints learned the hard way (WD-357, WD-380):

* ``hermes cron run`` executes synchronously, so launch it DETACHED and never
  wait on or time out a worker. Each lane is isolated from the others.
* Keep our own in-flight marker until ``last_run_at`` changes or the 90-minute
  TTL expires. Hermes' process-local guard alone cannot prevent double-firing.
* Report ``dispatched``, not a completed run. Only a following tick may report
  ``confirmed-ran`` or ``no-run-observed``.
* Record the fingerprint AT DISPATCH, never at confirmation: changes during a
  worker run must remain visible. Keep it outside the popped in-flight marker.
  Exclude ``updatedAt``, ``delegate`` and ``SWEPT:`` comment churn so silent
  integrate runs settle without duplicating worker skip rules here.
* Linear/env/probe failures fail OPEN (subject to the in-flight guard), except
  a Linear HTTP 401 anywhere in the tick: skip build/review and dispatch only
  integrate for re-mint. Probe all ready lanes before dispatching any of them.
  Slack is also an exception: on an otherwise unchanged integrate lane,
  missing credentials/policy or API failures report ``slack-check-failed`` and
  do NOT dispatch. The safety net and typed approval comments bound the delay.
* Tokens come from the .env FILE, never the process environment or argv. Never
  print remote error bodies or exception messages, which may echo credentials.

Read-only against Linear and Slack; a rocket dispatches, never approves/merges.
Canonical: deploy/factory-gatekeeper.py; installed: scripts/factory_gatekeeper.py
Wiring and rollback: docs/factory-gatekeeper.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DEFAULT_JOB_MAP = {
    "build": ("Todo", "a1ca59d16be5"),
    "review": ("In Review", "a8004c5f3bab"),
    "integrate": ("Approved", "a5d6e054f3fa"),
}
INFLIGHT_TTL_MINUTES = 90

# Live-verified direction: inverseRelations(type=blocks).issue is the blocker;
# relations(type=blocks).relatedIssue is an issue WE block, not our blocker.
QUERY = """
query($state: String!, $after: String) {
  issues(first: 100, after: $after, filter: {
    team: { key: { eq: "WD" } }
    labels: { name: { eq: "Factory Queue" } }
    state: { name: { eq: $state } }
  }) {
    nodes {
      identifier state { name } assignee { id } project { name }
      priority description
      labels { nodes { name } pageInfo { hasNextPage endCursor } }
      comments(first: 50, orderBy: createdAt) { nodes { id createdAt body } }
      inverseRelations {
        nodes { type issue { identifier state { name } } }
        pageInfo { hasNextPage endCursor }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""
RELATIONS_QUERY = """
query($id: String!, $after: String) {
  issue(id: $id) {
    inverseRelations(after: $after) {
      nodes { type issue { identifier state { name } } }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""
LABELS_QUERY = """
query($id: String!, $after: String) {
  issue(id: $id) {
    labels(after: $after) {
      nodes { name } pageInfo { hasNextPage endCursor }
    }
  }
}
"""
PERMALINK = re.compile(r"https://[^/\s]+/archives/([A-Z0-9]+)/p(\d{10})(\d{6})")


class ProbeError(Exception):
    """A deliberately non-sensitive diagnostic, never a remote response body."""


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
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_token(env_path: str, name="LINEAR_FACTORY_BOT_TOKEN") -> str:
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(name + "="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value
    raise ProbeError(name + " missing or empty")


def error_reason(exc: Exception) -> str:
    # Only our own fixed diagnostics are safe; HTTP/JSON/OS errors can contain
    # echoed tokens, response bodies, URLs or even a whole malformed .env line.
    return str(exc) if isinstance(exc, ProbeError) else type(exc).__name__


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for key in ("lanes", "fingerprints", "approval_dispatched"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    return data


def save_state(path: str, state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass  # state is an optimisation; losing it degrades to fail-open


def job_last_run(jobs_path: str, job_id: str):
    """Return the job's recorded last_run_at, or None if unknown/unreadable."""
    try:
        with open(jobs_path, encoding="utf-8") as fh:
            jobs = json.load(fh).get("jobs", [])
        for job in jobs:
            if job.get("id") == job_id:
                return job.get("last_run_at")
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return None


def linear_query(token: str, query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise ProbeError("Linear GraphQL error")
    return payload["data"]


def next_cursor(connection: dict, seen: set):
    page = connection["pageInfo"]
    if not page["hasNextPage"]:
        return None
    cursor = page["endCursor"]
    if not cursor or cursor in seen:
        raise ProbeError("Linear pagination did not advance")
    seen.add(cursor)
    return cursor


def lane_issues(token: str, state: str) -> list[dict]:
    """Exhaust lane pages and nested labels/blockers; comments are newest 50."""
    issues = []
    after = None
    seen = set()
    while True:
        page = linear_query(token, QUERY, {"state": state, "after": after})["issues"]
        for issue in page["nodes"]:
            for field, query in (("labels", LABELS_QUERY), ("inverseRelations", RELATIONS_QUERY)):
                connection = issue[field]
                nodes = list(connection["nodes"])
                nested_seen = set()
                while cursor := next_cursor(connection, nested_seen):
                    connection = linear_query(token, query, {
                        "id": issue["identifier"], "after": cursor,
                    })["issue"][field]
                    nodes.extend(connection["nodes"])
                issue[field] = {"nodes": nodes}
            issues.append(issue)
        after = next_cursor(page, seen)
        if after is None:
            return issues


def lane_fingerprint(issues: list[dict]) -> str:
    records = []
    for issue in issues:
        comments = issue["comments"]["nodes"]
        meaningful = [c["createdAt"] for c in comments if not (c["body"] or "").startswith("SWEPT:")]
        newest = max(meaningful, default="swept-only" if comments else None)
        blockers = [
            [r["issue"]["identifier"], r["issue"]["state"]["name"]]
            for r in issue["inverseRelations"]["nodes"] if r["type"] == "blocks"
        ]
        records.append({
            "identifier": issue["identifier"], "state": issue["state"]["name"],
            "assignee": (issue["assignee"] or {}).get("id"),
            "labels": sorted(label["name"] for label in issue["labels"]["nodes"]),
            "project": (issue["project"] or {}).get("name"),
            "priority": issue["priority"],
            "description": hashlib.sha256((issue["description"] or "").encode()).hexdigest(),
            "comment": newest, "blockedBy": sorted(blockers),
        })
    canonical = json.dumps(sorted(records, key=lambda x: x["identifier"]),
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def slack_approval(issues: list[dict], dispatched: dict, env_path: str, policy_path: str):
    """Return one unconsumed (issue, ts) rocket; never approve or write to Slack."""
    token = read_token(env_path, "SLACK_FACTORY_BOT_TOKEN")
    with open(policy_path, encoding="utf-8") as fh:
        policy = json.load(fh)
    channel = policy.get("slack_channel_id")
    approver = policy.get("approver_slack_user_id")
    if not isinstance(channel, str) or not channel or not isinstance(approver, str) or not approver:
        raise ProbeError("approval policy missing channel or approver")
    for issue in sorted(issues, key=lambda x: x["identifier"]):
        packs = [c for c in issue["comments"]["nodes"]
                 if any(marker in (c["body"] or "") for marker in ("MERGE PROOF PACK", "LANDING PROOF PACK"))]
        if not packs:
            continue
        pack = max(packs, key=lambda c: c["createdAt"])
        match = PERMALINK.search(pack["body"])
        if not match or match[1] != channel:
            continue
        ts = match[2] + "." + match[3]
        if dispatched.get(issue["identifier"]) == ts:
            continue
        params = urllib.parse.urlencode({"channel": channel, "timestamp": ts, "full": "true"})
        req = urllib.request.Request("https://slack.com/api/reactions.get?" + params,
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.load(resp)
        if payload.get("ok") is not True:
            raise ProbeError("Slack API ok:false")
        for reaction in payload["message"].get("reactions", []):
            if reaction.get("name") == "rocket" and approver in reaction.get("users", []):
                return issue["identifier"], ts
    return None


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
    """Launch DETACHED; the following tick verifies whether a run occurred."""
    if dry_run:
        return "dispatched(dry-run)"
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
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed command shape
    except OSError as exc:
        return f"dispatch-failed({type(exc).__name__})"
    finally:
        if log_handle != subprocess.DEVNULL:
            log_handle.close()
    return f"dispatched(pid={proc.pid})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Wolftac factory gatekeeper")
    parser.add_argument("--dry-run", action="store_true", help="report decisions, dispatch nothing")
    parser.add_argument("--env-path", default=None, help="override .env location (scratch testing)")
    parser.add_argument("--jobs-path", default=None, help="override cron jobs.json (scratch testing)")
    parser.add_argument("--state-path", default=None, help="override state file (scratch testing)")
    parser.add_argument("--job-map", default=None, help='JSON lane->[state,jobId] override (scratch testing)')
    parser.add_argument("--exec-override", default=None,
                        help="TEST ONLY: JSON argv list to launch instead of the hermes CLI")
    parser.add_argument("--approval-policy", default=os.path.join(os.path.dirname(__file__), "approval-policy.json"))
    parser.add_argument("--no-slack", action="store_true", help="skip Slack checks")
    args = parser.parse_args()
    job_map = json.loads(args.job_map) if args.job_map else DEFAULT_JOB_MAP
    exec_override = json.loads(args.exec_override) if args.exec_override else None
    home = hermes_home()
    env_path = args.env_path or os.path.join(home, ".env")
    jobs_path = args.jobs_path or os.path.join(home, "cron", "jobs.json")
    state_path = args.state_path or os.path.join(home, "cron", "gatekeeper-state.json")
    state = load_state(state_path)
    lanes_state = state["lanes"]
    fingerprints = state["fingerprints"]
    approvals = state["approval_dispatched"]
    token = None
    fail_open_reason = None
    try:
        token = read_token(env_path)
    except Exception as exc:  # noqa: BLE001 — env failures fail open
        fail_open_reason = "env unreadable: " + error_reason(exc)

    lines: list[str] = []
    ready_lanes = {}
    probes = {}
    linear_auth_failed = False
    # The token is shared. Finish all non-in-flight probes before launching
    # anything, so a later lane's 401 can veto earlier build/review decisions.
    for lane, (linear_state, job_id) in job_map.items():
        try:
            observed_last_run = job_last_run(jobs_path, job_id)
            marker = lanes_state.get(lane)
            if marker:
                dispatched_at = parse_ts(marker.get("dispatched_at")) if isinstance(marker, dict) else None
                if dispatched_at is None:
                    lanes_state.pop(lane, None)
                    fingerprints.pop(lane, None)
                    lines.append(f"{lane}: no-run-observed (invalid in-flight marker)")
                elif observed_last_run and observed_last_run != marker.get("job_last_run_at"):
                    lanes_state.pop(lane, None)
                    lines.append(f"{lane}: confirmed-ran")
                elif now_utc() - dispatched_at > timedelta(minutes=INFLIGHT_TTL_MINUTES):
                    lanes_state.pop(lane, None)
                    fingerprints.pop(lane, None)
                    lines.append(f"{lane}: no-run-observed (stale in-flight cleared after {INFLIGHT_TTL_MINUTES}m)")
                else:
                    lines.append(f"{lane}: in-flight (skip)")
                    continue

            ready_lanes[lane] = (job_id, observed_last_run)
            if fail_open_reason is None:
                try:
                    issues = lane_issues(token, linear_state)
                    probes[lane] = (issues, lane_fingerprint(issues), None)
                except Exception as exc:  # noqa: BLE001 — isolate probe failure
                    probes[lane] = (None, None, error_reason(exc))
                    if isinstance(exc, urllib.error.HTTPError):
                        if exc.code == 401:
                            linear_auth_failed = True
                        # Do not retain the response/traceback or let an unclosed
                        # HTTPError warning echo a remote credential-bearing URL.
                        exc.close()
        except Exception as exc:  # noqa: BLE001 — lane isolation
            ready_lanes.pop(lane, None)
            lines.append(f"{lane}: LANE-ERROR ({error_reason(exc)})")

    for lane, (job_id, observed_last_run) in ready_lanes.items():
        try:
            fingerprint = None
            approval = None
            if linear_auth_failed:
                reason = "linear-auth-failed (401)"
                if lane != "integrate":
                    fingerprints.pop(lane, None)
                    lines.append(f"{lane}: {reason} -> skipped")
                    continue
            elif fail_open_reason is not None:
                reason = f"FAIL-OPEN ({fail_open_reason})"
            else:
                issues, fingerprint, probe_error = probes[lane]
                if probe_error is not None:
                    reason = f"probe failed ({probe_error}) -> FAIL-OPEN"
                else:
                    if not issues:
                        fingerprints.pop(lane, None)
                        lines.append(f"{lane}: idle")
                        continue
                    baseline = fingerprints.get(lane)
                    if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline):
                        reason = "no-baseline"
                    elif fingerprint != baseline:
                        reason = "changed"
                    else:
                        if lane == "integrate" and not args.no_slack:
                            try:
                                approval = slack_approval(issues, approvals, env_path, args.approval_policy)
                            except Exception as exc:  # noqa: BLE001 — Slack never fails open
                                lines.append(f"{lane}: slack-check-failed ({error_reason(exc)})")
                                continue
                        if approval is None:
                            lines.append(f"{lane}: unchanged (skip)")
                            continue
                        reason = f"slack-approval {approval[0]}"

            result = dispatch(job_id, lane, args.dry_run, exec_override)
            suffix = " for re-mint" if linear_auth_failed and result.startswith("dispatched(") else ""
            lines.append(f"{lane}: {reason} -> {result}{suffix}")
            if not args.dry_run and result.startswith("dispatched(pid="):
                lanes_state[lane] = {
                    "dispatched_at": now_utc().isoformat(),
                    "job_last_run_at": observed_last_run,
                }
                if fingerprint is not None:
                    fingerprints[lane] = fingerprint
                else:
                    fingerprints.pop(lane, None)
                if approval is not None:
                    approvals[approval[0]] = approval[1]
        except Exception as exc:  # noqa: BLE001 — lane isolation is the whole point
            lines.append(f"{lane}: LANE-ERROR ({error_reason(exc)})")

    if not args.dry_run:
        save_state(state_path, state)
    print("gatekeeper: " + "; ".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
