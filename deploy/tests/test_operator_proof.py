"""WD-629: execute parity/semantic gates against isolated policy mutations."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
SPEC = [
    "canonical/wolftac-spec/SKILL.md",
    "canonical/wolftac-spec/spec-template.md",
    "plugins/wolftac-factory/skills/wolftac-spec/SKILL.md",
    "plugins/wolftac-factory/skills/wolftac-spec/references/spec-template.md",
    "ports/codex/wolftac-spec.md",
    "ports/claude-desktop/wolftac-spec/SKILL.md",
    "ports/hermes/wolftac-spec/SKILL.md",
]
REVIEW = [
    "canonical/wolftac-review/SKILL.md",
    "plugins/wolftac-factory/skills/wolftac-review/SKILL.md",
    "ports/codex/wolftac-review.md",
    "ports/claude-desktop/wolftac-review/SKILL.md",
    "ports/hermes/wolftac-review/SKILL.md",
]


def paragraph(text, marker):
    match = re.search(r"(?m)^ *\*\*" + re.escape(marker) + r":\*\*[^\n]*(?:\n(?! *\n)[^\n]*)*", text)
    assert match is not None, f"Missing rule: {marker}"
    return match.group()


@unittest.skipUnless(PWSH, "PowerShell 7 required for actual policy gates")
class OperatorProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="operator-proof-", dir=os.getenv("TMPDIR"))
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.repo = Path(cls.tmp.name) / "repo"
        # Only repository-controlled files, never a live skill/profile or env file.
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        for relative in filter(None, tracked):
            if any(part.startswith(".env") for part in Path(relative).parts):
                continue
            target = cls.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        cls.env = {k: v for k, v in os.environ.items() if k.upper() in {
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES",
            "PROGRAMFILES(X86)", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR"}}
        cls.env.update(HOME=cls.tmp.name, USERPROFILE=cls.tmp.name, LOCALAPPDATA=cls.tmp.name)

    def gate(self, name, expected=0, file=None):
        assert PWSH is not None
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File",
                                 str(self.repo / "deploy" / f"{name}-check.ps1"),
                                 "-RepoRoot", str(self.repo)],
                                env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=45)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        self.assertIn(name.upper() + (" OK" if expected == 0 else " FAILED"), output)
        if file:
            self.assertIn(file, output)
        return output

    def mutate(self, file, before, after, gate):
        path = self.repo / file
        original = path.read_bytes()
        text = original.decode("utf-8")
        self.assertEqual(text.count(before), 1, (file, before))
        try:
            path.write_bytes(text.replace(before, after, 1).encode("utf-8"))
            self.gate(gate, expected=1, file=file)
        finally:
            path.write_bytes(original)

    def test_clean_gates_and_identical_rules(self):
        self.gate("parity")
        self.gate("semantic")
        for files, marker in [(SPEC, "UNATTENDED-PROOF-SELF-CHECK"), (REVIEW, "OPERATOR-PASS-SCOPE")]:
            rules = {re.sub(r"\s+", " ", paragraph((self.repo / p).read_text(encoding="utf-8"), marker)).strip()
                     for p in files}
            self.assertEqual(len(rules), 1)
        invariants = json.loads((self.repo / "deploy/invariants.json").read_text(encoding="utf-8"))
        checks = next(item["checks"] for item in invariants if item["id"] == "spec-unattended-proof")
        self.assertEqual(sorted(c["file"] for c in checks), sorted(SPEC))
        for p in SPEC:
            text = (self.repo / p).read_text(encoding="utf-8")
            rule = paragraph(text, "UNATTENDED-PROOF-SELF-CHECK")
            normalized = re.sub(r"\s+", " ", rule)
            self.assertIn("stop and ask the user", normalized)
            self.assertIn("do not file until resolved", normalized)
            self.assertIn("Do not rewrite existing specs", normalized)
            if p.endswith("SKILL.md") or p.startswith("ports/codex"):
                approval = re.search(r"(?im)^ *(?:### 3\.|[35]\.) .*approval", text)
                self.assertIsNotNone(approval, p)
                assert approval is not None
                self.assertLess(text.index(rule), approval.start(), p)
            rows = [line.strip() for line in text.splitlines() if line.strip().startswith("| Criterion |")]
            self.assertEqual(len(rows), 1, p)
            self.assertEqual(len(rows[0].split("|")), 6, p)
            self.assertIn("Proven by (worker command or [operator])", rows[0])

    def test_every_spec_rule_removal_fails_parity(self):
        for p in SPEC:
            with self.subTest(file=p):
                text = (self.repo / p).read_text(encoding="utf-8")
                self.mutate(p, paragraph(text, "UNATTENDED-PROOF-SELF-CHECK"), "", "parity")
        self.gate("parity")

    def test_every_spec_column_removal_fails_parity(self):
        for p in SPEC:
            with self.subTest(file=p):
                text = (self.repo / p).read_text(encoding="utf-8")
                header = next(line for line in text.splitlines() if line.strip().startswith("| Criterion |"))
                self.mutate(p, header, header.replace("Proven by (worker command or [operator])", "Proof"), "parity")
        self.gate("parity")

    def test_marker_alone_cannot_hide_weakened_spec_rule(self):
        for p in SPEC:
            with self.subTest(file=p):
                self.mutate(p, "do not file until resolved", "file before resolving", "parity")
        self.gate("parity")

    def test_every_review_rule_removal_fails_semantic(self):
        for p in REVIEW:
            with self.subTest(file=p):
                text = (self.repo / p).read_text(encoding="utf-8")
                self.mutate(p, paragraph(text, "OPERATOR-PASS-SCOPE"), "", "semantic")
        self.gate("semantic")

    def test_review_cannot_invert_scope_or_claim_operator_proof(self):
        changes = [("do not block PASS", "block PASS"),
                   ("operator step, not verified", "operator step, verified"),
                   ("leave its checkbox unchecked", "tick its checkbox"),
                   ("All non-operator criteria, baseline checks, CI gates and merge approval rules remain",
                    "Only selected non-operator criteria remain")]
        # Mutate each copy; normalization tolerates wrapping but never changed meaning.
        for p in REVIEW:
            text = (self.repo / p).read_text(encoding="utf-8")
            rule = paragraph(text, "OPERATOR-PASS-SCOPE")
            normalized = re.sub(r"\s+", " ", rule).strip()
            for before, after in changes:
                with self.subTest(file=p, clause=before):
                    self.assertIn(before, normalized)
                    self.mutate(p, rule, normalized.replace(before, after), "semantic")
        self.gate("semantic")


if __name__ == "__main__":
    unittest.main()
