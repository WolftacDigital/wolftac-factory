"""WD-574: real child processes, synthetic credentials, scratch git repos only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "canonical/wolftac-integrate/scripts/run_gate.py"
COPIES = [RUNNER] + [ROOT / p / "run_gate.py" for p in (
    "ports/hermes/wolftac-integrate/scripts",
    "plugins/wolftac-factory/skills/wolftac-integrate/scripts",
    "ports/codex/wolftac-integrate-scripts",
    "ports/claude-desktop/wolftac-integrate/scripts",
    "ports/hermes-factory-integrate/skills/wolftac-integrate/scripts",
)]
spec = importlib.util.spec_from_file_location("run_gate", RUNNER)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wd574-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        self.home = self.root / "fake-home"
        self.home.mkdir()
        self.policy = self.root / "allowlist.json"
        self.policy.write_text(json.dumps({"version": 1, "repos": [
            {"path": str(self.repo), "github": None}]}), encoding="utf-8")
        # Do not copy worker credentials or use any live Hermes file.
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR"}}
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home),
                        LOCALAPPDATA=str(self.home), HERMES_HOME=str(self.home),
                        FACTORY_REPO_ALLOWLIST=str(self.policy), GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE="1")
        self.secrets = ["SYNTHETIC_ONLY_" + str(n) + "_NOT_A_REAL_SECRET" for n in range(6)]
        for name, value in zip(("LINEAR_FACTORY_BOT_TOKEN", "LINEAR_FACTORY_APP_SECRET",
                                "SLACK_FACTORY_BOT_TOKEN", "OPENROUTER_API_KEY", "FAL_KEY",
                                "MY_PASSWORD"), self.secrets):
            self.env[name] = value
        self.env["NOT_LISTED"] = "1"
        self.git("init", str(self.repo))
        self.git("-C", str(self.repo), "-c", "user.name=Synthetic Fixture", "-c",
                 "user.email=fixture@example.invalid", "commit", "--allow-empty", "-m", "fixture")

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.env, capture_output=True,
                              text=True, timeout=15, check=True)

    def invoke(self, args, env=None, cwd=None):
        result = subprocess.run([sys.executable, str(RUNNER), *args],
                                env=self.env if env is None else env,
                                cwd=cwd or self.root, capture_output=True, text=True, timeout=20)
        # Uniform assertion covers success, refusal, usage errors, stderr and trailer.
        for secret in self.secrets:
            self.assertNotIn(secret, result.stdout + result.stderr)
        return result

    def run_gate(self, code, options=(), cwd=None, env=None):
        return self.invoke(["run", "--repo-path", str(self.repo), "--cwd", str(cwd or self.repo),
                            *options, "--", sys.executable, "-c", code], env=env)

    def trailer(self, result):
        trailers = [line for line in result.stdout.splitlines() if line.startswith("GATE-RESULT ")]
        self.assertEqual(len(trailers), 1, result.stdout)
        return json.loads(trailers[0].removeprefix("GATE-RESULT "))

    def test_minimal_environment(self):
        result = self.run_gate("import os,json; print(json.dumps(sorted(os.environ)))")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = {x.upper() for x in json.loads(result.stdout.splitlines()[0])}
        self.assertIn("PATH", names)
        for name in ("LINEAR_FACTORY_BOT_TOKEN", "LINEAR_FACTORY_APP_SECRET", "SLACK_FACTORY_BOT_TOKEN",
                     "OPENROUTER_API_KEY", "FAL_KEY", "MY_PASSWORD", "HERMES_HOME", "NOT_LISTED"):
            self.assertNotIn(name, names)
        self.assertFalse(self.trailer(result)["timed_out"])

    def test_override_and_denied_names_never_start(self):
        result = self.run_gate("import os; print(os.environ['CI'])", ["--set-env", "CI=1"])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[0], "1")
        marker = self.root / "started"
        code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        for name in ("GH_TOKEN", "my_password", "APIKEY", "API_KEY", "KEY", "SSH_KEY",
                     "CREDENTIAL_FILE", "AUTH", "COOKIE", "SESSION", "PRIVATE_DATA", "HERMES_HOME"):
            result = self.run_gate(code, ["--set-env", name + "=" + self.secrets[0]])
            self.assertEqual(result.returncode, 2)
            self.assertFalse(marker.exists())

    def test_case_insensitive_allowlist_and_deny_precedence(self):
        env = gate.child_environment({"pAtH": "safe-path", "NOT_LISTED": "no", "AUTH": "no"}, ["path=new"])
        self.assertEqual(env, {"path": "new"})
        with patch.object(gate, "ALLOW_ENV", frozenset({"AUTH", "PATH"})):
            self.assertEqual(gate.child_environment({"AUTH": "no", "PATH": "yes"}), {"PATH": "yes"})

    def test_file_secret_and_token_shape_redacted_exactly_twice(self):
        fake = "SCRATCH_FILE_ONLY_RANDOM_VALUE"
        token = "xoxb-SYNTHETIC-not-a-live-token"
        self.secrets.extend([fake, token])
        fixture = self.home / ".env"
        fixture.write_text("export APP_SECRET='" + fake + "'\n", encoding="utf-8")
        result = self.run_gate(f"from pathlib import Path; print(Path({str(fixture)!r}).read_text().split(chr(39))[1]); print({token!r})")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[:2], ["<redacted>", "<redacted>"])
        self.assertEqual(self.trailer(result)["redacted"], 2)

    def test_parent_values_stderr_split_writes_and_unterminated_line(self):
        fake = self.secrets[0]
        result = self.run_gate(f"import os,time; os.write(2,{fake[:10].encode()!r}); time.sleep(.05); os.write(2,{fake[10:].encode()!r})")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines()[0], "<redacted>")
        self.assertEqual(self.trailer(result)["redacted"], 1)

    def test_all_token_shapes_and_quoted_env(self):
        shapes = ["lin_oauth_" + "a" * 24, "lin_api_" + "b" * 24,
                  *["xox" + c + "-synthetic-token" for c in "abposr"], "xapp-synthetic-token",
                  "sk-" + "C" * 25, *["gh" + c + "_" + "d" * 25 for c in "pousr"],
                  "github_pat_" + "e" * 25]
        values = ["quoted_double_fake_value", "unquoted_fake_value"]
        self.secrets.extend(shapes + values)
        (self.home / ".env").write_text('API_KEY="' + values[0] + '"\nMY_PASSWORD=' + values[1] + ' # note\n', encoding="utf-8")
        result = self.run_gate(f"print(chr(10).join({(shapes + values)!r}))")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.trailer(result)["redacted"], len(shapes + values))

    def test_timeout_kills_child_tree(self):
        pidfile = self.root / "child.pid"
        code = ("import subprocess,sys,time; from pathlib import Path; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                f"Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)")
        start = time.monotonic()
        result = self.run_gate(code, ["--timeout-sec", "3"])
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(result.returncode, 124)
        self.assertTrue(self.trailer(result)["timed_out"])
        pid = int(pidfile.read_text())
        if os.name == "nt":
            listing = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                     env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(listing.returncode, 0)
            self.assertNotIn(f'"{pid}"', listing.stdout)
        else:
            # A killed orphan may remain a zombie until PID 1 reaps it in containers.
            stat = Path(f"/proc/{pid}/stat")
            if stat.exists():
                self.assertEqual(stat.read_text().split(") ", 1)[1].split()[0], "Z")
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_exit_codes_and_command_not_found(self):
        for child, runner in ((0, 0), (7, 1)):
            result = self.run_gate(f"raise SystemExit({child})")
            self.assertEqual(result.returncode, runner)
            self.assertEqual(self.trailer(result)["exit_code"], child)
        result = self.invoke(["run", "--repo-path", str(self.repo), "--cwd", str(self.repo),
                              "--", str(self.root / self.secrets[0])])
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(self.trailer(result)["exit_code"])
        self.assertIn("error", self.trailer(result))

    def test_usage_errors_do_not_echo_secrets(self):
        for args in (["--unknown", self.secrets[0]], ["run", "--repo-path", str(self.repo), "--cwd", str(self.repo)],
                     ["run", "--repo-path", str(self.repo), "--cwd", str(self.repo), "--set-env", self.secrets[0], "--", "python"]):
            self.assertEqual(self.invoke(args).returncode, 2)
        for seconds in ("0", "-1", "nan", "inf"):
            self.assertEqual(self.run_gate("print('bad')", ["--timeout-sec", seconds]).returncode, 2)

    def test_check_repo_codes_and_windows_normalization(self):
        paths = [(str(self.repo), 0), (str(self.root / "foreign"), 3)]
        if os.name == "nt":
            paths.append((str(self.repo).swapcase().replace("\\", "/") + "/", 0))
        for path, expected in paths:
            result = self.invoke(["check-repo", "--repo-path", path])
            self.assertEqual(result.returncode, expected)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertEqual(json.loads(result.stdout)["exit_code"], expected)
            self.assertEqual(result.stderr, "")
        for content in ("{", "[]", '{"version":2,"repos":[]}', '{"version":1,"repos":[{"path":"relative","github":null}]}'):
            self.policy.write_text(content, encoding="utf-8")
            result = self.invoke(["check-repo", "--repo-path", str(self.repo)])
            self.assertEqual(result.returncode, 4)
            self.assertEqual(len(result.stdout.splitlines()), 1)
        self.policy.unlink()
        self.assertEqual(self.invoke(["check-repo", "--repo-path", str(self.repo)]).returncode, 4)
        self.assertEqual(self.run_gate("print('must not start')").returncode, 4)

    def test_worktree_identity_and_fsmonitor_disabled(self):
        other = self.root / "foreign"
        self.git("init", str(other))
        marker = self.root / "gate-started"
        code = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        for invalid in (other, self.home):
            result = self.run_gate(code, cwd=invalid)
            self.assertEqual(result.returncode, 3)
            self.assertFalse(marker.exists())
        linked = self.root / "linked"
        self.git("-C", str(self.repo), "worktree", "add", "--detach", str(linked))
        self.git("-C", str(self.repo), "config", "core.fsmonitor", "nonexistent-monitor")
        # The identity command must not inherit a hostile alternate Git directory.
        env = dict(self.env, GIT_DIR=str(other / ".git"), GIT_CONFIG_COUNT="1",
                   GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="nonexistent-monitor")
        result = self.run_gate(code, cwd=linked, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(marker.exists())

    def test_discovery_never_uses_cwd_and_explicit_override_fails_closed(self):
        script = self.root / "trusted" / "nested" / "run_gate.py"
        script.parent.mkdir(parents=True)
        policy = (script.parent.parent / "deploy/repo-allowlist.json").resolve()
        policy.parent.mkdir()
        policy.write_text("{}", encoding="utf-8")
        self.assertEqual(gate.find_allowlist({}, script), policy)
        missing = self.root / "missing.json"
        self.assertEqual(gate.find_allowlist({"FACTORY_REPO_ALLOWLIST": str(missing)}, script), missing)
        # Assert discovery probes the script hierarchy, not the process cwd.
        with patch.object(gate.Path, "cwd", side_effect=AssertionError("cwd discovery forbidden")):
            self.assertEqual(gate.find_allowlist({}, script), policy)

    def test_default_fake_localappdata_redaction(self):
        local = self.home / "local"
        (local / "hermes").mkdir(parents=True)
        fake = "LOCALAPPDATA_SYNTHETIC_SECRET"
        self.secrets.append(fake)
        (local / "hermes/.env").write_text("APP_SECRET=" + fake, encoding="utf-8")
        env = dict(self.env, LOCALAPPDATA=str(local))
        del env["HERMES_HOME"]
        result = self.run_gate(f"print({fake!r})", env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.trailer(result)["redacted"], 1)


class PackagingTests(unittest.TestCase):
    def test_exact_seed_list(self):
        data = json.loads((ROOT / "deploy/repo-allowlist.json").read_text())
        self.assertEqual(data["version"], 1)
        names = ["Factory", "atlas_raw_only", "wolftac-digital-site", "peptide-vault", "factory-console",
                 "retty-labs-design", "retty-ops", "Warchest", "logan-hermes-config", "MissionControl", "omas-relief-site"]
        expected = {"D:\\Wolftac\\" + name for name in names}
        expected.add(r"C:\Users\logan_vwagmmf\OneDrive\Wolftac Digital\Codex\wolftac-crm")
        self.assertEqual(len(data["repos"]), 12)
        self.assertEqual({r["path"] for r in data["repos"]}, expected)

    def test_six_byte_identical_copies_and_deploy_pairs(self):
        self.assertEqual(len(COPIES), 6)
        self.assertEqual(len({hashlib.sha256(p.read_bytes()).hexdigest() for p in COPIES}), 1)
        text = (ROOT / "deploy/deploy.ps1").read_text()
        pairs = [line for line in text.splitlines() if "slack_bot.py" in line and "Src =" in line]
        self.assertEqual(len(pairs), 3)
        for line in pairs:
            self.assertIn(line.replace("slack_bot.py", "run_gate.py"), text)

    def test_semantic_negative_one_byte_copy_edit(self):
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            self.skipTest("PowerShell unavailable; run TP6 on a PowerShell host")
        with tempfile.TemporaryDirectory(prefix="wd574-semantic-", dir=os.getenv("TMPDIR")) as temp:
            scratch = Path(temp)
            for name in ("canonical", "ports", "plugins", "deploy"):
                shutil.copytree(ROOT / name, scratch / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("README.md", "AGENTS.md"):
                shutil.copyfile(ROOT / name, scratch / name)
            command = [pwsh, "-NoProfile", "-File", str(scratch / "deploy/semantic-check.ps1")]
            positive = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
            copy = scratch / "ports/hermes/wolftac-integrate/scripts/run_gate.py"
            original = copy.read_bytes()
            try:
                copy.write_bytes(original + b" ")
                negative = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(negative.returncode, 0)
                self.assertIn("ports/hermes/wolftac-integrate/scripts/run_gate.py - drifted", negative.stdout)
            finally:
                copy.write_bytes(original)
            restored = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
            canonical = scratch / "canonical/wolftac-integrate/scripts/run_gate.py"
            original = canonical.read_bytes()
            try:
                canonical.write_bytes(b"")
                empty = subprocess.run(command, capture_output=True, text=True, timeout=30)
                self.assertNotEqual(empty.returncode, 0)
                self.assertIn("canonical/wolftac-integrate/scripts/run_gate.py - gate runner is missing or empty", empty.stdout)
            finally:
                canonical.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
