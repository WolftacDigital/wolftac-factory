"""WD-580: execute the real tick against fake HTTP and isolated scratch state."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from datetime import timedelta
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

import test_factory_gatekeeper as base

g = base.g
NOW = base.NOW
TOKEN = base.LINEAR


def done(identifier, bodies):
    return {"identifier": identifier, "completedAt": (NOW - timedelta(hours=2)).isoformat(),
            "comments": base.connection([{"body": body} for body in bodies])}


class AuditTests(unittest.TestCase):
    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd580-gk-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / "fixture.env"
        self.jobs = self.root / "jobs.json"
        self.state = self.root / "state.json"
        self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
        self.write(self.jobs, {"jobs": []})
        self.args = ["gatekeeper", "--env-path", str(self.env), "--jobs-path", str(self.jobs),
                     "--state-path", str(self.state), "--no-slack", "--exec-override", '["fake-noop"]']
        self.issues = {"Todo": [base.issue()], "In Review": [], "Approved": []}
        self.rows = []
        self.audit_error = None
        self.linear_error = None
        self.comment_pages = {}
        self.repeat_cursor = False
        self.requests = []
        for name, value in [("hermes_home", str(self.root)), ("now_utc", NOW)]:
            mocker = patch.object(g, name, return_value=value)
            mocker.start()
            self.addCleanup(mocker.stop)
        http = patch.object(g.urllib.request, "urlopen", side_effect=self.open)
        self.http = http.start()
        self.addCleanup(http.stop)
        spawn = patch.object(g.subprocess, "Popen", return_value=SimpleNamespace(pid=1234))
        self.spawn = spawn.start()
        self.addCleanup(spawn.stop)

    def open(self, request, timeout):
        data = json.loads(request.data)
        self.requests.append(request)
        self.assertEqual(request.full_url, "https://api.linear.app/graphql")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(timeout, 20)
        self.assertNotIn("mutation", data["query"].lower())
        if "state" in data["variables"]:
            if self.linear_error and data["variables"]["state"] == "Todo":
                raise self.linear_error
            return io.StringIO(json.dumps({"data": {"issues": base.connection(self.issues[data["variables"]["state"]])}}))
        if self.audit_error:
            if isinstance(self.audit_error, Exception):
                raise self.audit_error
            return io.StringIO(json.dumps(self.audit_error))
        variables = data["variables"]
        if "completedAt" in data["query"]:
            offset = int(variables["after"] or 0)
            page = base.connection(self.rows[offset:offset + 50],
                                   str(offset + 50) if len(self.rows) > offset + 50 else None)
            if self.repeat_cursor:
                page["pageInfo"] = {"hasNextPage": True, "endCursor": "50"}
            payload = {"issues": page}
        else:
            payload = {"issue": {"comments": self.comment_pages[variables["id"]]}}
        return io.StringIO(json.dumps({"data": payload}))

    def tick(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        self.spawn.reset_mock()
        self.http.reset_mock()
        self.requests.clear()
        with patch.object(g.sys, "argv", self.args + list(extra)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(g.main(), 0)
        output = stdout.getvalue() + stderr.getvalue()
        artifacts = output + (self.state.read_text() if self.state.exists() else "")
        self.assertNotIn(TOKEN, artifacts)
        for request in self.requests:
            self.assertEqual(request.full_url, "https://api.linear.app/graphql")
            self.assertEqual(request.get_method(), "POST")
            self.assertNotIn("mutation", json.loads(request.data)["query"].lower())
        return output

    def audit_requests(self):
        return [json.loads(r.data) for r in self.requests if "completedAt" in json.loads(r.data)["query"]]

    def test_first_window_real_formats_and_numeric_order(self):
        self.rows = [done("WD-413", ["Merged to main (#82)"]), done("WD-371", ["plain"]),
                     done("WD-424", ["**INTEGRATION REPORT — WD-424**"]),
                     done("WD-437", ["**Agent:** Hermes / x\n\n**INTEGRATION REPORT — MERGED**"])]
        out = self.tick()
        self.assertIn("audit: done-without-integration: WD-371,WD-413 (window ", out)
        requests = self.audit_requests()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["variables"], {"after": None,
                         "from": (NOW - timedelta(days=7)).isoformat(),
                         "to": (NOW - timedelta(minutes=60)).isoformat()})
        for fragment in ('team: { key: { eq: "WD" } }', 'labels: { name: { eq: "Factory Queue" } }',
                         'state: { name: { eq: "Done" } }', 'completedAt: { gte: $from, lt: $to }',
                         '$from: DateTimeOrDuration!', '$to: DateTimeOrDuration!'):
            self.assertIn(fragment, requests[0]["query"])
        self.assertEqual(json.loads(self.state.read_text())["audit"], {
            "found": ["WD-371", "WD-413"], "covered_until": (NOW - timedelta(hours=1)).isoformat(),
            "last_ok_at": NOW.isoformat()})

    def test_not_due_and_next_window(self):
        self.rows = [done("WD-371", [])]
        self.tick("--audit-only")
        with patch.object(g, "now_utc", return_value=NOW + timedelta(hours=1)):
            self.assertIn("WD-371 (last audit ", self.tick("--audit-only"))
            self.assertEqual(self.audit_requests(), [])
        self.rows = []
        with patch.object(g, "now_utc", return_value=NOW + timedelta(hours=24)):
            self.assertIn("audit: clean (window ", self.tick("--audit-only"))
            self.assertEqual(self.audit_requests()[0]["variables"]["from"], (NOW - timedelta(hours=1)).isoformat())
        self.assertEqual(json.loads(self.state.read_text())["audit"]["found"], [])

    def test_comments_page_120_and_midtext_marker(self):
        row = done("WD-120", ["plain"] * 100)
        row["comments"]["pageInfo"] = {"hasNextPage": True, "endCursor": "100"}
        self.comment_pages["WD-120"] = base.connection([{"body": "plain"}] * 19 +
            [{"body": "**INTEGRATION REPORT**"}] + [{"body": "plain"}] * 30)
        self.rows = [row, done("WD-10", ["Mention INTEGRATION REPORT here\n**INTEGRATION REPORT**"])]
        self.assertIn("done-without-integration: WD-10 (window", self.tick("--audit-only"))
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.state.read_text())["audit"]["found"], ["WD-10"])

    def test_marker_variants(self):
        for body in ["INTEGRATION REPORT", " \n### INTEGRATION REPORT", "- **Agent:** A\n\n**INTEGRATION REPORT**"]:
            with self.subTest(body=body):
                self.assertIsNotNone(g.INTEGRATION_REPORT.match(body))
        for body in ["quoted INTEGRATION REPORT", "> INTEGRATION REPORT", "BUILD REPORT\nINTEGRATION REPORT", "INTEGRATION REPORTER"]:
            self.assertIsNone(g.INTEGRATION_REPORT.match(body))

    def test_issue_pages_and_numeric_sort(self):
        self.rows = [done(f"WD-{n}", []) for n in reversed(range(101))]
        self.tick("--audit-only")
        self.assertEqual([r["variables"]["after"] for r in self.audit_requests()], [None, "50", "100"])
        self.assertEqual(json.loads(self.state.read_text())["audit"]["found"], [f"WD-{n}" for n in range(101)])

    def test_nonadvancing_cursors_fail_without_checkpoint(self):
        row = done("WD-120", ["plain"])
        row["comments"]["pageInfo"] = {"hasNextPage": True, "endCursor": "repeat"}
        self.rows = [row]
        self.comment_pages["WD-120"] = copy.deepcopy(row["comments"])
        self.assertIn("audit: failed (Linear pagination did not advance)", self.tick("--audit-only"))
        self.assertEqual(json.loads(self.state.read_text())["audit"], {"pending_since": NOW.isoformat()})
        self.rows = [done("WD-1", [])]
        self.repeat_cursor = True
        self.assertIn("Linear pagination did not advance", self.tick("--audit-only"))

    def test_lane_isolation_success_http_and_graphql_failure(self):
        initial = {"lanes": {}, "fingerprints": {}, "approval_dispatched": {"WD-9": "123"}}
        self.write(self.state, initial)
        control = self.tick("--no-audit")
        control_state = json.loads(self.state.read_text())
        control_calls = [copy.deepcopy(c.args) for c in self.spawn.call_args_list]
        failures = [None, urllib.error.HTTPError("https://api.linear.app/graphql", 500, TOKEN, Message(), None),
                    {"errors": [{"message": TOKEN}]}]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                self.write(self.state, initial)
                self.audit_error = failure
                out = self.tick()
                self.assertEqual(out.split("; audit:")[0], control.rstrip())
                actual = json.loads(self.state.read_text())
                for field in ("lanes", "fingerprints", "approval_dispatched"):
                    self.assertEqual(actual[field], control_state[field])
                # Log file handles differ; argv and all non-handle dispatch parameters do not.
                self.assertEqual([c.args for c in self.spawn.call_args_list], control_calls)
                if failure:
                    self.assertIn("audit: failed (", out)
                    self.assertEqual(actual["audit"], {"pending_since": NOW.isoformat()})

    def test_failed_audit_retains_previous_findings_and_checkpoint(self):
        old = {"covered_until": (NOW - timedelta(hours=50)).isoformat(),
               "last_ok_at": (NOW - timedelta(hours=49)).isoformat(), "found": ["WD-371"]}
        self.write(self.state, {"audit": old})
        self.audit_error = {"errors": [{"message": TOKEN}]}
        out = self.tick("--audit-only")
        self.assertIn("audit: overdue (last ok ", out)
        self.assertIn("audit: done-without-integration: WD-371 (last audit ", out)
        self.assertEqual(json.loads(self.state.read_text())["audit"], old)

    def test_first_failure_retries_and_overdue_after_48_hours(self):
        self.audit_error = TimeoutError(TOKEN)
        self.tick("--audit-only")
        with patch.object(g, "now_utc", return_value=NOW + timedelta(hours=48)):
            self.assertIn("audit: overdue (last ok never)", self.tick("--audit-only"))
            self.assertEqual(len(self.audit_requests()), 1)
        self.audit_error = None
        self.tick("--audit-only")
        self.assertNotIn("pending_since", json.loads(self.state.read_text())["audit"])

    def test_lane_401_skips_audit(self):
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, TOKEN, Message(), None)
        self.assertIn("audit: skipped (linear-auth-failed)", self.tick())
        self.assertEqual(self.audit_requests(), [])

    def test_dry_run_and_flags(self):
        self.write(self.state, {"audit": {"last_ok_at": NOW.isoformat(), "found": []}})
        before = self.state.read_bytes()
        out = self.tick("--dry-run", "--audit-only", "--audit-since", "2026-09-01T00:00:00Z")
        self.assertIn(" (dry-run)", out)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.audit_requests()[0]["variables"]["from"], "2026-09-01T00:00:00+00:00")
        self.spawn.assert_not_called()
        self.assertNotIn("build:", out)
        self.assertNotIn("audit:", self.tick("--no-audit"))
        self.assertEqual(self.audit_requests(), [])

    def test_since_rejects_missing_timezone(self):
        with patch.object(g.sys, "argv", self.args + ["--audit-since", "2026-09-01"]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stopped:
            g.main()
        self.assertEqual(stopped.exception.code, 2)
        self.http.assert_not_called()

    def test_missing_token_and_invalid_state_are_safe(self):
        self.env.unlink()
        self.write(self.state, {"audit": []})
        self.assertIn("audit: failed (Linear token unavailable)", self.tick("--audit-only"))
        self.http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
