#!/usr/bin/env python3
"""Bot-authenticated Linear client for /wolftac-integrate.

Reads LINEAR_FACTORY_BOT_TOKEN internally (process env first, then the Hermes
.env) so the credential never appears on a command line. Every subcommand
prints JSON to stdout and exits non-zero on any failure, so the skill's
fail-closed rule holds without the caller inspecting output.

Usage:
  python linear_bot.py viewer
  python linear_bot.py queue [--state Approved] [--lane crm]
  python linear_bot.py issue WD-123
  python linear_bot.py comment WD-123 --body-file -
  python linear_bot.py claim WD-123
  python linear_bot.py unclaim WD-123
  python linear_bot.py state WD-123 Done
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Proof packs carry emoji and em dashes; Windows consoles default to cp1252.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):  # not a reconfigurable text stream
        pass

API = "https://api.linear.app/graphql"
TEAM_KEY = "WD"
QUEUE_LABEL = "Factory Queue"
TOKEN_KEY = "LINEAR_FACTORY_BOT_TOKEN"

_TOKEN = None


def fail(msg, code=1):
    print(json.dumps({"ok": False, "error": redact(msg)}), file=sys.stderr)
    sys.exit(code)


def redact(text):
    text = str(text)
    if _TOKEN:
        text = text.replace(_TOKEN, "<redacted>")
    return text


def env_path():
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
        r"~\AppData\Local"
    )
    return os.path.join(local, "hermes", ".env")


def load_token():
    """Process env wins; otherwise parse the Hermes .env. Never printed."""
    tok = os.environ.get(TOKEN_KEY)
    if tok:
        return tok.strip()
    path = env_path()
    if not os.path.exists(path):
        fail("%s not set and %s not found" % (TOKEN_KEY, path))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith(TOKEN_KEY + "="):
                continue
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val
    fail("%s absent or empty in %s" % (TOKEN_KEY, path))


def gq(query, variables=None):
    """POST a GraphQL request. Retries once with a Bearer prefix on 401/403."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()

    def attempt(auth):
        req = urllib.request.Request(
            API,
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": auth},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.load(resp)

    header = _TOKEN
    try:
        body = attempt(header)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403) and not header.lower().startswith("bearer "):
            try:
                body = attempt("Bearer " + _TOKEN)
            except urllib.error.HTTPError as exc2:
                fail("Linear API HTTP %s (token invalid or expired)" % exc2.code)
            except Exception as exc2:  # noqa: BLE001
                fail("Linear API request failed: %s" % exc2)
        else:
            fail("Linear API HTTP %s" % exc.code)
    except Exception as exc:  # noqa: BLE001
        fail("Linear API request failed: %s" % exc)

    if body.get("errors"):
        fail("Linear GraphQL error: %s" % json.dumps(body["errors"]))
    return body["data"]


def emit(data):
    out = {"ok": True}
    out.update(data)
    sys.stdout.write(redact(json.dumps(out, indent=2)) + "\n")


ISSUE_FIELDS = """
  id
  identifier
  title
  url
  updatedAt
  state { id name }
  project { id name }
  assignee { id name }
  delegate { id name }
  labels { nodes { name } }
"""

COMMENT_FIELDS = """
  comments(first: 100) {
    nodes {
      id
      createdAt
      body
      user { id name email }
      botActor { id name }
    }
  }
"""


def get_viewer():
    return gq("{ viewer { id name email } }")["viewer"]


def resolve_issue(identifier):
    data = gq(
        "query($id: String!) { issue(id: $id) { %s } }" % ISSUE_FIELDS,
        {"id": identifier},
    )
    issue = data.get("issue")
    if not issue:
        fail("issue %s not found" % identifier)
    return issue


def cmd_viewer(_args):
    emit({"viewer": get_viewer()})


def cmd_queue(args):
    query = """
    query($team: String!, $state: String!, $label: String!) {
      issues(
        filter: {
          team: { key: { eq: $team } }
          state: { name: { eq: $state } }
          labels: { name: { eq: $label } }
        }
        first: 50
      ) {
        nodes { %s %s }
      }
    }
    """ % (ISSUE_FIELDS, COMMENT_FIELDS)
    nodes = gq(
        query, {"team": TEAM_KEY, "state": args.state, "label": QUEUE_LABEL}
    )["issues"]["nodes"]

    if args.lane:
        lane = args.lane.lower()

        def in_lane(issue):
            project = (issue.get("project") or {}).get("name", "") or ""
            labels = [n["name"] for n in issue["labels"]["nodes"]]
            return lane in project.lower() or any(lane in l.lower() for l in labels)

        nodes = [n for n in nodes if in_lane(n)]

    nodes.sort(key=lambda n: n["updatedAt"])  # oldest updated first
    emit({"viewer": get_viewer(), "count": len(nodes), "issues": nodes})


def cmd_issue(args):
    query = "query($id: String!) { issue(id: $id) { %s description %s } }" % (
        ISSUE_FIELDS,
        COMMENT_FIELDS,
    )
    data = gq(query, {"id": args.identifier})
    if not data.get("issue"):
        fail("issue %s not found" % args.identifier)
    emit({"viewer": get_viewer(), "issue": data["issue"]})


def read_body(args):
    if args.body_file == "-":
        return sys.stdin.read()
    if args.body_file:
        with open(args.body_file, "r", encoding="utf-8") as fh:
            return fh.read()
    return args.body


def cmd_comment(args):
    body = read_body(args)
    if not body or not body.strip():
        fail("empty comment body")
    issue = resolve_issue(args.identifier)
    data = gq(
        """
        mutation($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment { id createdAt url }
          }
        }
        """,
        {"issueId": issue["id"], "body": body},
    )["commentCreate"]
    if not data.get("success"):
        fail("commentCreate returned success: false")
    emit({"issue": issue["identifier"], "comment": data["comment"]})


def set_delegate(issue, delegate_id):
    data = gq(
        """
        mutation($id: String!, $delegateId: String) {
          issueUpdate(id: $id, input: { delegateId: $delegateId }) { success }
        }
        """,
        {"id": issue["id"], "delegateId": delegate_id},
    )["issueUpdate"]
    if not data.get("success"):
        fail("issueUpdate(delegateId) returned success: false")
    return resolve_issue(issue["identifier"])  # read-back verification


def cmd_claim(args):
    viewer = get_viewer()
    issue = resolve_issue(args.identifier)
    after = set_delegate(issue, viewer["id"])
    held = (after.get("delegate") or {}).get("id")
    if held != viewer["id"]:
        # Claim race or app-user translation failure: report, never assume.
        emit({
            "claimed": False,
            "reason": "delegate read-back mismatch",
            "viewer": viewer,
            "issue": after,
        })
        sys.exit(2)
    emit({"claimed": True, "viewer": viewer, "issue": after})


def cmd_unclaim(args):
    issue = resolve_issue(args.identifier)
    after = set_delegate(issue, None)
    emit({"released": True, "issue": after})


def cmd_state(args):
    issue = resolve_issue(args.identifier)

    states = gq(
        """
        query($team: String!, $name: String!) {
          workflowStates(
            filter: { team: { key: { eq: $team } }, name: { eq: $name } }
            first: 5
          ) { nodes { id name } }
        }
        """,
        {"team": TEAM_KEY, "name": args.state},
    )["workflowStates"]["nodes"]
    if not states:
        fail("no workflow state named %r on team %s" % (args.state, TEAM_KEY))

    if args.dry_run:
        emit({
            "dry_run": True,
            "issue": issue["identifier"],
            "from": (issue.get("state") or {}).get("name"),
            "to": states[0],
        })
        return

    data = gq(
        """
        mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) { success }
        }
        """,
        {"id": issue["id"], "stateId": states[0]["id"]},
    )["issueUpdate"]
    if not data.get("success"):
        fail("issueUpdate(stateId) returned success: false")

    after = resolve_issue(args.identifier)  # read-back verification
    if (after.get("state") or {}).get("name") != args.state:
        fail(
            "state read-back mismatch: expected %s, got %s"
            % (args.state, (after.get("state") or {}).get("name"))
        )
    emit({"issue": after})


def main():
    global _TOKEN

    parser = argparse.ArgumentParser(prog="linear_bot.py", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("viewer", help="fetch the bot's own Linear identity")

    p_queue = sub.add_parser("queue", help="list Factory Queue issues in a state")
    p_queue.add_argument("--state", default="Approved")
    p_queue.add_argument("--lane", default=None)

    p_issue = sub.add_parser("issue", help="fetch one issue with spec + comments")
    p_issue.add_argument("identifier")

    p_comment = sub.add_parser("comment", help="post a comment on an issue")
    p_comment.add_argument("identifier")
    p_comment.add_argument("--body", default=None)
    p_comment.add_argument(
        "--body-file", default=None, help="path, or - to read stdin"
    )

    p_claim = sub.add_parser("claim", help="set delegate to the bot, verified")
    p_claim.add_argument("identifier")

    p_unclaim = sub.add_parser("unclaim", help="clear the delegate")
    p_unclaim.add_argument("identifier")

    p_state = sub.add_parser("state", help="transition state, verified")
    p_state.add_argument("identifier")
    p_state.add_argument("state")
    p_state.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve the target state without mutating the issue",
    )

    args = parser.parse_args()
    _TOKEN = load_token()

    {
        "viewer": cmd_viewer,
        "queue": cmd_queue,
        "issue": cmd_issue,
        "comment": cmd_comment,
        "claim": cmd_claim,
        "unclaim": cmd_unclaim,
        "state": cmd_state,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
