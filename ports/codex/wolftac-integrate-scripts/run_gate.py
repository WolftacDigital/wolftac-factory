#!/usr/bin/env python3
"""Run an allowlisted repository's proof gate with a minimal environment.

This is NOT a filesystem/network sandbox: the child has the operator's filesystem
permissions and can read files on disk. Redaction covers known values/token shapes,
not arbitrary encoded secrets. No credentials are used for API calls by this helper.
"""
import argparse
import json
import math
import ntpath
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time

ALLOW_ENV = frozenset("""PATH PATHEXT SYSTEMROOT SYSTEMDRIVE WINDIR COMSPEC TEMP TMP
TMPDIR HOME USERPROFILE HOMEDRIVE HOMEPATH APPDATA LOCALAPPDATA PROGRAMDATA
PROGRAMFILES PROGRAMFILES(X86) PROGRAMW6432 COMMONPROGRAMFILES COMMONPROGRAMFILES(X86)
COMMONPROGRAMW6432 PSMODULEPATH USERNAME USERDOMAIN COMPUTERNAME OS PROCESSOR_ARCHITECTURE
NUMBER_OF_PROCESSORS LANG LC_ALL LC_CTYPE TERM TZ JAVA_HOME GOROOT GOPATH GOCACHE
CARGO_HOME RUSTUP_HOME NVM_HOME NVM_SYMLINK PNPM_HOME DOTNET_ROOT VIRTUAL_ENV
CONDA_PREFIX PYTHONIOENCODING PYTHONUTF8""".split())
DENY_ENV = re.compile(
    r"TOKEN|SECRET|PASSW|CREDENTIAL|PRIVATE|AUTH|COOKIE|SESSION|API_?KEY|_KEY$|^KEY$",
    re.I,
)
TOKEN_SHAPES = (
    r"lin_(?:oauth|api)_[A-Za-z0-9]+|xox[abposr]-[A-Za-z0-9-]+|"
    r"xapp-[A-Za-z0-9-]+|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}"
)


def env_get(env, name):
    return next((v for k, v in env.items() if k.upper() == name), None)


def child_environment(parent, additions=()):
    env = {k: v for k, v in parent.items()
           if k.upper() in ALLOW_ENV and not DENY_ENV.search(k)}
    for item in additions:
        name, sep, value = item.partition("=")
        if (not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                or DENY_ENV.search(name) or name.upper() == "HERMES_HOME"
                or "\x00" in value):
            raise ValueError("invalid or forbidden --set-env name/value")
        # Names compare case-insensitively even on POSIX.
        env = {k: v for k, v in env.items() if k.upper() != name.upper()}
        env[name] = value
    return env


class Redactor:
    def __init__(self, parent):
        values = {v for k, v in parent.items() if DENY_ENV.search(k) and len(v) >= 8}
        home = env_get(parent, "HERMES_HOME")
        local = env_get(parent, "LOCALAPPDATA")
        path = Path(home) / ".env" if home else Path(local) / "hermes/.env" if local else None
        if path is not None:
            try:
                # No interpolation, shell execution, or credential transport.
                for line in path.read_text(encoding="utf-8-sig").splitlines():
                    line = line.strip()
                    if line.startswith("export "):
                        line = line[7:].lstrip()
                    key, sep, value = line.partition("=")
                    if not sep or not DENY_ENV.search(key.strip()) or key.startswith("#"):
                        continue
                    value = value.strip()
                    if value[:1] in ("'", '"') and value[-1:] == value[:1]:
                        value = value[1:-1]
                    else:
                        value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
                    if len(value) >= 8:
                        values.add(value)
            except FileNotFoundError:
                pass
            # Other read errors fail closed; do not run with incomplete redaction.
        literals = [re.escape(v) for v in sorted(values, key=len, reverse=True)]
        self.pattern = re.compile("|".join(literals + [TOKEN_SHAPES]))
        self.count = 0

    def clean(self, text):
        text, count = self.pattern.subn("<redacted>", text)
        self.count += count
        return text


def normalized_path(value):
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def find_allowlist(parent, script=__file__):
    override = env_get(parent, "FACTORY_REPO_ALLOWLIST")
    if override is not None:
        return Path(override)
    for directory in Path(script).resolve().parents:
        candidate = directory / "deploy/repo-allowlist.json"
        if candidate.is_file():
            return candidate
    for root in ("D:/Wolftac/.factory-live", "D:/Wolftac/Factory"):
        candidate = Path(root) / "deploy/repo-allowlist.json"
        if candidate.is_file():
            return candidate
    raise ValueError("allowlist unavailable")


def repo_allowed(repo, parent):
    try:
        data = json.loads(find_allowlist(parent).read_text(encoding="utf-8-sig"))
        if (not isinstance(data, dict) or type(data.get("version")) is not int
                or data["version"] != 1 or not isinstance(data.get("repos"), list)):
            raise ValueError("invalid allowlist schema")
        paths = []
        for row in data["repos"]:
            if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                    or not row["path"] or "\x00" in row["path"]
                    or not (os.path.isabs(row["path"]) or ntpath.isabs(row["path"]))
                    or "github" not in row
                    or (row["github"] is not None and not isinstance(row["github"], str))):
                raise ValueError("invalid allowlist entry")
            paths.append(normalized_path(row["path"]))
    except (OSError, ValueError, TypeError):
        return 4
    return 0 if os.path.isabs(repo) and normalized_path(repo) in paths else 3


def common_dir(path, parent):
    env = child_environment(parent)
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false")
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--git-common-dir"],
            env=env, capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode or not result.stdout.strip():
            return None
        directory = result.stdout.strip()
        if not os.path.isabs(directory):
            directory = os.path.join(path, directory)
        return normalized_path(os.path.realpath(directory))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def kill_tree(proc, env):
    """Only terminate the gate group created by this runner, never broad classes."""
    if os.name == "nt":
        tool = Path(env_get(env, "SYSTEMROOT") or r"C:\Windows") / "System32/taskkill.exe"
        result = subprocess.run([str(tool), "/T", "/F", "/PID", str(proc.pid)],
                                env=env, capture_output=True, timeout=8, check=False)
        if result.returncode and proc.poll() is None:
            raise OSError("gate tree termination failed")
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def execute(command, cwd, env, seconds, redactor):
    start = time.monotonic()
    result = {"exit_code": None, "timed_out": False, "duration_s": 0, "redacted": 0}
    try:
        proc = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
    except (OSError, ValueError) as exc:
        result["error"] = redactor.clean(str(exc))
        result["duration_s"] = round(time.monotonic() - start, 3)
        result["redacted"] = redactor.count
        print("GATE-RESULT " + json.dumps(result), flush=True)
        return 1

    lines = queue.Queue(maxsize=128)

    def read_output():
        assert proc.stdout is not None
        try:
            for line in iter(proc.stdout.readline, b""):
                lines.put(line)
        finally:
            proc.stdout.close()
            lines.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    eof = False
    deadline = start + seconds
    cleanup_deadline = None
    while not eof or proc.poll() is None:
        now = time.monotonic()
        if not result["timed_out"] and now >= deadline:
            result["timed_out"] = True
            try:
                kill_tree(proc, env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result["error"] = redactor.clean(str(exc))
            cleanup_deadline = time.monotonic() + 2
        if cleanup_deadline is not None and time.monotonic() >= cleanup_deadline:
            if proc.poll() is None or not eof:
                result["error"] = "gate tree/output did not close after termination"
            break
        try:
            line = lines.get(timeout=0.05)
        except queue.Empty:
            continue
        if line is None:
            eof = True
        else:
            # Line framing retains secrets split across multiple writes. A missing
            # final newline must not swallow the machine-readable result trailer.
            text = redactor.clean(line.decode("utf-8", errors="replace").replace("\r\n", "\n"))
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
            sys.stdout.flush()
    result.update(exit_code=proc.poll(), duration_s=round(time.monotonic() - start, 3),
                  redacted=redactor.count)
    print("GATE-RESULT " + json.dumps(result), flush=True)
    return 124 if result["timed_out"] else 0 if result["exit_code"] == 0 else 1


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default errors echo arbitrary argv (including rejected values).
        self.exit(2, "usage error: invalid gate runner arguments\n")


def main(argv=None):
    parser = SafeParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("check-repo")
    check.add_argument("--repo-path", required=True)
    run = sub.add_parser("run")
    run.add_argument("--repo-path", required=True)
    run.add_argument("--cwd", required=True)
    run.add_argument("--timeout-sec", type=float, default=900)
    run.add_argument("--set-env", action="append", default=[])
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    # Gate output is decoded as UTF-8; a piped stdout's legacy code page must not
    # turn a passing gate that prints a non-ASCII character into a runner crash.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parent = os.environ
    command = []
    env = {}
    if args.action == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else []
        if not command or not math.isfinite(args.timeout_sec) or args.timeout_sec <= 0:
            parser.error("invalid command/timeout")
        try:
            env = child_environment(parent, args.set_env)
        except ValueError:
            parser.error("invalid environment override")
    status = repo_allowed(args.repo_path, parent)
    if args.action == "check-repo" or status:
        print(json.dumps({"allowed": status == 0, "exit_code": status}), flush=True)
        return status
    repo_dir = common_dir(args.repo_path, parent)
    if not repo_dir or repo_dir != common_dir(args.cwd, parent):
        print(json.dumps({"allowed": False, "exit_code": 3, "error": "foreign or invalid worktree"}))
        return 3
    try:
        redactor = Redactor(parent)
    except (OSError, UnicodeError, ValueError):
        print('GATE-RESULT {"exit_code": null, "timed_out": false, "duration_s": 0, '
              '"redacted": 0, "error": "redaction source unavailable"}')
        return 1
    return execute(command, args.cwd, env, args.timeout_sec, redactor)


if __name__ == "__main__":
    sys.exit(main())
