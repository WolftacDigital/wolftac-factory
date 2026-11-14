"""WD-585: the five residual rules retain identical wording and checks."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    "canonical/wolftac-build/SKILL.md",
    "plugins/wolftac-factory/skills/wolftac-build/SKILL.md",
    "ports/codex/wolftac-build.md",
    "ports/hermes/wolftac-build/SKILL.md",
    "ports/claude-desktop/wolftac-build/SKILL.md",
)


class ResidualRuleTests(unittest.TestCase):
    def test_equal_rule_slices(self):
        expected = None
        for file in FILES:
            with self.subTest(file=file):
                text = (ROOT / file).read_text(encoding="utf-8")
                match = re.search(r"\*\*RESIDUAL-LANE:\*\*.*?the text inside the backticks\.", text, re.S)
                if match is None:
                    self.fail(file + ": residual rule missing")
                actual = " ".join(match.group().split())
                if expected is None:
                    expected = actual
                self.assertEqual(expected, actual, file)

    def test_invariant_covers_exact_five_surfaces(self):
        invariants = json.loads((ROOT / "deploy/invariants.json").read_text(encoding="utf-8"))
        rules = [rule for rule in invariants if rule["id"] == "residual-lane-rule"]
        self.assertEqual(len(rules), 1)
        checks = rules[0]["checks"]
        self.assertEqual(len(checks), 5)
        self.assertEqual({c["file"] for c in checks}, set(FILES))
        for check in checks:
            text = (ROOT / check["file"]).read_text(encoding="utf-8")
            self.assertRegex(text, check["pattern"])


if __name__ == "__main__":
    unittest.main()
