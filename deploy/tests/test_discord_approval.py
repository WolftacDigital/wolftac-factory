"""Offline tests for deploy/discord_approval.py (WD-546 AC-2, AC-3).

R1 is replaced by fixtures shaped like its contract. No Discord, resolver or
Hermes call happens here.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("discord_approval", ROOT / "deploy" / "discord_approval.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)

GUILD = "923752167293222912"
CHANNEL = "1562236246158221323"
THREAD = "1569999999999999991"
APPROVER = "1022999767753691186"
HARPER = "1562178628211449966"
OTHER_BOT = "1562178628211449999"
MESSAGE = "1569128910862684180"
SHA = "c226caf0a4bb6f483770cf0e3e4e2236843cedb4"
REPO = "WolftacDigital/wolftac-factory"
ROCKET = "\U0001F680"
SENTINEL = "discord-token-SENTINEL-546"
POLICY = {"discord_guild_id": GUILD, "discord_channel_id": CHANNEL, "approver_discord_user_id": APPROVER,
          "pack_author_discord_user_ids": [HARPER], "reader_bot_discord_user_id": HARPER,
          "approval_emoji": ROCKET}
PACK = "https://linear.app/wolftac-digital/issue/WD-546#comment-1"
MENTION = f"<@{APPROVER}> react {ROCKET} to approve this exact commit. A new commit cancels this approval."


def body(issue="WD-546", repo=REPO, pr=f"#75 https://github.com/{REPO}/pull/75", head=SHA,
         pack=PACK, mention=MENTION, extra=None):
    lines = ["FACTORY MERGE APPROVAL REQUEST", f"Issue: {issue}", f"Repo: {repo}", f"PR: {pr}",
             f"Head: {head}", f"Proof pack: {pack}", mention]
    if extra is not None:
        lines.append(extra)
    return "\n".join(lines)


def document(reactors=None, content=None, **overrides):
    content = body() if content is None else content
    doc = {
        "ok": True, "contract": 1, "reader_bot_id": HARPER, "guild_id": GUILD, "channel_id": CHANNEL,
        "parent_id": None, "emoji": ROCKET, "reactor_pages": 1,
        "message": {"id": MESSAGE, "author": {"id": HARPER, "bot": True}, "timestamp": "2026-11-08T23:00:00+00:00",
                    "edited_timestamp": None, "content": content, "content_sha256": "0" * 64},
        "reactors": [{"id": APPROVER, "bot": False}] if reactors is None else reactors,
    }
    for key, value in overrides.items():
        if key.startswith("message_"):
            doc["message"][key[len("message_"):]] = value
        else:
            doc[key] = value
    return doc


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy = Path(self.tmp.name) / "approval-policy.json"
        self.policy.write_text(json.dumps(POLICY), encoding="utf-8")
        self.result = (0, json.dumps(document()))
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(v, "run_reader", side_effect=self.reader).start()

    def reader(self, argv, env, timeout=v.READER_TIMEOUT_SECONDS):
        self.calls.append((list(argv), dict(env), timeout))
        return self.result

    def verify(self, *, channel=CHANNEL, message=MESSAGE, issue="WD-546", repo=REPO, pr="75", sha=SHA,
               policy=None, pack=PACK):
        argv = ["verify", "--policy", str(policy or self.policy), "--channel-id", channel, "--message-id", message,
                "--issue", issue, "--repo", repo, "--pr", pr, "--sha", sha, "--proof-pack", pack]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = v.main(argv)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, out.getvalue())
        result = json.loads(lines[0])
        self.assertEqual(set(result), {"verdict", "reason", "issue", "message_id"})
        return code, result

    def assertVerdict(self, expected_code, verdict, reason, **kwargs):
        code, result = self.verify(**kwargs)
        self.assertEqual((code, result["verdict"], result["reason"]), (expected_code, verdict, reason))
        return result

    # --- positive cases -------------------------------------------------
    def test_approved_only_when_every_check_passes(self):
        result = self.assertVerdict(0, "APPROVED", "approver-reacted")
        self.assertEqual((result["issue"], result["message_id"]), ("WD-546", MESSAGE))
        argv, env, timeout = self.calls[0]
        self.assertEqual(argv[-10:], ["-p", "integrator", "wolftac-approval-read", "--channel", CHANNEL,
                                      "--message", MESSAGE, "--emoji", ROCKET, "--json"])
        self.assertEqual(timeout, 75)

    def test_approver_on_a_later_page(self):
        reactors = [{"id": str(1100000000000000000 + i), "bot": i % 3 == 0} for i in range(240)]
        reactors.insert(170, {"id": APPROVER, "bot": False})
        self.result = (0, json.dumps(document(reactors, reactor_pages=3)))
        self.assertVerdict(0, "APPROVED", "approver-reacted")

    def test_thread_under_approvals_is_allowed(self):
        self.result = (0, json.dumps(document(channel_id=THREAD, parent_id=CHANNEL)))
        self.assertVerdict(0, "APPROVED", "approver-reacted", channel=THREAD)

    def test_branchless_landing_and_pr_without_url(self):
        self.result = (0, json.dumps(document(content=body(pr="none (branchless)"))))
        self.assertVerdict(0, "APPROVED", "approver-reacted", pr="none")
        self.result = (0, json.dumps(document(content=body(pr="#75"))))
        self.assertVerdict(0, "APPROVED", "approver-reacted")

    def test_hermes_banner_lines_around_the_contract_line_are_ignored(self):
        self.result = (0, "Hermes banner\n" + json.dumps(document()) + "\nbye\n")
        self.assertVerdict(0, "APPROVED", "approver-reacted")

    # --- each failure on its own ------------------------------------------
    def test_each_body_and_message_failure(self):
        cases = {
            "wrong-channel": (dict(channel_id="1562236246158221999", parent_id=None), {"channel": "1562236246158221999"},
                              "channel-not-allowed"),
            "thread-wrong-parent": (dict(channel_id=THREAD, parent_id="1562236246158221999"), {"channel": THREAD},
                                    "channel-not-allowed"),
            "wrong-guild": (dict(guild_id="923752167293222999"), {}, "channel-not-allowed"),
            "message-id-mismatch": (dict(message_id="1569128910862684999"), {}, "message-mismatch"),
            "wrong-author": (dict(message_author={"id": OTHER_BOT, "bot": True}), {}, "author-not-allowed"),
            "author-not-bot": (dict(message_author={"id": HARPER, "bot": False}), {}, "author-not-allowed"),
            "edited": (dict(message_edited_timestamp="2026-11-08T23:05:00+00:00"), {}, "message-edited"),
            "wrong-emoji": (dict(emoji="\U0001F44D"), {}, "wrong-emoji"),
            "wrong-reader": (dict(reader_bot_id=OTHER_BOT), {}, "wrong-reader"),
            "repo-mismatch": (dict(message_content=body(repo="WolftacDigital/other",
                                                        pr="#75 https://github.com/WolftacDigital/other/pull/75")),
                              {}, "repo-mismatch"),
            "pr-mismatch": (dict(message_content=body(pr="#76 https://github.com/" + REPO + "/pull/76")), {},
                            "pr-mismatch"),
            "pr-url-mismatch": (dict(message_content=body(pr="#75 https://github.com/" + REPO + "/pull/76")), {},
                                "pr-mismatch"),
            "pr-for-branchless": (dict(message_content=body()), {"pr": "none"}, "pr-mismatch"),
            "sha-mismatch": (dict(message_content=body(head="d" * 40)), {}, "sha-mismatch"),
            "issue-mismatch": (dict(message_content=body(issue="WD-547")), {}, "issue-mismatch"),
            "header": (dict(message_content=body().replace("FACTORY MERGE APPROVAL REQUEST", "FACTORY MERGE")), {},
                       "malformed-body"),
            "missing-line": (dict(message_content="\n".join(body().split("\n")[:-1])), {}, "malformed-body"),
            "extra-line": (dict(message_content=body(extra="Also approved: WD-1")), {}, "malformed-body"),
            "reordered": (dict(message_content="\n".join([body().split("\n")[i] for i in (0, 2, 1, 3, 4, 5, 6)])), {},
                          "malformed-body"),
            "wrong-mention": (dict(message_content=body(mention=MENTION.replace(APPROVER, OTHER_BOT))), {},
                              "malformed-body"),
            "pack-not-linear": (dict(message_content=body(pack="https://example.com/x")), {},
                                "proof-pack-mismatch"),
            "other-issue-pack": (dict(message_content=body(
                pack="https://linear.app/wolftac-digital/issue/WD-999#comment-1")), {}, "proof-pack-mismatch"),
            "older-pack-same-issue": (dict(message_content=body(
                pack="https://linear.app/wolftac-digital/issue/WD-546#comment-0")), {}, "proof-pack-mismatch"),
            "recovery-request": (dict(message_content=body().replace("REQUEST", "REQUEST (REQUEST-ONLY RECOVERY)", 1)),
                                 {}, "pre-cutover-request"),
            "interim-request": (dict(message_content=body(extra="No merge will run until the Discord gate (WD-546) "
                                                                "is live.")), {}, "pre-cutover-request"),
        }
        for name, (overrides, verify_args, reason) in cases.items():
            with self.subTest(case=name):
                self.result = (0, json.dumps(document(**overrides)))
                self.assertVerdict(1, "REJECTED", reason, **verify_args)

    def test_reactions_from_anyone_else_are_not_approval(self):
        for name, reactors in {
            "none": [],
            "other-users": [{"id": "1022999767753691187", "bot": False}],
            "bots-only": [{"id": HARPER, "bot": True}, {"id": OTHER_BOT, "bot": True}],
            "approver-id-flagged-bot": [{"id": APPROVER, "bot": True}],
        }.items():
            with self.subTest(case=name):
                self.result = (0, json.dumps(document(reactors)))
                self.assertVerdict(1, "NOT_APPROVED", "approver-absent")

    def test_malformed_reactor_rows_are_rejected(self):
        for rows in ([{"id": APPROVER}], [{"id": 1022999767753691186, "bot": False}], ["x"]):
            with self.subTest(rows=rows):
                self.result = (0, json.dumps(document(rows)))
                self.assertVerdict(1, "REJECTED", "malformed-reader-output")

    def test_every_reader_failure_is_rejected(self):
        def err(code, exit_code):
            return exit_code, json.dumps({"ok": False, "contract": 1, "error": code})

        cases = {
            "identity": (err("identity_mismatch", 4), "api-error(identity_mismatch)"),
            "changed": (err("message_changed", 5), "api-error(message_changed)"),
            "deleted": (err("message_deleted", 5), "api-error(message_deleted)"),
            "token": (err("token_unavailable", 4), "api-error(token_unavailable)"),
            "ok-false-exit-zero": (err("pagination_stalled", 0), "api-error(pagination_stalled)"),
            "ok-true-nonzero": ((5, json.dumps(document())), "api-error(exit-5)"),
            "nonzero-no-json": ((124, ""), "api-error(exit-124)"),
            "timeout": ((None, "timeout"), "api-error(timeout)"),
            "launch": ((None, "launch-failed"), "api-error(launch-failed)"),
            "unparseable": ((0, "{not json"), "api-error(malformed-output)"),
            "two-lines": ((0, json.dumps(document()) + "\n" + json.dumps(document())), "api-error(malformed-output)"),
            "no-contract": ((0, json.dumps({"ok": True})), "api-error(malformed-output)"),
            "hostile-code": (err("x" * 80, 5), "api-error(reader-error)"),
            "missing-message": ((0, json.dumps(dict(document(), message=None))), None),
        }
        for name, (result, reason) in cases.items():
            with self.subTest(case=name):
                self.result = result
                if reason is None:
                    self.assertVerdict(1, "REJECTED", "malformed-reader-output")
                else:
                    self.assertVerdict(1, "REJECTED", reason)

    def test_bad_arguments_never_run_the_reader(self):
        for kwargs in ({"sha": SHA.upper()}, {"sha": SHA[:39]}, {"issue": "WD-"}, {"issue": "XX-1"},
                       {"pr": "0"}, {"pr": "#75"}, {"channel": "1234"}, {"message": "1" * 21},
                       {"repo": "no-slash"}, {"repo": "a/b/c"}, {"pack": "https://example.com/x"},
                       {"pack": "https://linear.app/x y"}, {"pack": ""}):
            with self.subTest(kwargs=kwargs):
                self.calls.clear()
                code, result = self.verify(**kwargs)
                self.assertEqual((code, result["verdict"], result["reason"]), (2, "REJECTED", "bad-arguments"))
                self.assertEqual(self.calls, [])

    def test_policy_must_be_exactly_the_six_discord_keys(self):
        bad = {
            "slack": {"slack_channel_id": "C123", "approver_slack_user_id": "U123"},
            "extra-key": dict(POLICY, slack_channel_id="C123"),
            "missing-key": {k: val for k, val in POLICY.items() if k != "approval_emoji"},
            "wrong-emoji": dict(POLICY, approval_emoji=":rocket:"),
            "bad-id": dict(POLICY, approver_discord_user_id="U040Y6QPU49"),
            "no-authors": dict(POLICY, pack_author_discord_user_ids=[]),
        }
        for name, policy in bad.items():
            with self.subTest(case=name):
                self.policy.write_text(json.dumps(policy), encoding="utf-8")
                self.calls.clear()
                self.assertVerdict(2, "REJECTED", "policy-invalid")
                self.assertEqual(self.calls, [])
        self.policy.write_text("not json", encoding="utf-8")
        self.assertVerdict(2, "REJECTED", "policy-invalid")
        self.assertVerdict(2, "REJECTED", "policy-invalid", policy=Path(self.tmp.name) / "missing.json")

    def test_repository_policy_is_the_six_key_discord_policy(self):
        policy = v.load_policy(str(ROOT / "deploy" / "approval-policy.json"))
        self.assertEqual(policy, POLICY)

    # --- AC-3: no credential ever surfaces --------------------------------
    def test_sentinel_never_appears_in_output(self):
        outputs = []
        for result in ((5, json.dumps({"ok": False, "contract": 1, "error": SENTINEL})),
                       (5, "Traceback " + SENTINEL), (0, SENTINEL), (None, "timeout")):
            self.result = result
            out = io.StringIO()
            err = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                v.main(["verify", "--policy", str(self.policy), "--channel-id", CHANNEL, "--message-id", MESSAGE,
                        "--issue", "WD-546", "--repo", REPO, "--pr", "75", "--sha", SHA, "--proof-pack", PACK])
            outputs.append(out.getvalue() + err.getvalue())
        self.assertNotIn(SENTINEL, "".join(outputs))


class ReaderProcessTests(unittest.TestCase):
    """The real subprocess wrapper, against a local Python stand-in for R1."""

    def test_child_environment_has_no_discord_names(self):
        script = ("import json, os; print(json.dumps(sorted(k for k in os.environ "
                  "if k.upper().startswith('DISCORD'))))")
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": SENTINEL, "discord_lower": SENTINEL,
                                     "NOT_DISCORD": "kept"}):
            env = v.reader_env()
            self.assertEqual(env.get("NOT_DISCORD"), "kept")
            code, text = v.run_reader([sys.executable, "-c", script], env)
        self.assertEqual((code, json.loads(text)), (0, []))

    def test_timeout_and_launch_failure(self):
        self.assertEqual(v.run_reader([sys.executable, "-c", "import time; time.sleep(30)"], {}, timeout=1),
                         (None, "timeout"))
        self.assertEqual(v.run_reader([str(Path(tempfile.gettempdir()) / "no-such-r1-binary")], {}),
                         (None, "launch-failed"))

    def test_reader_command_shape(self):
        argv = v.reader_argv(CHANNEL, MESSAGE, ROCKET)
        self.assertEqual(argv[-10:], ["-p", "integrator", "wolftac-approval-read", "--channel", CHANNEL,
                                      "--message", MESSAGE, "--emoji", ROCKET, "--json"])

    def test_cli_has_no_token_env_or_reader_override(self):
        source = (ROOT / "deploy" / "discord_approval.py").read_text(encoding="utf-8")
        for flag in ("--env-path", "--token", "--reader", "--exec-override", "--approval-reader"):
            self.assertNotIn(f'"{flag}"', source)
        self.assertNotIn("DISCORD_BOT_TOKEN", source)


if __name__ == "__main__":
    unittest.main()
