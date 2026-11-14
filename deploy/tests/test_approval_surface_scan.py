"""WD-546 AC-6: the enumerated approval-surface scan.

The scope below is the exact AC-6 list. A missing path fails, so the scope can
never silently shrink. Deliberately excluded, and never edited to force a pass:
the watchdog script, docs and tests (WD-551); other non-approval notifications;
deploy/tests fixtures; labelled history; the forbidden-pattern definitions in
deploy/invariants.json and deploy/semantic-check.ps1; and the dormant legacy
*/wolftac-integrate*/scripts/slack_bot.py copies and their deploy/deploy.ps1
entries, which stay until the Slack cleanup issue.
"""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]

APPROVAL_SURFACE_SCOPE = (
    "deploy/approval-policy.json",
    "deploy/factory-gatekeeper.py",
    "deploy/discord_approval.py",
    "ports/hermes-plugins/wolftac-approval-read/",
    "AGENTS.md",
    "README.md",
    "canonical/wolftac-integrate/SKILL.md",
    "ports/hermes/wolftac-integrate/SKILL.md",
    "ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL.md",
    "plugins/wolftac-factory/skills/wolftac-integrate/SKILL.md",
    "ports/codex/wolftac-integrate.md",
    "ports/claude-desktop/wolftac-integrate/SKILL.md",
    "ports/hermes-factory-integrate/deploy/bot-runtime.md",
    "docs/factory-gatekeeper.md",  # scanned with its labelled History section removed
)
HISTORY_EXEMPT = {"docs/factory-gatekeeper.md"}
FORBIDDEN = ("SLACK_FACTORY_BOT_TOKEN", "reactions.get", "chat.postMessage", "approver_slack_user_id",
             "slack.com", "slack_bot")
TYPED = re.compile(r"APPROVE(D)? MERGE", re.IGNORECASE)
NEVER = re.compile(r"\bnever\b", re.IGNORECASE)
HEADING = re.compile(r"^(#{1,6})\s")


def without_history(text: str) -> str:
    """Drop each heading labelled History, up to the next heading of the same or higher level."""
    kept, skip_level = [], None
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            level = len(match.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and re.match(r"#{1,6}\s+History\b", line):
                skip_level = level
                continue
        if skip_level is None:
            kept.append(line)
    return "\n".join(kept)


def scoped_files():
    files = []
    for entry in APPROVAL_SURFACE_SCOPE:
        path = ROOT / entry
        if entry.endswith("/"):
            if not path.is_dir():
                raise AssertionError(f"scoped directory missing: {entry}")
            files.extend(sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts))
        else:
            if not path.is_file():
                raise AssertionError(f"scoped file missing: {entry}")
            files.append(path)
    return files


def scan_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT).as_posix()
    return without_history(text) if rel in HISTORY_EXEMPT else text


class ApprovalSurfaceScanTests(unittest.TestCase):
    def test_scope_constant_is_exactly_the_ac6_list(self):
        self.assertEqual(len(APPROVAL_SURFACE_SCOPE), 14)
        self.assertEqual(len(set(APPROVAL_SURFACE_SCOPE)), 14)
        integrate_copies = [e for e in APPROVAL_SURFACE_SCOPE if "wolftac-integrate" in e and "deploy" not in e]
        self.assertEqual(len(integrate_copies), 6)
        self.assertGreaterEqual(len(scoped_files()), 16)

    def test_a_no_slack_route_in_scope(self):
        for path in scoped_files():
            text = scan_text(path)
            for needle in FORBIDDEN:
                self.assertNotIn(needle, text, f"{path.relative_to(ROOT)} contains {needle}")

    def test_b_no_active_consumer_uses_legacy_slack_bot(self):
        for path in scoped_files():
            if path.suffix != ".py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse(any("slack" in a.name.lower() for a in node.names), path)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotIn("slack", (node.module or "").lower(), path)
                    self.assertFalse(any("slack" in a.name.lower() for a in node.names), path)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertNotIn("slack_bot", node.value, path)

    def test_c_every_typed_approval_line_says_never(self):
        for path in scoped_files():
            for number, line in enumerate(scan_text(path).splitlines(), 1):
                if TYPED.search(line):
                    self.assertRegex(line, NEVER, f"{path.relative_to(ROOT)}:{number}: {line.strip()}")

    def test_history_is_removed_only_up_to_the_next_peer_heading(self):
        text = "# A\nkeep 1\n### History: old\nslack.com\n#### detail\nslack_bot\n### Next\nkeep 2\n## Up\nkeep 3"
        self.assertEqual(without_history(text), "# A\nkeep 1\n### Next\nkeep 2\n## Up\nkeep 3")
        gatekeeper_doc = (ROOT / "docs/factory-gatekeeper.md").read_text(encoding="utf-8")
        self.assertIn("### History: the Slack approval check", gatekeeper_doc)
        self.assertIn("reactions.get", gatekeeper_doc)  # history is kept, only exempted
        self.assertNotIn("reactions.get", without_history(gatekeeper_doc))

    def test_missing_scoped_path_fails(self):
        global APPROVAL_SURFACE_SCOPE
        saved = APPROVAL_SURFACE_SCOPE
        try:
            APPROVAL_SURFACE_SCOPE = saved + ("deploy/no-such-approval-file.json",)
            with self.assertRaises(AssertionError):
                scoped_files()
        finally:
            APPROVAL_SURFACE_SCOPE = saved

    def test_excluded_legacy_slack_helpers_are_still_dormant_not_deleted(self):
        legacy = sorted(ROOT.glob("**/wolftac-integrate*/scripts/slack_bot.py"))
        self.assertTrue(legacy, "legacy slack_bot.py copies are excluded from scope but must not be deleted here")


if __name__ == "__main__":
    unittest.main()
