"""Offline behavior tests: fake HTTP, fake Popen, temporary state and credentials."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("gatekeeper", Path(__file__).resolve().parents[1] / "factory-gatekeeper.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)
LINEAR = "lin_TEST_SENTINEL"
SLACK = "xoxb-TEST-SENTINEL"
NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)


def connection(nodes, cursor=None):
    return {"nodes": copy.deepcopy(nodes), "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def comment(body="human decision", created="2026-09-19T12:00:00Z"):
    return {"id": "comment-1", "createdAt": created, "body": body}


def issue(identifier="WD-1", state="Todo"):
    return {"identifier": identifier, "state": {"name": state}, "assignee": {"id": "operator"},
            "project": {"name": "project"}, "priority": 2, "description": "approved spec",
            "labels": connection([{"name": "Factory Queue"}, {"name": "agentic"}]),
            "comments": {"nodes": [comment()]},
            "inverseRelations": connection([{"type": "blocks", "issue": {"identifier": "WD-0", "state": {"name": "Done"}}}])}


def proof(ts="1789819979335359", channel="C123", created="2026-09-19T13:00:00Z", marker="MERGE PROOF PACK"):
    return comment(f"{marker}\nhttps://example.slack.com/archives/{channel}/p{ts}", created)


class GatekeeperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"
        self.jobs = self.root / "jobs.json"
        self.state = self.root / "state.json"
        self.policy = self.root / "approval-policy.json"
        self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={LINEAR}\nSLACK_FACTORY_BOT_TOKEN={SLACK}\n", encoding="utf-8")
        self.write(self.jobs, {"jobs": [{"id": pair[1], "last_run_at": "2026-09-19T10:00:00Z"}
                                       for pair in g.DEFAULT_JOB_MAP.values()]})
        self.write(self.policy, {"slack_channel_id": "C123", "approver_slack_user_id": "U123"})
        self.issues = {"Todo": [issue()], "In Review": [], "Approved": []}
        self.reactions = [{"name": "rocket", "users": ["U123"]}]
        self.linear_error = None
        self.slack_error = None
        self.requests = []
        self.args = ["gatekeeper", "--env-path", str(self.env), "--jobs-path", str(self.jobs),
                     "--state-path", str(self.state), "--approval-policy", str(self.policy),
                     "--exec-override", json.dumps(["fake-noop"])]
        self.addCleanup(patch.stopall)
        patch.object(g, "hermes_home", return_value=str(self.root)).start()
        patch.object(g, "now_utc", return_value=NOW).start()
        self.http = patch.object(g.urllib.request, "urlopen", side_effect=self.open).start()
        self.spawn = patch.object(g.subprocess, "Popen", return_value=SimpleNamespace(pid=1234)).start()
        self.output = []

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def open(self, request, timeout):
        self.requests.append(request)
        self.assertEqual(timeout, 20)
        if request.full_url == "https://api.linear.app/graphql":
            self.assertEqual(request.get_header("Authorization"), "Bearer " + LINEAR)
            data = json.loads(request.data)
            self.assertNotIn("mutation", data["query"].lower())
            variables = data["variables"]
            if self.linear_error and variables.get("state") == "Todo":
                if isinstance(self.linear_error, Exception):
                    raise self.linear_error
                return io.StringIO(json.dumps(self.linear_error))
            rows = self.issues[variables["state"]]
            offset = int(variables["after"] or 0)
            following = str(offset + 100) if len(rows) > offset + 100 else None
            return io.StringIO(json.dumps({"data": {"issues": connection(rows[offset:offset + 100], following)}}))
        self.assertTrue(request.full_url.startswith("https://slack.com/api/reactions.get?"))
        self.assertEqual(request.get_header("Authorization"), "Bearer " + SLACK)
        if isinstance(self.slack_error, Exception):
            raise self.slack_error
        payload = self.slack_error or {"ok": True, "message": {"reactions": self.reactions}}
        return io.StringIO(json.dumps(payload))

    def tick(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        self.spawn.reset_mock()
        self.http.reset_mock()
        self.requests.clear()
        with patch.object(g.sys, "argv", self.args + list(extra)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(g.main(), 0)
        output = stdout.getvalue() + stderr.getvalue()
        self.output.append(output)
        return output

    def baseline(self):
        self.write(self.state, {"lanes": {}, "fingerprints": {
            lane: g.lane_fingerprint(self.issues[state]) for lane, (state, _) in g.DEFAULT_JOB_MAP.items()
        }, "approval_dispatched": {}})

    def confirm(self):
        jobs = json.loads(self.jobs.read_text())
        for job in jobs["jobs"]:
            job["last_run_at"] = NOW.isoformat()
        self.write(self.jobs, jobs)

    def with_pack(self):
        self.issues["Todo"] = []
        self.issues["Approved"] = [issue(state="Approved")]
        self.issues["Approved"][0]["comments"]["nodes"].append(proof())
        self.baseline()

    def test_unchanged_lane_skips(self):
        self.fail("WD-380 AC-16 intentional CI tripwire; revert after red proof")
        self.assertIn("no-baseline -> dispatched(pid=1234)", self.tick())
        self.confirm()
        out = self.tick()
        self.assertIn("build: confirmed-ran", out)
        self.assertIn("build: unchanged (skip)", out)
        self.spawn.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["fingerprints"]["build"], g.lane_fingerprint(self.issues["Todo"]))

    def test_each_meaningful_change_dispatches(self):
        changes = {
            "comment": lambda rows: rows[0]["comments"]["nodes"].append(comment("new", "2026-09-19T14:00:00Z")),
            "state": lambda rows: rows[0]["state"].update(name="Other"),
            "assignee": lambda rows: rows[0].update(assignee={"id": "someone-else"}),
            "label": lambda rows: rows[0]["labels"]["nodes"].append({"name": "new"}),
            "project": lambda rows: rows[0].update(project={"name": "new"}),
            "priority": lambda rows: rows[0].update(priority=1),
            "description": lambda rows: rows[0].update(description="changed"),
            "blocker": lambda rows: rows[0]["inverseRelations"]["nodes"][0]["issue"]["state"].update(name="Todo"),
            "enters-lane": lambda rows: rows.append(issue("WD-3")),
            "leaves-lane": lambda rows: rows.pop(),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.issues["Todo"] = [issue(), issue("WD-2")]
                self.baseline()
                change(self.issues["Todo"])
                self.assertIn("build: changed -> dispatched(pid=1234)", self.tick())
                self.spawn.assert_called_once()

    def test_swept_and_delegate_are_ignored(self):
        self.baseline()
        self.issues["Todo"][0]["comments"]["nodes"].append(comment("SWEPT: stale claim", "2026-09-19T15:00:00Z"))
        self.issues["Todo"][0].update(delegate={"id": "bot"}, updatedAt="2026-09-19T15:00:00Z")
        self.assertIn("build: unchanged (skip)", self.tick())
        self.spawn.assert_not_called()

    def test_canonical_order_and_comment_boundaries(self):
        rows = [issue(), issue("WD-2")]
        baseline = g.lane_fingerprint(rows)
        rows.reverse()
        rows[1]["labels"]["nodes"].reverse()
        rows[1]["inverseRelations"]["nodes"].append({"type": "related", "issue": {"identifier": "WD-9", "state": {"name": "Todo"}}})
        self.assertEqual(baseline, g.lane_fingerprint(rows))
        rows[0]["comments"]["nodes"] = []
        empty = g.lane_fingerprint(rows)
        rows[0]["comments"]["nodes"] = [comment("SWEPT: old") for _ in range(50)]
        swept = g.lane_fingerprint(rows)
        self.assertNotEqual(empty, swept)
        rows[0]["comments"]["nodes"] = [comment("SWEPT: newer", "2026-09-19T16:00:00Z") for _ in range(50)]
        self.assertEqual(swept, g.lane_fingerprint(rows))
        rows[0]["comments"]["nodes"].append(comment("not a sweep"))
        self.assertNotEqual(swept, g.lane_fingerprint(rows))

    def test_empty_lane_is_idle(self):
        for has_baseline in (False, True):
            with self.subTest(baseline=has_baseline):
                self.issues["Todo"] = []
                if has_baseline:
                    self.baseline()
                out = self.tick()
                self.assertIn("build: idle", out)
                self.spawn.assert_not_called()

    def test_no_baseline_dispatches(self):
        self.assertIn("build: no-baseline -> dispatched(pid=1234)", self.tick())
        state = json.loads(self.state.read_text())
        self.assertEqual(state["fingerprints"]["build"], g.lane_fingerprint(self.issues["Todo"]))
        self.assertIn("build", state["lanes"])
        self.spawn.assert_called_once()
        kwargs = self.spawn.call_args.kwargs
        if os.name == "nt":
            self.assertEqual(kwargs["creationflags"], 0x208)
        else:
            self.assertTrue(kwargs["start_new_session"])

    def test_corrupt_state_file(self):
        for contents in ("broken json", "[]", '{"lanes":null,"fingerprints":[],"approval_dispatched":false}',
                         '{"fingerprints":{"build":"not-a-hash"}}'):
            with self.subTest(contents=contents):
                self.state.write_text(contents)
                self.assertIn("build: no-baseline", self.tick())
                self.spawn.assert_called_once()

    def test_pagination_covers_all_pages(self):
        self.issues["Todo"] = [issue(f"WD-{n}") for n in range(101)]
        self.baseline()
        self.issues["Todo"][-1]["description"] = "last page changed"
        self.assertIn("build: changed -> dispatched", self.tick())
        pages = [json.loads(r.data)["variables"]["after"] for r in self.requests
                 if r.data and json.loads(r.data)["variables"]["state"] == "Todo"]
        self.assertEqual(pages, [None, "100"])
        self.spawn.assert_called_once()

    def test_nested_pagination_and_nonadvancing_cursor(self):
        row = issue()
        row["inverseRelations"]["pageInfo"] = {"hasNextPage": True, "endCursor": "r1"}
        extra = {"type": "blocks", "issue": {"identifier": "WD-99", "state": {"name": "Todo"}}}
        responses = [{"data": {"issues": connection([row])}},
                     {"data": {"issue": {"inverseRelations": connection([extra])}}}]
        with patch.object(g.urllib.request, "urlopen", side_effect=[io.StringIO(json.dumps(x)) for x in responses]):
            rows = g.lane_issues(LINEAR, "Todo")
        self.assertEqual(rows[0]["inverseRelations"]["nodes"][-1], extra)
        with self.assertRaises(g.ProbeError):
            g.next_cursor({"pageInfo": {"hasNextPage": True, "endCursor": "repeat"}}, {"repeat"})

    def test_slack_rocket_dispatches_once(self):
        self.with_pack()
        self.assertIn("integrate: slack-approval WD-1 -> dispatched(pid=1234)", self.tick())
        self.spawn.assert_called_once()
        state = json.loads(self.state.read_text())
        self.assertEqual(state["approval_dispatched"], {"WD-1": "1789819979.335359"})
        self.confirm()
        self.assertIn("integrate: unchanged (skip)", self.tick())
        self.spawn.assert_not_called()
        self.assertFalse(any("slack.com" in r.full_url for r in self.requests))

    def test_slack_negative_cases(self):
        for case in ("other-user", "other-reaction", "other-channel", "no-permalink", "newest-no-permalink"):
            with self.subTest(case=case):
                self.with_pack()
                self.reactions = [{"name": "rocket", "users": ["U123"]}]
                packs = self.issues["Approved"][0]["comments"]["nodes"]
                if case == "other-user":
                    self.reactions[0]["users"] = ["U999"]
                elif case == "other-reaction":
                    self.reactions[0]["name"] = "thumbsup"
                elif case == "other-channel":
                    packs[-1] = proof(channel="C999")
                elif case == "no-permalink":
                    packs[-1] = comment("MERGE PROOF PACK", "2026-09-19T13:00:00Z")
                else:
                    packs.append(comment("LANDING PROOF PACK no URL", "2026-09-19T14:00:00Z"))
                self.baseline()
                self.assertIn("integrate: unchanged (skip)", self.tick())
                self.spawn.assert_not_called()
                self.assertLessEqual(sum("slack.com" in r.full_url for r in self.requests), 1)

    def test_new_pack_ts_dispatches_again(self):
        self.with_pack()
        self.tick()
        self.confirm()
        self.tick()
        self.issues["Approved"][0]["comments"]["nodes"].append(proof("1789819979444444", created="2026-09-19T14:00:00Z", marker="LANDING PROOF PACK"))
        state = json.loads(self.state.read_text())
        state["fingerprints"]["integrate"] = g.lane_fingerprint(self.issues["Approved"])
        self.write(self.state, state)
        self.assertIn("integrate: slack-approval WD-1 -> dispatched", self.tick())
        self.assertEqual(json.loads(self.state.read_text())["approval_dispatched"]["WD-1"], "1789819979.444444")

    def test_slack_check_failures(self):
        for case in ("missing-token", "missing-policy", "invalid-policy", "empty-policy", "http", "ok-false"):
            with self.subTest(case=case):
                self.with_pack()
                self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={LINEAR}\nSLACK_FACTORY_BOT_TOKEN={SLACK}\n")
                self.write(self.policy, {"slack_channel_id": "C123", "approver_slack_user_id": "U123"})
                self.slack_error = None
                if case == "missing-token":
                    self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={LINEAR}\n")
                elif case == "missing-policy":
                    self.policy.unlink()
                elif case == "invalid-policy":
                    self.policy.write_text("broken")
                elif case == "empty-policy":
                    self.write(self.policy, {})
                elif case == "http":
                    self.slack_error = OSError("remote echoed " + SLACK)
                else:
                    self.slack_error = {"ok": False, "error": SLACK}
                # Integrate FIRST demonstrates that failures do not starve later lanes.
                mapping = dict(reversed(list(g.DEFAULT_JOB_MAP.items())))
                out = self.tick("--job-map", json.dumps(mapping))
                self.assertIn("integrate: slack-check-failed (", out)
                self.assertIn("build: idle", out)
                self.assertNotIn(SLACK, out)
                self.spawn.assert_not_called()

    def test_linear_failure_fails_open(self):
        for failure in (OSError("echo " + LINEAR), {"errors": [{"message": LINEAR}]}):
            with self.subTest(failure=type(failure).__name__):
                self.baseline()
                self.linear_error = failure
                out = self.tick()
                self.assertIn("build: probe failed", out)
                self.assertIn("FAIL-OPEN -> dispatched", out)
                self.assertIn("review: idle", out)
                self.assertIn("integrate: idle", out)
                self.assertNotIn(LINEAR, out)
                self.spawn.assert_called_once()
                self.assertNotIn("build", json.loads(self.state.read_text())["fingerprints"])

    def test_inflight_guard_and_ttl_clears_fingerprint(self):
        self.with_pack()
        self.baseline()
        state = json.loads(self.state.read_text())
        state["lanes"]["integrate"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        self.write(self.state, state)
        self.assertIn("integrate: in-flight (skip)", self.tick())
        self.spawn.assert_not_called()
        self.assertFalse(any("slack.com" in r.full_url for r in self.requests))
        self.assertFalse(any(r.data and json.loads(r.data)["variables"]["state"] == "Approved" for r in self.requests))
        state["lanes"]["integrate"]["dispatched_at"] = (NOW - timedelta(minutes=91)).isoformat()
        self.write(self.state, state)
        out = self.tick()
        self.assertIn("integrate: no-run-observed", out)
        self.assertIn("integrate: no-baseline -> dispatched", out)
        self.spawn.assert_called_once()

    def test_change_during_inflight_is_not_absorbed(self):
        self.tick()
        self.issues["Todo"][0]["description"] = "human amended while running"
        self.confirm()
        out = self.tick()
        self.assertIn("build: confirmed-ran", out)
        self.assertIn("build: changed -> dispatched", out)
        self.spawn.assert_called_once()

    def test_dry_run_writes_nothing(self):
        for mode in ("new", "changed", "same", "slack", "fail-open", "inflight", "expired"):
            with self.subTest(mode=mode):
                self.issues = {"Todo": [issue()], "In Review": [], "Approved": []}
                self.linear_error = None
                self.baseline()
                if mode == "new":
                    self.state.write_text('{"lanes":{}}')
                elif mode == "changed":
                    self.issues["Todo"][0]["description"] = "new"
                elif mode == "slack":
                    self.with_pack()
                elif mode == "fail-open":
                    self.linear_error = OSError("failed")
                elif mode in ("inflight", "expired"):
                    state = json.loads(self.state.read_text())
                    stamp = NOW if mode == "inflight" else NOW - timedelta(minutes=91)
                    state["lanes"]["build"] = {"dispatched_at": stamp.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
                    self.write(self.state, state)
                before = self.state.read_bytes()
                out = self.tick("--dry-run")
                self.assertEqual(before, self.state.read_bytes())
                self.spawn.assert_not_called()
                if mode not in ("same", "inflight"):
                    self.assertIn("dispatched(dry-run)", out)
        self.state.unlink()
        self.tick("--dry-run")
        self.assertFalse(self.state.exists())

    def test_no_slack_and_process_environment_ignored(self):
        self.with_pack()
        self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={LINEAR}\n")
        with patch.dict(os.environ, {"SLACK_FACTORY_BOT_TOKEN": SLACK}):
            self.assertIn("slack-check-failed", self.tick())
        self.assertIn("integrate: unchanged (skip)", self.tick("--no-slack"))
        self.spawn.assert_not_called()
        self.env.unlink()
        with patch.dict(os.environ, {"LINEAR_FACTORY_BOT_TOKEN": LINEAR}):
            self.assertIn("FAIL-OPEN (env unreadable", self.tick("--dry-run"))

    def test_spawn_failure_does_not_consume_baseline_or_approval(self):
        self.with_pack()
        self.spawn.side_effect = OSError(LINEAR + SLACK)
        out = self.tick()
        self.assertIn("dispatch-failed(OSError)", out)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["lanes"], {})
        self.assertEqual(state["approval_dispatched"], {})
        self.assertNotIn(LINEAR, out)
        self.assertNotIn(SLACK, out)

    def test_tokens_never_leak(self):
        self.with_pack()
        self.tick()
        self.confirm()
        self.tick()
        self.baseline()
        self.slack_error = OSError(LINEAR + SLACK)
        self.tick()
        self.issues["Todo"] = [issue()]
        self.linear_error = {"errors": [{"message": LINEAR + SLACK}]}
        self.tick()
        artifacts = "".join(self.output) + self.state.read_text()
        for log in (self.root / "cron/gatekeeper-logs").glob("*.log"):
            artifacts += log.read_text()
        for token in (LINEAR, SLACK):
            self.assertNotIn(token, artifacts)

    def test_read_only_traffic(self):
        self.with_pack()
        self.tick()
        self.assertTrue(any("slack.com" in r.full_url for r in self.requests))
        for request in self.requests:
            if request.data:
                self.assertEqual(request.get_method(), "POST")
                self.assertNotIn("mutation", json.loads(request.data)["query"].lower())
            else:
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(g.urllib.parse.urlparse(request.full_url).path, "/api/reactions.get")
                params = g.urllib.parse.parse_qs(g.urllib.parse.urlparse(request.full_url).query)
                self.assertEqual(params["timestamp"], ["1789819979.335359"])
                self.assertEqual(params["full"], ["true"])


if __name__ == "__main__":
    unittest.main()
