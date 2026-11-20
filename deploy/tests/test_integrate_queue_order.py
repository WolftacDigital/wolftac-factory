"""WD-657: execute queue ordering offline; never load credentials or call Linear."""
import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SKILLS = [
    "canonical/wolftac-integrate/SKILL.md",
    "plugins/wolftac-factory/skills/wolftac-integrate/SKILL.md",
    "ports/claude-desktop/wolftac-integrate/SKILL.md",
    "ports/codex/wolftac-integrate.md",
    "ports/hermes/wolftac-integrate/SKILL.md",
    "ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL.md",
]
BOTS = [str(Path(f).parent / "scripts/linear_bot.py") for f in SKILLS]
BOTS[3] = "ports/codex/wolftac-integrate-scripts/linear_bot.py"
spec = importlib.util.spec_from_file_location("queue_order_bot", ROOT / BOTS[0])
assert spec is not None and spec.loader is not None
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)  # main/load_token are not called on import


def timestamp(hour):
    return f"2026-01-01T{hour:02d}:00:00.000Z"


def history(hour, state="Approved"):
    return {"createdAt": timestamp(hour), "toState": {"name": state}}


def issue(number, entered, updated):
    return {
        "id": f"synthetic-{number}", "identifier": f"WD-{number}",
        "createdAt": timestamp(0), "updatedAt": timestamp(updated),
        "history": {"nodes": [history(entered)]},
        "project": None, "labels": {"nodes": [{"name": "Factory Queue"}]},
        "comments": {"nodes": []}, "delegate": None, "assignee": None,
    }


class QueueOrderTests(unittest.TestCase):
    def queue(self, nodes, lane=None, state="Approved"):
        calls = []

        def fake_gq(query, variables=None):
            self.assertNotIn("mutation", query)
            calls.append((query, variables))
            if "issues(" in query:
                self.assertIn("createdAt history(last: 50)", query)
                self.assertIn("toState { name }", query)
                self.assertEqual(variables, {"team": "WD", "state": state,
                                             "label": "Factory Queue"})
                return {"issues": {"nodes": copy.deepcopy(nodes)}}
            self.assertIn("viewer", query)
            return {"viewer": {"id": "synthetic-viewer"}}

        output = io.StringIO()
        with patch.object(bot, "gq", side_effect=fake_gq), \
                patch.object(bot, "load_token", side_effect=AssertionError("no credentials")), \
                patch.object(bot.urllib.request, "urlopen", side_effect=AssertionError("no network")), \
                contextlib.redirect_stdout(output):
            bot.cmd_queue(argparse.Namespace(state=state, lane=lane))
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], len(result["issues"]))
        self.assertEqual(len(calls), 2)
        return result["issues"]

    def test_state_entry_beats_comment_update_ac1(self):
        a, b = issue(99999, 1, 9), issue(100000, 2, 3)
        a["comments"]["nodes"] = [{"createdAt": timestamp(9), "body": "synthetic"}]
        self.assertEqual([x["identifier"] for x in sorted([a, b], key=lambda x: x["updatedAt"])],
                         ["WD-100000", "WD-99999"])
        result = self.queue([b, a])
        self.assertEqual([x["identifier"] for x in result], ["WD-99999", "WD-100000"])
        self.assertEqual([x["enteredStateAt"] for x in result], [timestamp(1), timestamp(2)])
        self.assertEqual(result[0]["updatedAt"], timestamp(9))

    def test_unordered_history_uses_latest_matching_entry_ac2(self):
        a = issue(99999, 1, 9)
        a["history"]["nodes"] = [history(4), history(9, "Todo"), history(1),
                                   {"createdAt": timestamp(8), "toState": None}, history(3)]
        self.assertEqual(self.queue([a])[0]["enteredStateAt"], timestamp(4))
        self.assertEqual(self.queue([a], state="Todo")[0]["enteredStateAt"], timestamp(9))

    def test_missing_matching_history_falls_back_ac2(self):
        for entries in ([], [history(7, "Todo")], [{"createdAt": timestamp(8), "toState": None}]):
            with self.subTest(entries=entries):
                a = issue(99999, 1, 9)
                a["history"]["nodes"] = entries
                self.assertEqual(self.queue([a])[0]["enteredStateAt"], a["createdAt"])
        for value in (None, {}):
            a["history"] = value
            self.assertEqual(self.queue([a])[0]["enteredStateAt"], a["createdAt"])
        del a["history"]
        self.assertEqual(self.queue([a])[0]["enteredStateAt"], a["createdAt"])

    def test_ties_are_numeric_ac2(self):
        result = self.queue([issue(100000, 1, 3), issue(99999, 1, 9)])
        self.assertEqual([x["identifier"] for x in result], ["WD-99999", "WD-100000"])

    def test_timestamp_precision_and_offsets_use_instants(self):
        a, b = issue(99999, 1, 9), issue(100000, 2, 3)
        a["history"]["nodes"] = [
            {"createdAt": "2026-01-01T01:00:00Z", "toState": {"name": "Approved"}},
            {"createdAt": "2026-01-01T01:00:00.500Z", "toState": {"name": "Approved"}},
        ]
        b["history"]["nodes"][0]["createdAt"] = "2025-12-31T19:00:00-06:00"
        result = self.queue([a, b])
        self.assertEqual([x["identifier"] for x in result], ["WD-100000", "WD-99999"])
        self.assertEqual(result[1]["enteredStateAt"], "2026-01-01T01:00:00.500Z")

    def test_lane_matching_unchanged_ac2(self):
        a, b, c = issue(99999, 1, 9), issue(100000, 2, 3), issue(100001, 3, 4)
        a["project"] = {"name": "Synthetic Agentic Project"}
        b["labels"]["nodes"].append({"name": "AGENTIC-test"})
        self.assertEqual([x["identifier"] for x in self.queue([c, b, a], lane="AgEnTiC")],
                         ["WD-99999", "WD-100000"])
        self.assertEqual(len(self.queue([a, b, c])), 3)
        self.assertEqual(self.queue([a, b, c], lane="unmatched"), [])

    def test_non_state_writes_cannot_resort_ac10(self):
        a, b = issue(99999, 1, 1), issue(100000, 2, 3)
        writes = [(5, "delegate", {"id": "synthetic-bot"}),
                  (6, "comments", {"nodes": [{"body": "synthetic comment"}]}),
                  (7, "assignee", {"id": "synthetic-operator"}),
                  (8, "relations", {"nodes": [{"id": "synthetic-link"}]})]
        for hour, field, value in writes:
            a[field], a["updatedAt"] = value, timestamp(hour)
            with self.subTest(write=field):
                result = self.queue([b, a])
                self.assertEqual([x["identifier"] for x in result], ["WD-99999", "WD-100000"])
                self.assertEqual(result[0]["enteredStateAt"], timestamp(1))
                self.assertEqual(result[0][field], value)
        self.assertEqual(a["updatedAt"], timestamp(8))

    def test_empty_queue(self):
        self.assertEqual(self.queue([]), [])


class SurfaceTests(unittest.TestCase):
    def test_bot_bytes_and_approval_blocks_match_ac3(self):
        self.assertEqual(len({hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in BOTS}), 1)
        blocks = []
        for f in SKILLS:
            text = (ROOT / f).read_text(encoding="utf-8")
            blocks.append(text.split("**APPROVAL-FIRST:**", 1)[1].split("**Own claims and waiting issues", 1)[0].rstrip())
        self.assertEqual(len(set(blocks)), 1)

    def test_approval_and_delivery_contract_ac5_ac10(self):
        for f in SKILLS:
            with self.subTest(file=f):
                text = (ROOT / f).read_text(encoding="utf-8")
                normalized = " ".join(text.split())
                self.assertNotIn("oldest updated first", text)
                self.assertLess(text.index("**APPROVAL-FIRST:**"), text.index("**Own claims and waiting issues"))
                for phrase in ("read-only pass", "posts nothing and changes no state",
                               "first candidate returning `APPROVED` (exit 0)",
                               "Step 6 re-runs the verifier before acting",
                               "An `api-error` skips that candidate for this pass only",
                               "never the approval decision for that request, on this tick or any later one",
                               "Only a new verifier run on a later tick",
                               "labelled `delivery check` in the existing delivery reply, with no extra comment"):
                    self.assertIn(phrase, normalized)

    def test_invariant_order_coverage_and_missing_block_ac4(self):
        invariants = json.loads((ROOT / "deploy/invariants.json").read_text(encoding="utf-8"))
        ids = [x["id"] for x in invariants]
        self.assertEqual(ids.index("integrate-approval-first"), ids.index("integrate-no-self-sweep") + 1)
        invariant = invariants[ids.index("integrate-approval-first")]
        self.assertEqual({x["file"] for x in invariant["checks"]}, set(SKILLS))
        for check in invariant["checks"]:
            text = (ROOT / check["file"]).read_text(encoding="utf-8")
            self.assertIn("delivery check", check["pattern"])
            self.assertRegex(text, check["pattern"])
            stripped = re.sub(r"\*\*APPROVAL-FIRST:\*\*.*?(?=\*\*Own claims and waiting issues)",
                              "", text, flags=re.S)
            self.assertNotRegex(stripped, check["pattern"])


if __name__ == "__main__":
    unittest.main()
