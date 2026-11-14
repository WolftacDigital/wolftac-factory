"""Offline suite for R1, the wolftac-approval-read Hermes plugin (WD-546 AC-13, AC-14).

Every transport here is synthetic: a fake opener stands in for Discord and a
fake getter stands in for the Hermes secret resolver. Nothing calls the live
resolver, Discord or the network.
"""
import ast
import email.message
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "ports" / "hermes-plugins" / "wolftac-approval-read"
SPEC = importlib.util.spec_from_file_location("wolftac_approval_reader", PLUGIN / "reader.py")
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)

GUILD = "923752167293222912"
CHANNEL = "1562236246158221323"
THREAD = "1569999999999999991"
HARPER = "1562178628211449966"
SIBLING_BOT = "1562178628211449999"
APPROVER = "1022999767753691186"
MESSAGE = "1569128910862684180"
TOKEN = "harper-token-SENTINEL-546"
SIBLING = "sibling-token-SENTINEL-546"
ROCKET = "\U0001F680"
API = "https://discord.com/api/v10"
ALLOWED = [
    re.compile(r"/users/@me"),
    re.compile(r"/channels/[0-9]{17,20}"),
    re.compile(r"/channels/[0-9]{17,20}/messages/[0-9]{17,20}"),
    re.compile(r"/channels/[0-9]{17,20}/messages/[0-9]{17,20}/reactions/%F0%9F%9A%80\?limit=100(&after=[0-9]{17,20})?"),
]


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._buf = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self.closed = False

    def read(self, n=-1):
        return self._buf.read(n)

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


def http_error(url, status, payload=b"", headers=None):
    hdrs = email.message.Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return urllib.error.HTTPError(url, status, "error", hdrs, io.BytesIO(body))


def user_ids(count, start=1100000000000000000):
    return [str(start + i) for i in range(count)]


class FakeDiscord:
    """Routes the four allowed GET shapes; anything else fails the test."""

    def __init__(self, test):
        self.test = test
        self.requests = []
        self.identities = {TOKEN: {"id": HARPER, "bot": True, "username": "Harper"},
                           SIBLING: {"id": SIBLING_BOT, "bot": True, "username": "Logan"}}
        self.channels = {
            CHANNEL: {"id": CHANNEL, "guild_id": GUILD, "type": 0, "parent_id": "1562236246158220000"},
            THREAD: {"id": THREAD, "guild_id": GUILD, "type": 11, "parent_id": CHANNEL},
        }
        self.message = {"id": MESSAGE, "channel_id": CHANNEL, "author": {"id": HARPER, "bot": True},
                        "timestamp": "2026-11-08T23:00:00+00:00", "edited_timestamp": None,
                        "content": "FACTORY MERGE APPROVAL REQUEST\nIssue: WD-546"}
        self.message_reads = 0
        self.second_message = None  # override for the re-read
        self.reactors = [{"id": APPROVER}]
        self.overrides = {}  # path prefix -> callable(request) -> response or raises
        self.page_hook = None

    def open(self, request, timeout):
        self.requests.append(request)
        self.test.assertEqual(request.get_method(), "GET")
        self.test.assertTrue(request.full_url.startswith(API + "/"))
        self.test.assertGreater(timeout, 0)
        self.test.assertLessEqual(timeout, 10)
        path = request.full_url[len(API):]
        self.test.assertTrue(any(p.fullmatch(path) for p in ALLOWED), path)
        auth = request.get_header("Authorization")
        self.test.assertTrue(auth.startswith("Bot "))
        for prefix, handler in self.overrides.items():
            if path.startswith(prefix) or path == prefix:
                result = handler(request, path)
                if result is not None:
                    return result
        if path == "/users/@me":
            identity = self.identities.get(auth[4:])
            if identity is None:
                raise http_error(request.full_url, 401, {"message": "401: Unauthorized " + auth})
            return FakeResponse(200, identity)
        if "/reactions/" in path:
            after = re.search(r"&after=([0-9]+)", path)
            after = int(after[1]) if after else 0
            if self.page_hook:
                return FakeResponse(200, self.page_hook(after))
            rows = sorted((row for row in self.reactors if int(row["id"]) > after), key=lambda x: int(x["id"]))
            return FakeResponse(200, rows[:100])
        if "/messages/" in path:
            self.message_reads += 1
            if self.message_reads > 1 and self.second_message is not None:
                if self.second_message == 404:
                    raise http_error(request.full_url, 404, {"message": "Unknown Message"})
                return FakeResponse(200, self.second_message)
            return FakeResponse(200, self.message)
        channel = path.split("/")[2]
        if channel in self.channels:
            return FakeResponse(200, self.channels[channel])
        raise http_error(request.full_url, 404, {"message": "Unknown Channel"})


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.discord = FakeDiscord(self)
        self.token = TOKEN
        self.getter_calls = 0
        self.sleeps = []

    def getter(self, name):
        self.getter_calls += 1
        self.assertEqual(name, "DISCORD_BOT_TOKEN")
        if isinstance(self.token, BaseException):
            raise self.token
        return self.token

    def run_reader(self, channel=CHANNEL, message=MESSAGE, emoji=ROCKET, json_flag=True, **kwargs):
        out = io.StringIO()
        kwargs.setdefault("secret_getter", self.getter)
        kwargs.setdefault("opener", self.discord)
        kwargs.setdefault("sleep", self.sleeps.append)
        kwargs.setdefault("exit_fn", lambda code: self.fail("watchdog fired"))
        code = r.run(channel, message, emoji, json_flag, stdout=out, **kwargs)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(SIBLING, out.getvalue())
        doc = json.loads(lines[0])
        self.assertEqual(doc["contract"], 1)
        if doc["ok"] is False:
            self.assertNotIn("reactors", doc)  # never a partial list
            self.assertLessEqual(set(doc), {"ok", "contract", "error", "http_status"})
        self.assertEqual(lines[0], json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return code, doc

    def assertError(self, expected_code, error, status=None, **kwargs):
        code, doc = self.run_reader(**kwargs)
        self.assertEqual((code, doc["ok"], doc["error"]), (expected_code, False, error))
        if status is not None:
            self.assertEqual(doc.get("http_status"), status)
        return doc

    def paths(self):
        return [req.full_url[len(API):] for req in self.discord.requests]

    # --- success contract -------------------------------------------------
    def test_success_document(self):
        code, doc = self.run_reader()
        self.assertEqual(code, 0)
        self.assertEqual(set(doc), {"ok", "contract", "reader_bot_id", "guild_id", "channel_id", "parent_id",
                                    "message", "emoji", "reactors", "reactor_pages"})
        self.assertEqual((doc["reader_bot_id"], doc["guild_id"], doc["channel_id"], doc["emoji"]),
                         (HARPER, GUILD, CHANNEL, ROCKET))
        self.assertEqual(set(doc["message"]), {"id", "author", "timestamp", "edited_timestamp", "content",
                                               "content_sha256"})
        self.assertEqual(doc["message"]["author"], {"id": HARPER, "bot": True})
        self.assertEqual(doc["reactors"], [{"id": APPROVER, "bot": False}])  # absent flag means not a bot
        self.assertEqual(self.paths()[:3], ["/users/@me", f"/channels/{CHANNEL}",
                                            f"/channels/{CHANNEL}/messages/{MESSAGE}"])
        self.assertEqual(self.paths()[-1], f"/channels/{CHANNEL}/messages/{MESSAGE}")
        for req in self.discord.requests:
            self.assertEqual(req.get_header("Authorization"), "Bot " + TOKEN)

    def test_thread_under_approvals(self):
        self.discord.message = dict(self.discord.message, channel_id=THREAD)
        code, doc = self.run_reader(channel=THREAD)
        self.assertEqual((code, doc["channel_id"], doc["parent_id"]), (0, THREAD, CHANNEL))

    # --- identity, checked first -----------------------------------------
    def test_identity_failures_reject_before_any_later_request(self):
        cases = {
            "sibling-profile-credential": (SIBLING, None, "identity_mismatch", 4),
            "env-fallback-wrong-bot": ("fallback-SENTINEL", {"id": SIBLING_BOT, "bot": True}, "identity_mismatch", 4),
            "bot-false": ("human", {"id": HARPER, "bot": False}, "identity_mismatch", 4),
            "bot-missing": ("nobot", {"id": HARPER}, "identity_mismatch", 4),
            "missing-id": ("noid", {"bot": True}, "identity_unverified", 4),
            "unauthorized": ("revoked", "401", "identity_unverified", 4),
            "malformed-self": ("list", [1, 2], "identity_unverified", 4),
            "non-json-self": ("text", b"<html>", "malformed_response", 5),
        }
        for name, (token, identity, error, exit_code) in cases.items():
            with self.subTest(case=name):
                self.setUp()
                self.token = token
                if identity is not None and identity != "401":
                    if isinstance(identity, (bytes, list)):
                        self.discord.overrides["/users/@me"] = lambda req, path, body=identity: FakeResponse(200, body)
                    else:
                        self.discord.identities[token] = identity
                self.assertError(exit_code, error)
                self.assertEqual(self.paths(), ["/users/@me"])

    # --- credential --------------------------------------------------------
    def test_credential_failures_are_token_unavailable(self):
        class UnscopedSecretError(RuntimeError):
            pass

        for name, token in {"none": None, "empty": "", "blank": "   ", "newline": "a\nb", "number": 5,
                            "unscoped": UnscopedSecretError("profile-scoping bug SENTINEL"),
                            "resolver-error": OSError("vault down " + TOKEN),
                            "import": ImportError("no agent")}.items():
            with self.subTest(case=name):
                self.setUp()
                self.token = token
                self.assertError(4, "token_unavailable")
                self.assertEqual(self.discord.requests, [])

    def test_default_resolver_is_agent_secret_scope(self):
        """The import path through agent.secret_scope, with a synthetic module only."""
        calls = []

        class UnscopedSecretError(RuntimeError):
            pass

        def get_secret(name, default=None):
            calls.append(name)
            raise UnscopedSecretError("no scope")

        agent = types.ModuleType("agent")
        scope = types.ModuleType("agent.secret_scope")
        scope.get_secret = get_secret
        scope.UnscopedSecretError = UnscopedSecretError
        agent.secret_scope = scope
        with patch.dict(sys.modules, {"agent": agent, "agent.secret_scope": scope}):
            self.assertError(4, "token_unavailable", secret_getter=None)
        self.assertEqual(calls, ["DISCORD_BOT_TOKEN"])
        scope.get_secret = lambda name, default=None: TOKEN
        with patch.dict(sys.modules, {"agent": agent, "agent.secret_scope": scope}):
            code, doc = self.run_reader(secret_getter=None)
        self.assertEqual(code, 0)

    # --- transport -----------------------------------------------------------
    def test_invalid_arguments_never_reach_the_opener_or_resolver(self):
        for name, kwargs in {
            "short-channel": {"channel": "1234"}, "long-channel": {"channel": "1" * 21},
            "alpha-message": {"message": "abc12345678901234567"}, "none-channel": {"channel": None},
            "unicode-digits": {"channel": "١" * 18}, "path-injection": {"message": MESSAGE + "/../x"},
            "wrong-emoji": {"emoji": "\U0001F44D"}, "emoji-name": {"emoji": ":rocket:"},
            "no-json": {"json_flag": False},
        }.items():
            with self.subTest(case=name):
                self.setUp()
                self.assertError(2, "usage", opener=None, **kwargs)
                self.assertEqual(self.getter_calls, 0)
                self.assertEqual(self.discord.requests, [])

    def test_opener_ignores_proxies_verifies_tls_and_refuses_redirects(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                                     "ALL_PROXY": "http://127.0.0.1:9"}):
            opener = r.build_opener()
        # ProxyHandler({}) displaces urllib's environment-reading default; having no
        # proxies, it registers no *_open method, so no proxy handler is installed at all.
        self.assertEqual([h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)], [])
        redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual([type(h) for h in redirects], [r._RefuseRedirects])
        https = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
        self.assertEqual(len(https), 1)
        context = https[0]._context
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        fp = io.BytesIO(b"")
        with self.assertRaises(r.ReadError) as caught:
            r._RefuseRedirects().redirect_request(None, fp, 302, "Found", {}, "https://evil.example/")
        self.assertEqual(caught.exception.code, "redirect_refused")

    def test_redirect_from_transport_is_refused(self):
        def redirect(req, path):
            raise r.ReadError("redirect_refused", 5, 301)
        self.discord.overrides[f"/channels/{CHANNEL}"] = redirect
        self.assertError(5, "redirect_refused", status=301)

    def test_size_limits(self):
        for name, setup, error in (
            ("object", lambda d: d.overrides.__setitem__(
                "/users/@me", lambda req, path: FakeResponse(200, b'{"id":"' + b"1" * (70 * 1024) + b'"}')),
             "response_too_large"),
            ("page", lambda d: setattr(d, "page_hook", lambda after: [{"id": APPROVER, "pad": "x" * 300000}]),
             "response_too_large"),
            ("content", lambda d: d.message.__setitem__("content", "x" * 4001), "response_too_large"),
        ):
            with self.subTest(case=name):
                self.setUp()
                setup(self.discord)
                self.assertError(5, error)

    def test_transport_exception_text_is_never_echoed(self):
        def boom(req, path):
            raise OSError("connection reset; token=" + TOKEN)
        self.discord.overrides[f"/channels/{CHANNEL}"] = boom
        self.assertError(5, "network_error")

    # --- deadline ------------------------------------------------------------
    def test_blocking_read_hits_the_watchdog_exit_124(self):
        script = textwrap.dedent(f"""
            import importlib.util, sys, threading
            spec = importlib.util.spec_from_file_location("r1", {str(PLUGIN / 'reader.py')!r})
            r = importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
            class Blocking:
                def open(self, request, timeout):
                    threading.Event().wait()
            sys.exit(r.run({CHANNEL!r}, {MESSAGE!r}, "\\U0001F680", True, secret_getter=lambda n: "t",
                           opener=Blocking(), deadline_seconds=1.0))
        """)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=30)
        elapsed = time.monotonic() - started
        self.assertEqual(proc.returncode, 124)
        self.assertEqual(json.loads(proc.stdout.decode().strip()), {"contract": 1, "error": "timeout", "ok": False})
        self.assertLess(elapsed, 1.0 + 1.0 + 2.5)  # deadline + 1s, plus interpreter start-up

    def test_no_retry_crosses_the_deadline(self):
        clock = [0.0]

        def rate_limited(req, path):
            clock[0] = 57.5  # 2.5s left: a 3s retry_after must not be waited out
            raise http_error(req.full_url, 429, {"retry_after": 3.0})
        self.discord.overrides[f"/channels/{CHANNEL}"] = rate_limited
        self.assertError(5, "rate_limited", status=429, clock=lambda: clock[0])
        self.assertEqual(self.sleeps, [])

    def test_expired_deadline_stops_before_the_next_request(self):
        clock = [0.0]

        def slow_identity(req, path):
            clock[0] = 61.0
            return None
        self.discord.overrides["/users/@me"] = slow_identity
        code, doc = self.run_reader(clock=lambda: clock[0])
        self.assertEqual((code, doc["error"]), (124, "timeout"))
        self.assertEqual(self.paths(), ["/users/@me"])

    def test_cli_offers_no_deadline_override(self):
        import argparse
        parser = argparse.ArgumentParser()
        r.setup_parser(parser)
        options = {opt for action in parser._actions for opt in action.option_strings}
        self.assertEqual(options, {"-h", "--help", "--channel", "--message", "--emoji", "--json"})

    # --- pagination ------------------------------------------------------------
    def test_page_counts_and_completeness(self):
        for rows, pages in ((0, 1), (1, 1), (100, 2), (101, 2), (250, 3)):
            with self.subTest(rows=rows):
                self.setUp()
                self.discord.reactors = [{"id": i, "bot": True} for i in user_ids(rows)]
                code, doc = self.run_reader()
                self.assertEqual((code, len(doc["reactors"]), doc["reactor_pages"]), (0, rows, pages))
                self.assertEqual([x["id"] for x in doc["reactors"]], user_ids(rows))

    def test_approver_on_page_two_or_three(self):
        for position in (150, 230):
            with self.subTest(position=position):
                self.setUp()
                ids = user_ids(260, start=1000000000000000000)
                ids[position] = APPROVER
                self.discord.reactors = [{"id": i, "bot": n % 2 == 0} for n, i in enumerate(sorted(ids, key=int))]
                for row in self.discord.reactors:
                    if row["id"] == APPROVER:
                        row.pop("bot")
                code, doc = self.run_reader()
                self.assertEqual(code, 0)
                self.assertIn({"id": APPROVER, "bot": False}, doc["reactors"])
                self.assertEqual(len(doc["reactors"]), 260)

    def test_pagination_faults_never_return_a_partial_list(self):
        full = [{"id": i} for i in user_ids(100)]

        def dup_across(after):
            return full if after == 0 else [{"id": full[-1]["id"]}]

        def stalled(after):
            return full

        def dup_within(after):
            return [{"id": APPROVER}, {"id": APPROVER}]

        def malformed_id(after):
            return [{"id": 1022999767753691186}]

        def malformed_flag(after):
            return [{"id": APPROVER, "bot": "no"}]

        def not_a_list(after):
            return {"users": []}

        def too_many(after):
            return [{"id": i} for i in user_ids(101)]

        for name, hook, error in (("dup-across-pages", dup_across, "pagination_stalled"),
                                  ("stalled-cursor", stalled, "pagination_stalled"),
                                  ("dup-within-page", dup_within, "malformed_row"),
                                  ("malformed-id", malformed_id, "malformed_row"),
                                  ("malformed-bot-flag", malformed_flag, "malformed_row"),
                                  ("not-a-list", not_a_list, "malformed_response"),
                                  ("oversized-page", too_many, "malformed_response")):
            with self.subTest(case=name):
                self.setUp()
                self.discord.page_hook = hook
                self.assertError(5, error)

    def test_fifty_page_cap(self):
        self.discord.reactors = [{"id": i} for i in user_ids(50 * 100)]
        self.assertError(5, "pagination_overflow")
        self.assertEqual(sum("/reactions/" in p for p in self.paths()), 50)
        self.setUp()
        self.discord.reactors = [{"id": i} for i in user_ids(49 * 100 + 99)]
        code, doc = self.run_reader()
        self.assertEqual((code, doc["reactor_pages"], len(doc["reactors"])), (0, 50, 4999))

    # --- scope and freshness -----------------------------------------------------
    def test_scope_is_fixed_to_approvals(self):
        other = "1562236246158221999"
        for name, channel, record in (
            ("wrong-guild", CHANNEL, {"id": CHANNEL, "guild_id": "923752167293222999", "type": 0}),
            ("other-channel", other, {"id": other, "guild_id": GUILD, "type": 0}),
            ("thread-wrong-parent", other, {"id": other, "guild_id": GUILD, "type": 11, "parent_id": other}),
            ("non-thread-child", other, {"id": other, "guild_id": GUILD, "type": 0, "parent_id": CHANNEL}),
            ("dm", other, {"id": other, "type": 1}),
        ):
            with self.subTest(case=name):
                self.setUp()
                self.discord.channels[channel] = record
                self.assertError(3, "channel_not_allowed", channel=channel)
                self.assertFalse(any("/messages/" in p for p in self.paths()))

    def test_message_changed_or_deleted_between_reads(self):
        for name, second, error in (
            ("edited", dict(FakeDiscord(self).message, edited_timestamp="2026-11-08T23:05:00+00:00"),
             "message_changed"),
            ("content", dict(FakeDiscord(self).message, content="FACTORY MERGE APPROVAL REQUEST\nIssue: WD-1"),
             "message_changed"),
            ("deleted", 404, "message_deleted"),
        ):
            with self.subTest(case=name):
                self.setUp()
                self.discord.second_message = second
                self.assertError(5, error)

    def test_malformed_message_documents(self):
        for name, change in (("wrong-id", {"id": "1569128910862684999"}),
                             ("wrong-channel", {"channel_id": THREAD}),
                             ("author-missing", {"author": None}),
                             ("bot-flag-string", {"author": {"id": HARPER, "bot": "yes"}}),
                             ("content-not-string", {"content": 5})):
            with self.subTest(case=name):
                self.setUp()
                self.discord.message = dict(self.discord.message, **change)
                self.assertError(5, "malformed_response")

    # --- HTTP status handling ------------------------------------------------------
    def test_http_statuses(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                self.setUp()
                self.discord.overrides[f"/channels/{CHANNEL}"] = (
                    lambda req, path, s=status: (_ for _ in ()).throw(http_error(req.full_url, s, {"m": TOKEN})))
                self.assertError(5, "http_error", status=status)
        self.setUp()
        self.discord.overrides[f"/channels/{CHANNEL}/messages/"] = (
            lambda req, path: (_ for _ in ()).throw(http_error(req.full_url, 404, {})))
        self.assertError(5, "message_not_found", status=404)

    def test_rate_limits(self):
        seen = []

        def once(req, path):
            seen.append(path)
            if len(seen) == 1:
                raise http_error(req.full_url, 429, {"retry_after": 0.5})
            return None
        self.discord.overrides[f"/channels/{CHANNEL}"] = once
        code, doc = self.run_reader()
        self.assertEqual((code, self.sleeps), (0, [0.5]))

        self.setUp()
        self.discord.overrides[f"/channels/{CHANNEL}"] = (
            lambda req, path: (_ for _ in ()).throw(http_error(req.full_url, 429, {"retry_after": 10})))
        self.assertError(5, "rate_limited", status=429)
        self.assertEqual(self.sleeps, [])

        self.setUp()
        self.discord.overrides[f"/channels/{CHANNEL}"] = (
            lambda req, path: (_ for _ in ()).throw(http_error(req.full_url, 429, b"", {"Retry-After": "0.2"})))
        self.assertError(5, "rate_limited", status=429)  # retried once from the header, then gave up
        self.assertEqual(self.sleeps, [0.2])


class StaticGuardTests(unittest.TestCase):
    """AC-14 supporting guards. AC-13's executed cases, not these scans, prove egress."""

    SOURCES = ("reader.py", "__init__.py")

    def trees(self):
        return {name: ast.parse((PLUGIN / name).read_text(encoding="utf-8")) for name in self.SOURCES}

    def test_imports_are_stdlib_secret_scope_or_local(self):
        for name, tree in self.trees().items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], sys.stdlib_module_names, (name, alias.name))
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        self.assertEqual((node.module, [a.name for a in node.names]), (None, ["reader"]))
                    elif node.module == "agent.secret_scope":
                        self.assertEqual([a.name for a in node.names], ["get_secret"])
                    else:
                        self.assertIn(node.module.split(".")[0], sys.stdlib_module_names, (name, node.module))

    def test_no_file_env_or_dotenv_access(self):
        for name, tree in self.trees().items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"open", "exec", "eval", "__import__"}, name)
                if isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"environ", "getenv", "putenv", "system", "popen"}, name)
            source = (PLUGIN / name).read_text(encoding="utf-8")
            for needle in ("dotenv", ".env", "os.environ", "subprocess", "socket."):
                self.assertNotIn(needle, source, (name, needle))

    def test_only_get_requests_to_the_fixed_origin(self):
        source = (PLUGIN / "reader.py").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'method="([A-Z]+)"', source), ["GET"])
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertNotIn(f'"{verb}"', source)
        self.assertEqual(re.findall(r"https://[^\s\"']+", source.split('"""', 2)[2]),
                         ["https://discord.com/api/v10", "https://github.com/WolftacDigital/wolftac-factory,"])

    def test_exactly_one_cli_command_and_inert_registration(self):
        calls = []

        class Ctx:
            def register_cli_command(self, **kwargs):
                calls.append(kwargs)

            def __getattr__(self, attr):
                raise AssertionError("unexpected registration: " + attr)

        forbidden = lambda *a, **k: (_ for _ in ()).throw(AssertionError("I/O during import or register"))
        for mod in [m for m in sys.modules if m.startswith("wolftac_approval_read_probe")]:
            del sys.modules[mod]
        with patch("builtins.open", forbidden), patch("socket.socket", forbidden), \
                patch("socket.create_connection", forbidden), patch.object(threading.Thread, "start", forbidden), \
                patch("urllib.request.urlopen", forbidden):
            spec = importlib.util.spec_from_file_location(
                "wolftac_approval_read_probe", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
                module.register(Ctx())
            finally:
                for mod in [m for m in sys.modules if m.startswith("wolftac_approval_read_probe")]:
                    del sys.modules[mod]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "wolftac-approval-read")

    def test_manifest(self):
        text = (PLUGIN / "plugin.yaml").read_text(encoding="utf-8")
        self.assertIn("name: wolftac-approval-read", text)
        self.assertNotIn("provides_tools", text)
        self.assertNotIn("provides_hooks", text)
        self.assertEqual(sorted(p.name for p in PLUGIN.iterdir() if p.name != "__pycache__"),
                         ["__init__.py", "plugin.yaml", "reader.py"])


def hermes_cli():
    home = os.environ.get("LOCALAPPDATA")
    for candidate in ([os.path.join(home, "hermes", "bin", "hermes.exe")] if home else []) + [shutil.which("hermes")]:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


@unittest.skipUnless(hermes_cli(), "Hermes CLI not installed here (CI runs the offline suite only)")
class PluginChecksTests(unittest.TestCase):
    """AC-13 plugin checks. Both are local and inert: they import and probe the source tree only."""

    def test_validate_is_clean(self):
        proc = subprocess.run([hermes_cli(), "plugins", "validate", str(PLUGIN), "--json"],
                              capture_output=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:])
        report = json.loads(proc.stdout.decode("utf-8"))
        self.assertTrue(report["ok"])
        self.assertEqual(report["warnings"], [])

    def test_doctor_is_clean(self):
        proc = subprocess.run([hermes_cli(), "plugins", "doctor", str(PLUGIN), "--ci"],
                              capture_output=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
