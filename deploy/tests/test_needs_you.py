"""WD-628: synthetic Linear transport and real watchdog CLI, no live delivery."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_factory_gatekeeper as base

g = base.g
NOW = datetime(2026, 11, 14, 18, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which('pwsh')


def comment(body, hours=5, **fields):
    return dict(body=body, createdAt=(NOW - timedelta(hours=hours)).isoformat(), **fields)


def row(identifier='WD-1', comments=None, state='Todo'):
    return dict(identifier=identifier, state=dict(name=state, type='unstarted'),
                assignee=None, comments=base.connection(comments or []))


class NeedsYouGatekeeperTests(unittest.TestCase):
    def setUp(self):
        self.guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_amended_blocked_heading_and_clear_rules(self):
        blocked = comment('\n**Agent:** Fixture\n\nBLOCKED: PRIVATE-BODY')
        r = row(comments=[blocked])
        original = copy.deepcopy(r)
        self.assertEqual(g.needs_you_status([r], NOW), 'needs-you: WD-1=5h(blocked-question)')
        self.assertEqual(r, original)
        for body in ('BUILD SKIPPED: private', 'SWEPT: private', '**Agent:** x\nBUILD SKIPPED: private'):
            r['comments']['nodes'] = [comment(body, 1), blocked]
            self.assertEqual(g.needs_you_status([r], NOW), 'needs-you: WD-1=5h(blocked-question)')
        for body in ('**Agent:** Fixture\nREADY: private', 'plain text', '**REVIEW REPORT — FAIL**',
                     'CHANGES_NEEDED'):
            for user in (None, {'id': g.PARK_OWNER_ID}, {'id': 'worker'}):
                r['comments']['nodes'] = [blocked, comment(body, 1, user=user)]
                self.assertEqual(g.needs_you_status([r], NOW), 'needs-you: none')
        r['comments']['nodes'] = [comment('quoted BLOCKED: text')]
        self.assertEqual(g.needs_you_status([r], NOW), 'needs-you: none')

    def test_park_is_exact_existing_contract_and_uses_review_age(self):
        review = comment('**REVIEW REPORT — NEEDS HUMAN**\nPRIVATE', 8 * 24)
        r = row(comments=[review], state='In Progress')
        decision = comment('**DECISION RECORD — WD-1**\nhttps://discord.com/channels/'
                           '12345678901234567/12345678901234568/12345678901234569', 2)
        for assigned, later, parked in ((None, [], True), (g.PARK_OWNER_ID, [decision], True),
                                       (None, [decision], False), (None, [comment('READY', 2)], True),
                                       (None, [comment('ready', 2, user={'id': g.PARK_OWNER_ID})], False)):
            r['assignee'] = {'id': assigned} if assigned else None
            r['comments']['nodes'] = [review, *later]
            self.assertEqual(not g.park_cleared(r, r['comments']['nodes']), parked)
            self.assertEqual(g.needs_you_status([r], NOW),
                             'needs-you: WD-1=8d(parked)' if parked else 'needs-you: none')
        r['comments']['nodes'] = [review, comment('**REVIEW REPORT — PASS**', 1)]
        self.assertEqual(g.needs_you_status([r], NOW), 'needs-you: none')

    def test_exclusions_sort_both_reasons_and_threshold_precision(self):
        rows = [row('WD-10', [comment('BLOCKED: private', 3)], 'Backlog'),
                row('WD-2', [comment('BLOCKED: private', 5)]),
                row('WD-3', [comment('BLOCKED: private')], 'Done'),
                row('WD-4', [comment('BLOCKED: private')], 'Canceled')]
        rows.append(row('WD-8', [comment('**REVIEW REPORT — NEEDS HUMAN**', 192),
                                 comment('BLOCKED: private', 4 + 1 / 3600)]))
        self.assertEqual(g.needs_you_status(rows, NOW),
                         'needs-you: WD-2=5h(blocked-question),WD-8=14401s(blocked-question),'
                         'WD-8=8d(parked),WD-10=3h(blocked-question)')
        for field, value in (('identifier', 'WD-1; PRIVATE'),):
            bad = row(comments=[comment('BLOCKED: secret')]); bad[field] = value
            with self.assertRaises(g.ProbeError):
                g.needs_you_status([bad], NOW)
        bad = row(comments=[dict(body='BLOCKED: private', createdAt='PRIVATE')])
        with self.assertRaises(g.ProbeError):
            g.needs_you_status([bad], NOW)

    def test_http_inventory_exhausts_three_issue_and_comment_pages(self):
        calls = []
        r = row(comments=[comment('BUILD SKIPPED: private', 1)])
        r['comments']['pageInfo'] = dict(hasNextPage=True, endCursor='c1')
        responses = [dict(issues=base.connection([r], 'p1')),
                     dict(issue=dict(comments=base.connection([comment('SWEPT: private', 2)], 'c2'))),
                     dict(issue=dict(comments=base.connection([comment('BLOCKED: PRIVATE', 5)]))),
                     dict(issues=base.connection([row('WD-2')], 'p2')),
                     dict(issues=base.connection([row('WD-3')]))]

        def fake(request, timeout):
            payload = json.loads(request.data)
            self.assertNotIn('mutation', payload['query'].lower())
            self.assertEqual(request.full_url, 'https://api.linear.app/graphql')
            self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-only')
            calls.append(payload)
            return io.StringIO(json.dumps(dict(data=responses.pop(0))))

        with patch.object(g.urllib.request, 'urlopen', side_effect=fake):
            rows = g.needs_you_issues('synthetic-only')
        self.assertEqual(len(rows), 3)
        self.assertFalse(responses)
        self.assertEqual([c['variables']['after'] for c in calls], [None, 'c1', 'c2', 'p1', 'p2'])
        self.assertIn('nin: ["completed", "canceled"]', calls[0]['query'])
        self.assertIn('Factory Queue', calls[0]['query'])
        self.assertEqual(g.needs_you_status(rows, NOW), 'needs-you: WD-1=5h(blocked-question)')

    def test_missing_and_nonadvancing_pages_fail_closed(self):
        r = row(comments=[comment('BLOCKED: private')])
        cases = [dict(issues={'nodes': [r]}),
                 dict(issues=base.connection([r], 'repeat'))]
        for data in cases:
            with self.subTest(data=data), patch.object(g, 'linear_query', return_value=data):
                with self.assertRaises((KeyError, g.ProbeError)):
                    g.needs_you_issues('synthetic')
        r['comments'].pop('pageInfo')
        with patch.object(g, 'linear_query', return_value=dict(issues=base.connection([r]))):
            with self.assertRaises(KeyError):
                g.needs_you_issues('synthetic')

    def tick(self, error=None, inflight=False, dry=True):
        with tempfile.TemporaryDirectory(dir=os.getenv('TMPDIR')) as folder:
            path = Path(folder) / 'state.json'
            state = {'lanes': {lane: dict(dispatched_at=NOW.isoformat(), job_last_run_at=None)
                               for lane in g.DEFAULT_JOB_MAP}} if inflight else {}
            path.write_text(json.dumps(state))
            before = path.read_bytes()
            argv = ['gatekeeper', '--no-audit', '--no-discord', '--state-path', str(path)]
            if dry:
                argv.append('--dry-run')
            output = io.StringIO()
            with patch.object(g.sys, 'argv', argv), patch.object(g, 'now_utc', return_value=NOW), \
                    patch.object(g, 'hermes_home', return_value=folder), \
                    patch.object(g, 'read_token', return_value='synthetic-only'), \
                    patch.object(g, 'lane_issues', return_value=[base.issue()]), \
                    patch.object(g, 'needs_you_issues', side_effect=error,
                                 return_value=[row(comments=[comment('BLOCKED: PRIVATE')])]) as probe, \
                    patch.object(g, 'dispatch', return_value='dispatched(dry-run)') as dispatch, \
                    contextlib.redirect_stdout(output):
                self.assertEqual(g.main(), 0)
            self.assertEqual(probe.call_count, 1)
            if dry:
                self.assertEqual(before, path.read_bytes())
            self.assertNotIn('PRIVATE', output.getvalue())
            self.assertNotIn('synthetic-only', output.getvalue())
            return output.getvalue(), dispatch.call_args_list

    def test_tick_probes_even_when_every_lane_inflight_and_dryrun_no_write(self):
        text, calls = self.tick(inflight=True)
        self.assertIn('needs-you: WD-1=5h(blocked-question)', text)
        self.assertEqual(calls, [])

    def test_probe_failure_unknown_isolated_and_401_obeys_existing_veto(self):
        for error in (TimeoutError('PRIVATE'), g.ProbeError('PRIVATE')):
            text, calls = self.tick(error)
            self.assertIn('needs-you: unknown', text)
            self.assertGreater(len(calls), 0)
        error = urllib.error.HTTPError('synthetic', 401, 'PRIVATE', {}, None)
        text, calls = self.tick(error)
        self.assertIn('needs-you: unknown', text)
        self.assertEqual([c.args[1] for c in calls], ['integrate'])
        self.assertIn('build: linear-auth-failed (401) -> skipped', text)


@unittest.skipUnless(PWSH, 'PowerShell 7 required')
class NeedsYouWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='wd628-', dir=os.getenv('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cron = self.root / 'cron'
        self.cron.mkdir()
        (self.cron / 'ticker_heartbeat').write_text('fixture')
        jobs = [dict(id='gk-fixture', name='Wolftac Factory Gatekeeper', enabled=True,
                     paused=False, last_status='ok', schedule=dict(minutes=30),
                     next_run_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())]
        (self.cron / 'jobs.json').write_text(json.dumps(dict(jobs=jobs)))
        self.output = self.cron / 'output/gk-fixture'
        self.output.mkdir(parents=True)
        self.report = self.output / 'new.txt'
        self.expiry = self.root / 'expiry.txt'
        self.expiry.write_text('LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT=2099-01-01T00:00:00Z\n')
        self.state = self.cron / 'watchdog-state.json'
        self.outbox = self.root / 'outbox'
        self.env = {k: v for k, v in os.environ.items() if k.upper() in {
            'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'SYSTEMDRIVE'}}
        for key in ('HOME', 'USERPROFILE', 'LOCALAPPDATA', 'HERMES_HOME', 'TEMP', 'TMP', 'TMPDIR'):
            self.env[key] = str(self.root)

    def seed(self, **state):
        self.state.write_text(json.dumps(state))

    def assert_open_findings_equal(self, actual, expected):
        # PowerShell may trim fractional trailing zeros while round-tripping JSON.
        # Preserve identities and first-seen instants, not their string spelling.
        self.assertEqual(actual.keys(), expected.keys())
        for key in actual:
            self.assertEqual(datetime.fromisoformat(actual[key]),
                             datetime.fromisoformat(expected[key]), key)

    def run_script(self, value, dry=True, outbox=None, degraded=False):
        segment = '' if value is None else 'needs-you: ' + value + '; '
        lane = 'FAIL-OPEN' if degraded else 'idle'
        self.report.write_text('gatekeeper: build: ' + lane + '; ' + segment + 'audit: clean\n', encoding='utf-8')
        before = self.state.read_bytes() if self.state.exists() else None
        inputs = {p: p.read_bytes() for p in (self.report, self.expiry, self.cron / 'jobs.json')}
        args = [PWSH, '-NoProfile', '-NonInteractive', '-File', str(ROOT / 'deploy/factory-watchdog.ps1'),
                '-CronDir', str(self.cron), '-EnvFile', str(self.expiry), '-PolicyPath', str(self.root / 'unused-policy.json'),
                '-SkipDrift', '-SkipLearn', '-SkipAudit', '-StuckRunThreshold', '0',
                '-AlertOutbox', str(outbox or self.outbox)]
        if dry:
            args.append('-DryRun')
        result = subprocess.run(args, env=self.env, capture_output=True, encoding='utf-8', timeout=30)
        text = result.stdout + result.stderr
        self.assertNotIn('PRIVATE', text)
        for p, content in inputs.items():
            self.assertEqual(p.read_bytes(), content)
        if dry:
            self.assertIn('DRY RUN:', text)
            self.assertEqual(before, self.state.read_bytes() if self.state.exists() else None)
            self.assertFalse(self.outbox.exists())
        return result.returncode, text

    def test_threshold_and_keep_drop_boundaries(self):
        for age, failed in (('3h', False), ('4h', False), ('14401s', True), ('5h', True)):
            with self.subTest(age=age):
                code, text = self.run_script(f'WD-1={age}(blocked-question)')
                self.assertEqual(code, int(failed), text)
                self.assertEqual(text.count('  - needs-you:'), int(failed))
                self.assertEqual(text.count('DRY RUN POST: *Factory needs you*'), 1)
        for age, human_age, suffix in (('7d', '7d', False), ('604801s', '7d', True), ('8d', '8d', True)):
            code, text = self.run_script(f'WD-2={age}(parked)')
            self.assertEqual(code, 1, text)
            self.assertEqual('(keep or drop?)' in text, suffix)
            if suffix:
                self.assertIn(f'WD-2={human_age}(parked) (keep or drop?)\n', text)
        self.assertNotIn('(keep or drop?)', self.run_script('WD-2=8d(blocked-question)')[1])

    def test_daily_cap_old_state_and_dryrun(self):
        for hours, posts in ((None, 1), (3, 0), (23.99, 0), (24, 1), (25, 1)):
            fields = {} if hours is None else dict(needs_you_last_digest_at=
                (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat())
            self.seed(**fields)
            code, text = self.run_script('WD-1=3h(blocked-question),WD-2=2h(parked)')
            self.assertEqual(code, 0, text)
            self.assertEqual(text.count('DRY RUN POST: *Factory needs you*'), posts)
            if posts:
                self.assertIn('WD-1=3h(blocked-question)\nWD-2=2h(parked)', text)

    def test_none_unknown_and_invalid_segment_do_not_post_or_leak(self):
        for value in ('none', 'unknown', 'WD-1=8d(parked)PRIVATE',
                      'WD-1=5h(plan-changes-needed)', 'WD-X=5h(parked)',
                      'WD-1=-5h(parked)', 'PRIVATE', ''):
            with self.subTest(value=value):
                code, text = self.run_script(value)
                self.assertEqual(code, 0, text)
                self.assertNotIn('Factory needs you', text)
                self.assertNotIn('  - needs-you:', text)

    def test_newest_output_only_and_missing_output(self):
        old = self.output / 'old.txt'
        old.write_text('gatekeeper: needs-you: WD-2=8d(parked)\n')
        os.utime(old, (1, 1))
        self.assertNotIn('WD-2', self.run_script('none')[1])
        self.assertNotIn('WD-2', self.run_script('unknown')[1])

    def test_unknown_missing_invalid_preserve_open_incidents_across_ticks(self):
        key = 'needs-you|WD-547|parked'
        code, text = self.run_script('WD-547=2077194s(parked)', dry=False)
        self.assertEqual(code, 1, text)
        initial = json.loads(self.state.read_text(encoding='utf-8-sig'))
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 2)
        for value in ('unknown', None, 'WD-547=8d(parked)PRIVATE', '', 'unknown'):
            with self.subTest(value=value):
                # Even an overdue daily list must not replay stale inventory.
                state = json.loads(self.state.read_text(encoding='utf-8-sig'))
                state['needs_you_last_digest_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
                self.seed(**state)
                code, text = self.run_script(value, dry=False)
                self.assertEqual(code, 1, text)
                after = json.loads(self.state.read_text(encoding='utf-8-sig'))
                self.assert_open_findings_equal(after['open_findings'], initial['open_findings'])
                self.assertEqual(after['finding_texts'], initial['finding_texts'])
                self.assertEqual(after['status'], 'unhealthy')
                self.assertEqual(datetime.fromisoformat(after['needs_you_last_digest_at']),
                                 datetime.fromisoformat(state['needs_you_last_digest_at']))
                self.assertEqual(len(list(self.outbox.glob('*.txt'))), 2)
                self.assertNotIn('recovered:', text)
        # Return of the same known item is not a new incident; the due list may send.
        code, text = self.run_script('WD-547=2082594s(parked)', dry=False)
        self.assertEqual(code, 1, text)
        after = json.loads(self.state.read_text(encoding='utf-8-sig'))
        self.assert_open_findings_equal(after['open_findings'], initial['open_findings'])
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 3)
        self.assertEqual(sum('new: needs-you:' in p.read_text() for p in self.outbox.glob('*.txt')), 1)
        self.run_script('none', dry=False)
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8-sig'))['open_findings'], {})
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 4)
        self.assertEqual(sum('recovered: needs-you:' in p.read_text() for p in self.outbox.glob('*.txt')), 1)

    def test_unknown_preserves_wait_without_masking_other_liveness_transitions(self):
        self.run_script('WD-1=5h(blocked-question)', dry=False)
        key = 'needs-you|WD-1|blocked-question'
        initial = json.loads(self.state.read_text(encoding='utf-8-sig'))
        job_file = self.cron / 'jobs.json'
        jobs = json.loads(job_file.read_text())
        jobs['jobs'][0]['last_status'] = 'error'
        job_file.write_text(json.dumps(jobs))
        code, text = self.run_script('unknown', dry=False)
        self.assertEqual(code, 1, text)
        state = json.loads(self.state.read_text(encoding='utf-8-sig'))
        self.assertEqual(len(state['open_findings']), 2)
        self.assert_open_findings_equal({key: state['open_findings'][key]}, initial['open_findings'])
        jobs['jobs'][0]['last_status'] = 'ok'
        job_file.write_text(json.dumps(jobs))
        code, text = self.run_script('unknown', dry=False)
        self.assertEqual(code, 1, text)
        self.assert_open_findings_equal(json.loads(self.state.read_text(encoding='utf-8-sig'))['open_findings'],
                                       initial['open_findings'])
        messages = '\n'.join(p.read_text() for p in self.outbox.glob('*.txt'))
        self.assertIn('new: [', messages)
        self.assertIn('recovered: [', messages)
        self.assertNotIn('recovered: needs-you:', messages)
        self.assertNotIn('all liveness checks passing again', messages)
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 4)
        # Dry-run preserves both the incident and all state without sending.
        before = self.state.read_bytes()
        self.outbox = self.root / 'dry-outbox'
        code, text = self.run_script(None)
        self.assertEqual(code, 1, text)
        self.assertEqual(self.state.read_bytes(), before)
        # A valid, changed inventory may close the old wait and open another.
        code, text = self.run_script('WD-2=5h(parked)', dry=False)
        self.assertEqual(code, 1, text)
        state = json.loads(self.state.read_text(encoding='utf-8-sig'))
        self.assertEqual(list(state['open_findings']), ['needs-you|WD-2|parked'])

    def test_degraded_ticks_and_needs_you_recover_independently(self):
        # Current-main WD-578 must remain active alongside the WD-628 hold.
        for number in (1, 2):
            old = self.output / f'older-{number}.txt'
            old.write_text('gatekeeper: build: FAIL-OPEN; needs-you: unknown; audit: clean\n')
            os.utime(old, (number, number))
        value = 'WD-1=5h(blocked-question)'
        code, text = self.run_script(value, degraded=True)
        self.assertEqual(code, 1, text)
        self.assertEqual(text.count('  - needs-you:'), 1)
        self.assertIn('gatekeeper-degraded: the newest 3', text)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.outbox.exists())
        code, text = self.run_script(value, dry=False, degraded=True)
        self.assertEqual(code, 1, text)
        initial = json.loads(self.state.read_text(encoding='utf-8-sig'))
        key = 'needs-you|WD-1|blocked-question'
        self.assertEqual(len(initial['open_findings']), 2)
        self.assertIn(key, initial['open_findings'])
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 2)
        # Healthy output closes degradation, but an unknown wait is not recovery.
        code, text = self.run_script('unknown', dry=False)
        self.assertEqual(code, 1, text)
        after = json.loads(self.state.read_text(encoding='utf-8-sig'))
        self.assert_open_findings_equal(after['open_findings'], {key: initial['open_findings'][key]})
        messages = '\n'.join(p.read_text() for p in self.outbox.glob('*.txt'))
        self.assertIn('recovered: [', messages)
        self.assertIn('gatekeeper-degraded:', messages)
        self.assertNotIn('recovered: needs-you:', messages)
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 3)
        code, text = self.run_script('none', dry=False)
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8-sig'))['open_findings'], {})
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 4)

    def test_precise_linear_timestamps_render_human_ages_without_losing_thresholds(self):
        now = datetime(2026, 11, 14, 23, 22, 41, 987654, tzinfo=timezone.utc)
        rows = [row('WD-547', [dict(body='**REVIEW REPORT — NEEDS HUMAN**',
                                  createdAt='2026-10-21T23:22:47.333Z')]),
                row('WD-628', [dict(body='BLOCKED: PRIVATE',
                                   createdAt=(now - timedelta(seconds=241565.123)).isoformat())]),
                row('WD-700', [dict(body='BLOCKED: PRIVATE',
                                   createdAt=(now - timedelta(seconds=14430.456)).isoformat())])]
        wire = g.needs_you_status(rows, now).removeprefix('needs-you: ')
        code, text = self.run_script(wire, dry=False)
        self.assertEqual(code, 1, text)
        messages = '\n'.join(p.read_text() for p in self.outbox.glob('*.txt'))
        for expected in ('WD-547=23d(parked) (keep or drop?)',
                         'WD-628=2d(blocked-question)', 'WD-700=4h(blocked-question)'):
            self.assertIn(expected, messages)
            self.assertIn('needs-you: ' + expected, text)
        self.assertNotRegex(messages, r'=[0-9]{4,}s\(')
        self.assertNotIn('PRIVATE', messages)

    def test_actual_persistence_delivery_failure_retry_and_no_number_churn(self):
        value = 'WD-1=5h(blocked-question)'
        code, text = self.run_script(value, dry=False)
        self.assertEqual(code, 1, text)
        state = json.loads(self.state.read_text(encoding='utf-8-sig'))
        stamp = state['needs_you_last_digest_at']
        self.assertIsNotNone(stamp)
        self.assertEqual(list(state['open_findings']), ['needs-you|WD-1|blocked-question'])
        messages = list(self.outbox.glob('*.txt'))
        self.assertEqual(len(messages), 2)  # immediate finding and separate daily list
        # Deterministically exercise PowerShell's seven-to-six fractional-digit
        # round trip instead of waiting for the clock to end in a zero.
        stamp = datetime.fromisoformat(stamp).replace(microsecond=571782).isoformat(timespec='microseconds')
        stamp = stamp[:-6] + '0' + stamp[-6:]
        state['needs_you_last_digest_at'] = stamp
        self.seed(**state)
        code, text = self.run_script('WD-1=6h(blocked-question)', dry=False)
        self.assertEqual(code, 1, text)
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 2)
        saved = json.loads(self.state.read_text(encoding='utf-8-sig'))['needs_you_last_digest_at']
        self.assertEqual(datetime.fromisoformat(saved), datetime.fromisoformat(stamp))
        # Force only the list due, and fail the existing outbox sender without invoking a CLI.
        state = json.loads(self.state.read_text(encoding='utf-8-sig'))
        prior = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        state['needs_you_last_digest_at'] = prior
        self.seed(**state)
        blocked = self.root / 'not-a-directory'
        blocked.write_text('fixture')
        for _ in range(2):
            code, text = self.run_script('WD-1=6h(blocked-question)', dry=False, outbox=blocked)
            self.assertEqual(code, 3, text)
            saved = json.loads(self.state.read_text(encoding='utf-8-sig'))['needs_you_last_digest_at']
            self.assertEqual(datetime.fromisoformat(saved), datetime.fromisoformat(prior))
        code, text = self.run_script('WD-1=6h(blocked-question)', dry=False)
        self.assertEqual(code, 1, text)
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 3)
        saved = json.loads(self.state.read_text(encoding='utf-8-sig'))['needs_you_last_digest_at']
        self.assertGreater(datetime.fromisoformat(saved), datetime.fromisoformat(prior))
        self.run_script('none', dry=False)
        self.assertEqual(len(list(self.outbox.glob('*.txt'))), 4)  # existing liveness recovery only
        self.assertEqual(sum('Factory needs you' in p.read_text() for p in self.outbox.glob('*.txt')), 2)


if __name__ == '__main__':
    unittest.main()
