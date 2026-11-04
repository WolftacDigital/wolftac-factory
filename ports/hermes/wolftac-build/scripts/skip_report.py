"""Prepare no-silent-skip comments from observed gates; never select, claim or send.

JSON stdin: {lane, taken, race_lost, now, issues}. Each issue is an enriched
Todo snapshot: id, team, status, labels (names), project (name or null), skip,
comments [{body, createdAt}], comments_complete. See the build skill for the
observed skip schema. No credentials, environment reads, files or network I/O.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

PREFIX = "BUILD SKIPPED: "
CHECKS = frozenset({"Repo", "repo-unreachable", "repo-identity", "worktree-dirty",
                    "orphaned-worktree", "merge-prerequisite", "Linear",
                    "comment-history", "protected-file", "BLOCKED"})
STATES = frozenset({"triage", "backlog", "unstarted", "started", "completed", "canceled"})


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,9}", value):
        raise ValueError("invalid issue identifier")
    return value


def number(value):
    if type(value) is not int or not 0 <= value <= 100000:
        raise ValueError("invalid estimate")
    return value


def reason(skip):
    """Format the actual first failed gate, not a guessed historical cause."""
    gate = skip["gate"]
    if gate == "dependency":
        blockers = []
        for blocker in skip["blockers"]:
            ident = identifier(blocker["id"])
            state = blocker["stateType"]
            if state not in STATES:
                raise ValueError("unresolved blocker state")
            if state not in {"completed", "canceled"}:
                blockers.append((ident, state))
        if not blockers:
            return None  # retained Done relations are not open blockers
        ident, state = sorted(blockers)[0]
        return f"blocked by {ident} ({state})"
    if gate == "park":
        return "parked"
    if gate == "fence":
        # Opaque run identifiers only, never a command line or lease payload.
        run = skip["run"]
        if not isinstance(run, str) or not re.fullmatch(r"[a-f0-9]{8,32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", run):
            raise ValueError("invalid run identifier")
        return f"fence held by {run}"
    if gate == "lane":
        field = skip["field"]
        if field not in {"project", "labels", "project/labels"}:
            raise ValueError("invalid lane field")
        return f"lane mismatch: {field}"
    if gate == "budget":
        return (f"completion budget (files={number(skip['files'])}, "
                f"checks={number(skip['checks'])}, reserve=20%)")
    if gate == "preclaim":
        check = skip["check"]
        if check not in CHECKS:
            raise ValueError("invalid preclaim check")
        return f"preclaim check failed: {check}"
    raise ValueError("unknown skip gate")


def latest_work_comment(comments):
    """Diagnostics cannot clear a prior BLOCKED work-order marker."""
    items = [c for c in comments if not c["body"].startswith(PREFIX)]
    return max(items, key=lambda c: stamp(c["createdAt"]), default=None)


def in_scope(issue, lane):
    if (not isinstance(issue["labels"], list)
            or not all(isinstance(label, str) for label in issue["labels"])
            or not (issue["project"] is None or isinstance(issue["project"], str))):
        raise ValueError("enriched project and label names required")
    if (issue["team"] != "Wolftac Digital" or issue["status"] != "Todo"
            or "Factory Queue" not in issue["labels"]):
        return False
    fields = [issue["project"] or "", *issue["labels"]]
    return not lane or any(lane.casefold() in field.casefold() for field in fields)


def prepare(payload):
    """Return proposed comments only. The worker rechecks and sends via MCP."""
    if type(payload["taken"]) is not bool or type(payload["race_lost"]) is not bool:
        raise ValueError("explicit run outcome required")
    if payload["taken"] or payload["race_lost"]:
        return []
    now = stamp(payload["now"])
    lane = payload["lane"]
    if not isinstance(lane, str) or not isinstance(payload["issues"], list):
        raise ValueError("explicit lane and issue list required")
    proposed = {}
    seen = set()
    for issue in payload["issues"]:
        if not in_scope(issue, lane):
            continue  # lane mismatch is local diagnostic only, never cross-lane I/O
        ident = identifier(issue["id"])
        if ident in seen:
            raise ValueError("duplicate issue snapshot")
        seen.add(ident)
        if issue["comments_complete"] is not True:
            raise ValueError("incomplete comment history")
        why = reason(issue["skip"])
        if why is None:
            raise ValueError("dependency gate did not fail; record the actual gate")
        if issue["skip"]["gate"] == "lane":
            raise ValueError("in-lane snapshot cannot have a lane mismatch")
        body = PREFIX + why
        recent = False
        for comment in issue["comments"]:
            if comment["body"] == body:
                at = stamp(comment["createdAt"])
                if at > now:
                    raise ValueError("future diagnostic timestamp")
                if now - at < timedelta(hours=24):
                    recent = True
        if not recent:
            proposed[ident] = {"issueId": ident, "body": body}
    return list(proposed.values())


def main():
    try:
        result = prepare(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, AttributeError):
        # Do not echo rejected input: even an exception can contain private data.
        print("skip-report: invalid or incomplete observations; no comments prepared", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
