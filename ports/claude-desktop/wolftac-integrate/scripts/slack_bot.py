#!/usr/bin/env python3
"""Bot-authenticated Slack client for the /wolftac-integrate approval lane.

Resolves SLACK_FACTORY_BOT_TOKEN and deploy/approval-policy.json itself, so a
run working inside ANY target repo finds both. Before this helper the lookup
was improvised per run: it worked from the factory repo and reported the token
"absent" from other repos (WD-363, WD-365 on 2026-10-26), because the relative
policy path and the agent's ad-hoc .env read both depended on the cwd.

The token never appears on a command line or in output. Message text is read
from a file or stdin and sent as UTF-8 JSON. Every subcommand prints JSON to
stdout and exits non-zero on failure.

Usage:
  python slack_bot.py check
  python slack_bot.py post --body-file - [--thread-ts TS]
  python slack_bot.py reactions TS
  python slack_bot.py delete TS

Exit codes: 0 ok, 1 Slack/API failure, 2 lane unavailable (no token, no
policy, or a rejected token) -- disclose "Slack lane unavailable" and use the
typed-comment gate.
"""

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

API = "https://slack.com/api/"
TOKEN_KEY = "SLACK_FACTORY_BOT_TOKEN"
POLICY_REL = os.path.join("deploy", "approval-policy.json")
POLICY_FALLBACKS = [r"D:\Wolftac\.factory-live", r"D:\Wolftac\Factory"]
AUTH_ERRORS = ("invalid_auth", "not_authed", "token_expired", "token_revoked", "account_inactive")

_TOKEN = None


def redact(text):
    text = str(text)
    if _TOKEN:
        text = text.replace(_TOKEN, "<redacted>")
    return text


def fail(msg, code=1):
    print(json.dumps({"ok": False, "error": redact(msg)}), file=sys.stderr)
    sys.exit(code)


def env_files():
    # Same precedence as linear_bot.py: the executing profile's .env first,
    # then the default Hermes home.
    files = []
    home = os.environ.get("HERMES_HOME", "").strip()
    if home:
        files.append(os.path.join(home, ".env"))
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")
    files.append(os.path.join(local, "hermes", ".env"))
    return files


def _token_from_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(TOKEN_KEY + "="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return None


def load_token():
    """Profile .env wins inside a Hermes profile; otherwise process env, then
    the Hermes .env files. Returns (token, source); source is a label only."""
    files = env_files()
    if os.environ.get("HERMES_HOME", "").strip():
        val = _token_from_file(files[0])
        if val:
            return val, files[0]
    val = os.environ.get(TOKEN_KEY, "").strip()
    if val:
        return val, "process env"
    for path in files:
        val = _token_from_file(path)
        if val:
            return val, path
    return None, None


def find_policy():
    """FACTORY_APPROVAL_POLICY, then deploy/approval-policy.json above this
    script's real location (junctioned skills resolve into the factory repo),
    then the known factory checkouts. Never the cwd: runs work in target repos."""
    explicit = os.environ.get("FACTORY_APPROVAL_POLICY", "").strip()
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    d = os.path.dirname(os.path.realpath(__file__))
    while True:
        cand = os.path.join(d, POLICY_REL)
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for root in POLICY_FALLBACKS:
        cand = os.path.join(root, POLICY_REL)
        if os.path.isfile(cand):
            return cand
    return None


def load_lane():
    global _TOKEN
    tok, source = load_token()
    policy_path = find_policy()
    missing = []
    if not tok:
        missing.append("%s not found (process env, %s)" % (TOKEN_KEY, ", ".join(env_files())))
    if not policy_path:
        missing.append("approval-policy.json not found")
    if missing:
        fail("Slack lane unavailable: " + "; ".join(missing), code=2)
    _TOKEN = tok
    with open(policy_path, "r", encoding="utf-8") as fh:
        policy = json.load(fh)
    return policy, policy_path, source


def call(method, payload=None, query=None):
    url = API + method
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", "Bearer " + _TOKEN)
    if data:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        fail("%s HTTP %s" % (method, e.code))
    except (urllib.error.URLError, OSError, ValueError) as e:
        fail("%s failed: %s" % (method, e))
    if not body.get("ok"):
        err = body.get("error", "unknown")
        fail("%s: %s" % (method, err), code=2 if err in AUTH_ERRORS else 1)
    return body


def identity(policy, sender):
    ids = policy.get("sender_identities", {})
    key = sender or "hermes@" + socket.gethostname()
    return ids.get(key) or policy.get("fallback_identity") or {}


def read_body(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def cmd_check(args):
    policy, policy_path, source = load_lane()
    auth = call("auth.test")
    print(json.dumps({"ok": True, "token_source": source, "policy": policy_path,
                      "channel": policy.get("slack_channel_id"), "bot_user": auth.get("user_id")}))


def cmd_post(args):
    policy, _, _ = load_lane()
    text = read_body(args.body_file).strip()
    if not text:
        fail("empty message body")
    channel = policy["slack_channel_id"]
    who = identity(policy, args.sender)
    payload = {"channel": channel, "text": text}
    for k in ("username", "icon_emoji"):
        if who.get(k):
            payload[k] = who[k]
    if args.thread_ts:
        payload["thread_ts"] = args.thread_ts
    res = call("chat.postMessage", payload)
    ts = res["ts"]
    if res.get("message", {}).get("text") != text:
        # Mangled text must not leave a second approval message around.
        call("chat.delete", {"channel": channel, "ts": ts})
        fail("posted text differed from the body; post deleted")
    link = call("chat.getPermalink", query={"channel": channel, "message_ts": ts})
    print(json.dumps({"ok": True, "ts": ts, "channel": channel,
                      "permalink": link.get("permalink"), "username": payload.get("username")}))


def cmd_reactions(args):
    policy, _, _ = load_lane()
    res = call("reactions.get", query={"channel": policy["slack_channel_id"],
                                       "timestamp": args.ts, "full": "true"})
    approver = policy.get("approver_slack_user_id")
    users = []
    for r in res.get("message", {}).get("reactions", []):
        if r.get("name") == "rocket":
            users = r.get("users", [])
    # Only the configured approver counts; other reactors are ignored silently.
    print(json.dumps({"ok": True, "ts": args.ts, "approved": bool(approver) and approver in users}))


def cmd_delete(args):
    policy, _, _ = load_lane()
    call("chat.delete", {"channel": policy["slack_channel_id"], "ts": args.ts})
    print(json.dumps({"ok": True, "deleted": args.ts}))


def main():
    p = argparse.ArgumentParser(description="Factory Slack approval-lane client")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sp = sub.add_parser("post")
    sp.add_argument("--body-file", required=True)
    sp.add_argument("--thread-ts")
    sp.add_argument("--sender", help="sender_identities key; default hermes@<hostname>")
    sp.set_defaults(fn=cmd_post)
    sr = sub.add_parser("reactions")
    sr.add_argument("ts")
    sr.set_defaults(fn=cmd_reactions)
    sd = sub.add_parser("delete")
    sd.add_argument("ts")
    sd.set_defaults(fn=cmd_delete)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
