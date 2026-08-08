#!/usr/bin/env python3
"""Wolftac Factory integrate-worker preflight: identity + capability assertions.

REQUIRED first step of every /wolftac-integrate invocation (see the integrate
SKILL.md in each port, and deploy/bot-runtime.md for the isolated-profile
recipe). Exits non-zero with a specific message and ZERO side effects when any
assertion fails; the worker must then stop without claiming anything.

Assertions:
  1. Bot credentials present in the profile env (token, or app id+secret to mint).
  2. The executing runtime IS this profile: HERMES_HOME must be the scanned
     profile root. This is a positive attestation, not a credential blacklist —
     an agent session reaching Linear over MCP holds no Linear env var at all,
     so only "you are the isolated profile" is a gate it cannot pass.
  3. No Linear-capable transport wired anywhere in the profile: EVERY *.yaml/*.yml
     directly under the profile root is scanned, and ANY occurrence of the tokens
     `linear` or `composio` (quoted, inline, block, or commented — fail-closed by
     design) is a violation. Composio counts because it can proxy Linear tools.
  4. The bot token authenticates and `viewer` resolves to the pinned factory bot
     id. Re-mint via client_credentials happens only when needed, and a freshly
     minted token is VERIFIED to be the factory bot BEFORE it is persisted.

The expected bot id is pinned in this file and is deliberately NOT overridable
by flag or environment — a caller that could supply its own id could defeat the
gate. Rotating the bot identity is a reviewed change to this constant.

Exit codes: 0 ok · 2 missing/unmintable credentials · 3 not the isolated runtime,
or a Linear-capable transport wired in the profile · 4 viewer identity mismatch ·
5 profile root/env unreadable.

Because assertion 2 is strict, running this by hand from an operator shell or an
agent session correctly exits 3 — that IS the gate working. The green path is
observed in the worker's own cron output, not from a human terminal.

Credential VALUES are never printed. Output is paths, ids, names, and counts only.

Honest residual trust: the scan proves the profile's on-disk config wires no
Linear-capable transport; it cannot prove what an arbitrary process could do
with credentials found elsewhere on the host. Isolation is by profile
construction — this preflight verifies that construction on every tick, and the
Linear comment audit trail (author ids) verifies conduct after the fact.
"""
import argparse, glob, json, os, re, sys, urllib.request, urllib.parse

EXPECTED_BOT_ID = "b8fece8e-8ec5-4125-b4b2-d7fa882db137"  # Wolftac Factory Bot — pinned, not overridable
FORBIDDEN_TOKENS = re.compile(r"\b(linear|composio)\b", re.I)

def lf(t): return t.replace("\r\n", "\n").replace("\r", "\n")

def load_env(path):
    try:
        txt = lf(open(path, encoding="utf-8").read())
    except OSError as e:
        print(f"PREFLIGHT FAIL (5): cannot read profile env {path}: {e.strerror}")
        sys.exit(5)
    def get(k):
        m = re.search(rf"^{k}=(.*)$", txt, re.M)
        return m.group(1).strip() if m else ""
    return txt, get

def scan_profile_configs(root):
    """Fail-closed capability scan: any linear/composio token in any yaml under root."""
    files = sorted(glob.glob(os.path.join(root, "*.yaml")) + glob.glob(os.path.join(root, "*.yml")))
    for p in files:
        try:
            content = lf(open(p, encoding="utf-8").read())
        except OSError as e:
            print(f"PREFLIGHT FAIL (5): cannot read profile config {p}: {e.strerror}")
            sys.exit(5)
        m = FORBIDDEN_TOKENS.search(content)
        if m:
            print(f"PREFLIGHT FAIL (3): {p} contains the token '{m.group(0)}' — a Linear-capable "
                  "transport (direct or Composio-proxied) may be wired into this profile; the scan is "
                  "fail-closed (quoted/inline/commented forms all count). This runtime is NOT "
                  "capability-isolated; claim nothing")
            sys.exit(3)
    print(f"capability check: scanned profile root {root} — {len(files)} yaml file(s) "
          f"({', '.join(os.path.basename(f) for f in files) or 'none'}); no Linear-capable transport tokens — OK")

def _samepath(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.realpath(a).rstrip("\\/")) == \
               os.path.normcase(os.path.realpath(b).rstrip("\\/"))


def check_executing_runtime(root):
    """Assert the process running this preflight IS the isolated profile.

    Enumerating every way a human Linear path could exist is impossible: the
    original check inspected only LINEAR_API_KEY / LINEAR_OAUTH_TOKEN /
    COMPOSIO_API_KEY, so an agent session reaching Linear over MCP — which
    holds no such env var — passed a gate it should have failed (test-plan
    step 3). A blacklist cannot cover transports it has never heard of.

    So attest positively instead: HERMES_HOME must BE this profile's root.
    Cron is per-profile by design (hermes cron/scheduler.py::_get_hermes_home —
    "a profile's jobs are stored AND executed under that profile's home"), so
    the integrate worker satisfies this, while an operator shell or an agent
    session under the default profile does not, whatever credentials it holds.
    HERMES_HOME is one of Hermes' explicitly global env vars
    (agent/secret_scope.py::_GLOBAL_ENV_EXACT), so it is meaningful in
    os.environ even in a multiplex gateway, unlike per-profile secrets.

    The credential blacklist is kept as defense in depth: it catches a
    mis-provisioned profile whose .env forgot the blank overrides.
    """
    home = os.environ.get("HERMES_HOME", "").strip()
    if not home:
        print("PREFLIGHT FAIL (3): the executing process has no HERMES_HOME, so it was not launched "
              "under the isolated Hermes profile (operator shell, agent session, or bare process). "
              "This runtime may hold a human Linear toolset this check cannot see; claim nothing. "
              "Run the integrate worker under the factory-integrate profile — see deploy/bot-runtime.md")
        sys.exit(3)
    if not _samepath(home, root):
        print(f"PREFLIGHT FAIL (3): the executing runtime's HERMES_HOME is {home}, not the isolated "
              f"profile root {root}. A runtime scoped to a different profile can hold that profile's "
              "Linear-capable transports (MCP included, which carry no env credential and are "
              "invisible to a credential scan); claim nothing")
        sys.exit(3)
    for var in ("LINEAR_API_KEY", "LINEAR_OAUTH_TOKEN", "COMPOSIO_API_KEY"):
        if os.environ.get(var, "").strip():
            print(f"PREFLIGHT FAIL (3): the executing process environment carries a non-empty {var} — "
                  "the runtime that launched this preflight retains a human/default Linear-capable "
                  "credential and is NOT capability-isolated; claim nothing. (The isolated profile's "
                  ".env must blank-override inherited credentials; see deploy/bot-runtime.md.)")
            sys.exit(3)
    print(f"executing-runtime check: HERMES_HOME is the isolated profile root ({home}); no "
          "human/default Linear-capable credentials in the process environment — OK")


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

def mint(app_id, secret):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": app_id,
        "client_secret": secret, "scope": "read,write,app:assignable,app:mentionable"}).encode()
    req = urllib.request.Request(
        "https://api.linear.app/oauth/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        resp = json.loads(urllib.request.urlopen(req).read())
        return resp.get("access_token"), resp.get("expires_in")
    except Exception:
        return None, None

def persist_token(env_path, env_txt, token):
    if re.search(r"^LINEAR_FACTORY_BOT_TOKEN=", env_txt, re.M):
        env_txt = re.sub(r"^LINEAR_FACTORY_BOT_TOKEN=.*$",
                         "LINEAR_FACTORY_BOT_TOKEN=" + token, env_txt, flags=re.M)
    else:
        env_txt += "\nLINEAR_FACTORY_BOT_TOKEN=" + token + "\n"
    # Atomic persist: write a sibling temp file, then os.replace — an interrupted
    # write can never leave a truncated/corrupt .env behind.
    tmp = env_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(env_txt)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, env_path)

def main():
    ap = argparse.ArgumentParser()
    default_root = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes",
                                "profiles", "factory-integrate")
    ap.add_argument("--profile-root", default=default_root,
                    help="Root of the isolated worker profile (scanned root and its .env are "
                         "both reported in output so the audit trail shows what was checked)")
    a = ap.parse_args()

    root = os.path.abspath(a.profile_root)
    if not os.path.isdir(root):
        print(f"PREFLIGHT FAIL (5): profile root {root} does not exist")
        sys.exit(5)
    env_path = os.path.join(root, ".env")
    env_txt, get = load_env(env_path)
    token = get("LINEAR_FACTORY_BOT_TOKEN")
    app_id, secret = get("LINEAR_FACTORY_APP_ID"), get("LINEAR_FACTORY_APP_SECRET")
    if not token and not (app_id and secret):
        print(f"PREFLIGHT FAIL (2): {env_path} has no bot token and no app credentials to mint one — "
              "fail closed, claim nothing")
        sys.exit(2)

    scan_profile_configs(root)

    check_executing_runtime(root)

    v = viewer(token) if token else None
    if v is None:
        if not (app_id and secret):
            print("PREFLIGHT FAIL (2): bot token invalid and no app credentials to re-mint — fail closed")
            sys.exit(2)
        fresh, expires = mint(app_id, secret)
        if fresh is None:
            print("PREFLIGHT FAIL (2): token re-mint failed — fail closed, claim nothing")
            sys.exit(2)
        v = viewer(fresh)
        if v is None:
            print("PREFLIGHT FAIL (2): re-minted token did not authenticate — fail closed, nothing persisted")
            sys.exit(2)
        if v.get("id") != EXPECTED_BOT_ID:
            print(f"PREFLIGHT FAIL (4): re-minted token resolves to {v.get('name')} ({v.get('id')}), "
                  f"not the pinned factory bot {EXPECTED_BOT_ID} — nothing persisted, claim nothing")
            sys.exit(4)
        persist_token(env_path, env_txt, fresh)
        print(f"token re-minted via client_credentials (expires_in: {expires}), viewer-verified as the "
              "factory bot, then stored")

    if v.get("id") != EXPECTED_BOT_ID:
        print(f"PREFLIGHT FAIL (4): viewer is {v.get('name')} ({v.get('id')}), expected pinned factory "
              f"bot {EXPECTED_BOT_ID} — this runtime is not the bot; claim nothing")
        sys.exit(4)

    print(f"PREFLIGHT OK: profile {root}; viewer = {v.get('name')} ({v.get('id')}); "
          "no Linear-capable transport wired in profile configs")
    sys.exit(0)

if __name__ == "__main__":
    main()
