"""WD-671: real CLI against synthetic pending records; never a live profile."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'deploy/learn-pending.py'
SENTINEL = 'LP-SENTINEL'
FAKE = 'fixture-only-token-671'


class LearnPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wd671-', dir=os.getenv('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'hermes'
        self.home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE'}}
        for key in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'HERMES_HOME', 'TEMP', 'TMP', 'TMPDIR'):
            self.env[key] = str(self.root)

    def record(self, profile='builder', id='abcdef01', hours=2, **fields):
        folder = self.home / 'profiles' / profile / 'pending/skills'
        folder.mkdir(parents=True, exist_ok=True)
        record = dict(id=id, subsystem='skills', action='patch', summary=SENTINEL + FAKE,
                      origin='fixture', created_at=time.time() - hours * 3600,
                      payload={'name': 'wolftac-build', 'content': SENTINEL + FAKE})
        record.update(fields)
        path = folder / (id + '.json')
        path.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + '\n').encode())
        return path

    def run_cli(self, *args, expected=0):
        before = {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*.json')}
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), args[0],
                                 '--hermes-home', str(self.home), *args[1:]],
                                env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn(SENTINEL, result.stdout + result.stderr)
        self.assertNotIn(FAKE, result.stdout + result.stderr)
        self.assertEqual(before, {p.relative_to(self.home): p.read_bytes() for p in self.home.rglob('*.json')})
        return result.stdout

    def test_list_counts_age_empty_and_json_no_content(self):
        self.assertEqual(self.run_cli('list').strip(), 'learn-pending: none')
        self.record()
        self.record(id='abcdef02', hours=30)
        self.record(profile='integrator', id='abcdef03')
        text = self.run_cli('list')
        lines = text.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[-1], 'learn-pending: builder=2, integrator=1')
        self.assertEqual(lines[0], 'builder abcdef01 patch wolftac-build 2h')
        self.assertEqual(lines[1], 'builder abcdef02 patch wolftac-build 30h')
        data = json.loads(self.run_cli('list', '--json'))
        self.assertEqual(len(data['records']), 3)
        self.assertEqual(data['summary'], lines[-1])
        self.assertEqual(set(data['records'][0]), {'profile', 'id', 'action', 'skill', 'age_hours'})

    def test_unreadable_and_safe_metadata(self):
        path = self.record(action=SENTINEL, payload={'name': SENTINEL})
        folder = path.parent
        for name, text in [('broken.json', SENTINEL), ('array.json', '["LP-SENTINEL"]'),
                           ('bad-id.json', '{"id":"../x","summary":"LP-SENTINEL"}')]:
            (folder / name).write_text(text)
        text = self.run_cli('list')
        self.assertIn('builder abcdef01 ? ? 2h', text)
        self.assertIn('builder unreadable=3', text)
        data = json.loads(self.run_cli('list', '--json'))
        self.assertEqual(data['unreadable'], {'builder': 3})
        self.assertEqual(len(data['records']), 1)

    def test_name_validation_and_unknown_age(self):
        for name in (None, [], 'a/b', 'a\n', 'UPPER', 'a' * 65):
            with self.subTest(name=name):
                self.record(created_at='LP-SENTINEL', payload={'name': name})
                data = json.loads(self.run_cli('list', '--json'))
                self.assertEqual(data['records'][0]['skill'], '?')
                self.assertIsNone(data['records'][0]['age_hours'])

    def test_export_preserves_exact_bytes_and_skips_invalid(self):
        a = self.record()
        b = self.record(profile='integrator', id='abcdef02', summary='unicode: café\r\n')
        (a.parent / 'broken.json').write_text(SENTINEL)
        for dirname in ('new', 'empty'):
            out = self.root / dirname
            if dirname == 'empty':
                out.mkdir()
            self.run_cli('export', '--out', str(out))
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ['builder-abcdef01.json', 'integrator-abcdef02.json'])
            self.assertEqual((out / 'builder-abcdef01.json').read_bytes(), a.read_bytes())
            self.assertEqual((out / 'integrator-abcdef02.json').read_bytes(), b.read_bytes())

    def test_export_refuses_home_git_worktree_and_nonempty_without_writes(self):
        self.record()
        repo = self.root / 'repo'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True, timeout=10)
        nonempty = self.root / 'nonempty'
        nonempty.mkdir()
        keep = nonempty / 'keep.txt'
        keep.write_bytes(b'unchanged')
        for out in (self.home / 'export', repo / 'nested/export', nonempty):
            with self.subTest(out=out):
                self.run_cli('export', '--out', str(out), expected=2)
                if out != nonempty:
                    self.assertFalse(out.exists())
        self.assertEqual(list(nonempty.iterdir()), [keep])
        self.assertEqual(keep.read_bytes(), b'unchanged')
        # A linked worktree has a .git file, not a .git directory.
        linked = self.root / 'linked'
        linked.mkdir()
        (linked / '.git').write_text('gitdir: synthetic\n')
        self.run_cli('export', '--out', str(linked / 'export'), expected=2)
        self.assertFalse((linked / 'export').exists())

    def test_duplicate_id_export_and_profile_traversal_are_refused(self):
        path = self.record()
        shutil.copyfile(path, path.parent / 'second.json')
        out = self.root / 'export'
        self.run_cli('export', '--out', str(out), expected=2)
        self.assertFalse(out.exists())
        text = self.run_cli('list', '--profiles', '../x,builder,builder')
        self.assertIn('invalid-profile unreadable=1', text)
        self.assertTrue(text.endswith('learn-pending: builder=2\n'))

    def test_symlink_destinations_are_resolved_before_export(self):
        self.record()
        link = self.root / 'link'
        try:
            link.symlink_to(self.home, target_is_directory=True)
        except OSError:
            self.skipTest('directory symlink privilege unavailable')
        self.run_cli('export', '--out', str(link / 'export'), expected=2)
        self.assertFalse((self.home / 'export').exists())

    def test_skill_paragraph_identity_and_parity_negative(self):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            self.skipTest('PowerShell 7 required')
        files = [f'ports/hermes/wolftac-{stage}/SKILL.md' for stage in ('build', 'review', 'integrate')]
        paragraphs = []
        for relative in files:
            text = (ROOT / relative).read_text(encoding='utf-8')
            paragraphs.append(' '.join(next(p for p in text.split('\n\n') if p.startswith('**LEARN-STAGED:**')).split()))
        self.assertEqual(paragraphs, [paragraphs[0]] * 3)
        invariants = json.loads((ROOT / 'deploy/invariants.json').read_text(encoding='utf-8'))
        entry = next(i for i in invariants if i['id'] == 'learn-staged')
        self.assertEqual(len(entry['checks']), 3)
        self.assertEqual({c['file'] for c in entry['checks']}, set(files))
        # Copy only tracked files needed by the parity checker, never .git or credentials.
        scratch = self.root / 'parity'
        for item in invariants:
            for check in item['checks']:
                src = ROOT / check['file']
                dst = scratch / check['file']
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.is_file():
                    shutil.copyfile(src, dst)
        (scratch / 'deploy').mkdir(exist_ok=True)
        for filename in ('invariants.json', 'parity-check.ps1'):
            shutil.copyfile(ROOT / 'deploy' / filename, scratch / 'deploy' / filename)
        def parity():
            return subprocess.run([pwsh, '-NoProfile', '-File', str(scratch / 'deploy/parity-check.ps1')],
                                  cwd=scratch, env=self.env, capture_output=True, text=True, timeout=45)
        positive = parity()
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        target = scratch / files[0]
        target.write_text(target.read_text(encoding='utf-8').replace('LEARN-STAGED', 'REMOVED'), encoding='utf-8', newline='\n')
        negative = parity()
        self.assertNotEqual(negative.returncode, 0)
        self.assertIn('learn-staged', negative.stdout + negative.stderr)
        self.assertIn(files[0], (negative.stdout + negative.stderr).replace('\\', '/'))


if __name__ == '__main__':
    unittest.main()
