#!/usr/bin/env python3
"""Bot-authenticated Linear client for /wolftac-integrate.

Credentials stay off command lines and output. Hermes profile files take
precedence over the process snapshot; other callers retain env-first loading.
Run `remint` (renew if due, then verify), followed by `viewer`, before pickup.
Any request can recover once from HTTP 401. --no-mint forbids credential writes.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

# Proof packs carry emoji and em dashes; Windows consoles default to cp1252.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

API = "https://api.linear.app/graphql"
OAUTH_API = "https://api.linear.app/oauth/token"
EXPECTED_BOT_ID = "b8fece8e-8ec5-4125-b4b2-d7fa882db137"
OAUTH_SCOPE = "read,write,app:assignable,app:mentionable"
TEAM_KEY = "WD"
QUEUE_LABEL = "Factory Queue"
TOKEN_KEY = "LINEAR_FACTORY_BOT_TOKEN"
EXPIRY_KEY = "LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT"
APP_ID_KEY = "LINEAR_FACTORY_APP_ID"
APP_SECRET_KEY = "LINEAR_FACTORY_APP_SECRET"
RENEW_WINDOW = timedelta(days=5)
TEST_HOMES_OVERRIDE = "LINEAR_FACTORY_TOKEN_HOMES_TEST_JSON"

_TOKEN = None
_TOKEN_SOURCE = None
_TOKEN_PATH = None
_NO_MINT = False
_MINT_ATTEMPTED = False
_SECRETS = set()


def fail(msg, code=1):
    print(json.dumps({"ok": False, "error": redact(msg)}), file=sys.stderr)
    sys.exit(code)


def redact(text):
    text = str(text)
    for value in sorted(_SECRETS | ({_TOKEN} if _TOKEN else set()), key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    return text


def env_path():
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        return os.path.join(home, ".env")
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    return os.path.join(local, "hermes", ".env")


def _read_env(path):
    """Return original bytes and parsed values, without logging any values."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return b"", {}
    except OSError:
        fail("Cannot read credential file", 5)
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        fail("Credential file is not valid UTF-8", 5)
    values = {}
    for line in text.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if match:
            value = match[2].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[match[1]] = value
    for key in (TOKEN_KEY, APP_ID_KEY, APP_SECRET_KEY):
        if values.get(key):
            _SECRETS.add(values[key])
    return raw, values


def load_token():
    """Profile file wins over Hermes' start-of-job process env snapshot.

    Outside Hermes, retain env-first compatibility. After renewal this process
    uses the new token directly; subsequent Hermes calls read the new file.
    App credentials are always read from this same resolved file, not inherited
    from a different profile's environment.
    """
    global _TOKEN_SOURCE, _TOKEN_PATH
    _TOKEN_PATH = os.path.abspath(env_path())
    _, values = _read_env(_TOKEN_PATH)
    file_token = values.get(TOKEN_KEY)
    process_token = os.environ.get(TOKEN_KEY, "").strip()
    if process_token:
        _SECRETS.add(process_token)
    if os.environ.get("HERMES_HOME", "").strip() and file_token:
        _TOKEN_SOURCE = "file"
        return file_token
    if process_token:
        _TOKEN_SOURCE = "env"
        return process_token
    _TOKEN_SOURCE = "file"
    return file_token


def initialize(no_mint=False):
    global _TOKEN, _NO_MINT, _MINT_ATTEMPTED
    _NO_MINT = no_mint
    _MINT_ATTEMPTED = False
    _SECRETS.clear()
    _TOKEN = load_token()


def _request(query, variables, token):
    """One logical request, preserving the existing raw/Bearer compatibility."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()

    def attempt(auth):
        req = urllib.request.Request(
            API, data=payload,
            headers={"Content-Type": "application/json", "Authorization": auth},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.load(resp)

    try:
        body = attempt(token)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code not in (400, 401, 403) or token.lower().startswith("bearer "):
            raise
        body = attempt("Bearer " + token)
    if not isinstance(body, dict) or body.get("errors") or not isinstance(body.get("data"), dict):
        # Do not echo server-controlled error bodies, which can contain secrets.
        fail("Linear GraphQL error or malformed response")
    return body["data"]


def _verified_viewer(data):
    viewer = data.get("viewer")
    if not isinstance(viewer, dict) or viewer.get("id") != EXPECTED_BOT_ID:
        fail("Linear viewer does not match the pinned factory bot id", 4)
    return viewer


def _renewed_bytes(original, token, expires_at):
    """Replace only credential lines, including duplicates; keep all other bytes."""
    replacements = {TOKEN_KEY: token, EXPIRY_KEY: expires_at}
    lines = []
    seen = set()
    for line in original.splitlines(keepends=True):
        match = re.match(rb"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", line)
        key = match[1].decode("ascii") if match else None
        if key in replacements:
            ending = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b"\r" if line.endswith(b"\r") else b""
            line = (key + "=" + replacements[key]).encode("utf-8") + ending
            seen.add(key)
        lines.append(line)
    # Prepend missing keys: appending would alter an unrelated unterminated line.
    missing = b"".join(f"{key}={value}\n".encode("utf-8") for key, value in replacements.items() if key not in seen)
    return missing + b"".join(lines)


def _persist_token(path, original, token, expires_at):
    """Atomic replacement; callers decide whether a failure is fatal or advisory."""
    tmp = None
    try:
        target = Path(path)
        if target.is_symlink() or not target.is_file() or not target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise OSError("Credential file is missing, linked or read-only")
        fd, tmp = tempfile.mkstemp(prefix=".linear-bot-", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as fh:
            fh.write(_renewed_bytes(original, token, expires_at))
            fh.flush()
            os.fsync(fh.fileno())
        # Fail closed instead of overwriting a concurrent profile edit.
        if Path(path).read_bytes() != original:
            raise OSError("Credential file changed during renewal; nothing persisted")
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _propagation_skip(path):
    # Never include exception messages or file contents from an optional home.
    print(json.dumps({"token_home_skipped": redact(str(path))}), file=sys.stderr)


def _registered_token_homes():
    """Load the checked-in registry (packaged alongside each deployed helper).

    The explicitly test-only override is a JSON array of absolute scratch file
    paths; it replaces the registry entirely, never supplements live homes.
    Production entries are LOCALAPPDATA-relative, never arbitrary absolute paths.
    """
    registry = Path(__file__).resolve().with_name("token-homes.json")
    override = os.environ.get(TEST_HOMES_OVERRIDE)
    try:
        if override is not None:
            entries = json.loads(override)
            if not isinstance(entries, list) or any(not isinstance(p, str) or "\x00" in p or not Path(p).is_absolute() for p in entries):
                raise ValueError()
            return [Path(p) for p in entries]
        entries = json.loads(registry.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError()
        local = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"))
        homes = []
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError()
            parts = entry.replace("\\", "/").split("/")
            if any(p in ("", ".", "..") or ":" in p or "\x00" in p for p in parts):
                raise ValueError()
            homes.append(local.joinpath(*parts))
        return homes
    except (OSError, ValueError):
        # A broken optional registry cannot turn a verified current mint into
        # a failed run, and a bad test override must never fall back to live files.
        _propagation_skip(registry)
        return []


def _propagate_token(token, expires_at):
    seen = {os.path.normcase(os.path.abspath(_TOKEN_PATH))}
    for home in _registered_token_homes():
        identity = os.path.normcase(os.path.abspath(home))
        if identity in seen:
            continue
        seen.add(identity)
        try:
            if home.is_symlink() or not home.is_file():
                raise OSError()
            original = home.read_bytes()
            # No parsing/copying of another home's app credentials is needed.
            if not re.search(rb"(?m)^[ \t]*(?:export[ \t]+)?LINEAR_FACTORY_BOT_TOKEN[ \t]*=", original):
                raise OSError()
            _persist_token(str(home), original, token, expires_at)
        except OSError:
            _propagation_skip(home)


def mint():
    """Mint at most once per invocation, verify the pinned identity, then persist."""
    global _TOKEN, _TOKEN_SOURCE, _MINT_ATTEMPTED
    if _NO_MINT:
        fail("Token renewal required but --no-mint forbids minting")
    if _MINT_ATTEMPTED:
        fail("Linear token rejected after one renewal; refusing another mint")
    _MINT_ATTEMPTED = True
    original, values = _read_env(_TOKEN_PATH)
    for key in (APP_ID_KEY, APP_SECRET_KEY):
        if not values.get(key):
            fail("Missing " + key + " in credential file", 2)
    payload = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": values[APP_ID_KEY],
        "client_secret": values[APP_SECRET_KEY],
        "scope": OAUTH_SCOPE,
    }).encode()
    req = urllib.request.Request(
        OAUTH_API, data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        exc.close()
        fail("Linear OAuth HTTP %s; nothing persisted" % exc.code)
    except Exception:
        fail("Linear OAuth request failed; nothing persisted")
    if not isinstance(result, dict):
        fail("Linear OAuth returned an invalid response; nothing persisted")
    fresh = result.get("access_token")
    if not isinstance(fresh, str) or not fresh or any(c.isspace() for c in fresh):
        fail("Linear OAuth returned an invalid access token; nothing persisted")
    _SECRETS.add(fresh)
    ttl = result.get("expires_in")
    try:
        if isinstance(ttl, bool) or not isinstance(ttl, (int, str)) or int(ttl) <= 0:
            raise ValueError()
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(ttl))).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        fail("Linear OAuth returned an invalid expiry; nothing persisted")
    try:
        viewer = _verified_viewer(_request("{ viewer { id name email } }", None, fresh))
    except urllib.error.HTTPError as exc:
        exc.close()
        fail("Renewed token verification HTTP %s; nothing persisted" % exc.code)
    except Exception:
        fail("Renewed token verification failed; nothing persisted")
    try:
        _persist_token(_TOKEN_PATH, original, fresh, expires_at)
    except OSError:
        fail("Could not atomically persist renewed credential (file changed during renewal or unavailable); old file retained")
    _TOKEN = fresh
    _TOKEN_SOURCE = "minted"
    _propagate_token(fresh, expires_at)
    return viewer


def gq(query, variables=None):
    """Retry the original request once on HTTP 401, never recursively mint."""
    if not _TOKEN:
        if _NO_MINT:
            fail(TOKEN_KEY + " absent or empty", 2)
        mint()
    try:
        return _request(query, variables, _TOKEN)
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code != 401 or _NO_MINT or _MINT_ATTEMPTED:
            fail("Linear API HTTP %s (token invalid or expired)" % exc.code)
        mint()
        try:
            return _request(query, variables, _TOKEN)
        except urllib.error.HTTPError as retry_error:
            retry_error.close()
            fail("Linear API HTTP %s (token invalid or expired after renewal)" % retry_error.code)
        except Exception:
            fail("Linear API request failed after renewal")
    except Exception:
        fail("Linear API request failed")


def ensure_fresh_token():
    """Renew if expiry is absent/invalid or at most five days away, then verify."""
    _, values = _read_env(_TOKEN_PATH)
    expiry = None
    # An expiry stored for a different token cannot validate an env-only override.
    if values.get(TOKEN_KEY) == _TOKEN and values.get(EXPIRY_KEY):
        try:
            expiry = datetime.fromisoformat(values[EXPIRY_KEY].replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = None
        except ValueError:
            pass
    if not _TOKEN or expiry is None or expiry <= datetime.now(timezone.utc) + RENEW_WINDOW:
        return mint()
    return get_viewer()


def token_metadata():
    return {"token_source": _TOKEN_SOURCE, "token_path": _TOKEN_PATH}


def cmd_remint(_args):
    viewer = ensure_fresh_token()
    _, values = _read_env(_TOKEN_PATH)
    emit({"viewer": viewer, **token_metadata(), "expires_at": values.get(EXPIRY_KEY)})


def emit(data):
    def clean(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    out = {"ok": True}
    out.update(data)
    sys.stdout.write(json.dumps(clean(out), indent=2) + "\n")


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

COMMENT_NODE_FIELDS = """
  id
  createdAt
  body
  user { id name email }
  botActor { id name }
"""

COMMENT_FIELDS = """
  comments(first: 100) {
    nodes { %s }
  }
""" % COMMENT_NODE_FIELDS


def get_viewer():
    return _verified_viewer(gq("{ viewer { id name email } }"))


def resolve_issue(identifier):
    data = gq(
        "query($id: String!) { issue(id: $id) { %s } }" % ISSUE_FIELDS,
        {"id": identifier},
    )
    issue = data.get("issue")
    if not issue:
        fail("issue %s not found" % identifier)
    return issue


def get_all_comments(issue_id):
    """Fetch every comment page so proof and approval artifacts cannot age past page 1."""
    query = """
    query($id: String!, $after: String) {
      issue(id: $id) {
        comments(first: 100, after: $after) {
          nodes { %s }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """ % COMMENT_NODE_FIELDS
    nodes = []
    cursor = None
    while True:
        issue = gq(query, {"id": issue_id, "after": cursor}).get("issue")
        if not issue:
            fail("issue %s not found while paging comments" % issue_id)
        connection = issue["comments"]
        nodes.extend(connection["nodes"])
        page = connection["pageInfo"]
        if not page.get("hasNextPage"):
            return nodes
        cursor = page.get("endCursor")
        if not cursor:
            fail("comment pagination for %s reported another page without an end cursor" % issue_id)


def cmd_viewer(_args):
    viewer = get_viewer()
    emit({"viewer": viewer, **token_metadata()})


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
    query = "query($id: String!) { issue(id: $id) { %s description } }" % ISSUE_FIELDS
    data = gq(query, {"id": args.identifier})
    issue = data.get("issue")
    if not issue:
        fail("issue %s not found" % args.identifier)
    # Same shape as before ({"nodes": [...]}), but no longer truncated at 100 comments.
    issue["comments"] = {"nodes": get_all_comments(issue["id"])}
    emit({"viewer": get_viewer(), "issue": issue})


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
    parser = argparse.ArgumentParser(prog="linear_bot.py", description=__doc__)
    parser.add_argument("--no-mint", action="store_true", help="never mint or write credentials")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("viewer", help="fetch the bot's own Linear identity")
    sub.add_parser("remint", help="renew if expiry is absent/within five days, then verify")

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

    for child in sub.choices.values():
        child.add_argument("--no-mint", action="store_true", default=argparse.SUPPRESS,
                           help="never mint or write credentials")
    args = parser.parse_args()
    initialize(args.no_mint)

    {
        "viewer": cmd_viewer,
        "remint": cmd_remint,
        "queue": cmd_queue,
        "issue": cmd_issue,
        "comment": cmd_comment,
        "claim": cmd_claim,
        "unclaim": cmd_unclaim,
        "state": cmd_state,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
