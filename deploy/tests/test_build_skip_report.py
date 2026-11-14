"""WD-621 offline contract: real reporting helper, mocked Linear snapshots only."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ports/hermes/wolftac-build/scripts/skip_report.py"
spec = importlib.util.spec_from_file_location("skip_report", SCRIPT)
assert spec is not None and spec.loader is not None
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def issue(ident="WD-615", skip=None):
    return {"id": ident, "team": "Wolftac Digital", "status": "Todo",
            "labels": ["Factory Queue"], "project": "Wolftac CRM v2",
            "assignee": "operator", "comments": [], "comments_complete": True,
            "relations": {"blockedBy": [{"id": "WD-489", "stateType": "completed"},
                                          {"id": "WD-560", "stateType": "completed"}]},
            "skip": skip or {"gate": "budget", "files": 29, "checks": 6}}


def payload(*issues):
    return {"lane": "Wolftac CRM v2", "taken": False, "race_lost": False,
            "now": "2026-11-04T20:00:00Z", "issues": list(issues)}


class SkipReportTests(unittest.TestCase):
    def test_budget_and_done_relations_with_assignee(self):
        item = issue()
        before = copy.deepcopy(item)
        output = report.prepare(payload(item))
        self.assertEqual(output, [{"issueId": "WD-615", "body":
            "BUILD SKIPPED: completion budget (files=29, checks=6, reserve=20%)"}])
        self.assertNotIn("blocked by", json.dumps(output))
        self.assertEqual(item, before)
        self.assertIsNone(report.reason({"gate": "dependency", "blockers": item["relations"]["blockedBy"]}))
        item["skip"] = {"gate": "dependency", "blockers": item["relations"]["blockedBy"]}
        with self.assertRaises(ValueError):
            report.prepare(payload(item))  # cannot manufacture a blocker or infer a budget skip

    def test_open_blockers_still_report_skips(self):
        for state in ["started", "unstarted", "backlog", "triage"]:
            with self.subTest(state=state):
                item = issue(skip={"gate": "dependency", "blockers": [
                    {"id": "WD-489", "stateType": "completed"},
                    {"id": "WD-560", "stateType": state}]})
                self.assertEqual(report.prepare(payload(item))[0]["body"],
                                 f"BUILD SKIPPED: blocked by WD-560 ({state})")

    def test_all_reason_formats(self):
        for skip, expected in [
            ({"gate": "park"}, "parked"),
            ({"gate": "fence", "run": "0123abcd"}, "fence held by 0123abcd"),
            ({"gate": "lane", "field": "project/labels"}, "lane mismatch: project/labels"),
            *[({"gate": "preclaim", "check": check}, f"preclaim check failed: {check}")
              for check in report.CHECKS],
        ]:
            self.assertEqual(report.reason(skip), expected)

    def test_one_per_issue_then_same_reason_dedupes_for_24_hours(self):
        first, second = issue(), issue("WD-616", {"gate": "park"})
        data = payload(first, second)
        emitted = report.prepare(data)
        self.assertEqual(len(emitted), 2)
        self.assertEqual({r["issueId"] for r in emitted}, {"WD-615", "WD-616"})
        # Fake connected-tool comment write/readback, no external transport.
        for item, output in zip(data["issues"], emitted):
            item["comments"].append({"body": output["body"], "createdAt": data["now"]})
        data["now"] = "2026-11-05T19:59:59Z"
        self.assertEqual(report.prepare(data), [])
        data["now"] = "2026-11-05T20:00:00Z"
        self.assertEqual(report.prepare(data), emitted)

    def test_changed_reason_and_issue_are_not_suppressed(self):
        a, b = issue(), issue("WD-616")
        old = report.prepare(payload(a))[0]["body"]
        a["comments"] = [{"body": old, "createdAt": "2026-11-04T19:00:00Z"}]
        a["skip"] = {"gate": "park"}
        output = report.prepare(payload(a, b))
        self.assertEqual(len(output), 2)
        self.assertEqual(output[0]["body"], "BUILD SKIPPED: parked")

    def test_budget_dedupes_by_gate_not_estimate_until_exactly_24_hours(self):
        item = issue()
        data = payload(item)
        first = report.prepare(data)[0]
        item["comments"] = [{"body": first["body"], "createdAt": data["now"]}]
        item["skip"].update(files=30, checks=7)
        for now in ("2026-11-04T22:00:00Z", "2026-11-05T19:59:59Z"):
            data["now"] = now
            self.assertEqual(report.prepare(data), [])
        data["now"] = "2026-11-05T20:00:00Z"
        self.assertEqual(report.prepare(data), [{"issueId": "WD-615", "body":
            "BUILD SKIPPED: completion budget (files=30, checks=7, reserve=20%)"}])

    def test_budget_gate_matching_preserves_other_reasons_and_issue_scope(self):
        item = issue()
        previous = {"body": report.prepare(payload(item))[0]["body"],
                    "createdAt": "2026-11-04T18:00:00Z"}
        item["comments"] = [previous]
        item["skip"]["files"] = 30
        other = issue("WD-616")
        self.assertEqual(report.prepare(payload(item, other)), report.prepare(payload(other)))
        item["skip"] = {"gate": "park"}
        self.assertEqual(report.prepare(payload(item))[0]["body"], "BUILD SKIPPED: parked")
        # Non-budget reasons retain exact-string dedupe, including check names.
        item["comments"] = [{"body": "BUILD SKIPPED: preclaim check failed: Repo",
                             "createdAt": previous["createdAt"]}]
        item["skip"] = {"gate": "preclaim", "check": "Repo"}
        self.assertEqual(report.prepare(payload(item)), [])
        item["skip"]["check"] = "repo-identity"
        self.assertEqual(report.prepare(payload(item))[0]["body"],
                         "BUILD SKIPPED: preclaim check failed: repo-identity")

    def test_budget_gate_requires_exact_diagnostic_format(self):
        item = issue()
        for body in ("BUILD SKIPPED: completion budget review",
                     "build skipped: completion budget (files=29, checks=6, reserve=20%)",
                     "Decision: BUILD SKIPPED: completion budget (files=29, checks=6, reserve=20%)"):
            with self.subTest(body=body):
                item["comments"] = [{"body": body, "createdAt": "2026-11-04T18:00:00Z"}]
                self.assertEqual(len(report.prepare(payload(item))), 1)
        item["comments"] = [{"body": "BUILD SKIPPED: completion budget (files=30, checks=7, reserve=20%)",
                             "createdAt": "2026-11-05T20:00:00Z"}]
        with self.assertRaises(ValueError):
            report.prepare(payload(item))

    def test_empty_lane_taken_and_race_have_no_comments(self):
        self.assertEqual(report.prepare(payload()), [])
        for field in ["taken", "race_lost"]:
            data = payload(issue())
            data[field] = True
            self.assertEqual(report.prepare(data), [])
        for key, value in [("team", "Other"), ("status", "In Progress"),
                           ("labels", []), ("project", "Other")]:
            item = issue()
            item[key] = value
            self.assertEqual(report.prepare(payload(item)), [])

    def test_agentic_labels_casefold_and_lane_mismatch(self):
        item = issue()
        item["project"] = None
        item["labels"].append("AGENTIC")
        data = payload(item)
        data["lane"] = "agentic"
        self.assertEqual(len(report.prepare(data)), 1)
        data["lane"] = "nonmatching"
        item["skip"] = {"gate": "lane", "field": "labels"}
        self.assertEqual(report.prepare(data), [])

    def test_diagnostic_does_not_clear_blocked_marker(self):
        blocked = {"body": "BLOCKED: operator decision required", "createdAt": "2026-11-03T20:00:00Z"}
        comments = [blocked, {"body": "BUILD SKIPPED: preclaim check failed: BLOCKED",
                              "createdAt": "2026-11-04T19:00:00Z"}]
        self.assertEqual(report.latest_work_comment(comments), blocked)
        ready = {"body": "Ready", "createdAt": "2026-11-04T19:30:00Z"}
        self.assertEqual(report.latest_work_comment(comments + [ready]), ready)

    def test_incomplete_unknown_duplicate_and_missing_observations_fail_closed(self):
        for changes in [{"comments_complete": False}, {"id": "WD-615\nsecret"},
                        {"skip": {"gate": "preclaim", "check": "C:/private/.env"}},
                        {"skip": {"gate": "budget", "files": "private text", "checks": 3}},
                        {"skip": {"gate": "fence", "run": "prompt text"}},
                        {"skip": {"gate": "dependency", "blockers": [{"id": "WD-1", "stateType": "unknown"}]}},
                        {"skip": {"gate": "lane", "field": "project"}}]:
            item = issue()
            item.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                report.prepare(payload(item))
        with self.assertRaises(ValueError):
            report.prepare(payload(issue(), issue()))
        for changes in [{"lane": None}, {"issues": {}}, {"taken": "false"}]:
            data = payload(issue())
            data.update(changes)
            with self.assertRaises(ValueError):
                report.prepare(data)
        for changes in [{"labels": "Factory Queue"}, {"project": {"name": "CRM"}}]:
            item = issue()
            item.update(changes)
            with self.assertRaises(ValueError):
                report.prepare(payload(item))
        data = payload(issue())
        del data["issues"][0]["skip"]
        with self.assertRaises(KeyError):
            report.prepare(data)

    def test_full_history_timezone_and_invalid_time(self):
        item = issue()
        body = report.prepare(payload(item))[0]["body"]
        item["comments"] = [{"body": "Other", "createdAt": "2026-11-04T19:30:00Z"}] * 60
        item["comments"].append({"body": body, "createdAt": "2026-11-04T13:00:00-06:00"})
        self.assertEqual(report.prepare(payload(item)), [])
        for timestamp in ["bad", "2026-11-04T19:00:00", "2026-11-05T20:00:00Z"]:
            item["comments"][-1]["createdAt"] = timestamp
            with self.assertRaises(ValueError):
                report.prepare(payload(item))

    def test_real_cli_prepares_json_and_does_not_echo_bad_input(self):
        data = payload(issue())
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], input=json.dumps(data),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), report.prepare(data))
        data["issues"][0]["skip"] = {"gate": "preclaim", "check": "SECRET-fixture-prompt"}
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], input=json.dumps(data),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("SECRET-fixture-prompt", result.stderr)

    def test_all_skill_surfaces_carry_identical_reporting_contract(self):
        paths = ["canonical/wolftac-build/SKILL.md", "ports/codex/wolftac-build.md",
                 "ports/claude-desktop/wolftac-build/SKILL.md", "ports/hermes/wolftac-build/SKILL.md",
                 "plugins/wolftac-factory/skills/wolftac-build/SKILL.md"]
        sections = []
        for path in paths:
            text = (ROOT / path).read_text(encoding="utf-8")
            sections.append(text.split("<!-- NO-SILENT-SKIP-START -->", 1)[1].split("<!-- NO-SILENT-SKIP-END -->", 1)[0])
            self.assertNotIn("This sweep is the ONLY time", text)
        self.assertEqual(len(set(sections)), 1)
        for required in ["skip_report.py", "comments_complete", "24 hours", "20%",
                         "blocked by", "parked", "fence held by", "lane mismatch:",
                         "completion budget", "preclaim check failed:", "read back", "race_lost"]:
            self.assertIn(required, sections[0])


if __name__ == "__main__":
    unittest.main()
