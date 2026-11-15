"""WD-646: synthetic Contract v1 proof. No installed paths are opened."""
import ast
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "canonical/wolftac-integrate/scripts/operate_contract.py"
spec = importlib.util.spec_from_file_location("wd646_contract", MODULE)
assert spec is not None and spec.loader is not None
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
REPO = "WolftacDigital/wolftac-factory"
TARGET_MAP = {"version": 1, "targets": {
    "gatekeeper-script": {"kind": "file", "installed": r"%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py", "source": "deploy/factory-gatekeeper.py", "repo": REPO},
    "approval-policy": {"kind": "file", "installed": r"%LOCALAPPDATA%\hermes\scripts\approval-policy.json", "source": "deploy/approval-policy.json", "repo": REPO},
    "factory-checkout": {"kind": "git-head", "installed": r"D:\Wolftac\Factory", "repo": REPO},
    "factory-live": {"kind": "git-head", "installed": r"D:\Wolftac\.factory-live", "repo": REPO},
}}
EXAMPLE = {"version": 1, "owner": "Jonathan", "checkpoint_days": 2,
           "approval": "Jonathan runs it; nothing automatic",
           "smoke": ["next Gatekeeper output shows build-crm: and audit:"],
           "targets": [{"id": "gatekeeper-script"}, {"id": "factory-checkout"}]}
COPIES = ["canonical/wolftac-integrate/scripts/operate_contract.py",
          "plugins/wolftac-factory/skills/wolftac-integrate/scripts/operate_contract.py",
          "ports/claude-desktop/wolftac-integrate/scripts/operate_contract.py",
          "ports/codex/wolftac-integrate-scripts/operate_contract.py",
          "ports/hermes/wolftac-integrate/scripts/operate_contract.py",
          "ports/hermes-factory-integrate/skills/wolftac-integrate/scripts/operate_contract.py"]


def fenced(obj, tag="operate"):
    return "```" + tag + "\n" + json.dumps(obj) + "\n```\n"


def set_path(obj, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        obj = obj[int(key)] if isinstance(obj, list) else obj[key]
    obj[int(keys[-1]) if isinstance(obj, list) else keys[-1]] = value


class OperateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="wd646-contract-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.scratch = Path(cls.temp.name)
        cls.repo = cls.scratch / "fixture"
        cls.repo.mkdir()
        config = cls.scratch / "gitconfig"
        config.write_text("", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR"
        }}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(config),
                    "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_COMMITTER_EMAIL": "fixture@example.invalid", "HOME": str(cls.scratch),
                    "USERPROFILE": str(cls.scratch)})
        cls.env = env
        cls.git("init", "-b", "main")
        deploy = cls.repo / "deploy"
        deploy.mkdir()
        cls.before = b"print('synthetic before')\n"
        cls.after = b"print('synthetic after')\n"
        (deploy / "factory-gatekeeper.py").write_bytes(cls.before)
        cls.git("add", ".")
        env.update({"GIT_AUTHOR_DATE": "2026-10-31T05:00:00+00:00", "GIT_COMMITTER_DATE": "2026-10-31T05:00:00+00:00"})
        cls.git("commit", "-m", "synthetic parent")
        cls.parent = cls.git("rev-parse", "HEAD").strip()
        (deploy / "factory-gatekeeper.py").write_bytes(cls.after)
        (deploy / "approval-policy.json").write_bytes(b'{"synthetic":true}\n')
        cls.git("add", ".")
        env.update({"GIT_AUTHOR_DATE": "2026-11-01T01:30:00-05:00", "GIT_COMMITTER_DATE": "2026-11-01T01:30:00-05:00"})
        cls.git("commit", "-m", "synthetic merge candidate")
        cls.head = cls.git("rev-parse", "HEAD").strip()
        cls.source = {"issue_id": "11111111-2222-3333-4444-555555555555", "identifier": "WD-576", "repo": REPO, "merge_sha": cls.head}
        # A dirty working file must never supply a committed expected hash.
        (deploy / "factory-gatekeeper.py").write_bytes(b"dirty decoy, not a committed blob\n")

    @classmethod
    def git(cls, *args):
        return subprocess.run(["git", "-C", str(cls.repo), *args], env=cls.env,
                              capture_output=True, text=True, check=True, timeout=30).stdout

    def setUp(self):
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex

        def guard(original):
            def checked(sock, address):
                if isinstance(address, tuple):
                    host = address[0]
                    try:
                        allowed = ipaddress.ip_address(host).is_loopback
                    except ValueError:
                        allowed = host == "localhost"
                    if not allowed:
                        raise AssertionError("non-loopback network is forbidden")
                return original(sock, address)
            return checked

        for name, original in (("connect", original_connect), ("connect_ex", original_connect_ex)):
            patcher = patch.object(socket.socket, name, guard(original))
            patcher.start()
            self.addCleanup(patcher.stop)
        env_patch = patch.dict(os.environ, self.env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)

    def build(self, block=None, parsed=None, source=None, target_map=None):
        return contract.build_record(source or self.source,
            parsed if parsed is not None else contract.parse_operate(fenced(block if block is not None else EXAMPLE)),
            target_map or TARGET_MAP, self.repo)

    def record(self):
        built = self.build()
        self.assertEqual(built["status"], "ok", built)
        return built["record"]

    def test_example_and_constants(self):
        self.assertEqual(contract.CONTRACT_VERSION, 1)
        self.assertEqual(contract.OPERATE_EPOCH, "2026-10-30T23:30:00Z")
        self.assertEqual(contract.validate_block(EXAMPLE, TARGET_MAP), {"status": "ok", "error": None})
        self.assertEqual(contract.parse_operate(fenced(EXAMPLE))["block"], EXAMPLE)

    def test_approval_and_smoke_allow_multiline_text_within_length_limits(self):
        block = copy.deepcopy(EXAMPLE)
        block.update(approval="First approval step\nSecond step", smoke=["First check\nSecond check"])
        self.assertEqual(contract.validate_block(block, TARGET_MAP)["status"], "ok")
        built = self.build(block)
        self.assertEqual(built["status"], "ok", built)
        self.assertEqual(contract.parse_record(built["text"]), built["record"])
        self.assertEqual(built["record"]["approval"]["value"], block["approval"])
        self.assertEqual(built["record"]["smoke"]["value"], block["smoke"])

    def test_block_rejections_name_fields(self):
        cases = [("extra", 1, "block"), ("version", 2, "version"), ("version", True, "version"),
                 *[("checkpoint_days", value, "checkpoint_days") for value in (True, 0, 1.5, 15)],
                 ("targets", [], "targets"), ("targets", [{"id": "gatekeeper-script"}] * 2, "targets"),
                 ("targets", [{"id": "unknown"}], "targets"), ("targets", [{"id": "a" * 65}], "targets"),
                 ("smoke", ["x"] * 11, "smoke"), ("owner", "x" * 65, "owner"),
                 ("owner", "bad\nowner", "owner"), ("approval", "", "approval"),
                 ("targets", [{"id": "gatekeeper-script", "path": "elsewhere"}], "targets")]
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                block = copy.deepcopy(EXAMPLE)
                block[field] = value
                self.assertEqual(contract.validate_block(block, TARGET_MAP), {"status": "malformed", "error": expected})
        two = contract.parse_operate(fenced(EXAMPLE) + fenced(EXAMPLE))
        self.assertEqual((two["status"], two["error"]), ("malformed", "blocks"))

    def test_prose_marker_truth_table(self):
        positives = ["## Post-merge operator step", "- **Post-merge operator step**",
                     "* **Post-merge operator step (not a worker):**", "**Post-merge operator step, amended:**",
                     "## Install / wiring — operator only"]
        for text in positives:
            with self.subTest(text=text):
                self.assertIsNotNone(contract.PROSE_MARKER.search(text))
                self.assertEqual(contract.parse_operate(text)["status"], "missing")
        for text in ["we run the post-merge operator step later", "No post-merge here"]:
            self.assertIsNone(contract.PROSE_MARKER.search(text))
            self.assertEqual(contract.parse_operate(text)["status"], "none")
        self.assertEqual(contract.parse_operate("## Build notes\n* Post-merge operator step (not a worker): ...")["status"], "missing")

    def test_fences_strict_json_and_nested_examples(self):
        cases = ["```operate\nnull\n```", "```operate\n{}", "```operate\n{bad}\n```",
                 '```operate\n{"version":1,"version":1,"targets":[]}\n```',
                 '```operate\n{"version":NaN,"targets":[]}\n```']
        for text in cases:
            self.assertEqual(contract.parse_operate(text)["status"], "malformed")
        self.assertEqual(contract.parse_operate("````text\n" + fenced(EXAMPLE) + "````")["status"], "none")
        self.assertEqual(contract.parse_operate(fenced(EXAMPLE).replace("```", "~~~"))["status"], "ok")
        self.assertEqual(contract.parse_operate("```other\n{}\n```\n" + fenced(EXAMPLE))["status"], "ok")

    def test_policy_defaults_and_actual_git_hashes(self):
        built = self.build({"version": 1, "targets": EXAMPLE["targets"]})
        self.assertEqual(built["status"], "ok", built)
        record = built["record"]
        self.assertEqual(record["owner"], {"value": "Jonathan", "provenance": "policy-default"})
        self.assertEqual(record["checkpoint_provenance"], "policy-default")
        self.assertEqual(record["source"]["merged_at"], "2026-11-01T06:30:00Z")
        self.assertEqual(record["checkpoint_at"], "2026-11-03T06:30:00Z")
        self.assertEqual(record["approval"], {"value": None, "provenance": "none"})
        self.assertEqual(record["smoke"], {"value": [], "provenance": "none"})
        file, checkout = record["targets"]
        self.assertEqual(file["expected"], hashlib.sha256(self.after).hexdigest())
        self.assertEqual(file["previous"], {"value": hashlib.sha256(self.before).hexdigest(), "provenance": "merge-parent"})
        self.assertEqual(checkout["expected"], self.head)
        self.assertEqual(checkout["previous"], {"value": self.parent, "provenance": "merge-parent"})
        self.assertNotIn("installed", built["text"])
        self.assertNotIn("synthetic after", built["text"])
        self.assertNotIn("dirty decoy", built["text"])

    def test_spec_provenance_and_checkpoint(self):
        block = copy.deepcopy(EXAMPLE)
        block.update(owner="Fixture operator", checkpoint_days=14)
        record = self.build(block)["record"]
        self.assertEqual(record["owner"], {"value": "Fixture operator", "provenance": "spec"})
        self.assertEqual(record["checkpoint_at"], "2026-11-15T06:30:00Z")
        for field in ("owner", "approval", "smoke"):
            self.assertEqual(record[field]["provenance"], "spec")
        self.assertEqual(record["checkpoint_provenance"], "spec")

    def test_missing_malformed_and_none(self):
        for parsed in [contract.parse_operate("## Post-merge operator step"), contract.parse_operate("```operate\n{}\n```")]:
            built = self.build(parsed=parsed)
            self.assertEqual(built["status"], "ok", built)
            record = built["record"]
            self.assertEqual(record["targets"], [])
            self.assertEqual(record["metadata"], parsed["status"])
            self.assertEqual(record["metadata_error"], parsed["error"])
            self.assertEqual(record["owner"]["provenance"], "policy-default")
            self.assertEqual(contract.parse_record(built["text"]), record)
        self.assertEqual(self.build(parsed=contract.parse_operate("nothing"))["error"], "parsed.status")
        block = copy.deepcopy(EXAMPLE)
        block["targets"] = [{"id": "unknown"}]
        built = self.build(block)
        self.assertEqual(built["record"]["metadata"], "malformed")
        self.assertEqual(built["record"]["metadata_error"], "targets")
        self.assertEqual(built["record"]["targets"], [])

    def test_first_commit_and_new_file_previous_none(self):
        source = dict(self.source, merge_sha=self.parent)
        first = self.build(source=source)["record"]
        for target in first["targets"]:
            self.assertEqual(target["previous"], {"value": None, "provenance": "none"})
        new_file = self.build({"version": 1, "targets": [{"id": "approval-policy"}]})
        self.assertEqual(new_file["record"]["targets"][0]["previous"], {"value": None, "provenance": "none"})
        self.assertEqual(new_file["record"]["targets"][0]["expected"], hashlib.sha256(b'{"synthetic":true}\n').hexdigest())

    def test_operator_provenance_and_roundtrip(self):
        record = self.record()
        for field in ("owner", "approval", "smoke"):
            record[field]["provenance"] = "operator"
        record["checkpoint_provenance"] = "operator"
        record["checkpoint_at"] = "2027-01-01T00:00:00Z"
        self.assertEqual(contract.validate_record(record, TARGET_MAP)["status"], "ok")
        text = contract.serialize_record(record)
        self.assertEqual(contract.parse_record(text), record)
        self.assertEqual(contract.serialize_record(contract.parse_record(text)), text)

    def test_determinism_independent_of_dictionary_order(self):
        first, second = self.build(), self.build()
        self.assertEqual(first, second)
        self.assertEqual(contract.parse_record(first["text"]), first["record"])
        self.assertEqual(contract.serialize_record(contract.parse_record(first["text"])), first["text"])
        def reverse(value):
            if isinstance(value, dict):
                return {k: reverse(v) for k, v in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reverse(v) for v in value]
            return value
        self.assertEqual(contract.serialize_record(reverse(first["record"])), first["text"])
        self.assertNotIn("\r", first["text"])

    def test_record_rejections_at_every_level(self):
        cases = [("extra", 1, "record"), ("version", True, "version"),
                 ("source.extra", 1, "source"), ("source.issue_id", "bad", "source.issue_id"),
                 ("source.identifier", "WD-x", "source.identifier"), ("source.merge_sha", "main", "source.merge_sha"),
                 ("source.merged_at", "2026-02-30T00:00:00Z", "source.merged_at"),
                 ("owner.extra", 1, "owner"), ("owner.provenance", "guessed", "owner.provenance"),
                 ("checkpoint_provenance", "guessed", "checkpoint_provenance"),
                 ("checkpoint_at", "2026-11-01T00:00:00", "checkpoint_at"),
                 ("approval.extra", 1, "approval"), ("approval.provenance", "policy-default", "approval.provenance"),
                 ("smoke.extra", 1, "smoke"), ("smoke.provenance", "policy-default", "smoke.provenance"),
                 ("targets.0.extra", 1, "targets"), ("targets.0.expected", "a" * 40, "targets.expected"),
                 ("targets.1.expected", "b" * 64, "targets.expected"),
                 ("targets.0.previous.extra", 1, "targets.previous"),
                 ("targets.0.previous.provenance", "operator", "targets.previous"),
                 ("targets.0.previous.value", None, "targets.previous"),
                 ("metadata", "none", "metadata"), ("metadata_error", "invented", "metadata_error")]
        base = self.record()
        for path, value, error in cases:
            with self.subTest(path=path):
                obj = copy.deepcopy(base)
                set_path(obj, path, value)
                self.assertEqual(contract.validate_record(obj, TARGET_MAP), {"status": "malformed", "error": error})
        text = contract.serialize_record(base)
        self.assertEqual(contract.parse_record(text + text)["error"], "blocks")

    def test_map_exact_equality_and_identity(self):
        self.assertEqual(json.loads((ROOT / "deploy/operate-targets.json").read_text()), TARGET_MAP)
        canonical = MODULE.read_bytes()
        for path in COPIES:
            self.assertEqual((ROOT / path).read_bytes(), canonical, path)
            self.assertNotIn(b"\r", (ROOT / path).read_bytes(), path)

    def test_map_validation_and_repository_binding(self):
        for path, value in [("extra", 1), ("version", True), ("targets.gatekeeper-script.extra", 1),
                            ("targets.gatekeeper-script.source", "../secret"),
                            ("targets.gatekeeper-script.source", "C:/secret"),
                            ("targets.gatekeeper-script.source", "deploy\\secret"),
                            ("targets.gatekeeper-script.source", "deploy//secret")]:
            target_map = copy.deepcopy(TARGET_MAP)
            set_path(target_map, path, value)
            self.assertEqual(contract.validate_block(EXAMPLE, target_map)["status"], "malformed")
        changed = copy.deepcopy(TARGET_MAP)
        changed["targets"]["gatekeeper-script"]["repo"] = "Other/Repo"
        self.assertEqual(self.build(target_map=changed)["error"], "source.repo")
        self.assertEqual(contract.validate_record(self.record(), changed)["error"], "targets")

    def test_arbitrary_json_types_never_raise(self):
        values = [None, True, False, 0, 1, 1.5, "", "hello", [], [1], {}, {"version": []}]
        base = self.record()
        for value in values:
            for field in EXAMPLE:
                block = copy.deepcopy(EXAMPLE)
                block[field] = value
                self.assertIn(contract.validate_block(block, TARGET_MAP)["status"], ("ok", "malformed"))
            for field in base:
                record = copy.deepcopy(base)
                record[field] = value
                self.assertIn(contract.validate_record(record, TARGET_MAP)["status"], ("ok", "malformed"))
            self.assertIn(contract.validate_block(value, value)["status"], ("ok", "malformed"))
            self.assertIn(contract.validate_record(value, value)["status"], ("ok", "malformed"))
            self.assertIn(contract.parse_operate(value)["status"], ("ok", "malformed", "none"))
            self.assertIn("status", contract.parse_record(value))

    def test_git_failures_return_sanitized_error(self):
        self.assertEqual(self.build(source=dict(self.source, merge_sha="f" * 40))["status"], "error")
        for exception in (FileNotFoundError("synthetic-sensitive-content"), subprocess.TimeoutExpired("git", 30)):
            with patch.object(contract.subprocess, "run", side_effect=exception):
                built = self.build()
                self.assertEqual(built, {"status": "error", "error": "git", "record": None, "text": None})
                self.assertNotIn("synthetic-sensitive-content", str(built))
        absent = copy.deepcopy(TARGET_MAP)
        absent["targets"]["gatekeeper-script"]["source"] = "absent-file"
        self.assertEqual(self.build(target_map=absent)["status"], "error")

    def test_only_build_invokes_git_no_installed_io(self):
        tree = ast.parse(MODULE.read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "build_record":
                self.assertNotIn("subprocess.run", ast.unparse(node))
        original = subprocess.run
        calls = []
        def recorded(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertFalse(kwargs.get("shell", False))
            self.assertEqual(argv[:4], ["git", "--no-optional-locks", "-C", str(self.repo)])
            calls.append(argv)
            return original(argv, **kwargs)
        with patch.object(contract.subprocess, "run", side_effect=recorded), patch("builtins.open", side_effect=AssertionError("no Python file reads in build")):
            self.assertEqual(self.build()["status"], "ok")
        self.assertTrue(calls)
        for entry in TARGET_MAP["targets"].values():
            self.assertFalse(any(entry["installed"] in arg for call in calls for arg in call))

    def test_socket_guard_negative_control(self):
        with socket.socket() as sock:
            with self.assertRaisesRegex(AssertionError, "non-loopback"):
                sock.connect(("192.0.2.1", 443))
            with self.assertRaisesRegex(AssertionError, "non-loopback"):
                sock.connect_ex(("example.invalid", 443))


if __name__ == "__main__":
    unittest.main()
