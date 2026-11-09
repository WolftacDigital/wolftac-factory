"""WD-634: run real entry points with synthetic inputs, never a live profile.

python -B deploy/tests/linux_host_smoke.py --output <scratch>/proof.json
The JSON is OS-independent: exact lane lines and watchdog findings/exit codes.
Only the generated scratch-root spelling is normalized, not finding content.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import Message
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8", newline="\n")


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def isolated_env(root):
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "SYSTEMDRIVE", "LD_LIBRARY_PATH"}}
    for key in ("HOME", "USERPROFILE", "LOCALAPPDATA", "TMP", "TEMP", "TMPDIR",
                "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
        folder = root / key.lower()
        folder.mkdir(parents=True, exist_ok=True)
        env[key] = str(folder)
    env.update(HERMES_HOME=str(root / "hermes"), PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8", POWERSHELL_TELEMETRY_OPTOUT="1",
               POWERSHELL_UPDATECHECK="Off")
    return env


def issue(number, state, label):
    return {"identifier": f"WD-{number}", "state": {"name": state}, "assignee": None,
            "project": None, "priority": 2, "description": "Synthetic portability fixture",
            "updatedAt": "2030-01-01T00:00:00Z",
            "labels": {"nodes": [{"name": label}], "pageInfo": {"hasNextPage": False}},
            "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "inverseRelations": {"nodes": [], "pageInfo": {"hasNextPage": False}}}


def gatekeeper_child(case, root):
    """Only the clock and HTTP transport are replaced; real main parses argv."""
    def denied(*args, **kwargs):
        raise AssertionError("Network or process dispatch attempted in dry-run fixture")

    calls = []

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://api.linear.app/graphql"
        payload = json.loads(request.data)
        assert "mutation" not in payload["query"].lower()
        calls.append(payload["variables"].get("state", "audit"))
        if case == "unauthorized":
            raise urllib.error.HTTPError(request.full_url, 401, "fixture", Message(), io.BytesIO())
        state = payload["variables"].get("state")
        rows = []
        if case != "idle":
            rows = [i for i in [issue(9001, "Todo", "agentic"),
                               issue(9002, "Todo", "Wolftac CRM v2"),
                               issue(9003, "In Review", "Factory Queue"),
                               issue(9004, "Approved", "Factory Queue")]
                    if i["state"]["name"] == state]
        return io.BytesIO(json.dumps({"data": {"issues": {
            "nodes": rows, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}).encode())

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is not None else NOW.replace(tzinfo=None)

    argv = [str(ROOT / "deploy/factory-gatekeeper.py"), "--dry-run", "--no-discord",
            "--env-path", str(root / "fixture.env"), "--state-path", str(root / "state.json")]
    with patch.object(sys, "argv", argv), patch("datetime.datetime", FrozenDatetime), \
         patch.object(urllib.request, "urlopen", side_effect=fake_urlopen), \
         patch.object(socket.socket, "connect", side_effect=denied), \
         patch.object(socket.socket, "connect_ex", side_effect=denied), \
         patch.object(subprocess, "Popen", side_effect=denied):
        try:
            runpy.run_path(argv[0], run_name="__main__")
        except SystemExit as exc:
            assert exc.code in (None, 0), exc.code
    assert calls, "The real HTTP boundary must be exercised"


def gatekeeper_case(root, case, env):
    root.mkdir()
    (root / "fixture.env").write_text("LINEAR_FACTORY_BOT_TOKEN=synthetic-not-a-credential\n")
    state = {}
    lanes = {"build": ("builder", "9b7b5987bcd6"), "build-crm": ("builder", "1dd429fe03b2"),
             "review": ("reviewer", "8e5c37a23b91"), "integrate": ("integrator", "4e7a4d9c5fa4")}
    if case == "inflight":
        state["lanes"] = {"build": {"dispatched_at": "2029-12-31T23:55:00Z", "job_last_run_at": None}}
    write_json(root / "state.json", state)
    for profile in {p for p, _ in lanes.values()}:
        write_json(Path(env["HERMES_HOME"]) / "profiles" / profile / "cron/jobs.json",
                   {"jobs": [{"id": ident, "last_run_at": None} for p, ident in lanes.values() if p == profile]})
    before = digest_tree(root), digest_tree(Path(env["HERMES_HOME"]))
    run = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                          "--gatekeeper-child", case, "--root", str(root)],
                         env=env, capture_output=True, encoding="utf-8", timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    assert before == (digest_tree(root), digest_tree(Path(env["HERMES_HOME"]))), "Dry-run wrote state"
    text = run.stdout.strip()
    assert "synthetic-not-a-credential" not in text
    return text


def watchdog_case(root, case, env, home_mode="explicit"):
    root.mkdir()
    cron = root / "cron"
    cron.mkdir()
    (cron / "ticker_heartbeat").write_text("synthetic\n")
    job = {"id": "fixture-gk", "name": "Wolftac Factory Gatekeeper", "enabled": True,
           "paused": False, "last_status": "ok", "schedule": {"kind": "interval", "minutes": 30}}
    if case == "disabled":
        job["enabled"] = False
    elif case == "paused":
        job["paused"] = True
    elif case == "error":
        job.update(last_status="error", last_error="synthetic failure")
    write_json(cron / "jobs.json", {"jobs": [job]})
    if case == "auth":
        output = cron / "output/fixture-gk/tick.txt"
        output.parent.mkdir(parents=True)
        output.write_text("gatekeeper: build: linear-auth-failed (401) -> skipped")
    expiry = "not-a-date" if case == "expiry" else "2099-01-01T00:00:00Z"
    (root / ".env").write_text("SLACK_FACTORY_BOT_TOKEN=synthetic-not-a-credential\n"
                               f"LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT={expiry}\n")
    write_json(root / "policy.json", {"slack_channel_id": "fixture", "approver_slack_user_id": "fixture",
                                      "fallback_identity": {"username": "fixture", "icon_emoji": ":test_tube:"}})
    args = [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-File",
            str(ROOT / "deploy/factory-watchdog.ps1"), "-DryRun", "-SkipDrift", "-SkipLearn",
            "-PolicyPath", str(root / "policy.json"), "-SlackOutbox", str(root / "outbox")]
    child_env = env.copy()
    if home_mode == "explicit":
        args += ["-HermesHome", str(root)]
        # Check explicit parameter wins over a wrong environment default.
        child_env["HERMES_HOME"] = str(root / "missing")
    elif home_mode == "env":
        child_env["HERMES_HOME"] = str(root)
        child_env.pop("LOCALAPPDATA", None)
        child_env.pop("USERPROFILE", None)
    elif home_mode == "no-profile":
        # WD-634 rework: on Windows PowerShell's $HOME comes from USERPROFILE, so with
        # every profile variable unset the watchdog must still resolve from HERMES_HOME
        # alone, wherever the scratch dir sits (not only under ...\AppData\Local\hermes\).
        child_env["HERMES_HOME"] = str(root)
        for key in ("LOCALAPPDATA", "USERPROFILE", "HOME"):
            child_env.pop(key, None)
    elif home_mode == "windows-default":
        child_env.pop("HERMES_HOME", None)
        assert root.name == "hermes"
        child_env["LOCALAPPDATA"] = str(root.parent)
    elif home_mode == "home-default":
        child_env.pop("HERMES_HOME", None)
        child_env.pop("LOCALAPPDATA", None)
        child_env["HOME"] = child_env["USERPROFILE"] = str(root.parent)
    else:
        raise AssertionError(home_mode)
    before = digest_tree(root)
    run = subprocess.run(args, env=child_env, capture_output=True, encoding="utf-8", timeout=30)
    text = run.stdout + run.stderr
    assert "DRY RUN: no Slack post, no snapshot, no state written." in text, text
    assert "synthetic-not-a-credential" not in text
    assert before == digest_tree(root), "DryRun wrote state, backup or outbox"
    findings = [line.strip().replace(str(cron), "<scratch>/cron") for line in text.splitlines()
                if line.startswith("  - ")]
    assert run.returncode == (0 if case == "healthy" else 1), text
    assert len(findings) == (0 if case == "healthy" else 1), text
    return {"exit": run.returncode, "findings": findings}


def smoke(workspace):
    if not shutil.which("pwsh"):
        raise RuntimeError("PowerShell 7 is required, not a skipped portability pass")
    with tempfile.TemporaryDirectory(prefix="linux-host-", dir=workspace) as temp:
        root = Path(temp)
        env = isolated_env(root)
        result = {"gatekeeper": {}, "watchdog": {}}
        for case in ("idle", "ready", "inflight", "unauthorized"):
            result["gatekeeper"][case] = gatekeeper_case(root / ("gk-" + case), case, env)
        for case in ("healthy", "disabled", "paused", "error", "auth", "expiry"):
            result["watchdog"][case] = watchdog_case(root / ("wd-" + case), case, env)
        result["watchdog"]["environment-home"] = watchdog_case(root / "env-home", "healthy", env, "env")
        result["watchdog"]["no-profile-home"] = watchdog_case(root / "no-profile", "healthy", env, "no-profile")
        defaults = root / "legacy-default"
        defaults.mkdir()
        result["watchdog"]["legacy-default"] = watchdog_case(defaults / "hermes", "healthy", env, "windows-default")
        fallback = root / "home-default"
        fallback.mkdir()
        result["watchdog"]["home-default"] = watchdog_case(fallback / "hermes", "healthy", env, "home-default")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--gatekeeper-child", choices=("idle", "ready", "inflight", "unauthorized"))
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.gatekeeper_child:
        gatekeeper_child(args.gatekeeper_child, args.root)
        return
    if not args.output:
        parser.error("--output is required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = smoke(args.output.parent)
    # The expected outcomes are the same on every host; a shared failure cannot pass.
    expected = json.loads((Path(__file__).parent / "fixtures/linux-host-expected.json").read_text())
    assert result == expected, json.dumps(result, indent=2)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("PORTABILITY OK: 4 Gatekeeper cases and 10 watchdog cases; no dispatch, send or state write")


if __name__ == "__main__":
    main()
