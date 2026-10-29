"""WD-579: execute junction/deploy contracts against disposable Windows fixtures.

No live profile, credential file, scheduler, deploy switch or network is used.
The manifest-only case runs everywhere; junction cases require Windows + pwsh.
"""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
SKILLS = ["wolftac-" + stage for stage in ("spec", "build", "review", "integrate")]


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *map(str, args)],
                                   text=True, encoding="utf-8", stderr=subprocess.STDOUT,
                                   timeout=60)


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def is_junction(path):
    """Path.is_junction() needs Python 3.12; the Hermes venv runs 3.11."""
    try:
        return os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, AttributeError):
        return False


def tree_state(root):
    """Bytes and mtimes, including directory identities, for no-write assertions."""
    return {str(p.relative_to(root)): (p.is_dir(), p.stat().st_mtime_ns,
                                      None if p.is_dir() else p.read_bytes())
            for p in [root, *root.rglob("*")]}


def lf_tree(root):
    return {str(p.relative_to(root)): p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            for p in root.rglob("*") if p.is_file()}


class ManifestTests(unittest.TestCase):
    def test_exact_four_roots_and_unchanged_pairs(self):
        manifest = json.loads((ROOT / "deploy/junctions.json").read_text())
        self.assertEqual(manifest["skills_roots"], ["%LOCALAPPDATA%/hermes/skills"] + [
            f"%LOCALAPPDATA%/hermes/profiles/{p}/skills"
            for p in ("builder", "reviewer", "integrator")])
        self.assertNotIn("skills_root", manifest)
        base = json.loads(git("show", "origin/main:deploy/junctions.json"))
        self.assertEqual(manifest["pairs"], base["pairs"])
        self.assertEqual(len(manifest["pairs"]), 4)

    def test_docs_and_out_of_scope_files(self):
        watchdog = (ROOT / "docs/watchdog.md").read_text(encoding="utf-8")
        install = watchdog.split("## Install", 1)[1].split("## Verify", 1)[0]
        for profile in ("builder", "reviewer", "integrator"):
            self.assertIn(f"profiles\\{profile}\\cron", install)
        self.assertIn("hermes\\cron", install)
        self.assertIn("-EnvFile", install)
        self.assertNotIn("profiles\\factory-integrate", install)
        docs = (ROOT / "docs/hermes-skill-junctions.md").read_text(encoding="utf-8")
        for text in ("skills_roots", "D:\\Wolftac\\.factory-live", "builder", "reviewer", "integrator"):
            self.assertIn(text, docs)
        self.assertNotIn("factory-skills-live", docs)
        self.assertIn("test_junctions.py", (ROOT / "deploy/tests/README.md").read_text())
        self.assertEqual(git("diff", "origin/main", "--stat", "--",
                             "ports/hermes-factory-integrate", "deploy/invariants.json",
                             "deploy/factory-watchdog.ps1"), "")


@unittest.skipUnless(os.name == "nt" and PWSH, "Windows directory junctions and PowerShell 7 required")
class JunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="wd579-", dir=os.getenv("TMPDIR"))
        cls.scratch = Path(cls.tmp.name)
        cls.live = cls.scratch / "live"
        git("worktree", "add", "--detach", cls.live, "HEAD")

    @classmethod
    def tearDownClass(cls):
        git("worktree", "remove", "--force", cls.live)
        cls.tmp.cleanup()

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(prefix="case-", dir=self.scratch))
        self.roots = [self.case / "root one", self.case / "root two"]
        self.manifest = self.case / "manifest.json"
        self.pairs = json.loads((ROOT / "deploy/junctions.json").read_text())["pairs"]
        self.write_manifest()
        # Permit only OS/tool lookup inputs; all profile destinations are scratch.
        self.env = {k: v for k, v in os.environ.items()
                    if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
                                     "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA"}}
        self.env.update(USERPROFILE=str(self.case / "user"), HOME=str(self.case / "user"),
                        LOCALAPPDATA=str(self.case / "AppData/Local"),
                        TEMP=str(self.case), TMP=str(self.case), TMPDIR=str(self.case))
        self.links = []
        self.addCleanup(self.unlink_fixture_links)

    def unlink_fixture_links(self):
        for link in reversed(self.links):
            if is_junction(link):
                link.rmdir()  # Remove only the link, never recurse into a target.

    def write_manifest(self, legacy=False):
        data = {"pairs": self.pairs}
        data["skills_root" if legacy else "skills_roots"] = (
            str(self.roots[0]) if legacy else [str(p) for p in self.roots])
        self.manifest.write_text(json.dumps(data), encoding="utf-8", newline="\n")

    def copies(self):
        for root in self.roots:
            for pair in self.pairs:
                target = root / pair["name"]
                shutil.copytree(self.live / pair["repo"], target)
                self.links.append(target)

    def ps(self, text, code=0):
        assert PWSH is not None
        script = self.case / "probe.ps1"
        script.write_text("$ErrorActionPreference = 'Stop'\n" + text,
                          encoding="utf-8", newline="\n")
        result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)],
                                env=self.env, cwd=ROOT, capture_output=True,
                                encoding="utf-8", timeout=60)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, code, output)
        return output

    def run_script(self, name="setup", extra="", code=0):
        return self.ps(f"& {quote(ROOT / ('deploy/junction-' + name + '.ps1'))} "
                       f"-RepoRoot {quote(self.live)} -ManifestPath {quote(self.manifest)} {extra}\n"
                       "exit $LASTEXITCODE\n", code)

    def assert_rows(self, output, marker, count):
        rows = [line for line in output.splitlines() if line.startswith(marker + " ")]
        self.assertEqual(len(rows), count, output)
        return rows

    def test_legacy_manifest_and_skillsroot_override(self):
        self.copies()
        self.write_manifest(legacy=True)
        output = self.run_script(extra="-DryRun")
        self.assert_rows(output, "REPLACE", 4)
        self.assertEqual(output.count("skills root:"), 1)
        self.write_manifest()
        output = self.run_script(extra=f"-DryRun -SkillsRoot {quote(self.roots[1])}")
        rows = self.assert_rows(output, "REPLACE", 4)
        self.assertTrue(all(str(self.roots[1]) in row for row in rows))
        self.assertEqual(output.count("skills root:"), 1)

    def test_two_root_dry_run_setup_repeat_teardown_repeat(self):
        self.copies()
        before = [tree_state(p) for p in self.roots]
        output = self.run_script(extra="-DryRun")
        rows = self.assert_rows(output, "REPLACE", 8)
        self.assertEqual(output.count("skills root:"), 2)
        self.assertIn("DRY RUN: validation passed for 8 pair(s)", output)
        for link in self.links:
            self.assertTrue(any(str(link) in row for row in rows))
        self.assertEqual(before, [tree_state(p) for p in self.roots])
        self.assert_rows(self.run_script(), "LINKED", 8)
        for root in self.roots:
            for pair in self.pairs:
                link = root / pair["name"]
                self.assertTrue(is_junction(link))
                self.assertEqual(link.resolve(), (self.live / pair["repo"]).resolve())
        before = [tree_state(p) for p in self.roots]
        self.assert_rows(self.run_script(), "NOOP", 8)
        self.assertEqual(before, [tree_state(p) for p in self.roots])
        self.assert_rows(self.run_script("teardown"), "RESTORED", 8)
        for root in self.roots:
            for pair in self.pairs:
                link = root / pair["name"]
                self.assertFalse(is_junction(link))
                self.assertEqual(lf_tree(link), lf_tree(self.live / pair["repo"]))
        before = [tree_state(p) for p in self.roots]
        self.assert_rows(self.run_script("teardown"), "NOOP", 8)
        self.assertEqual(before, [tree_state(p) for p in self.roots])

    def test_second_root_drift_refuses_before_any_change(self):
        self.copies()
        bad = self.roots[1] / SKILLS[-1]
        (bad / "fixture-drift.txt").write_text("synthetic divergent content\n")
        before = [tree_state(p) for p in self.roots]
        output = self.run_script(code=1)
        self.assertIn(f"REFUSE {bad}:", output)
        self.assertEqual(before, [tree_state(p) for p in self.roots])
        self.assertTrue(all(not is_junction(p) for p in self.links))

    def test_missing_roots_create_all_eight(self):
        self.links = [root / skill for root in self.roots for skill in SKILLS]
        self.assert_rows(self.run_script(extra="-DryRun"), "CREATE", 8)
        self.assertTrue(all(not p.exists() for p in self.roots))
        self.assert_rows(self.run_script(), "LINKED", 8)
        self.assertTrue(all(is_junction(p) for p in self.links))

    def make_link(self, link, target):
        link.parent.mkdir(parents=True, exist_ok=True)
        self.ps(f"New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} | Out-Null")
        self.links.append(link)

    def deploy_probe(self, link=None):
        output = self.ps(f". {quote(ROOT / 'deploy/deploy.ps1')}\n"
                         "$data = @{ Dsts = @($Pairs.Dst); Roots = @($JunctionedRoots); "
                         f"Under = (Test-UnderJunction {quote((link or self.case) / 'SKILL.md')}) }}\n"
                         "Write-Output ('FIXTURE_JSON:' + ($data | ConvertTo-Json -Compress))\n")
        data = json.loads(next(line.removeprefix("FIXTURE_JSON:") for line in output.splitlines()
                               if line.startswith("FIXTURE_JSON:")))
        return output, data

    def test_deploy_covers_sixteen_destinations_and_trusts_worker_worktree(self):
        home = Path(self.env["LOCALAPPDATA"]) / "hermes"
        link = home / "profiles/builder/skills/wolftac-build"
        self.make_link(link, self.live / "ports/hermes/wolftac-build")
        _, data = self.deploy_probe(link)
        self.assertIn(str(link), data["Roots"])
        self.assertTrue(data["Under"])
        self.assertTrue(all("\\hermes\\profiles\\factory-integrate\\" not in d.lower()
                            for d in data["Dsts"]))
        roots = [home / "skills"] + [home / f"profiles/{p}/skills"
                                     for p in ("builder", "reviewer", "integrator")]
        for root in roots:
            for skill in SKILLS:
                self.assertEqual(data["Dsts"].count(str(root / skill / "SKILL.md")), 1)
        self.assertEqual(len(data["Dsts"]), len(set(d.lower() for d in data["Dsts"])))
        for profile in ("builder", "reviewer", "integrator"):
            self.assertFalse(any(str(home / f"profiles/{profile}/skills/wolftac-agent-command-patterns")
                                 in d for d in data["Dsts"]))

    def test_foreign_repository_tail_is_not_trusted(self):
        foreign = self.case / "foreign"
        subprocess.run(["git", "init", "-q", str(foreign)], check=True,
                       capture_output=True, timeout=15)
        target = foreign / "ports/hermes/wolftac-review"
        target.mkdir(parents=True)
        link = Path(self.env["LOCALAPPDATA"]) / "hermes/profiles/reviewer/skills/wolftac-review"
        self.make_link(link, target)
        output, data = self.deploy_probe(link)
        self.assertIn(f"FOREIGN-LINK {link}", output)
        self.assertNotIn(str(link), data["Roots"])
        self.assertFalse(data["Under"])
        self.assertIn(str(link / "SKILL.md"), data["Dsts"])


if __name__ == "__main__":
    unittest.main()
