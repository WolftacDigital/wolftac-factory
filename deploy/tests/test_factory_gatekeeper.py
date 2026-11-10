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
        self.args = ["gatekeeper", "--no-audit", "--env-path", str(self.env), "--jobs-path", str(self.jobs),
                     "--state-path", str(self.state), "--approval-policy", str(self.policy),
                     "--exec-override", json.dumps(["fake-noop"])]
        self.addCleanup(patch.stopall)
        patch.object(g, "hermes_home", return_value=str(self.root)).start()
        patch.object(g, "now_utc", return_value=NOW).start()
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


if __name__ == "__main__":
    unittest.main()
