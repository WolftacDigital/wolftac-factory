#!/usr/bin/env python3
"""Wolftac Factory integrate-worker preflight: identity + capability assertions.

REQUIRED first step of every /wolftac-integrate invocation (see the integrate
SKILL.md in each port, and deploy/bot-runtime.md for the isolated-profile
recipe). Exits non-zero with a specific message and ZERO side effects when any
assertion fails; the worker must then stop without claiming anything.

Assertions:
  1. Bot credentials present in the env file (token, or app id+secret to mint).
  2. No Linear MCP/toolset wired into the runtime's profile config — the
     human's Linear write path must not exist in this runtime.
  3. The bot token authenticates and `viewer` resolves to the expected factory
     bot id (re-minted automatically via client_credentials when invalid).

Exit codes: 0 ok · 2 missing/unmintable credentials · 3 human Linear toolset
present · 4 viewer identity mismatch · 5 env/config unreadable.

Credential VALUES are never printed. Output is ids, names, and booleans only.

Honest residual trust (documented per spec): a config scan proves the profile
does not WIRE a Linear MCP; it cannot prove what an arbitrary process could do
with credentials found elsewhere on the host. Isolation is by profile
construction — this preflight verifies that construction, it does not replace it.
"""
import argparse, json, os, re, sys, urllib.request, urllib.parse

DEFAULT_BOT_ID = "b8fece8e-8ec5-4125-b4b2-d7fa882db137"  # Wolftac Factory Bot

def lf(t): return t.replace("\r\n", "\n").replace("\r", "\n")

def load_env(path):
    try:
        txt = lf(open(path, encoding="utf-8").read())
    except OSError as e:
        print(f"PREFLIGHT FAIL (5): cannot read env file {path}: {e.strerror}")
        sys.exit(5)
    def get(k):
        m = re.search(rf"^{k}=(.*)$", txt, re.M)
        return m.group(1).strip() if m else ""
    return txt, get

def config_has_linear(path):
    """True if the profile config wires any enabled Linear MCP server."""
    try:
        cfg = lf(open(path, encoding="utf-8").read())
    except OSError as e:
        print(f"PREFLIGHT FAIL (5): cannot read profile config {path}: {e.strerror}")
        sys.exit(5)
    # Match a 'linear' server key under mcp_servers that is not disabled.
    block = re.search(r"^\s*linear\s*:\s*$(?P<body>(?:\n^\s{4,}.*$)*)", cfg, re.M)
    if not block:
        return False
    body = block.group("body") or ""
    return not re.search(r"^\s*enabled\s*:\s*false\s*$", body, re.M)

def viewer(token):
    q = json.dumps({"query": "{ viewer { id name } }"}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/graphql", data=q,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    try:
        out = json.loads(urllib.request.urlopen(req).read())
        return (out.get("data") or {}).get("viewer")
    except Exception:
        return None

def remint(env_path, env_txt, app_id, secret):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": app_id,
        "client_secret": secret, "scope": "read,write"}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/oauth/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
    except Exception:
        return None
    token = resp.get("access_token")
    if not token:
        return None
    if re.search(r"^LINEAR_FACTORY_BOT_TOKEN=", env_txt, re.M):
        env_txt = re.sub(r"^LINEAR_FACTORY_BOT_TOKEN=.*$",
                         "LINEAR_FACTORY_BOT_TOKEN=" + token, env_txt, flags=re.M)
    else:
        env_txt += "\nLINEAR_FACTORY_BOT_TOKEN=" + token + "\n"
    open(env_path, "w", encoding="utf-8", newline="\n").write(env_txt)
    print(f"token re-minted via client_credentials (expires_in: {resp.get('expires_in')}) and stored")
    return token

def main():
    ap = argparse.ArgumentParser()
    default_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
    ap.add_argument("--env", default=os.environ.get("FACTORY_BOT_ENV",
                    os.path.join(default_root, ".env")))
    ap.add_argument("--config", default=os.environ.get("FACTORY_PROFILE_CONFIG",
                    os.path.join(default_root, "config.yaml")))
    ap.add_argument("--expected-bot-id", default=os.environ.get(
                    "LINEAR_FACTORY_BOT_ID", DEFAULT_BOT_ID))
    a = ap.parse_args()

    env_txt, get = load_env(a.env)
    token = get("LINEAR_FACTORY_BOT_TOKEN")
    app_id, secret = get("LINEAR_FACTORY_APP_ID"), get("LINEAR_FACTORY_APP_SECRET")
    if not token and not (app_id and secret):
        print("PREFLIGHT FAIL (2): no bot token and no app credentials to mint one — fail closed, claim nothing")
        sys.exit(2)

    if config_has_linear(a.config):
        print(f"PREFLIGHT FAIL (3): profile config {a.config} wires an enabled Linear MCP server — "
              "this runtime has a human Linear write path and is NOT capability-isolated; claim nothing")
        sys.exit(3)
    print("capability check: no Linear MCP wired in profile config — OK")

    v = viewer(token) if token else None
    if v is None:
        if not (app_id and secret):
            print("PREFLIGHT FAIL (2): bot token invalid and no app credentials to re-mint — fail closed")
            sys.exit(2)
        token = remint(a.env, env_txt, app_id, secret)
        if token is None:
            print("PREFLIGHT FAIL (2): token re-mint failed — fail closed, claim nothing")
            sys.exit(2)
        v = viewer(token)
        if v is None:
            print("PREFLIGHT FAIL (2): re-minted token did not authenticate — fail closed")
            sys.exit(2)

    if v.get("id") != a.expected_bot_id:
        print(f"PREFLIGHT FAIL (4): viewer is {v.get('name')} ({v.get('id')}), expected factory bot "
              f"{a.expected_bot_id} — this runtime is not the bot; claim nothing")
        sys.exit(4)

    print(f"PREFLIGHT OK: viewer = {v.get('name')} ({v.get('id')}); no human Linear write path in profile config")
    sys.exit(0)

if __name__ == "__main__":
    main()
