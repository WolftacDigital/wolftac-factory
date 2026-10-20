"""Offline renewal contract tests. Only dummy credentials and scratch homes.

The transport double exercises request construction and persistence, not Linear's
live token invalidation policy. Live AC-1..4/13..15 still need authorized fixtures.
"""
import ast
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "canonical/wolftac-integrate/scripts/linear_bot.py"
PREFLIGHT = ROOT / "ports/hermes-factory-integrate/deploy/integrate-preflight.py"
BOT_ID = "b8fece8e-8ec5-4125-b4b2-d7fa882db137"
APP_ID = "fixture-app-id-not-real"
SECRET = "fixture-app-secret-not-real"
OLD = "fixture-rejected-token-not-real"


def load_module(path):
    spec = importlib.util.spec_from_file_location("test_factory_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Transport:
    """Deliberate HTTP double; any unexpected endpoint fails rather than networking."""
    def __init__(self):
        self.calls = []
        self.mints = 0
        self.valid = set()
        self.viewer_id = BOT_ID
        self.viewer_name = "Fixture Bot"
        self.oauth_error = None
        self.api_error = None
        self.ttl = 2592000
        self.invalidate = False
        self.after_mint = None
        self.reject_after_verification = False
        self.fresh_successes = 0
        self.graphql_error = False

    def __call__(self, request, timeout=None):
        assert timeout == 45
        self.calls.append(request)
        if request.full_url == "https://api.linear.app/oauth/token":
            self.mints += 1
            if self.oauth_error:
                raise self.oauth_error
            form = urllib.parse.parse_qs(request.data.decode())
            if form.get("client_secret") != [SECRET]:
                raise urllib.error.HTTPError(request.full_url, 400, "invalid client", {}, None)
            assert form == {
                "grant_type": ["client_credentials"], "client_id": [APP_ID],
                "client_secret": [SECRET],
                "scope": ["read,write,app:assignable,app:mentionable"],
            }
            token = "fixture-new-token-%s-not-real" % self.mints
            if self.invalidate:
                self.valid.clear()
            self.valid.add(token)
            if self.after_mint:
                self.after_mint()
            return io.BytesIO(json.dumps({"access_token": token, "expires_in": self.ttl}).encode())
        assert request.full_url == "https://api.linear.app/graphql", "Unexpected network destination"
        if self.api_error:
            raise self.api_error
        auth = request.get_header("Authorization")
        token = auth.removeprefix("Bearer ")
        if not auth.startswith("Bearer ") or token not in self.valid:
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)
        if self.reject_after_verification and self.fresh_successes:
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", {}, None)
        self.fresh_successes += 1
        payload = json.loads(request.data)
        if self.graphql_error:
            return io.BytesIO(json.dumps({"errors": [{"message": SECRET + APP_ID + token}]}).encode())
        if "viewer" in payload["query"]:
            data = {"viewer": {"id": self.viewer_id, "name": self.viewer_name}}
        else:
            data = {"result": "fixture-success", "variables": payload["variables"]}
        return io.BytesIO(json.dumps({"data": data}).encode())


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd547-test-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "hermes"
        self.home.mkdir()
        self.env_file = self.home / ".env"
        self.bot = load_module(HELPER)
        self.transport = Transport()
        self.environment = {"LOCALAPPDATA": str(self.base), "HERMES_HOME": str(self.home)}
        self.write_env()

    def write_env(self, token=OLD, days=None, app=True, secret=True, expiry=None):
        values = ["# fixture only\r\n", "OTHER=unchanged\r\n"]
        if token is not None:
            values.append("LINEAR_FACTORY_BOT_TOKEN=" + token + "\n")
        if app:
            values.append("LINEAR_FACTORY_APP_ID=" + APP_ID + "\n")
        if secret:
            values.append("LINEAR_FACTORY_APP_SECRET=" + SECRET + "\n")
        if days is not None:
            expiry = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace("+00:00", "Z")
        if expiry is not None:
            values.append("LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=" + expiry + "\n")
        self.env_file.write_bytes("".join(values).encode())

    def invoke(self, *args, module=None, env=None):
        module = module or self.bot
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.environment if env is None else env, clear=True), \
             patch.object(sys, "argv", ["fixture", *args]), \
             patch("urllib.request.urlopen", self.transport), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                module.main()
                code = 0
            except SystemExit as exc:
                code = exc.code
        combined = out.getvalue() + err.getvalue()
        for value in (APP_ID, SECRET, OLD, *self.transport.valid):
            self.assertNotIn(value, combined)
        return code, out.getvalue(), err.getvalue()

    def successful(self, *args, **kwargs):
        code, out, err = self.invoke(*args, **kwargs)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_missing_expiry_renews_verifies_persists(self):
        before = self.env_file.read_bytes()
        result = self.successful("remint")
        self.assertEqual(result["viewer"]["id"], BOT_ID)
        self.assertEqual(result["token_source"], "minted")
        self.assertEqual(self.transport.mints, 1)
        after = self.env_file.read_bytes()
        self.assertNotEqual(before, after)
        self.assertIn(b"# fixture only\r\nOTHER=unchanged\r\n", after)
        expiry = datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00"))
        self.assertGreater(expiry, datetime.now(timezone.utc) + timedelta(days=29))
        self.assertEqual(self.successful("viewer")["token_source"], "file")
        self.assertEqual(self.transport.mints, 1)

    def test_future_valid_token_does_not_renew_or_change_file(self):
        self.write_env(days=10)
        self.transport.valid.add(OLD)
        before = self.env_file.read_bytes()
        result = self.successful("remint")
        self.assertEqual(result["token_source"], "file")
        self.assertEqual(self.transport.mints, 0)
        self.assertEqual(before, self.env_file.read_bytes())

    def test_within_five_days_and_expired_renew_proactively(self):
        for days in (-1, 0, 4, 5):
            with self.subTest(days=days):
                self.transport = Transport()
                self.transport.valid.add(OLD)
                self.write_env(days=days)
                self.assertEqual(self.successful("remint")["token_source"], "minted")
                self.assertEqual(self.transport.mints, 1)
                self.assertEqual(self.transport.calls[0].full_url, self.bot.OAUTH_API)

    def test_malformed_or_naive_expiry_renews(self):
        for expiry in ("invalid", "2099-01-01T00:00:00"):
            with self.subTest(expiry=expiry):
                self.transport = Transport()
                self.write_env(expiry=expiry)
                self.successful("remint")
                self.assertEqual(self.transport.mints, 1)

    def test_future_but_revoked_token_renews_on_401(self):
        self.write_env(days=10)
        self.successful("remint")
        self.assertEqual(self.transport.mints, 1)

    def test_corrupt_token_both_home_modes_and_second_call_file(self):
        for hermes in (False, True):
            with self.subTest(hermes=hermes):
                self.transport = Transport()
                self.write_env()
                env = dict(self.environment)
                if not hermes:
                    del env["HERMES_HOME"]
                first = self.successful("viewer", env=env)
                second = self.successful("viewer", env=env)
                self.assertEqual(first["token_source"], "minted")
                self.assertEqual(second["token_source"], "file")
                self.assertEqual(first["token_path"], str(self.env_file))
                self.assertEqual(self.transport.mints, 1)

    def test_no_mint_flag_before_or_after_command_is_read_only(self):
        for args in (("--no-mint", "viewer"), ("viewer", "--no-mint")):
            with self.subTest(args=args):
                before = self.env_file.read_bytes()
                code, _, err = self.invoke(*args)
                self.assertEqual(code, 1)
                self.assertIn("401", err)
                self.assertEqual(self.transport.mints, 0)
                self.assertEqual(before, self.env_file.read_bytes())

    def test_no_mint_remint_due_fails_without_network(self):
        before = self.env_file.read_bytes()
        code, _, err = self.invoke("remint", "--no-mint")
        self.assertEqual(code, 1)
        self.assertIn("--no-mint", err)
        self.assertFalse(self.transport.calls)
        self.assertEqual(before, self.env_file.read_bytes())

    def test_no_mint_future_valid_is_ok(self):
        self.write_env(days=10)
        self.transport.valid.add(OLD)
        self.successful("remint", "--no-mint")
        self.assertEqual(self.transport.mints, 0)

    def test_missing_app_credentials_exit_two_name_key(self):
        for key in ("LINEAR_FACTORY_APP_ID", "LINEAR_FACTORY_APP_SECRET"):
            with self.subTest(key=key):
                self.write_env(app=key != "LINEAR_FACTORY_APP_ID", secret=key != "LINEAR_FACTORY_APP_SECRET")
                before = self.env_file.read_bytes()
                code, _, err = self.invoke("viewer")
                self.assertEqual(code, 2)
                self.assertIn(key, err)
                self.assertEqual(self.transport.mints, 0)
                self.assertEqual(before, self.env_file.read_bytes())

    def test_missing_token_can_renew_but_no_mint_cannot(self):
        self.write_env(token=None)
        code, _, _ = self.invoke("viewer", "--no-mint")
        self.assertEqual(code, 2)
        self.assertFalse(self.transport.calls)
        self.successful("viewer")
        self.assertEqual(self.transport.mints, 1)

    def test_oauth_rejected_and_unreachable_leave_bytes_untouched(self):
        errors = [urllib.error.HTTPError(self.bot.OAUTH_API, 400, SECRET, {}, None),
                  urllib.error.URLError(SECRET + APP_ID)]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.transport = Transport()
                self.transport.oauth_error = error
                before = self.env_file.read_bytes()
                code, _, _ = self.invoke("remint")
                self.assertEqual(code, 1)
                self.assertEqual(before, self.env_file.read_bytes())
                self.assertEqual(self.transport.mints, 1)

    def test_wrong_app_secret_is_rejected_and_preserves_file(self):
        wrong = "fixture-deliberately-wrong-secret"
        self.env_file.write_bytes(self.env_file.read_bytes().replace(SECRET.encode(), wrong.encode()))
        before = self.env_file.read_bytes()
        code, out, err = self.invoke("remint")
        self.assertEqual(code, 1)
        self.assertIn("OAuth HTTP 400", err)
        self.assertNotIn(wrong, out + err)
        self.assertEqual(before, self.env_file.read_bytes())
        self.assertEqual(self.transport.mints, 1)

    def test_output_redacts_escaped_values_before_json_encoding(self):
        value = 'fixture-secret-with-"quote-and-\\\\slash'
        self.bot._SECRETS.add(value)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.bot.emit({"nested": [{"name": value}]})
        result = json.loads(out.getvalue())
        self.assertEqual(result["nested"][0]["name"], "<redacted>")

    def test_no_expected_identity_override_flag(self):
        before = self.env_file.read_bytes()
        code, _, _ = self.invoke("viewer", "--expected-bot-id", "other")
        self.assertEqual(code, 2)
        self.assertFalse(self.transport.calls)
        self.assertEqual(before, self.env_file.read_bytes())

    def test_wrong_fresh_identity_not_persisted(self):
        before = self.env_file.read_bytes()
        self.transport.viewer_id = "not-the-pinned-bot"
        code, _, err = self.invoke("viewer")
        self.assertEqual(code, 4)
        self.assertIn("pinned", err)
        self.assertEqual(before, self.env_file.read_bytes())

    def test_wrong_existing_identity_fails_closed(self):
        self.transport.valid.add(OLD)
        self.transport.viewer_id = "not-the-pinned-bot"
        before = self.env_file.read_bytes()
        code, _, _ = self.invoke("viewer")
        self.assertEqual(code, 4)
        self.assertEqual(self.transport.mints, 0)
        self.assertEqual(before, self.env_file.read_bytes())

    def test_hermes_process_snapshot_does_not_override_new_file(self):
        self.environment["LINEAR_FACTORY_BOT_TOKEN"] = OLD
        self.successful("remint")
        self.assertEqual(self.successful("viewer")["token_source"], "file")
        self.assertEqual(self.transport.mints, 1)

    def test_nonhermes_preserves_env_precedence_and_reports_source(self):
        del self.environment["HERMES_HOME"]
        self.environment["LINEAR_FACTORY_BOT_TOKEN"] = "fixture-env-token-not-real"
        self.transport.valid.add(self.environment["LINEAR_FACTORY_BOT_TOKEN"])
        result = self.successful("viewer")
        self.assertEqual(result["token_source"], "env")
        self.assertEqual(self.transport.mints, 0)

    def test_app_credentials_never_fall_back_to_inherited_other_home(self):
        self.write_env(app=False, secret=False)
        self.environment.update(LINEAR_FACTORY_APP_ID=APP_ID, LINEAR_FACTORY_APP_SECRET=SECRET)
        code, _, err = self.invoke("remint")
        self.assertEqual(code, 2)
        self.assertIn("LINEAR_FACTORY_APP_ID", err)
        self.assertEqual(self.transport.mints, 0)

    def test_retry_is_bounded_if_fresh_token_rejected(self):
        self.transport.reject_after_verification = True
        code, _, err = self.invoke("viewer")
        self.assertEqual(code, 1)
        self.assertIn("401", err)
        self.assertEqual(self.transport.mints, 1)

    def test_original_request_and_variables_replayed_not_lost(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.environment, clear=True), \
             patch("urllib.request.urlopen", self.transport), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.bot.initialize()
            result = self.bot.gq("query($id:String!){ fixture(id:$id) }", {"id": "WD-fixture"})
        self.assertEqual(result["variables"], {"id": "WD-fixture"})
        self.assertEqual(self.transport.mints, 1)
        original = json.loads(self.transport.calls[0].data)
        replay = json.loads(self.transport.calls[-1].data)
        self.assertEqual(original, replay)

    def test_non401_errors_do_not_mint(self):
        for status in (403, 429, 500):
            with self.subTest(status=status):
                self.transport.api_error = urllib.error.HTTPError(self.bot.API, status, SECRET, {}, None)
                before = self.env_file.read_bytes()
                code, _, _ = self.invoke("viewer")
                self.assertEqual(code, 1)
                self.assertEqual(self.transport.mints, 0)
                self.assertEqual(before, self.env_file.read_bytes())

    def test_graphql_error_is_sanitized_and_not_retried(self):
        self.transport.valid.add(OLD)
        self.transport.graphql_error = True
        code, _, err = self.invoke("viewer")
        self.assertEqual(code, 1)
        self.assertIn("GraphQL", err)
        self.assertEqual(self.transport.mints, 0)

    def test_invalid_oauth_expiry_is_never_persisted(self):
        for ttl in (None, -1, 0, True, "no", 1.5, "9" * 50):
            with self.subTest(ttl=ttl):
                self.transport = Transport()
                self.transport.ttl = ttl
                before = self.env_file.read_bytes()
                code, _, _ = self.invoke("remint")
                self.assertEqual(code, 1)
                self.assertEqual(before, self.env_file.read_bytes())

    def test_atomic_replace_failure_preserves_old_file_and_cleans_temp(self):
        before = self.env_file.read_bytes()
        with patch.object(self.bot.os, "replace", side_effect=PermissionError("fixture denied")):
            code, _, err = self.invoke("remint")
        self.assertEqual(code, 1)
        self.assertIn("atomically", err)
        self.assertEqual(before, self.env_file.read_bytes())
        self.assertEqual(list(self.home.glob(".linear-bot-*.tmp")), [])

    def test_concurrent_edit_not_overwritten(self):
        changed = self.env_file.read_bytes() + b"OPERATOR=preserve\n"
        self.transport.after_mint = lambda: self.env_file.write_bytes(changed)
        code, _, err = self.invoke("remint")
        self.assertEqual(code, 1)
        self.assertIn("changed during renewal", err)
        self.assertEqual(changed, self.env_file.read_bytes())
        self.assertEqual(list(self.home.glob(".linear-bot-*.tmp")), [])

    def test_repeated_mint_in_one_invocation_rejected(self):
        with patch.dict(os.environ, self.environment, clear=True), \
             patch("urllib.request.urlopen", self.transport), \
             contextlib.redirect_stderr(io.StringIO()):
            self.bot.initialize()
            self.bot.mint()
            with self.assertRaises(SystemExit) as exc:
                self.bot.mint()
        self.assertEqual(exc.exception.code, 1)
        self.assertEqual(self.transport.mints, 1)

    def test_two_homes_recover_under_both_simulated_invalidation_policies(self):
        for invalidates in (False, True):
            with self.subTest(invalidates=invalidates):
                self.transport = Transport()
                self.transport.invalidate = invalidates
                self.write_env()
                other = self.base / "other"
                other.mkdir(exist_ok=True)
                (other / ".env").write_bytes(self.env_file.read_bytes())
                env_b = {**self.environment, "HERMES_HOME": str(other)}
                self.successful("viewer")
                self.successful("viewer", env=env_b)
                third = self.successful("viewer")
                self.assertEqual(self.transport.mints, 3 if invalidates else 2)
                self.assertEqual(third["token_source"], "minted" if invalidates else "file")
                # Invalidation means only the newest token remains valid. This is
                # not evidence for the live service's policy or simultaneous validity.
                self.successful("viewer", env=env_b)

    def test_isolated_preflight_uses_shared_renewal_and_preserves_gates(self):
        preflight = load_module(PREFLIGHT)
        (self.home / "config.yaml").write_text("model: fixture\n", encoding="utf-8")
        code, out, err = self.invoke("--profile-root", str(self.home), module=preflight)
        self.assertEqual(code, 0, err)
        self.assertIn("PREFLIGHT OK", out)
        self.assertEqual(self.transport.mints, 1)
        # The source-only profile is not installed or started by this test.
        self.assertEqual(self.successful("viewer")["token_source"], "file")

    def test_isolation_violations_stop_before_network_or_write(self):
        preflight = load_module(PREFLIGHT)
        cases = [({}, None), ({**self.environment, "HERMES_HOME": str(self.base)}, None),
                 ({**self.environment, "LINEAR_API_KEY": "fixture-human"}, None),
                 (self.environment, "# linear transport forbidden\n"),
                 (self.environment, "tools: [composio]\n")]
        for env, config in cases:
            with self.subTest(config=config, home=env.get("HERMES_HOME")):
                config_path = self.home / "config.yaml"
                config_path.write_text(config or "model: fixture\n", encoding="utf-8")
                before = self.env_file.read_bytes()
                code, _, _ = self.invoke("--profile-root", str(self.home), module=preflight, env=env)
                self.assertEqual(code, 3)
                self.assertFalse(self.transport.calls)
                self.assertEqual(before, self.env_file.read_bytes())

    def test_isolated_output_redacts_server_echoed_credentials(self):
        self.transport.viewer_name = SECRET + APP_ID + OLD
        code, out, err = self.invoke("--profile-root", str(self.home), module=load_module(PREFLIGHT))
        self.assertEqual(code, 0, err)
        self.assertIn("PREFLIGHT OK", out)
        self.assertIn("<redacted>", out)

    def test_isolated_wrong_identity_not_persisted(self):
        self.transport.viewer_id = "other-bot"
        before = self.env_file.read_bytes()
        code, _, _ = self.invoke("--profile-root", str(self.home), module=load_module(PREFLIGHT))
        self.assertEqual(code, 4)
        self.assertEqual(before, self.env_file.read_bytes())


class DistributionTests(unittest.TestCase):
    def test_six_identical_helpers_with_one_shared_implementation(self):
        paths = sorted(ROOT.glob("**/linear_bot.py"))
        self.assertEqual(len(paths), 6)
        canonical = HELPER.read_bytes()
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertEqual(path.read_bytes(), canonical)
                tree = ast.parse(path.read_text(encoding="utf-8"))
                functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "mint"]
                self.assertEqual(len(functions), 1)
        tree = ast.parse(PREFLIGHT.read_text(encoding="utf-8"))
        self.assertFalse(any(isinstance(n, ast.FunctionDef) and n.name == "mint" for n in ast.walk(tree)))

    def test_pinned_identity_cannot_be_overridden_by_env_or_flags(self):
        for path in [*ROOT.glob("**/linear_bot.py"), PREFLIGHT]:
            with self.subTest(path=path.relative_to(ROOT)):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                stores = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "EXPECTED_BOT_ID" and isinstance(n.ctx, ast.Store)]
                self.assertEqual(len(stores), 1)
                assignments = [n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EXPECTED_BOT_ID" for t in n.targets)]
                self.assertEqual(len(assignments), 1)
                self.assertIsInstance(assignments[0].value, ast.Constant)
                self.assertEqual(assignments[0].value.value, BOT_ID)


if __name__ == "__main__":
    unittest.main()
