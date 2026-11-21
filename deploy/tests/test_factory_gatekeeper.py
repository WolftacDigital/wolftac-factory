"""Offline behavior tests: fake HTTP, fake Popen, temporary state and credentials."""
import contextlib
import copy
import importlib.util
import io
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import Message
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("gatekeeper", Path(__file__).resolve().parents[1] / "factory-gatekeeper.py")
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)
LINEAR = "lin_TEST_SENTINEL"
DISCORD = "discord-TEST-SENTINEL"
GUILD = "923752167293222912"
CHANNEL = "1562236246158221323"
APPROVER = "1022999767753691186"
HARPER = "1562178628211449966"
MESSAGE = "1569128910862684180"
POLICY = {"discord_guild_id": GUILD, "discord_channel_id": CHANNEL, "approver_discord_user_id": APPROVER,
          "pack_author_discord_user_ids": [HARPER], "reader_bot_discord_user_id": HARPER,
          "approval_emoji": "\U0001F680"}
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


def permalink(message=MESSAGE, channel=CHANNEL, guild=GUILD):
    return f"https://discord.com/channels/{guild}/{channel}/{message}"


def proof(message=MESSAGE, channel=CHANNEL, created="2026-09-19T13:00:00Z", marker="MERGE PROOF PACK", guild=GUILD):
    return comment(f"{marker}\nDiscord request: {permalink(message, channel, guild)}", created)


def reader_line(reactors, message=MESSAGE, ok=True, error=None):
    if not ok:
        return json.dumps({"ok": False, "contract": 1, "error": error})
    return json.dumps({"ok": True, "contract": 1, "reader_bot_id": HARPER, "guild_id": GUILD,
                       "channel_id": CHANNEL, "parent_id": None, "emoji": "\U0001F680",
                       "message": {"id": message}, "reactors": reactors, "reactor_pages": 1})


class GatekeeperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = self.root / ".env"
        self.jobs = self.root / "jobs.json"
        self.state = self.root / "state.json"
        self.policy = self.root / "approval-policy.json"
        self.env.write_text(f"LINEAR_FACTORY_BOT_TOKEN={LINEAR}\nDISCORD_BOT_TOKEN={DISCORD}\n", encoding="utf-8")
        self.write(self.jobs, {"jobs": [{"id": pair[1], "last_run_at": "2026-09-19T10:00:00Z"}
                                       for pair in g.DEFAULT_JOB_MAP.values()]})
        self.write(self.policy, POLICY)
        self.issues = {"Todo": [issue()], "In Review": [], "Approved": []}
        self.reactors = [{"id": APPROVER, "bot": False}]
        self.linear_error = None
        self.reader_result = None
        self.reader_calls = []
        self.requests = []
        self.comment_pages = {}
        self.args = ["gatekeeper", "--no-audit", "--env-path", str(self.env), "--jobs-path", str(self.jobs),
                     "--state-path", str(self.state), "--approval-policy", str(self.policy),
                     "--exec-override", json.dumps(["fake-noop"])]
        self.addCleanup(patch.stopall)
        patch.object(g, "hermes_home", return_value=str(self.root)).start()
        patch.object(g, "now_utc", return_value=NOW).start()
        # This suite isolates worker dispatch; WD-628 has its own paginated HTTP fixtures.
        patch.object(g, "needs_you_issues", return_value=[]).start()
        self.http = patch.object(g.urllib.request, "urlopen", side_effect=self.open).start()
        self.spawn = patch.object(g.subprocess, "Popen", return_value=SimpleNamespace(pid=1234)).start()
        patch.object(g, "run_reader", side_effect=self.reader).start()
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
        self.fail("the gatekeeper made a non-Linear HTTP request: " + request.full_url)

    def reader(self, argv, env, timeout=None):
        """Fake R1. The gatekeeper never makes a Discord request itself."""
        self.reader_calls.append(list(argv))
        self.assertEqual(argv[-10:-6], ["-p", "integrator", "wolftac-approval-read", "--channel"])
        self.assertEqual([argv[-5], argv[-3], argv[-2], argv[-1]], ["--message", "--emoji", "\U0001F680", "--json"])
        self.assertFalse([name for name in env if name.upper().startswith("DISCORD_")])
        if self.reader_result is not None:
            return self.reader_result
        return 0, "hermes banner line\n" + reader_line(self.reactors, argv[argv.index("--message") + 1]) + "\n"

    def tick(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        self.spawn.reset_mock()
        self.http.reset_mock()
        self.requests.clear()
        self.reader_calls.clear()
        with patch.object(g.sys, "argv", self.args + list(extra)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(g.main(), 0)
        output = stdout.getvalue() + stderr.getvalue()
        self.output.append(output)
        return output

    def baseline(self):
        self.write(self.state, {"lanes": {}, "fingerprints": {
            lane: g.lane_fingerprint([
                row for row in self.issues[state]
                if lane not in g.LANE_FILTERS or g.lane_matches(row, g.LANE_FILTERS[lane])
            ]) for lane, (state, *_) in g.DEFAULT_JOB_MAP.items()
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

    def test_build_skipped_never_changes_fingerprint(self):
        for existing in ([], [comment()], [comment("SWEPT: old")]):
            with self.subTest(existing=existing):
                row = issue()
                row["comments"]["nodes"] = copy.deepcopy(existing)
                baseline = g.lane_fingerprint([row])
                row["comments"]["nodes"].append(comment(
                    "BUILD SKIPPED: completion budget (files=29, checks=6, reserve=20%)",
                    NOW.isoformat()))
                self.assertEqual(baseline, g.lane_fingerprint([row]))
                for field in ("comment", "state", "description"):
                    changed = copy.deepcopy(row)
                    if field == "comment":
                        changed["comments"]["nodes"].append(comment("Ready", NOW.isoformat()))
                    elif field == "state":
                        changed["state"]["name"] = "In Progress"
                    else:
                        changed["description"] = "Revised spec"
                    self.assertNotEqual(baseline, g.lane_fingerprint([changed]))

    def test_build_skipped_prefix_is_exact_and_case_sensitive(self):
        row = issue()
        baseline = g.lane_fingerprint([row])
        for body in ("build skipped: parked", " BUILD SKIPPED: parked",
                     "BUILD SKIPPED parked", "Decision: BUILD SKIPPED: parked"):
            with self.subTest(body=body):
                changed = copy.deepcopy(row)
                changed["comments"]["nodes"].append(comment(body, NOW.isoformat()))
                self.assertNotEqual(baseline, g.lane_fingerprint([changed]))
        row["comments"]["nodes"].append(comment("BUILD SKIPPED:parked", NOW.isoformat()))
        self.assertEqual(baseline, g.lane_fingerprint([row]))

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

    def test_discord_rocket_dispatches_once(self):
        self.with_pack()
        self.assertIn("integrate: discord-approval WD-1 -> dispatched(pid=1234)", self.tick())
        self.spawn.assert_called_once()
        self.assertEqual(len(self.reader_calls), 1)
        self.assertEqual(self.reader_calls[0][-9:], ["integrator", "wolftac-approval-read", "--channel", CHANNEL,
                                                     "--message", MESSAGE, "--emoji", "\U0001F680", "--json"])
        state = json.loads(self.state.read_text())
        self.assertEqual(state["approval_dispatched"], {"WD-1": MESSAGE})
        self.confirm()
        self.assertIn("integrate: unchanged (skip)", self.tick())
        self.spawn.assert_not_called()
        self.assertEqual(self.reader_calls, [])

    def test_permalink_in_pack_thread_reply_dispatches(self):
        self.with_pack()
        nodes = self.issues["Approved"][0]["comments"]["nodes"]
        nodes[-1] = comment("MERGE PROOF PACK\nDiscord request: not delivered: operator step pending",
                            "2026-09-19T13:00:00Z")
        nodes.append(comment(f"Discord message id {MESSAGE}: {permalink()}", "2026-09-19T13:01:00Z"))
        self.baseline()
        self.assertIn("integrate: discord-approval WD-1 -> dispatched(pid=1234)", self.tick())

    def test_discord_negative_cases(self):
        cases = ("other-user", "approver-as-bot", "no-reactors", "other-channel", "other-guild", "no-permalink",
                 "newest-no-permalink", "link-older-than-newest-pack")
        for case in cases:
            with self.subTest(case=case):
                self.with_pack()
                self.reactors = [{"id": APPROVER, "bot": False}]
                packs = self.issues["Approved"][0]["comments"]["nodes"]
                if case == "other-user":
                    self.reactors = [{"id": "1022999767753691187", "bot": False}, {"id": HARPER, "bot": True}]
                elif case == "approver-as-bot":
                    self.reactors = [{"id": APPROVER, "bot": True}]
                elif case == "no-reactors":
                    self.reactors = []
                elif case == "other-channel":
                    packs[-1] = proof(channel="1562236246158221999")
                elif case == "other-guild":
                    packs[-1] = proof(guild="923752167293222999")
                elif case == "no-permalink":
                    packs[-1] = comment("MERGE PROOF PACK", "2026-09-19T13:00:00Z")
                elif case == "newest-no-permalink":
                    packs.append(comment("LANDING PROOF PACK no URL", "2026-09-19T14:00:00Z"))
                else:
                    packs.append(comment("MERGE PROOF PACK refreshed, request not sent yet", "2026-09-19T14:00:00Z"))
                self.baseline()
                self.assertIn("integrate: unchanged (skip)", self.tick())
                self.spawn.assert_not_called()
                self.assertLessEqual(len(self.reader_calls), 1)
                if case in ("other-channel", "other-guild", "no-permalink", "newest-no-permalink",
                            "link-older-than-newest-pack"):
                    self.assertEqual(self.reader_calls, [])

    def test_new_pack_message_dispatches_again(self):
        self.with_pack()
        self.tick()
        self.confirm()
        self.tick()
        self.issues["Approved"][0]["comments"]["nodes"].append(
            proof("1569128910862684999", created="2026-09-19T14:00:00Z", marker="LANDING PROOF PACK"))
        state = json.loads(self.state.read_text())
        state["fingerprints"]["integrate"] = g.lane_fingerprint(self.issues["Approved"])
        self.write(self.state, state)
        self.assertIn("integrate: discord-approval WD-1 -> dispatched", self.tick())
        self.assertEqual(json.loads(self.state.read_text())["approval_dispatched"]["WD-1"], "1569128910862684999")

    def test_discord_check_failures(self):
        failures = {
            "missing-policy": None, "invalid-policy": None, "empty-policy": None, "slack-policy": None,
            "extra-key-policy": None,
            "timeout": ((None, "timeout"), "timeout"),
            "launch-failed": ((None, "launch-failed"), "launch-failed"),
            "nonzero-exit": ((5, json.dumps({"ok": False, "contract": 1, "error": "http_error",
                                             "http_status": 500})), "http_error"),
            "identity-mismatch": ((4, json.dumps({"ok": False, "contract": 1, "error": "identity_mismatch"})),
                                  "identity_mismatch"),
            "ok-false-exit-zero": ((0, json.dumps({"ok": False, "contract": 1, "error": "message_changed"})),
                                   "message_changed"),
            "unparseable": ((0, "not json at all"), "malformed-output"),
            "two-documents": ((0, reader_line([]) + "\n" + reader_line([])), "malformed-output"),
            "exit-without-json": ((7, ""), "exit-7"),
            "ok-but-nonzero": ((3, reader_line([{"id": APPROVER, "bot": False}])), "exit-3"),
            "secret-in-error": ((5, json.dumps({"ok": False, "contract": 1, "error": DISCORD})), "reader-error"),
        }
        for case, expected in failures.items():
            with self.subTest(case=case):
                self.with_pack()
                self.write(self.policy, POLICY)
                self.reader_result = None
                if case == "missing-policy":
                    self.policy.unlink()
                elif case == "invalid-policy":
                    self.policy.write_text("broken")
                elif case == "empty-policy":
                    self.write(self.policy, {})
                elif case == "slack-policy":
                    self.write(self.policy, {"slack_channel_id": "C123", "approver_slack_user_id": "U123"})
                elif case == "extra-key-policy":
                    self.write(self.policy, dict(POLICY, slack_channel_id="C123"))
                else:
                    self.reader_result = expected[0]
                # Integrate FIRST demonstrates that failures do not starve later lanes.
                mapping = dict(reversed(list(g.DEFAULT_JOB_MAP.items())))
                out = self.tick("--job-map", json.dumps(mapping))
                if expected is None:
                    self.assertIn("integrate: discord-check-failed (approval policy", out)
                else:
                    self.assertIn(f"integrate: discord-check-failed ({expected[1]})", out)
                self.assertIn("build: idle", out)
                self.assertNotIn(DISCORD, out)
                self.spawn.assert_not_called()
                self.assertEqual(json.loads(self.state.read_text())["approval_dispatched"], {})

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
                self.assertEqual(self.spawn.call_count, 2)
                self.assertIn("build-crm: probe failed", out)
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
                    for lane in ("build", "build-crm", "review"):
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
                    self.assertEqual(self.reader_calls, [])

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
                        self.issues["Todo"].append(self.crm_issue())
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
                    for lane in ("build", "build-crm", "review") if inflight else g.DEFAULT_JOB_MAP:
                        self.assertIn(f"{lane}: probe failed ({expected_reason}) -> FAIL-OPEN -> dispatched(pid=1234)", out)
                    self.assertEqual(self.spawn.call_count, 3 if inflight else 4)
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

    def test_reader_401_does_not_become_linear_auth_failure(self):
        self.with_pack()
        self.issues["Todo"] = [issue()]
        self.reader_result = (5, json.dumps({"ok": False, "contract": 1, "error": "http_error", "http_status": 401}))
        out = self.tick()
        self.assertIn("integrate: discord-check-failed (http_error)", out)
        self.assertIn("build: changed -> dispatched", out)
        self.assertNotIn("linear-auth-failed", out)
        self.spawn.assert_called_once()

    def test_inflight_guard_and_ttl_clears_fingerprint(self):
        self.with_pack()
        self.baseline()
        state = json.loads(self.state.read_text())
        state["lanes"]["integrate"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        self.write(self.state, state)
        self.assertIn("integrate: in-flight (skip)", self.tick())
        self.spawn.assert_not_called()
        self.assertEqual(self.reader_calls, [])
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
        for mode in ("new", "changed", "same", "discord", "fail-open", "inflight", "expired"):
            with self.subTest(mode=mode):
                self.issues = {"Todo": [issue()], "In Review": [], "Approved": []}
                self.linear_error = None
                self.baseline()
                if mode == "new":
                    self.state.write_text('{"lanes":{}}')
                elif mode == "changed":
                    self.issues["Todo"][0]["description"] = "new"
                elif mode == "discord":
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

    def test_no_discord_and_process_environment_ignored(self):
        self.with_pack()
        self.reader_result = (4, json.dumps({"ok": False, "contract": 1, "error": "token_unavailable"}))
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": DISCORD, "discord_other": DISCORD}):
            self.assertIn("discord-check-failed (token_unavailable)", self.tick())
        self.assertEqual(len(self.reader_calls), 1)
        self.assertIn("integrate: unchanged (skip)", self.tick("--no-discord"))
        self.assertEqual(self.reader_calls, [])
        self.spawn.assert_not_called()
        self.env.unlink()
        with patch.dict(os.environ, {"LINEAR_FACTORY_BOT_TOKEN": LINEAR}):
            self.assertIn("FAIL-OPEN (env unreadable", self.tick("--dry-run"))

    def test_spawn_failure_does_not_consume_baseline_or_approval(self):
        self.with_pack()
        self.spawn.side_effect = OSError(LINEAR + DISCORD)
        out = self.tick()
        self.assertIn("dispatch-failed(OSError)", out)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["lanes"], {})
        self.assertEqual(state["approval_dispatched"], {})
        self.assertNotIn(LINEAR, out)
        self.assertNotIn(DISCORD, out)

    def test_tokens_never_leak(self):
        self.with_pack()
        self.tick()
        self.confirm()
        self.tick()
        self.baseline()
        self.reader_result = (5, json.dumps({"ok": False, "contract": 1, "error": LINEAR + DISCORD}))
        self.tick()
        self.reader_result = (5, "Traceback: " + LINEAR + DISCORD)
        self.tick()
        self.issues["Todo"] = [issue()]
        self.linear_error = {"errors": [{"message": LINEAR + DISCORD}]}
        self.tick()
        artifacts = "".join(self.output) + self.state.read_text()
        for log in (self.root / "cron/gatekeeper-logs").glob("*.log"):
            artifacts += log.read_text()
        for token in (LINEAR, DISCORD):
            self.assertNotIn(token, artifacts)

    def test_read_only_traffic(self):
        self.with_pack()
        self.tick()
        self.assertEqual(len(self.reader_calls), 1)
        for request in self.requests:
            self.assertEqual(request.full_url, "https://api.linear.app/graphql")
            self.assertEqual(request.get_method(), "POST")
            self.assertNotIn("mutation", json.loads(request.data)["query"].lower())

    def test_gatekeeper_has_no_slack_route(self):
        source = (Path(__file__).resolve().parents[1] / "factory-gatekeeper.py").read_text(encoding="utf-8")
        for needle in ("slack.com", "SLACK_FACTORY_BOT_TOKEN", "slack_bot", "reactions.get", "approver_slack_user_id"):
            self.assertNotIn(needle, source)

    def test_default_lanes_run_as_exec_team_profiles(self):
        profiles = {lane: g.lane_entry(entry)[2] for lane, entry in g.DEFAULT_JOB_MAP.items()}
        self.assertEqual(profiles, {"build": "builder", "build-crm": "builder",
                                    "review": "reviewer", "integrate": "integrator"})

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
            jobs = json.loads(path.read_text())["jobs"] if path.exists() else []
            jobs.append({"id": job_id, "last_run_at": "2026-09-19T10:00:00Z"})
            self.write(path, {"jobs": jobs})
        out = self.tick()
        self.assertIn("build: no-baseline -> dispatched(pid=1234)", out)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["lanes"]["build"]["job_last_run_at"], "2026-09-19T10:00:00Z")

    @staticmethod
    def crm_issue(identifier="WD-CRM"):
        row = issue(identifier)
        row["project"] = {"name": "Wolftac CRM v2"}
        row["labels"] = connection([{"name": "Factory Queue"}])
        return row

    def build_lane_fixture(self):
        self.issues["Todo"] = [issue(), self.crm_issue()]
        i = self.args.index("--exec-override")
        del self.args[i:i + 2]
        patch.object(g, "hermes_cli", return_value=["hermes"]).start()
        # Popen remains fake: no real Hermes worker is launched.
        self.baseline()

    def test_lane_constants(self):
        self.assertEqual(g.DEFAULT_JOB_MAP, {
            "build": ("Todo", "9b7b5987bcd6", "builder"),
            "build-crm": ("Todo", "1dd429fe03b2", "builder"),
            "review": ("In Review", "8e5c37a23b91", "reviewer"),
            "integrate": ("Approved", "4e7a4d9c5fa4", "integrator"),
        })
        self.assertEqual(g.LANE_FILTERS, {"build": "agentic", "build-crm": "Wolftac CRM v2"})

    def test_crm_change_dispatches_only_crm(self):
        self.build_lane_fixture()
        self.issues["Todo"][1]["description"] = "CRM changed"
        out = self.tick()
        self.assertIn("build-crm: changed -> dispatched(pid=1234)", out)
        self.assertIn("build: unchanged (skip)", out)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], ["hermes", "-p", "builder", "cron", "run", "1dd429fe03b2"])

    def test_agentic_label_project_and_case_dispatch_only_agentic(self):
        self.build_lane_fixture()
        for project, labels in ((None, ["agentic"]),
                                ({"name": "Wolftac Agentic OS — Product Roadmap"}, []),
                                (None, ["AGENTIC"]), (None, ["prefix-AgEnTiC-suffix"])):
            with self.subTest(project=project, labels=labels):
                row = issue()
                row.update(project=project, labels=connection([{"name": s} for s in labels]))
                self.issues["Todo"] = [row, self.crm_issue()]
                self.baseline()
                row["description"] = "agentic changed"
                out = self.tick()
                self.assertIn("build: changed -> dispatched(pid=1234)", out)
                self.assertIn("build-crm: unchanged (skip)", out)
                self.spawn.assert_called_once()
                self.assertEqual(self.spawn.call_args.args[0], ["hermes", "-p", "builder", "cron", "run", "9b7b5987bcd6"])

    def test_unmatched_change_dispatches_neither_build(self):
        self.build_lane_fixture()
        row = issue("WD-OTHER")
        row.update(project=None, labels=connection([{"name": "Factory Queue"}]))
        self.issues["Todo"].append(row)
        self.baseline()
        row["description"] = "unmatched change"
        out = self.tick()
        for lane in ("build", "build-crm"):
            self.assertIn(f"{lane}: unchanged (skip)", out)
        self.spawn.assert_not_called()

    def test_empty_crm_and_shared_issue_fingerprints(self):
        self.baseline()
        self.assertIn("build-crm: idle", self.tick())
        self.spawn.assert_not_called()
        shared = issue("WD-SHARED")
        shared["project"] = {"name": "WOLFTAC CRM V2 / Pilot"}
        self.issues["Todo"] = [shared]
        self.baseline()
        shared["description"] = "shared change"
        out = self.tick()
        state = json.loads(self.state.read_text())
        for lane in ("build", "build-crm"):
            self.assertIn(f"{lane}: changed -> dispatched(pid=1234)", out)
            self.assertEqual(state["fingerprints"][lane], g.lane_fingerprint([shared]))
        self.assertEqual(self.spawn.call_count, 2)

    def test_shared_todo_pages_are_fetched_once(self):
        self.issues["Todo"] = [issue(f"WD-{n}") for n in range(100)] + [self.crm_issue()]
        self.baseline()
        self.issues["Todo"][-1]["description"] = "last-page CRM change"
        out = self.tick()
        pages = [json.loads(r.data)["variables"]["after"] for r in self.requests
                 if r.data and json.loads(r.data)["variables"].get("state") == "Todo"]
        self.assertEqual(pages, [None, "100"])
        self.assertIn("build: unchanged (skip)", out)
        self.assertIn("build-crm: changed -> dispatched(pid=1234)", out)
        self.spawn.assert_called_once()

    def test_shared_todo_probe_failure_is_cached(self):
        self.build_lane_fixture()
        self.linear_error = TimeoutError("synthetic")
        out = self.tick()
        for lane in ("build", "build-crm"):
            self.assertIn(f"{lane}: probe failed (TimeoutError) -> FAIL-OPEN -> dispatched(pid=1234)", out)
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(sum(json.loads(r.data)["variables"].get("state") == "Todo"
                             for r in self.requests if r.data), 1)
        self.baseline()
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, {}, None)
        out = self.tick()
        for lane in ("build", "build-crm", "review"):
            self.assertIn(f"{lane}: linear-auth-failed (401) -> skipped", out)
        self.assertIn("integrate: linear-auth-failed (401) -> dispatched(pid=1234) for re-mint", out)
        self.spawn.assert_called_once()
        self.assertEqual(self.spawn.call_args.args[0], ["hermes", "-p", "integrator", "cron", "run", "4e7a4d9c5fa4"])

    def test_build_inflight_and_last_run_are_independent(self):
        self.build_lane_fixture()
        i = self.args.index("--jobs-path")
        del self.args[i:i + 2]
        jobs_path = self.root / "profiles/builder/cron/jobs.json"
        jobs_path.parent.mkdir(parents=True)
        stamps = {"build": "2026-09-19T10:00:00Z", "build-crm": "2026-09-19T11:00:00Z"}
        self.write(jobs_path, {"jobs": [
            {"id": g.DEFAULT_JOB_MAP[lane][1], "last_run_at": stamp} for lane, stamp in stamps.items()
        ]})
        for held, ready in (("build", "build-crm"), ("build-crm", "build")):
            with self.subTest(held=held):
                self.baseline()
                before = json.loads(self.state.read_text())
                marker = {"dispatched_at": NOW.isoformat(), "job_last_run_at": stamps[held]}
                before["lanes"][held] = marker
                self.write(self.state, before)
                for row in self.issues["Todo"]:
                    row["description"] += " changed"
                out = self.tick()
                self.assertIn(f"{held}: in-flight (skip)", out)
                self.assertIn(f"{ready}: changed -> dispatched(pid=1234)", out)
                self.spawn.assert_called_once()
                self.assertEqual(self.spawn.call_args.args[0][-1], g.DEFAULT_JOB_MAP[ready][1])
                after = json.loads(self.state.read_text())
                self.assertEqual(after["lanes"][held], marker)
                self.assertEqual(after["fingerprints"][held], before["fingerprints"][held])
                self.assertEqual(after["lanes"][ready]["job_last_run_at"], stamps[ready])

    def test_legacy_state_preserves_markers_and_crm_gets_baseline(self):
        self.build_lane_fixture()
        marker = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        before = {
            "lanes": {lane: dict(marker) for lane in ("build", "review", "integrate")},
            "fingerprints": {lane: "a" * 64 for lane in ("build", "review", "integrate")},
            "approval_dispatched": {"WD-OLD": "1789819979.335359"},
        }
        self.write(self.state, before)
        out = self.tick()
        self.assertIn("build-crm: no-baseline -> dispatched(pid=1234)", out)
        self.spawn.assert_called_once()
        after = json.loads(self.state.read_text())
        for lane in before["lanes"]:
            self.assertIn(f"{lane}: in-flight (skip)", out)
            self.assertEqual(after["lanes"][lane], before["lanes"][lane])
            self.assertEqual(after["fingerprints"][lane], before["fingerprints"][lane])
        self.assertEqual(after["approval_dispatched"], before["approval_dispatched"])
        self.assertEqual(after["fingerprints"]["build-crm"], g.lane_fingerprint([self.issues["Todo"][1]]))

    def test_custom_lane_is_unfiltered_and_crm_label_matches(self):
        row = issue()
        row.update(project=None, labels=connection([{"name": "Factory Queue"}]))
        self.issues["Todo"] = [row]
        out = self.tick("--job-map", json.dumps({"other": ["Todo", "custom"]}))
        self.assertIn("other: no-baseline -> dispatched(pid=1234)", out)
        self.spawn.assert_called_once()
        self.assertFalse(g.lane_matches(row, "Wolftac CRM v2"))
        row["labels"]["nodes"].append({"name": "prefix WOLFTAC crm V2 suffix"})
        self.assertTrue(g.lane_matches(row, "Wolftac CRM v2"))

    def approval_fixture(self, hours=30, body="MERGE PROOF PACK", identifier="WD-354"):
        row = issue(identifier, "Approved")
        row["comments"] = connection([comment(body, (NOW - timedelta(hours=hours)).isoformat())])
        self.issues["Approved"] = [row]
        self.baseline()
        return row

    def approval_tick(self, *extra):
        result = self.tick("--no-discord", *extra)
        for request in self.requests:
            self.assertEqual(request.full_url, "https://api.linear.app/graphql")
            self.assertNotIn("mutation", json.loads(request.data)["query"].lower())
        self.assertEqual(self.reader_calls, [])
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
        for disclosure in ("not delivered: operator step pending", "NOT  DELIVERED:\n operator STEP pending"):
            self.approval_fixture(1, "MERGE PROOF PACK\n**Discord approval request:** " + disclosure, "WD-1")
            self.assertIn("approval-lane-down: WD-1=1h", self.approval_tick())
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
        self.approval_fixture(72, "MERGE PROOF PACK\nR3A-BODY-SENTINEL " + LINEAR + DISCORD + "\nlane unavailable")
        self.approval_tick()
        artifacts = "".join(self.output) + self.state.read_text()
        for log in (self.root / "cron/gatekeeper-logs").glob("*.log"):
            artifacts += log.read_text()
        for value in ("R3A-BODY-SENTINEL", LINEAR, DISCORD):
            self.assertNotIn(value, artifacts)

    # WD-626: real diagnostic path with fake HTTP, no live credential or sender.
    def no_pack_fixture(self, hours=3):
        row = issue("WD-1", "Approved")
        row.update(updatedAt=NOW.isoformat(), createdAt=(NOW - timedelta(days=100)).isoformat())
        row["comments"] = connection([])
        self.issues["Approved"] = [row]
        self.history_pages = {None: connection([
            {"createdAt": (NOW - timedelta(hours=hours)).isoformat(), "toState": {"name": "Approved"}}
        ])}
        original = self.open

        def history_http(request, timeout):
            data = json.loads(request.data)
            if data["query"] != g.APPROVAL_HISTORY_QUERY:
                return original(request, timeout)
            self.requests.append(request)
            self.assertEqual(timeout, 20)
            self.assertEqual(request.full_url, "https://api.linear.app/graphql")
            self.assertNotIn("mutation", data["query"].lower())
            page = self.history_pages[data["variables"]["after"]]
            if isinstance(page, Exception):
                raise page
            return io.StringIO(json.dumps({"data": {"issue": {"history": page}}}))

        self.http.side_effect = history_http
        self.baseline()
        return row

    def test_no_pack_transition_age_and_boundary(self):
        for hours, expected in ((1, False), (2, True), (3, True), (62, True)):
            row = self.no_pack_fixture(hours)
            before = copy.deepcopy(row)
            out = self.approval_tick()
            self.assertEqual("approval-no-pack:" in out, expected)
            if expected:
                self.assertIn(f"approval-no-pack: WD-1={hours}h(unknown)", out)
            self.assertEqual(row, before)
            self.spawn.assert_not_called()

    def test_no_pack_reason_precedence_and_sanitization(self):
        row = self.no_pack_fixture()
        allowed, denied = self.root / "allowed", self.root / "denied"
        policy = self.root / "allowlist.json"
        self.write(policy, {"version": 1, "repos": [{"path": str(allowed), "github": None}]})
        jobs = json.loads(self.jobs.read_text())
        for job in jobs["jobs"]:
            if job["id"] == g.DEFAULT_JOB_MAP["integrate"][1]:
                job["enabled"] = False
        self.write(self.jobs, jobs)
        row["comments"] = connection([comment("**Agent:** fixture\n\nPRECONDITION: " + LINEAR + DISCORD)])
        row["description"] = f"**Repo:** `fixture/repo` (`{denied}`) | **Priority:** High"
        with patch.dict(os.environ, {"FACTORY_REPO_ALLOWLIST": str(policy)}):
            self.assertEqual(g.approval_repo_allowed(str(denied)), 3)
            self.assertEqual(g.approval_repo_allowed(str(allowed)), 0)
            for path, comments, reason in (
                    (denied, row["comments"], "repo-not-allowed"),
                    (allowed, row["comments"], "precondition-failed"),
                    (allowed, connection([]), "integrate-paused")):
                row["description"] = f"**Repo:** `fixture/repo` (`{path}`)"
                row["comments"] = comments
                self.baseline()
                out = self.approval_tick()
                self.assertIn(f"approval-no-pack: WD-1=3h({reason})", out)
                for secret in (LINEAR, DISCORD, str(path)):
                    self.assertNotIn(secret, out)
            policy.write_text("invalid", encoding="utf-8")
            self.assertEqual(g.approval_repo_allowed(str(denied)), 4)
            self.assertEqual(g.no_pack_reason(row, False), "unknown")
        self.assertFalse(g.integrator_disabled(self.root / "absent.json", "x"))
        self.assertFalse(g.integrator_disabled(self.jobs, "wrong-id"))
        self.assertIsNone(g.approval_repo_path("**Repo:** `fixture/repo`\nElsewhere: `/secret`"))
        self.assertEqual(g.approval_repo_path("**Repo:** `D:\\Wolftac\\Factory`"), "D:\\Wolftac\\Factory")
        self.spawn.assert_not_called()

    def test_no_pack_newest_integrate_comment_not_newest_arbitrary_comment(self):
        row = self.no_pack_fixture()
        row["comments"] = connection([
            comment("PRECONDITION: old", (NOW - timedelta(hours=5)).isoformat()),
            comment("INTEGRATION REPORT\nchecks continuing", (NOW - timedelta(hours=4)).isoformat()),
            comment("random PRECONDITION mention", (NOW - timedelta(hours=1)).isoformat()),
        ])
        self.assertEqual(g.no_pack_reason(row, False), "unknown")
        row["comments"]["nodes"].append(comment("PRECONDITION: current", NOW.isoformat()))
        self.assertEqual(g.no_pack_reason(row, False), "precondition-failed")

    def test_no_pack_bounce_history_paging_and_fresh_pack(self):
        row = self.no_pack_fixture()
        self.history_pages = {
            None: connection([{"createdAt": (NOW - timedelta(hours=5)).isoformat(), "toState": {"name": "Todo"}}], "older"),
            "older": connection([
                {"createdAt": (NOW - timedelta(days=10)).isoformat(), "toState": {"name": "Approved"}},
                {"createdAt": (NOW - timedelta(hours=3)).isoformat(), "toState": {"name": "Approved"}},
            ]),
        }
        row["comments"] = connection([comment("MERGE PROOF PACK", (NOW - timedelta(days=9)).isoformat())])
        self.baseline()
        self.assertIn("approval-no-pack: WD-1=3h(unknown)", self.approval_tick())
        for marker in ("MERGE PROOF PACK", "LANDING PROOF PACK"):
            row["comments"]["nodes"].append(comment(marker, (NOW - timedelta(hours=1)).isoformat()))
            self.baseline()
            self.assertNotIn("approval-no-pack:", self.approval_tick())
            row["comments"]["nodes"].pop()
        row["comments"] = connection([comment("churn")], "older")
        self.comment_pages[("WD-1", "older")] = connection([comment("MERGE PROOF PACK", (NOW - timedelta(hours=1)).isoformat())])
        self.baseline()
        self.assertNotIn("approval-no-pack:", self.approval_tick())

    def test_no_pack_unreadable_history_uses_updated_unknown(self):
        row = self.no_pack_fixture()
        row["updatedAt"] = (NOW - timedelta(hours=4)).isoformat()
        row["comments"] = connection([comment("PRECONDITION: " + LINEAR)])
        for page in (OSError(LINEAR), connection([]), connection([], "repeat"),
                     connection([{"createdAt": "bad", "toState": {"name": "Approved"}}])):
            self.history_pages = {None: page, "repeat": connection([], "repeat")}
            self.baseline()
            out = self.approval_tick()
            self.assertIn("approval-no-pack: WD-1=4h(unknown)", out)
            self.assertNotIn(LINEAR, out)
        self.history_pages = {None: connection([], "0"), **{str(n): connection([], str(n+1)) for n in range(10)}}
        self.baseline()
        self.assertIn("approval-no-pack: WD-1=4h(unknown)", self.approval_tick())
        self.assertEqual(sum(json.loads(r.data)["query"] == g.APPROVAL_HISTORY_QUERY for r in self.requests), 10)

    def test_no_pack_incomplete_comments_cannot_claim_absence(self):
        row = self.no_pack_fixture()
        row["comments"] = connection([], "0")
        self.comment_pages = {("WD-1", str(n)): connection([comment()], str(n+1)) for n in range(10)}
        self.baseline()
        out = self.approval_tick()
        self.assertIn("approval-wait: unknown (approval comments incomplete)", out)
        self.assertNotIn("approval-no-pack:", out)

    def test_no_pack_diagnostics_do_not_change_dispatch_or_state(self):
        row = self.no_pack_fixture()
        self.baseline()
        before = self.state.read_bytes()
        with patch.object(g, "approval_issues", side_effect=lambda token, rows, *args: rows):
            plain = self.approval_tick()
            plain_state = self.state.read_bytes()
            plain_calls = copy.deepcopy(self.spawn.call_args_list)
        self.state.write_bytes(before)
        enriched = self.approval_tick()
        self.assertEqual(enriched.split("; approval-")[0], plain.split("; approval-")[0])
        self.assertEqual(self.state.read_bytes(), plain_state)
        self.assertEqual(self.spawn.call_args_list, plain_calls)
        self.assertIn("approval-no-pack:", enriched)
        snapshot = self.state.read_bytes()
        self.assertIn("approval-no-pack:", self.approval_tick("--dry-run"))
        self.assertEqual(self.state.read_bytes(), snapshot)

    def test_bad_profile_in_job_map_is_a_lane_error(self):
        out = self.tick("--job-map", json.dumps({"build": ["Todo", "x", "../evil"]}))
        self.assertIn("build: LANE-ERROR (", out)
        self.spawn.assert_not_called()

    # WD-620: additions only; existing tests and harness above stay unchanged.
    def ready(self, crm=True, identifier="WD-615"):
        row = GatekeeperTests.crm_issue(identifier) if crm else issue(identifier)
        row["updatedAt"] = (NOW - timedelta(hours=2)).isoformat()
        row["inverseRelations"]["nodes"][0]["issue"]["state"]["type"] = "completed"
        return row

    def prepare(self, minutes=16, two=False):
        self.write(self.jobs, {"jobs": [{"id": pair[1], "last_run_at": "2026-09-19T10:00:00Z"}
                                       for pair in g.DEFAULT_JOB_MAP.values()]})
        self.issues["Todo"] = [self.ready()]
        if two:
            self.issues["Todo"].append(self.ready(False, "WD-620"))
        self.baseline()
        state = json.loads(self.state.read_text())
        state["last_dispatch_at"] = {lane: (NOW - timedelta(minutes=minutes)).isoformat()
                                     for lane in ("build", "build-crm")}
        self.write(self.state, state)

    def status(self, value):
        jobs = json.loads(self.jobs.read_text())
        jobs["jobs"][0]["last_status"] = value
        self.write(self.jobs, jobs)

    def test_stale_boundary_and_repeat_guard(self):
        for minutes, expected in ((14, False), (15, True), (16, True)):
            with self.subTest(minutes=minutes):
                self.prepare(minutes)
                out = self.tick()
                self.assertEqual("stale-eligible" in out, expected)
                self.assertEqual(self.spawn.call_count, int(expected))
                if expected:
                    self.assertIn("build-crm: stale-eligible (1 ready, oldest WD-615 waiting 120m) -> dispatched(pid=1234)", out)
                    state = json.loads(self.state.read_text())
                    self.assertEqual(state["last_dispatch_at"]["build-crm"], NOW.isoformat())
                    self.assertIn("build-crm: in-flight (skip)", self.tick())
                    self.spawn.assert_not_called()
                    self.confirm()
                    with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=14)):
                        self.assertIn("build-crm: unchanged (skip)", self.tick())
                        self.spawn.assert_not_called()
                    with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=15)):
                        self.assertIn("build-crm: stale-eligible", self.tick())
                        self.spawn.assert_called_once()

    def test_budget_skips_two_hours_only_stale_eligible_dispatches(self):
        # Confirm fake worker completion; no worker or live Linear is invoked.
        # Probe between 15-minute ticks too, so a comment-triggered launch
        # cannot hide behind a coincident stale-eligible deadline.
        self.prepare(minutes=0)
        row = self.issues["Todo"][0]
        baseline = g.lane_fingerprint([row])
        launches = []
        for minutes in range(0, 121):
            at = NOW + timedelta(minutes=minutes)
            if minutes % 15 == 1:
                row["comments"]["nodes"].append(comment(
                    f"BUILD SKIPPED: completion budget (files={29 + minutes}, checks=6, reserve=20%)",
                    at.isoformat()))
                row["updatedAt"] = at.isoformat()
            jobs = json.loads(self.jobs.read_text())
            for job in jobs["jobs"]:
                job["last_run_at"] = at.isoformat()
            self.write(self.jobs, jobs)
            with patch.object(g, "now_utc", return_value=at):
                output = self.tick()
            self.assertEqual(baseline, g.lane_fingerprint([row]))
            self.assertNotIn("changed ->", output)
            expected = minutes > 0 and minutes % 15 == 0
            self.assertEqual(self.spawn.call_count, int(expected), output)
            if expected:
                self.assertIn("build-crm: stale-eligible", output)
                launches.append(minutes)
        self.assertEqual(launches, list(range(15, 121, 15)))

    def test_legacy_state_uses_oldest_update_and_not_priority(self):
        self.prepare()
        newer = self.ready(identifier="WD-999")
        newer.update(updatedAt=(NOW - timedelta(minutes=14)).isoformat(), priority=1)
        self.issues["Todo"].append(newer)
        self.baseline()
        self.assertIn("2 ready, oldest WD-615 waiting 120m", self.tick())
        self.issues["Todo"] = [newer]
        self.baseline()
        self.assertIn("build-crm: unchanged (skip)", self.tick())
        self.spawn.assert_not_called()
        newer["updatedAt"] = "invalid"
        self.assertIn("unchanged (skip)", self.tick())

    def test_blocker_state_types_and_park_decisions(self):
        self.prepare()
        row = self.issues["Todo"][0]
        for state_type, eligible in (("completed", True), ("canceled", True),
                                     ("started", False), ("unstarted", False), (None, False)):
            row["inverseRelations"]["nodes"][0]["issue"]["state"].update(name="custom", type=state_type)
            self.assertEqual(g.build_eligible(row), eligible)
        row["inverseRelations"]["nodes"] = []
        review = comment("**REVIEW REPORT — NEEDS HUMAN**")
        row["comments"]["nodes"] = [review]
        row["assignee"] = None
        self.assertFalse(g.build_eligible(row))
        decision: dict = comment("decision", "2026-09-19T13:00:00Z")
        decision["user"] = {"id": "bot"}
        row["comments"]["nodes"].append(decision)
        self.assertFalse(g.build_eligible(row))
        decision["user"]["id"] = g.PARK_OWNER_ID
        self.assertTrue(g.build_eligible(row))
        row["assignee"] = {"id": g.PARK_OWNER_ID}
        self.assertFalse(g.build_eligible(row))
        row["comments"]["nodes"].append(comment("REVIEW REPORT — FAIL", "2026-09-19T14:00:00Z"))
        self.assertTrue(g.build_eligible(row))
        row["comments"]["nodes"].append(comment("BLOCKED: decision needed", "2026-09-19T15:00:00Z"))
        self.assertFalse(g.build_eligible(row))

    def test_decision_record_clears_park_only_with_link_own_issue_and_unassignment(self):
        # WD-638. Synthetic ids and links only; the record's shape is checked, not its truth.
        link = "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333"
        recorder = "synthetic-recorder"
        self.assertNotEqual(recorder, g.PARK_OWNER_ID)

        def record(identifier="WD-615", evidence=link, created="2026-09-19T13:00:00Z",
                   header="**DECISION RECORD — {}**"):
            row = comment("**Agent:** Synthetic / fixture (Logan)\n\n"
                          + header.format(identifier) + "\n"
                          "- **Decision asked:** [SCOPE-CONFLICT] Decide synthetic option A or B\n"
                          "- **Jonathan's answer:** synthetic option A\n"
                          f"- **Discord message:** {evidence}\n"
                          "- **Recorded by:** Logan. This is an agent's record.", created)
            row["user"] = {"id": recorder}
            return row

        def parked(*later, assignee=None):
            row = self.ready()
            self.assertEqual(row["identifier"], "WD-615")
            row["assignee"] = assignee
            row["comments"]["nodes"] = [comment("**REVIEW REPORT — NEEDS HUMAN**"), *later]
            return row

        # AC-1: a valid newer record by someone other than the park owner clears the park.
        self.assertFalse(g.build_eligible(parked()))
        self.assertTrue(g.build_eligible(parked(record())))
        for header in ("DECISION RECORD - {}", "## DECISION RECORD — {}", "- **DECISION RECORD — {}**",
                       "  ### - **DECISION RECORD - {}"):
            with self.subTest(header=header):
                self.assertTrue(g.build_eligible(parked(record(header=header))))
        # AC-2: no Discord message link, another issue's identifier, or older than the report.
        for label, kwargs in (
            ("no link", {"evidence": "answered in the exec thread"}),
            ("other issue", {"identifier": "WD-616"}),
            ("identifier prefix", {"identifier": "WD-61"}),
            ("identifier extension", {"identifier": "WD-6150"}),
            ("older than report", {"created": "2026-09-19T11:00:00Z"}),
            ("same instant as report", {"created": "2026-09-19T12:00:00Z"}),
        ):
            with self.subTest(case=label):
                self.assertFalse(g.build_eligible(parked(record(**kwargs))))
        # Malformed links and loose mentions are not records; doubtful cases stay parked.
        for evidence in (
            "https://discord.com/channels/1111111111111111/222222222222222222/333333333333333333",
            "https://discord.com/channels/111111111111111111/222222222222222222/333333333333333333333",
            "https://discord.com/channels/111111111111111111/222222222222222222",
            "http://discord.com/channels/111111111111111111/222222222222222222/333333333333333333",
            "https://discordapp.com/channels/111111111111111111/222222222222222222/333333333333333333",
            "https://discord.com/channels/@me/222222222222222222/333333333333333333",
        ):
            with self.subTest(evidence=evidence):
                self.assertFalse(g.build_eligible(parked(record(evidence=evidence))))
        mention = comment(f"I will post DECISION RECORD — WD-615 later, see {link}", "2026-09-19T13:00:00Z")
        mention["user"] = {"id": recorder}
        self.assertFalse(g.build_eligible(parked(mention)))
        for body in (None, "", link):
            empty = comment(body, "2026-09-19T13:00:00Z")
            empty["user"] = {"id": recorder}
            with self.subTest(body=body):
                self.assertFalse(g.build_eligible(parked(empty)))
        self.assertFalse(g.is_decision_record(record()["body"], None))
        # AC-3: a valid newer record does not clear the park while Jonathan is still assigned.
        self.assertFalse(g.build_eligible(parked(record(), assignee={"id": g.PARK_OWNER_ID})))
        # AC-4: the older form still clears it: no record, unassigned, newer park-owner comment.
        owner = comment("decision", "2026-09-19T13:00:00Z")
        owner["user"] = {"id": g.PARK_OWNER_ID}
        self.assertTrue(g.build_eligible(parked(owner)))
        # A newer NEEDS HUMAN report parks the issue again until a newer record arrives.
        again = comment("REVIEW REPORT — NEEDS HUMAN", "2026-09-19T14:00:00Z")
        self.assertFalse(g.build_eligible(parked(record(), again)))
        self.assertTrue(g.build_eligible(parked(record(), again, record(created="2026-09-19T15:00:00Z"))))
        # The helper is pure and order-independent, and is true when nothing is parked.
        row = parked(record())
        before = copy.deepcopy(row)
        self.assertTrue(g.park_cleared(row, list(reversed(row["comments"]["nodes"]))))
        self.assertEqual(row, before)
        self.assertFalse(g.park_cleared(row, row["comments"]["nodes"][:1]))
        self.assertTrue(g.park_cleared(row, []))
        self.assertTrue(g.park_cleared(row, [comment("REVIEW REPORT — NEEDS HUMAN", "2026-09-19T11:00:00Z"),
                                              comment("REVIEW REPORT — FAIL")]))
        del row["identifier"]
        self.assertFalse(g.park_cleared(row, row["comments"]["nodes"]))

    def test_review_mentions_are_not_verdicts_and_paging_errors_are_safe(self):
        row = self.ready()
        row["comments"]["nodes"] = [comment("REVIEW REPORT — NEEDS HUMAN")]
        row["comments"]["nodes"].append(comment("I mentioned REVIEW REPORT — FAIL, not a new verdict", "2026-09-19T13:00:00Z"))
        self.assertFalse(g.build_eligible(row))
        row["comments"] = connection([comment("recent")], "repeat")
        pages = [{"issues": connection([row])}, {"issue": {"comments": connection([], "repeat")}}]
        with patch.object(g, "linear_query", side_effect=pages):
            with self.assertRaises(g.ProbeError):
                g.lane_issues(LINEAR, "Todo")

    def test_pending_recovery_never_overrides_401(self):
        self.prepare(16, two=True)
        state = json.loads(self.state.read_text())
        state["degraded_since"] = NOW.isoformat()
        state["recovery_pending"] = ["build", "build-crm"]
        self.write(self.state, state)
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, Message(), None)
        out = self.tick()
        self.assertNotIn("stale-eligible", out)
        self.assertNotIn("recovered after", out)
        self.assertIn("integrate: linear-auth-failed (401) -> dispatched", out)
        self.spawn.assert_called_once()
        after = json.loads(self.state.read_text())
        self.assertEqual(after["degraded_since"], state["degraded_since"])
        self.assertEqual(after["recovery_pending"], state["recovery_pending"])

    def test_recovery_after_fail_open_and_worker_errors(self):
        for failure in ("fail-open", "error", "interrupted"):
            with self.subTest(failure=failure):
                self.prepare(1, two=True)
                if failure == "fail-open":
                    self.linear_error = TimeoutError("synthetic")
                else:
                    self.status(failure)
                self.tick()
                self.assertEqual(json.loads(self.state.read_text())["degraded_since"], NOW.isoformat())
                self.linear_error = None
                self.status("ok")
                self.confirm()
                out = self.tick()
                self.assertEqual(out.count("recovered after degraded period -> dispatched(pid=1234)"), 2)
                self.assertEqual(self.spawn.call_count, 2)
                state = json.loads(self.state.read_text())
                self.assertNotIn("degraded_since", state)
                self.assertEqual(state["recovery_pending"], [])
                # Complete these launches and prove normal rules resume.
                with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=1)):
                    jobs = json.loads(self.jobs.read_text())
                    for job in jobs["jobs"]:
                        job["last_run_at"] = (NOW + timedelta(seconds=30)).isoformat()
                    self.write(self.jobs, jobs)
                    out = self.tick()
                    self.assertNotIn("recovered after", out)
                    self.spawn.assert_not_called()

    def test_recovery_keeps_guard_and_retries_failed_launch(self):
        self.prepare(1, two=True)
        state = json.loads(self.state.read_text())
        state["degraded_since"] = (NOW - timedelta(hours=1)).isoformat()
        state["lanes"]["build"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        self.write(self.state, state)
        self.spawn.side_effect = OSError("synthetic")
        out = self.tick()
        self.assertIn("build: in-flight (skip)", out)
        self.assertIn("recovered after degraded period -> dispatch-failed", out)
        self.assertEqual(json.loads(self.state.read_text())["recovery_pending"], ["build", "build-crm"])
        self.spawn.side_effect = None
        self.confirm()
        self.assertEqual(self.tick().count("recovered after degraded period -> dispatched"), 2)

    def test_no_ready_work_for_two_hours(self):
        for mode in ("empty", "blocked", "parked"):
            with self.subTest(mode=mode):
                self.prepare()
                if mode == "empty":
                    self.issues["Todo"] = []
                elif mode == "blocked":
                    self.issues["Todo"][0]["inverseRelations"]["nodes"][0]["issue"]["state"]["type"] = "started"
                else:
                    self.issues["Todo"][0]["comments"]["nodes"] = [comment("REVIEW REPORT — NEEDS HUMAN")]
                self.baseline()
                state = json.loads(self.state.read_text())
                state["degraded_since"] = NOW.isoformat()
                self.write(self.state, state)
                for minutes in range(0, 121, 15):
                    with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=minutes)):
                        out = self.tick()
                        self.assertNotIn("stale-eligible", out)
                        self.assertNotIn("recovered after", out)
                        self.spawn.assert_not_called()

    def test_new_rules_dry_run_and_failed_dispatch_are_non_consuming(self):
        self.prepare()
        for recovery in (False, True):
            state = json.loads(self.state.read_text())
            if recovery:
                state["degraded_since"] = NOW.isoformat()
                self.write(self.state, state)
            before = self.state.read_bytes()
            out = self.tick("--dry-run")
            self.assertIn("dispatched(dry-run)", out)
            self.assertEqual(self.state.read_bytes(), before)
            self.spawn.assert_not_called()
        self.prepare()
        before = json.loads(self.state.read_text())["last_dispatch_at"]
        self.spawn.side_effect = OSError("synthetic")
        self.assertIn("stale-eligible", self.tick())
        self.assertEqual(json.loads(self.state.read_text())["last_dispatch_at"], before)

    def test_older_park_comments_paged_without_changing_fingerprint(self):
        row = self.ready()
        row["comments"] = connection([comment("recent")], "c1")
        older = comment("REVIEW REPORT — NEEDS HUMAN", "2026-09-18T12:00:00Z")
        before = g.lane_fingerprint([row])
        pages = [{"issues": connection([row])}, {"issue": {"comments": connection([older])}}]
        with patch.object(g, "linear_query", side_effect=pages) as query:
            rows = g.lane_issues(LINEAR, "Todo")
        self.assertEqual(query.call_count, 2)
        self.assertFalse(g.build_eligible(rows[0]))
        self.assertEqual(g.lane_fingerprint(rows), before)

    def test_recovery_does_not_change_review_integrate_eligibility(self):
        self.prepare(1)
        self.issues["Todo"] = []
        self.issues["In Review"] = [issue(state="In Review")]
        self.issues["Approved"] = [issue(state="Approved")]
        self.baseline()
        state = json.loads(self.state.read_text())
        state["degraded_since"] = NOW.isoformat()
        self.write(self.state, state)
        out = self.tick()
        for lane in ("review", "integrate"):
            self.assertIn(f"{lane}: recovered after degraded period -> dispatched", out)
        self.assertEqual(self.spawn.call_count, 2)
        self.assertEqual(json.loads(self.state.read_text())["approval_dispatched"], {})


    # WD-578: synthetic inputs and fake HTTP/Popen/R1 from setUp only.
    def assert_fail_open_backoff_sequence(self, reason):
        self.assertEqual(g.FAIL_OPEN_BACKOFF_MINUTES, 120)
        out = self.tick()
        for lane in g.DEFAULT_JOB_MAP:
            self.assertIn(f"{lane}: {reason} -> dispatched(pid=1234)", out)
        self.assertEqual(self.spawn.call_count, len(g.DEFAULT_JOB_MAP))
        expected = {lane: NOW.isoformat() for lane in g.DEFAULT_JOB_MAP}
        self.assertEqual(json.loads(self.state.read_text())["fail_open_dispatched"], expected)
        self.confirm()
        until = (NOW + timedelta(minutes=120)).isoformat()
        for minutes in (30, 60, 90, 119):
            with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=minutes)):
                out = self.tick()
            for lane in g.DEFAULT_JOB_MAP:
                self.assertIn(f"{lane}: {reason} -> backoff until {until}", out)
            self.spawn.assert_not_called()
            state = json.loads(self.state.read_text())
            self.assertEqual(state["fail_open_dispatched"], expected)
            self.assertEqual(state["last_dispatch_at"], expected)
            self.assertEqual(state["lanes"], {})
            self.assertEqual(state["degraded_since"], NOW.isoformat())
        later = NOW + timedelta(minutes=121)
        with patch.object(g, "now_utc", return_value=later):
            out = self.tick()
        for lane in g.DEFAULT_JOB_MAP:
            self.assertIn(f"{lane}: {reason} -> dispatched(pid=1234)", out)
        self.assertEqual(self.spawn.call_count, len(g.DEFAULT_JOB_MAP))
        self.assertEqual(json.loads(self.state.read_text())["fail_open_dispatched"],
                         {lane: later.isoformat() for lane in g.DEFAULT_JOB_MAP})

    def test_fail_open_network_backoff_ac1(self):
        self.http.side_effect = urllib.error.URLError(LINEAR)
        self.assert_fail_open_backoff_sequence("probe failed (URLError) -> FAIL-OPEN")
        self.assertNotIn(LINEAR, "".join(self.output))

    def test_fail_open_missing_env_backoff_ac2(self):
        self.env.unlink()
        self.assert_fail_open_backoff_sequence("FAIL-OPEN (env unreadable: FileNotFoundError)")
        self.http.assert_not_called()

    def test_fail_open_healthy_tick_clears_backoff_ac3(self):
        self.http.side_effect = urllib.error.URLError("synthetic")
        self.tick()
        self.confirm()
        self.http.side_effect = self.open
        # Empty healthy queues clear the records even without a launch.
        self.issues = {state: [] for state in self.issues}
        with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=30)):
            out = self.tick()
        for lane in g.DEFAULT_JOB_MAP:
            self.assertIn(f"{lane}: idle", out)
        self.spawn.assert_not_called()
        self.assertEqual(json.loads(self.state.read_text())["fail_open_dispatched"], {})
        self.http.side_effect = urllib.error.URLError("synthetic")
        with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=31)):
            out = self.tick()
        self.assertEqual(out.count("FAIL-OPEN -> dispatched(pid=1234)"), len(g.DEFAULT_JOB_MAP))
        self.assertEqual(self.spawn.call_count, len(g.DEFAULT_JOB_MAP))

    def test_fail_open_backoff_is_per_lane_and_exact_boundary(self):
        self.baseline()
        state = json.loads(self.state.read_text())
        state["fail_open_dispatched"] = {
            "build": NOW.isoformat(),
            "build-crm": (NOW - timedelta(minutes=120)).isoformat(),
            "review": "invalid",
        }
        self.write(self.state, state)
        self.http.side_effect = urllib.error.URLError("synthetic")
        out = self.tick()
        self.assertIn("build: probe failed (URLError) -> FAIL-OPEN -> backoff until", out)
        for lane in ("build-crm", "review", "integrate"):
            self.assertIn(f"{lane}: probe failed (URLError) -> FAIL-OPEN -> dispatched(pid=1234)", out)
        self.assertEqual(self.spawn.call_count, 3)

    def test_fail_open_backoff_normalizes_legacy_state(self):
        for value in (None, [], False, "invalid"):
            with self.subTest(value=value):
                self.write(self.state, {"fail_open_dispatched": value})
                self.assertEqual(g.load_state(str(self.state))["fail_open_dispatched"], {})
        self.write(self.state, {})
        self.assertEqual(g.load_state(str(self.state))["fail_open_dispatched"], {})

    def test_fail_open_failed_dispatch_and_dry_run_ac4(self):
        self.http.side_effect = urllib.error.URLError("synthetic")
        self.spawn.side_effect = OSError(LINEAR)
        out = self.tick()
        self.assertEqual(out.count("FAIL-OPEN -> dispatch-failed(OSError)"), len(g.DEFAULT_JOB_MAP))
        self.assertEqual(json.loads(self.state.read_text())["fail_open_dispatched"], {})
        self.assertNotIn(LINEAR, out)
        self.spawn.side_effect = None
        before = {p: p.read_bytes() for p in (self.state, self.jobs, self.env)}
        out = self.tick("--dry-run")
        self.assertEqual(out.count("FAIL-OPEN -> dispatched(dry-run)"), len(g.DEFAULT_JOB_MAP))
        self.spawn.assert_not_called()
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.tick()
        self.confirm()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=30)), \
                patch.object(g.tempfile, "mkstemp", wraps=g.tempfile.mkstemp) as mkstemp:
            out = self.tick("--dry-run")
        self.assertEqual(out.count("FAIL-OPEN -> backoff until"), len(g.DEFAULT_JOB_MAP))
        self.spawn.assert_not_called()
        mkstemp.assert_not_called()
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)

    def test_linear_401_after_fail_open_bypasses_backoff_ac5(self):
        self.http.side_effect = urllib.error.URLError("synthetic")
        self.tick()
        self.confirm()
        before = json.loads(self.state.read_text())
        self.http.side_effect = self.open
        self.linear_error = urllib.error.HTTPError("https://api.linear.app/graphql", 401, LINEAR, Message(), None)
        with patch.object(g, "now_utc", return_value=NOW + timedelta(minutes=30)):
            out = self.tick()
        for lane in ("build", "build-crm", "review"):
            self.assertIn(f"{lane}: linear-auth-failed (401) -> skipped", out)
        self.assertIn("integrate: linear-auth-failed (401) -> dispatched(pid=1234) for re-mint", out)
        self.assertNotIn("backoff", out)
        self.assertNotIn(LINEAR, out)
        self.spawn.assert_called_once()
        after = json.loads(self.state.read_text())
        self.assertEqual(after["fail_open_dispatched"], before["fail_open_dispatched"])
        self.assertEqual(after["degraded_since"], before["degraded_since"])

    def test_healthy_probe_clears_backoff_before_skip_or_failed_launch(self):
        for mode in ("unchanged", "discord-failure", "spawn-failure"):
            with self.subTest(mode=mode):
                self.with_pack()
                self.reactors = []
                self.reader_result = (None, "timeout") if mode == "discord-failure" else None
                state = json.loads(self.state.read_text())
                state["fail_open_dispatched"] = {"integrate": NOW.isoformat()}
                self.write(self.state, state)
                if mode == "spawn-failure":
                    self.issues["Approved"][0]["description"] = "changed"
                    self.spawn.side_effect = OSError("synthetic")
                out = self.tick()
                expected = {"unchanged": "unchanged (skip)", "discord-failure": "discord-check-failed (timeout)",
                            "spawn-failure": "changed -> dispatch-failed(OSError)"}[mode]
                self.assertIn("integrate: " + expected, out)
                self.assertEqual(json.loads(self.state.read_text())["fail_open_dispatched"], {})

    def test_atomic_replace_failure_retains_state_and_cleans_temp_ac6(self):
        self.baseline()
        before = self.state.read_bytes()
        with patch.object(g.os, "replace", side_effect=PermissionError(LINEAR)):
            out = self.tick()
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        self.assertIn("state: save-failed (PermissionError)", out)
        self.assertNotIn(LINEAR, out)

    def test_atomic_normal_write_flushes_syncs_in_same_directory_ac6(self):
        self.baseline()
        old = self.state.read_bytes()
        sync = g.os.fsync
        replace = g.os.replace
        events = []

        def synced(fd):
            temporary = list(self.root.glob(".gatekeeper-state.*.tmp"))
            self.assertEqual(len(temporary), 1)
            # Reading valid JSON through a separate handle proves flush preceded fsync.
            self.assertIn("fail_open_dispatched", json.loads(temporary[0].read_text()))
            self.assertEqual(self.state.read_bytes(), old)
            events.append("fsync")
            return sync(fd)

        def replaced(source, destination):
            self.assertEqual(events, ["fsync"])
            self.assertEqual(Path(source).parent, self.state.parent)
            self.assertEqual(Path(destination), self.state)
            events.append("replace")
            return replace(source, destination)

        with patch.object(g.tempfile, "mkstemp", wraps=g.tempfile.mkstemp) as mkstemp, \
                patch.object(g.os, "fsync", side_effect=synced), \
                patch.object(g.os, "replace", side_effect=replaced):
            out = self.tick()
        mkstemp.assert_called_once_with(dir=str(self.root), prefix=".gatekeeper-state.", suffix=".tmp")
        self.assertEqual(events, ["fsync", "replace"])
        self.assertNotIn("save-failed", out)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_atomic_interrupted_json_keeps_recovery_inflight_ac7(self):
        self.prepare(1, two=True)
        state = json.loads(self.state.read_text())
        state["degraded_since"] = (NOW - timedelta(hours=1)).isoformat()
        state["lanes"]["build"] = {"dispatched_at": NOW.isoformat(), "job_last_run_at": "2026-09-19T10:00:00Z"}
        state["fail_open_dispatched"] = {"build": NOW.isoformat()}
        self.write(self.state, state)
        before = self.state.read_bytes()

        def interrupted(data, fh, **kwargs):
            fh.write('{"lanes":')
            fh.flush()
            raise OSError(LINEAR)

        with patch.object(g.json, "dump", side_effect=interrupted):
            out = self.tick()
        self.assertIn("state: save-failed (OSError)", out)
        self.assertNotIn(LINEAR, out)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])
        out = self.tick()
        self.assertIn("build: in-flight (skip)", out)
        self.assertIn("build-crm: recovered after degraded period -> dispatched(pid=1234)", out)
        after = json.loads(self.state.read_text())
        self.assertEqual(after["lanes"]["build"], state["lanes"]["build"])
        self.assertEqual(after["fail_open_dispatched"], state["fail_open_dispatched"])
        self.assertEqual(after["recovery_pending"], ["build"])
        self.assertNotIn("degraded_since", after)

    def test_atomic_creation_and_fsync_failures_are_safe(self):
        self.baseline()
        before = self.state.read_bytes()
        for target, name in ((g.tempfile, "mkstemp"), (g.os, "fsync")):
            with self.subTest(stage=name), patch.object(target, name, side_effect=OSError(LINEAR)):
                out = self.tick()
            self.assertIn("state: save-failed (OSError)", out)
            self.assertNotIn(LINEAR, out)
            self.assertEqual(self.state.read_bytes(), before)
            self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_quoted_linear_tokens_and_empty_value_ac8(self):
        for value in (f'"{LINEAR}"', f"'{LINEAR}'", LINEAR):
            with self.subTest(quoted=value != LINEAR):
                self.env.write_text(f"  LINEAR_FACTORY_BOT_TOKEN={value}  \n", encoding="utf-8")
                self.baseline()
                out = self.tick()
                self.assertTrue(self.requests)
                for request in self.requests:
                    self.assertEqual(request.get_header("Authorization"), "Bearer " + LINEAR)
                self.assertNotIn("FAIL-OPEN", out)
                self.assertNotIn(LINEAR, out + self.state.read_text())
        self.env.write_text('LINEAR_FACTORY_BOT_TOKEN=""\n', encoding="utf-8")
        with self.assertRaisesRegex(g.ProbeError, "LINEAR_FACTORY_BOT_TOKEN missing or empty"):
            g.read_token(str(self.env))
        self.baseline()
        out = self.tick()
        self.assertIn("FAIL-OPEN (env unreadable: LINEAR_FACTORY_BOT_TOKEN missing or empty)", out)
        self.http.assert_not_called()
        self.assertNotIn(LINEAR, out + self.state.read_text())


if __name__ == "__main__":
    unittest.main()
