"""Offline behavior tests: fake HTTP, fake Popen, temporary state and credentials."""
import contextlib
import copy
import importlib.util
import io
import itertools
import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("gatekeeper", Path(__file__).resolve().parents[1] / "factory-gatekeeper.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)
LINEAR = "lin_TEST_SENTINEL"
SLACK = "xoxb-TEST-SENTINEL"
NOW = datetime(2026, 9, 19, 20, 0, tzinfo=timezone.utc)
REAL_POPEN = subprocess.Popen


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
        self.comment_pages = {}
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
            if data["query"] == g.COMMENTS_QUERY:
                page = self.comment_pages[(variables["id"], variables["after"])]
                if isinstance(page, Exception):
                    raise page
                return io.StringIO(json.dumps({"data": {"issue": {"comments": page}}}))
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
            lane: g.lane_fingerprint(self.issues[state]) for lane, (state, *_) in g.DEFAULT_JOB_MAP.items()
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

    def test_linear_401_dispatches_only_integrate_in_every_lane_order(self):
        for order in itertools.permutations(g.DEFAULT_JOB_MAP):
            for rejected_lane in g.DEFAULT_JOB_MAP:
                with self.subTest(order=order, rejected_lane=rejected_lane):
                    self.issues = {state: [issue(state=state)] for state, *_ in g.DEFAULT_JOB_MAP.values()}
                    self.baseline()
                    # Earlier successful build/review probes would dispatch if
                    # the implementation did not wait for the later 401.
                    for state in ("Todo", "In Review"):
                        self.issues[state][0]["description"] = "changed after baseline"
                    mapping = {lane: g.DEFAULT_JOB_MAP[lane] for lane in order}
                    rejected_state = mapping[rejected_lane][0]

                    def opener(request, timeout):
                        if request.data and json.loads(request.data)["variables"].get("state") == rejected_state:
                            raise urllib.error.HTTPError(request.full_url, 401, LINEAR, {}, None)
                        return self.open(request, timeout)

                    with patch.object(g.urllib.request, "urlopen", side_effect=opener), \
                            patch.object(g, "dispatch", wraps=g.dispatch) as dispatch:
                        out = self.tick("--job-map", json.dumps(mapping))
                    for lane in ("build", "review"):
                        self.assertIn(f"{lane}: linear-auth-failed (401) -> skipped", out)
                    self.assertIn("integrate: linear-auth-failed (401) -> dispatched(pid=1234) for re-mint", out)
                    self.spawn.assert_called_once()
                    dispatch.assert_called_once_with(g.DEFAULT_JOB_MAP["integrate"][1], "integrate", False,
                                                     ["fake-noop"], "integrator")
                    state = json.loads(self.state.read_text())
                    self.assertEqual(set(state["lanes"]), {"integrate"})
                    self.assertEqual(state["fingerprints"], {})
                    self.assertEqual(state["approval_dispatched"], {})
                    self.assertNotIn("FAIL-OPEN", out)
                    self.assertNotIn(LINEAR, out)
                    self.assertFalse(any("slack.com" in r.full_url for r in self.requests))

    def test_two_phase_dispatch_preserves_each_lane_profile(self):
        i = self.args.index("--exec-override")
        del self.args[i:i + 2]
        for legacy in (False, True):
            for order in itertools.permutations(g.DEFAULT_JOB_MAP):
                for rejected in (False, True):
                    with self.subTest(legacy=legacy, order=order, rejected=rejected):
                        mapping = {lane: g.DEFAULT_JOB_MAP[lane][:2] if legacy else g.DEFAULT_JOB_MAP[lane]
                                   for lane in order}
                        self.issues = {state: [issue(state=state)] for state, *_ in mapping.values()}
                        self.write(self.state, {})
                        self.linear_error = (urllib.error.HTTPError(
                            "https://api.linear.app/graphql", 401, LINEAR, {}, None) if rejected else None)
                        with patch.object(g, "hermes_cli", return_value=["hermes"]):
                            self.tick("--job-map", json.dumps(mapping))
                        expected = []
                        for lane, entry in mapping.items():
                            if rejected and lane != "integrate":
                                continue
                            profile = [] if legacy else ["-p", entry[2]]
                            expected.append(["hermes", *profile, "cron", "run", entry[1]])
                        self.assertEqual([call.args[0] for call in self.spawn.call_args_list], expected)

    def test_linear_401_on_later_page_vetoes_earlier_success(self):
        self.issues["In Review"] = [issue(f"WD-{n}", "In Review") for n in range(101)]

        def opener(request, timeout):
            variables = json.loads(request.data)["variables"]
            if variables.get("state") == "In Review" and variables.get("after") == "100":
                raise urllib.error.HTTPError(request.full_url, 401, LINEAR, {}, None)
            return self.open(request, timeout)

        with patch.object(g.urllib.request, "urlopen", side_effect=opener):
            out = self.tick()
        self.assertIn("build: linear-auth-failed (401) -> skipped", out)
        self.assertIn("integrate: linear-auth-failed (401) -> dispatched", out)
        self.spawn.assert_called_once()

    def test_linear_401_keeps_integrate_inflight_guard(self):
        self.baseline()
        state = json.loads(self.state.read_text())
        marker = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        state["lanes"]["integrate"] = marker
        self.write(self.state, state)
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, {}, None)
        out = self.tick()
        self.assertIn("integrate: in-flight (skip)", out)
        self.assertIn("build: linear-auth-failed (401) -> skipped", out)
        self.assertIn("review: linear-auth-failed (401) -> skipped", out)
        self.spawn.assert_not_called()
        after = json.loads(self.state.read_text())
        self.assertEqual(after["lanes"]["integrate"], marker)
        self.assertEqual(after["fingerprints"], {"integrate": state["fingerprints"]["integrate"]})
        self.assertFalse(any(json.loads(r.data)["variables"].get("state") == "Approved" for r in self.requests))

    def test_other_probe_failures_keep_fail_open_for_each_ready_lane(self):
        failures = [urllib.error.URLError(LINEAR), TimeoutError(LINEAR),
                    urllib.error.HTTPError("https://api.linear.app/graphql", 500, LINEAR, {}, None), "invalid-json"]
        for failure in failures:
            for inflight in (False, True):
                with self.subTest(failure=type(failure).__name__, inflight=inflight):
                    self.baseline()
                    if inflight:
                        state = json.loads(self.state.read_text())
                        state["lanes"]["integrate"] = {
                            "dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
                        self.write(self.state, state)

                    def opener(request, timeout):
                        if isinstance(failure, Exception):
                            raise failure
                        return io.StringIO(LINEAR)

                    with patch.object(g.urllib.request, "urlopen", side_effect=opener):
                        out = self.tick()
                    expected_reason = type(failure).__name__ if isinstance(failure, Exception) else "JSONDecodeError"
                    for lane in ("build", "review") if inflight else g.DEFAULT_JOB_MAP:
                        self.assertIn(f"{lane}: probe failed ({expected_reason}) -> FAIL-OPEN -> dispatched(pid=1234)", out)
                    self.assertEqual(self.spawn.call_count, 2 if inflight else 3)
                    if inflight:
                        self.assertIn("integrate: in-flight (skip)", out)
                    self.assertNotIn("linear-auth-failed", out)
                    self.assertNotIn(LINEAR, out)

    def test_linear_401_dry_run_preserves_all_files(self):
        self.baseline()
        before = {p: p.read_bytes() for p in (self.env, self.jobs, self.state)}
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, {}, None)
        out = self.tick("--dry-run")
        self.assertIn("integrate: linear-auth-failed (401) -> dispatched(dry-run) for re-mint", out)
        self.spawn.assert_not_called()
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertFalse((self.root / "cron/gatekeeper-logs").exists())

    def test_linear_401_recovery_rechecks_skipped_lanes(self):
        self.issues["In Review"] = [issue(state="In Review")]
        self.baseline()
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, {}, None)
        self.tick()
        self.confirm()
        self.linear_error = None
        out = self.tick()
        self.assertIn("integrate: confirmed-ran", out)
        for lane in ("build", "review"):
            self.assertIn(f"{lane}: no-baseline -> dispatched", out)
        self.assertEqual(self.spawn.call_count, 2)

    def test_linear_401_failed_spawn_is_not_recorded_or_secret_echoed(self):
        self.baseline()
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, {}, None)
        self.spawn.side_effect = OSError(LINEAR)
        out = self.tick()
        self.assertIn("integrate: linear-auth-failed (401) -> dispatch-failed(OSError)", out)
        self.assertNotIn("for re-mint", out)
        self.assertNotIn(LINEAR, out)
        self.assertEqual(json.loads(self.state.read_text())["lanes"], {})
        self.spawn.assert_called_once()

    def test_slack_401_does_not_become_linear_auth_failure(self):
        self.with_pack()
        self.issues["Todo"] = [issue()]
        self.slack_error = urllib.error.HTTPError("https://slack.com/api/reactions.get", 401, SLACK, {}, None)
        self.addCleanup(self.slack_error.close)
        out = self.tick()
        self.assertIn("integrate: slack-check-failed (HTTPError)", out)
        self.assertIn("build: changed -> dispatched", out)
        self.assertNotIn("linear-auth-failed", out)
        self.assertNotIn(SLACK, out)
        self.spawn.assert_called_once()

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

    def test_default_lanes_run_as_exec_team_profiles(self):
        profiles = {lane: g.lane_entry(entry)[2] for lane, entry in g.DEFAULT_JOB_MAP.items()}
        self.assertEqual(profiles, {"build": "builder", "review": "reviewer", "integrate": "integrator"})

    def test_dispatch_targets_profile(self):
        with patch.object(g, "hermes_cli", return_value=["hermes"]):
            g.dispatch("job123", "build", False, None, "builder")
            self.assertEqual(self.spawn.call_args[0][0], ["hermes", "-p", "builder", "cron", "run", "job123"])
            g.dispatch("job123", "build", False, None, None)
            self.assertEqual(self.spawn.call_args[0][0], ["hermes", "cron", "run", "job123"])

    def test_profile_jobs_path_and_legacy_entry(self):
        self.assertEqual(g.profile_jobs_path(str(self.root), "reviewer"),
                         os.path.join(str(self.root), "profiles", "reviewer", "cron", "jobs.json"))
        self.assertEqual(g.profile_jobs_path(str(self.root), None), os.path.join(str(self.root), "cron", "jobs.json"))
        self.assertEqual(g.lane_entry(["Todo", "abc"]), ("Todo", "abc", None))
        for bad in (["Todo", "abc", "../x"], ["Todo", "abc", "A B"], ["Todo"], ["Todo", "abc", "p", "extra"]):
            with self.assertRaises(ValueError):
                g.lane_entry(bad)

    def test_last_run_read_from_lane_profile(self):
        args = [a for a in self.args]
        i = args.index("--jobs-path"); del args[i:i + 2]
        self.args = args
        for lane, entry in g.DEFAULT_JOB_MAP.items():
            state, job_id, profile = g.lane_entry(entry)
            path = self.root / "profiles" / profile / "cron" / "jobs.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.write(path, {"jobs": [{"id": job_id, "last_run_at": "2026-09-19T10:00:00Z"}]})
        out = self.tick()
        self.assertIn("build: no-baseline -> dispatched(pid=1234)", out)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["lanes"]["build"]["job_last_run_at"], "2026-09-19T10:00:00Z")

    def approval_fixture(self, hours=30, body="MERGE PROOF PACK", identifier="WD-354"):
        row = issue(identifier, "Approved")
        row["comments"] = connection([comment(body, (NOW - timedelta(hours=hours)).isoformat())])
        self.issues["Approved"] = [row]
        self.baseline()
        return row

    def approval_tick(self, *extra):
        result = self.tick("--no-slack", *extra)
        for request in self.requests:
            self.assertNotIn("slack.com", request.full_url)
            self.assertNotIn("mutation", json.loads(request.data)["query"].lower())
        return result

    def test_approval_stale_ages(self):
        for hours, segment in [(360, "approval-stale: WD-354=15d"),
                               (30, "approval-stale: WD-354=30h"),
                               (23, "approval-wait: ok (1 approved)"),
                               (24, "approval-wait: ok (1 approved)"),
                               (48, "approval-stale: WD-354=2d")]:
            with self.subTest(hours=hours):
                self.approval_fixture(hours)
                out = self.approval_tick()
                self.assertIn(segment, out)
                if hours <= 24:
                    self.assertNotIn("approval-stale:", out)
                self.spawn.assert_not_called()

    def test_approval_lane_down_disclosure(self):
        for marker in ("MERGE PROOF PACK", "LANDING PROOF PACK"):
            for hours, age in ((1, "1h"), (72, "3d")):
                self.approval_fixture(hours, marker + "\nSlack LaNe\nUNAVAILABLE this pack", "WD-1")
                out = self.approval_tick()
                self.assertIn("approval-lane-down: WD-1=" + age, out)
                self.assertNotIn("approval-stale:", out)
        row = self.approval_fixture(1, "LANDING PROOF PACK", "WD-1")
        row["comments"]["nodes"].append(comment("MERGE PROOF PACK\nlane unavailable", (NOW - timedelta(days=3)).isoformat()))
        self.baseline()
        self.assertIn("approval-wait: ok (1 approved)", self.approval_tick())

    def test_approval_pack_beyond_newest_50(self):
        row = self.approval_fixture()
        pack = row["comments"]["nodes"][0]
        row["comments"] = connection([comment("SWEPT: churn") for _ in range(50)], "older")
        self.comment_pages[("WD-354", "older")] = connection([pack], "unused")
        self.baseline()
        original = copy.deepcopy(row)
        out = self.approval_tick()
        self.assertIn("approval-stale: WD-354=30h", out)
        self.assertEqual(row, original)
        self.assertEqual(sum(json.loads(r.data)["query"] == g.COMMENTS_QUERY for r in self.requests), 1)
        success_lines, success_spawns = out.split("; approval-")[0], self.spawn.call_count
        for error in (urllib.error.URLError(LINEAR),
                      urllib.error.HTTPError("https://fixture.invalid", 401, LINEAR, {}, None)):
            self.comment_pages[("WD-354", "older")] = error
            self.baseline()
            out = self.approval_tick()
            self.assertIn("approval-wait: unknown (" + type(error).__name__ + ")", out)
            self.assertEqual(out.split("; approval-")[0], success_lines)
            self.assertEqual(self.spawn.call_count, success_spawns)
            self.assertNotIn(LINEAR, out)
        row["comments"] = connection([], "0")
        self.comment_pages = {("WD-354", str(n)): connection([comment()], str(n + 1)) for n in range(10)}
        self.baseline()
        self.assertIn("approval-wait: ok (1 approved)", self.approval_tick())
        self.assertEqual(sum(json.loads(r.data)["query"] == g.COMMENTS_QUERY for r in self.requests), 10)
        self.comment_pages[("WD-354", "0")] = connection([], "0")
        self.assertIn("approval-wait: unknown (Linear pagination did not advance)", self.approval_tick())
        self.assertEqual(sum(json.loads(r.data)["query"] == g.COMMENTS_QUERY for r in self.requests), 1)
        self.comment_pages[("WD-354", "0")] = connection([])
        self.assertIn("approval-wait: ok (1 approved)", self.approval_tick())

    def test_approval_unknown_when_integrate_not_probed(self):
        self.approval_fixture()
        state = json.loads(self.state.read_text())
        state["lanes"]["integrate"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        self.write(self.state, state)
        self.assertIn("approval-wait: unknown (integrate lane not probed)", self.approval_tick())
        self.assertFalse(any(json.loads(r.data)["variables"].get("state") == "Approved" for r in self.requests))
        self.assertFalse(any(json.loads(r.data)["query"] == g.COMMENTS_QUERY for r in self.requests))
        self.baseline()
        self.linear_error = urllib.error.HTTPError("https://fixture.invalid", 401, LINEAR, {}, None)
        self.assertIn("approval-wait: unknown (integrate lane not probed)", self.approval_tick())
        self.linear_error = None
        self.baseline()
        self.env.unlink()
        self.assertIn("approval-wait: unknown (integrate lane not probed)", self.approval_tick())
        self.assertEqual(self.requests, [])

    def test_approval_status_missing_invalid_and_sorted(self):
        row = self.approval_fixture()
        row["comments"]["nodes"][0]["createdAt"] = "not-a-date"
        self.assertEqual(g.approval_status([row], NOW), ["approval-wait: ok (1 approved)"])
        row["comments"] = {"nodes": []}
        self.baseline()
        self.assertIn("approval-wait: ok (1 approved)", self.approval_tick())
        self.assertFalse(any(json.loads(r.data)["query"] == g.COMMENTS_QUERY for r in self.requests))
        first = copy.deepcopy(self.approval_fixture(30, identifier="WD-2"))
        second = copy.deepcopy(self.approval_fixture(72, identifier="WD-1"))
        down = copy.deepcopy(self.approval_fixture(1, "LANDING PROOF PACK\nlane unavailable", "WD-3"))
        rows = [down, first, second]
        before = copy.deepcopy(rows)
        self.assertEqual(g.approval_status(rows, NOW), ["approval-stale: WD-1=3d,WD-2=30h", "approval-lane-down: WD-3=1h"])
        self.assertEqual(rows, before)
        self.assertEqual(g.approval_status([], NOW), ["approval-wait: ok (0 approved)"])

    def test_approval_dry_run_and_job_map_boundaries(self):
        self.approval_fixture()
        before = {p: p.read_bytes() for p in (self.env, self.jobs, self.state)}
        self.assertIn("approval-stale: WD-354=30h", self.approval_tick("--dry-run"))
        self.spawn.assert_not_called()
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertNotIn("approval-", self.approval_tick("--job-map", json.dumps({"build": ["Todo", "x"]})))
        out = self.approval_tick("--job-map", json.dumps({"integrate": ["Approved", "x", "../bad"]}))
        self.assertIn("approval-wait: unknown (integrate lane not probed)", out)
        self.assertEqual(self.requests, [])
        self.baseline()
        with patch.object(g, "lane_issues", side_effect=OSError(LINEAR)):
            self.assertIn("approval-wait: unknown (integrate lane not probed)", self.approval_tick())
        self.write(self.state, {})
        with patch.object(g, "dispatch", side_effect=RuntimeError(LINEAR)):
            self.assertIn("approval-wait: unknown (integrate lane not probed)", self.approval_tick())

    def test_approval_status_leaks_nothing(self):
        self.approval_fixture(72, "MERGE PROOF PACK\nR3A-BODY-SENTINEL " + LINEAR + SLACK + "\nlane unavailable")
        self.approval_tick()
        artifacts = "".join(self.output) + self.state.read_text()
        for log in (self.root / "cron/gatekeeper-logs").glob("*.log"):
            artifacts += log.read_text()
        for value in ("R3A-BODY-SENTINEL", LINEAR, SLACK):
            self.assertNotIn(value, artifacts)

    def test_approval_state_and_dispatch_equal_main(self):
        # Execute actual remote-main code against the same synthetic inputs.
        with patch.object(subprocess, "Popen", REAL_POPEN):
            source = subprocess.check_output(["git", "show", "origin/main:deploy/factory-gatekeeper.py"],
                                             cwd=Path(__file__).resolve().parents[2], encoding="utf-8", timeout=10)
        path = self.root / "gk-main.py"
        path.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("gk_main_581", path)
        base = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(base)
        for mode in ("stale", "down", "paging", "paging-failure", "slack", "inflight", "401", "changed"):
            with self.subTest(mode=mode):
                row = self.approval_fixture()
                self.linear_error = None
                if mode == "down":
                    row["comments"]["nodes"][0]["body"] += "\nlane unavailable"
                if mode in ("paging", "paging-failure"):
                    pack = row["comments"]["nodes"][0]
                    row["comments"] = connection([comment("SWEPT: churn")], "older")
                    self.comment_pages[("WD-354", "older")] = (connection([pack]) if mode == "paging"
                                                               else urllib.error.URLError(LINEAR))
                if mode == "slack":
                    row["comments"]["nodes"] = [proof()]
                self.baseline()
                if mode == "inflight":
                    state = json.loads(self.state.read_text())
                    state["lanes"]["integrate"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
                    self.write(self.state, state)
                if mode == "401":
                    self.linear_error = urllib.error.HTTPError("https://fixture.invalid", 401, LINEAR, {}, None)
                if mode == "changed":
                    row["description"] = "new scope"
                base_state = self.root / "main-state.json"
                base_state.write_bytes(self.state.read_bytes())
                current_out = self.tick()
                current_argv = [c.args for c in self.spawn.call_args_list]
                current_slack = [r.full_url for r in self.requests if "slack.com" in r.full_url]
                current_data = json.loads(self.state.read_text())
                args = [str(base_state) if a == str(self.state) else a for a in self.args]
                stdout = io.StringIO()
                self.requests.clear()
                self.spawn.reset_mock()
                with patch.object(base, "hermes_home", return_value=str(self.root)), \
                        patch.object(base, "now_utc", return_value=NOW), \
                        patch.object(base.sys, "argv", args), contextlib.redirect_stdout(stdout):
                    self.assertEqual(base.main(), 0)
                self.assertEqual(current_data, json.loads(base_state.read_text()))
                self.assertEqual(current_out.split("; approval-")[0], stdout.getvalue().strip())
                self.assertEqual(current_argv, [c.args for c in self.spawn.call_args_list])
                self.assertEqual(current_slack, [r.full_url for r in self.requests if "slack.com" in r.full_url])

    def test_bad_profile_in_job_map_is_a_lane_error(self):
        out = self.tick("--job-map", json.dumps({"build": ["Todo", "x", "../evil"]}))
        self.assertIn("build: LANE-ERROR (", out)
        self.spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
