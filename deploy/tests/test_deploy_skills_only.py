"""WD-632: execute the real deploy entry point against synthetic Git/home trees.

No live target, WSL home, credential or installed Hermes CLI is used. The WSL
probe is shadowed by an inert executable in the child working directory.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which('pwsh') or ''
GIT = shutil.which('git') or ''
SKILL = 'canonical/wolftac-build/SKILL.md'
REFERENCE = 'ports/hermes/wolftac-build/references/example.md'
SCRIPT = 'canonical/wolftac-integrate/scripts/linear_bot.py'


@unittest.skipUnless(os.name == 'nt' and PWSH and GIT, 'Windows deploy requires PowerShell and Git')
class SkillsOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wd632-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.home = self.root / 'home'
        self.local = self.home / 'AppData/Local'
        self.repo.mkdir()
        self.local.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {
            'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PROCESSOR_ARCHITECTURE',
        }}
        self.env.update(USERPROFILE=str(self.home), HOME=str(self.home),
                        LOCALAPPDATA=str(self.local), APPDATA=str(self.home / 'AppData/Roaming'),
                        TEMP=str(self.root), TMP=str(self.root), TMPDIR=str(self.root),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME='Fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
                        GIT_COMMITTER_NAME='Fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
        self.env['PATH'] = os.pathsep.join(dict.fromkeys([
            str(Path(GIT).parent), str(Path(PWSH).parent),
            str(Path(os.environ.get('SYSTEMROOT', '/')) / 'System32')]))
        # Windows executable search checks cwd before System32. where.exe rejects
        # the WSL argv and exits without stdout; it cannot boot WSL or access a home.
        if os.name == 'nt':
            shutil.copyfile(Path(os.environ['SYSTEMROOT']) / 'System32/where.exe', self.root / 'wsl.exe')
        else:
            stub = self.root / 'wsl.exe'
            stub.write_text('#!/bin/sh\nexit 1\n')
            stub.chmod(0o755)
            self.env['PATH'] = str(self.root) + os.pathsep + self.env['PATH']
        stages = ['spec', 'build', 'review', 'integrate', 'agent-command-patterns']
        self.sources = []
        for stage in stages:
            for prefix in ['canonical', 'ports/hermes', 'plugins/wolftac-factory/skills', 'ports/claude-desktop']:
                self.sources.append(f'{prefix}/wolftac-{stage}/SKILL.md')
            self.sources.append(f'ports/codex/wolftac-{stage}.md')
        self.sources += ['canonical/wolftac-spec/spec-template.md', REFERENCE]
        for prefix in ['canonical/wolftac-integrate/scripts', 'ports/codex/wolftac-integrate-scripts']:
            for name in ['linear_bot.py', 'slack_bot.py', 'run_gate.py']:
                self.sources.append(f'{prefix}/{name}')
        for path in self.sources:
            self.put(self.repo / path, 'version one\n')
        self.git('init', '-q')
        self.git('config', 'core.autocrlf', 'false')
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic first')
        self.first = self.git('rev-parse', 'HEAD').strip()
        for path in [SKILL, REFERENCE, SCRIPT]:
            self.put(self.repo / path, 'version two\n')
        self.git('commit', '-qam', 'synthetic second')
        self.second = self.git('rev-parse', 'HEAD').strip()
        for path in [SKILL, REFERENCE, SCRIPT]:
            self.put(self.repo / path, 'version three\nextra\n')
        self.git('commit', '-qam', 'synthetic third')
        self.skill = self.home / '.claude/skills/wolftac-build/SKILL.md'
        self.reference = self.local / 'hermes/skills/wolftac-build/references/example.md'
        self.script = self.home / '.claude/skills/wolftac-integrate/scripts/linear_bot.py'

    @staticmethod
    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))

    def git(self, *args):
        proc = subprocess.run([GIT, '-C', str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def deploy(self, *args, expected=0, script=None):
        proc = subprocess.run([PWSH, '-NoProfile', '-File', str(script or ROOT / 'deploy/deploy.ps1'),
                               '-RepoRoot', str(self.repo), *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        self.assertNotIn('\\\\wsl$', proc.stdout)
        self.assertNotIn('WSL probe timed out', proc.stdout)
        self.assertEqual(proc.stderr, '', proc.stderr)
        return proc.stdout

    def backups(self, path):
        return list(path.parent.glob('.deploy-backup-*/' + path.name))

    def test_stale_live_edit_deleted_line_script_and_check_together(self):
        self.put(self.skill, 'version one\r\n')
        self.put(self.reference, 'version two\n')
        self.put(self.script, 'version one\n')
        edited = self.home / '.claude/skills/wolftac-review/SKILL.md'
        deleted = self.local / 'hermes/skills/wolftac-build/SKILL.md'
        # This source has three commits too. Deleted text is a subset, NOT history.
        for text in ['old A\nold B\n', 'middle A\nmiddle B\n', 'current A\ncurrent B\n']:
            self.put(self.repo / 'ports/hermes/wolftac-build/SKILL.md', text)
            self.git('add', '.')
            self.git('commit', '-qm', 'synthetic deletion regression')
        self.put(edited, 'version one\nLIVE-ONLY\n')
        self.put(deleted, 'current A\n')
        out = self.deploy('-Deploy', '-SkillsOnly', expected=1)
        self.assertIn(f'STALE-UPDATED {self.skill} (was {self.first[:7]})', out)
        self.assertIn(f'STALE-UPDATED {self.reference}', out)
        self.assertEqual(self.skill.read_bytes(), b'version three\nextra\n')
        self.assertEqual(self.reference.read_bytes(), b'version three\nextra\n')
        self.assertEqual([p.read_bytes() for p in self.backups(self.skill)], [b'version one\r\n'])
        self.assertEqual([p.read_bytes() for p in self.backups(self.reference)], [b'version two\n'])
        self.assertEqual(self.script.read_bytes(), b'version one\n')
        self.assertIn(f'SKILLS-ONLY-SKIPPED {self.script}', out)
        for path, data in [(edited, b'version one\nLIVE-ONLY\n'), (deleted, b'current A\n')]:
            self.assertIn(f'DRIFT-SKIPPED {path}', out)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(self.backups(path), [])
        self.assertFalse((self.home / 'Downloads').exists())
        check = self.deploy('-Check', expected=1)
        self.assertNotIn(f'DRIFT    {self.skill}', check)
        self.assertNotIn(f'DRIFT    {self.reference}', check)
        self.assertIn(f'DRIFT    {edited}', check)
        self.assertIn(f'DRIFT    {deleted}', check)
        self.assertIn(f'DRIFT    {self.script}', check)
        again = self.deploy('-Deploy', '-SkillsOnly', expected=1)
        self.assertNotIn('STALE-UPDATED', again)
        self.assertEqual(len(self.backups(self.skill)), 1)

    def test_plain_deploy_still_skips_and_force_still_overwrites(self):
        self.put(self.skill, 'version one\n')
        self.put(self.script, 'version one\n')
        out = self.deploy('-Deploy', expected=1)
        self.assertIn(f'DRIFT-SKIPPED {self.skill}', out)
        self.assertEqual(self.skill.read_text(), 'version one\n')
        self.assertEqual(self.backups(self.skill), [])
        self.deploy('-Deploy', '-Force')
        self.assertEqual(self.skill.read_text(), 'version three\nextra\n')
        self.assertEqual(self.script.read_text(), 'version three\nextra\n')

    def assert_unknown(self, mode):
        self.put(self.skill, 'version one\n')
        if mode == 'untracked':
            # A new mapped reference that never existed in Git.
            never = self.repo / 'ports/hermes/wolftac-build/references/never.md'
            self.put(never, 'current\n')
            self.skill = self.local / 'hermes/skills/wolftac-build/references/never.md'
            self.put(self.skill, 'version one\n')
        elif mode == 'shallow':
            (self.repo / '.git/shallow').write_text(self.git('rev-parse', 'HEAD').strip() + '\n')
        elif mode == 'no-repo':
            old = self.repo
            self.repo = self.root / 'no-repo'
            shutil.copytree(old, self.repo, ignore=shutil.ignore_patterns('.git'))
        else:
            self.env['PATH'] = str(Path(PWSH).parent)
        out = self.deploy('-Deploy', '-SkillsOnly', expected=1)
        self.assertIn(f'DRIFT-SKIPPED {self.skill}', out)
        self.assertEqual(self.skill.read_bytes(), b'version one\n')
        self.assertEqual(self.backups(self.skill), [])

    def test_untracked_is_unknown(self):
        self.assert_unknown('untracked')

    def test_shallow_is_unknown(self):
        self.assert_unknown('shallow')

    def test_no_repository_is_unknown(self):
        self.assert_unknown('no-repo')

    def test_git_error_is_unknown(self):
        self.assert_unknown('git-error')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_foreign_junction_and_force_are_never_written(self):
        target = self.root / 'foreign'
        self.put(target / 'SKILL.md', 'foreign live edit\n')
        self.skill.parent.parent.mkdir(parents=True)
        command = f'New-Item -ItemType Junction -Path "{self.skill.parent}" -Target "{target}" | Out-Null'
        subprocess.run([PWSH, '-NoProfile', '-Command', command], env=self.env, check=True, timeout=20)
        self.addCleanup(lambda: os.rmdir(self.skill.parent))
        for flags in [[], ['-Force']]:
            out = self.deploy('-Deploy', '-SkillsOnly', *flags)
            self.assertIn(f'JUNCTION-NOOP {self.skill}', out)
            self.assertEqual((target / 'SKILL.md').read_text(), 'foreign live edit\n')
            self.assertEqual(self.backups(self.skill), [])

    def test_manifest_cannot_expand_skills_only_roots(self):
        harness = self.root / 'harness'
        harness.mkdir()
        for name in ['deploy.ps1', 'junction-lib.ps1']:
            shutil.copyfile(ROOT / 'deploy' / name, harness / name)
        outside = self.root / 'outside'
        manifest = {'skills_roots': [str(outside)], 'pairs': [
            {'repo': 'ports/hermes/wolftac-build', 'name': 'wolftac-build'}]}
        (harness / 'junctions.json').write_text(json.dumps(manifest), encoding='utf-8')
        out = self.deploy('-Deploy', '-SkillsOnly', script=harness / 'deploy.ps1')
        self.assertIn(f'SKILLS-ONLY-SKIPPED {outside / "wolftac-build/SKILL.md"}', out)
        self.assertFalse(outside.exists())

    def test_history_preserves_unicode_and_missing_final_newline(self):
        source = self.repo / 'ports/hermes/wolftac-build/references/unicode.md'
        for text in ['caf\u00e9', 'caf\u00e9\n', 'new\n']:
            self.put(source, text)
            self.git('add', '.')
            self.git('commit', '-qm', 'synthetic unicode history')
        target = self.reference.parent / 'unicode.md'
        self.put(target, 'caf\u00e9')
        out = self.deploy('-Deploy', '-SkillsOnly')
        self.assertIn(f'STALE-UPDATED {target}', out)
        self.assertEqual(target.read_bytes(), b'new\n')
        self.assertEqual([p.read_bytes() for p in self.backups(target)], ['caf\u00e9'.encode()])

    def test_target_changed_after_history_read_is_preserved(self):
        self.put(self.skill, 'new live edit\n')
        def quote(path):
            return "'" + str(path).replace("'", "''") + "'"
        probe = self.root / 'recheck.ps1'
        probe.write_text(
            f". {quote(ROOT / 'deploy/deploy.ps1')} -RepoRoot {quote(self.repo)}\n"
            f"try {{ Update-StaleTarget {quote(self.skill)} {quote(self.repo / SKILL)} "
            "(Get-LfContentHash \"version one`n\"); exit 4 }\n"
            "catch { if ($_.Exception.Message -ne 'target changed since history check') { throw }; 'PRESERVED' }\n",
            encoding='utf-8')
        proc = subprocess.run([PWSH, '-NoProfile', '-File', str(probe)], env=self.env,
                              cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('PRESERVED', proc.stdout)
        self.assertEqual(self.skill.read_text(), 'new live edit\n')
        self.assertEqual(self.backups(self.skill), [])

    def test_skills_only_rejects_other_modes_before_discovery(self):
        self.deploy('-Check', '-SkillsOnly', expected=1)
        self.deploy('-Package', '-SkillsOnly', expected=1)
        self.assertFalse(self.skill.exists())


if __name__ == '__main__':
    unittest.main()
