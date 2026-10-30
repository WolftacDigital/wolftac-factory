# Factory gatekeeper — zero-token queue change detection

Worker cost must scale with **work**, not with **ticks**. WD-357 introduced a
`no_agent` script to avoid paying model fare for empty queues. WD-380 extends
that protection to non-empty queues that hold blocked or parked work: an
unchanged queue must not dispatch another silent worker on every tick.

## How it works

`deploy/factory-gatekeeper.py` is canonical. The manually installed copy is
`%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py`. It runs as a Hermes
`no_agent` job with **no model calls**. Read the actual interval from the default
profile's `cron\jobs.json`; on AI-MSI it is 30 minutes as of 2026-09-19. This
change does not alter any schedule or worker prompt. The four worker jobs retain
two-hour safety-net schedules, covering time-based eligibility (including
four-hour stale claims), newly reachable repos, and a failed gatekeeper:
`builder/9b7b5987bcd6` (agentic), `builder/1dd429fe03b2` (crm),
`reviewer/8e5c37a23b91` and `integrator/4e7a4d9c5fa4`.
The operator script `deploy/worker-schedules.ps1` plans their reconciliation to
120 minutes; `-Apply` backs up each affected profile once, uses `hermes cron edit`
and verifies read-back. It never writes `jobs.json` directly. A second successful
apply is a no-op. The watchdog's `worker-safety-net` check alerts on intervals
below 120 minutes or non-interval worker schedules.

Each tick queries the Wolftac Digital team's `Factory Queue` issues, paginating
100 issues at a time: build and build-crm = `Todo`, review = `In Review`,
integrate = `Approved`. Each state is fetched once per tick, sharing all pages
and probe failures between its ready lanes. Build lanes then filter that result:
`build` uses `agentic`; `build-crm` uses `Wolftac CRM v2`. A filter matches a
case-insensitive substring of the project name or any label name; a missing
project contributes no match. An issue matching both filters belongs to both
fingerprints. Issues matching neither are not dispatched to a build job (NG-2);
suggest /wolftac-spec for a catch-all lane. Review, integrate and custom
`--job-map` lane names absent from `LANE_FILTERS` are unfiltered.
These remain **superset** lane lists, not full eligibility filters.
BLOCKED/NEEDS-HUMAN parks, Repo reachability and approval verification stay in skills.
The gatekeeper does not claim, comment or otherwise write to Linear or Slack.

### Fingerprint rule

For each issue, canonical sorted JSON includes:

- identifier, state name, assignee ID, sorted label names, project name, priority;
- SHA-256 of description (not the description itself);
- `createdAt` of the newest non-`SWEPT:` comment among the newest 50 comments;
- the sorted `[identifier, state.name]` pairs for all blocking issues.

The lane hash is SHA-256 of those records sorted by identifier, with JSON keys
sorted too. `updatedAt` and `delegate` are deliberately excluded: an integrate
self-sweep must not churn the fingerprint. No comments produces `null`; only
`SWEPT:` comments produces the literal `swept-only` component. The 50-comment
window is intentional. Labels and blocker relations also follow pagination.
Live Linear direction is `inverseRelations`, entries of type `blocks`, taking
`issue.identifier` and `issue.state.name`; outgoing `relations` points the other
way and is not our blocker list. Comments use `first: 50, orderBy: createdAt`
(newest first), and newest selection is independent of response order.

After the in-flight guard:

| Condition | Output / action |
|---|---|
| Empty lane | `idle`, no launch; clear old baseline |
| No valid baseline (new/missing/corrupt state) | `no-baseline -> dispatched(pid=...)` |
| Fingerprint differs | `changed -> dispatched(pid=...)` |
| Fingerprint equal | `unchanged (skip)`; integrate first checks Slack |
| Linear HTTP 401 on any ready lane | Skip build/review; dispatch integrate only for re-mint (subject to in-flight guard) |
| Other Linear probe or env failure | Loud `FAIL-OPEN` dispatch for that lane |

The baseline is recorded **at successful dispatch**, not when a run is later
confirmed. A human's change during a run therefore causes another dispatch.
A productive worker usually changes its queue; the next tick correctly runs
again because each worker takes at most one issue. A silent worker settles.
Failed process launches record neither a baseline nor an in-flight marker.

`cron\gatekeeper-state.json` keeps the existing `lanes[lane]` marker containing
`dispatched_at` and `job_last_run_at`. Sibling `fingerprints[lane]` stores that
lane's dispatched fingerprint, so removing a confirmed marker cannot erase the
baseline. Top-level `approval_dispatched` maps issue identifiers to Slack message
timestamps. Missing/unknown keys need no migration; the old script ignores the
new keys. Do not confuse a dispatch with a completed worker run.

### Linear authentication failure exception

All non-in-flight lanes are probed before any worker is dispatched. An HTTP 401
from any Linear probe rejects the shared token for the whole tick, even if the
build or review probe succeeded earlier. The output is:

```text
build: linear-auth-failed (401) -> skipped
build-crm: linear-auth-failed (401) -> skipped
review: linear-auth-failed (401) -> skipped
integrate: linear-auth-failed (401) -> dispatched(pid=...) for re-mint
```

Integrate uses the normal detached dispatch path. A live integrate marker still
prints `integrate: in-flight (skip)` and prevents a second launch. Skipped ready
lanes record no fingerprint and clear any old baseline, so the first healthy
tick re-evaluates them. No approval is consumed by this recovery dispatch.

The gatekeeper never mints or writes credentials. WD-547 supplies re-minting in
the integrate preflight; until that change is deployed, dispatching integrate
does not itself repair the token. Network errors, timeouts, HTTP 500 and invalid
responses still fail open independently for each non-in-flight lane. A Slack
HTTP 401 remains a Slack-check failure, not a Linear authentication failure.

### Slack approval check and its deliberate fail-open exception

Only an otherwise unchanged integrate lane checks Slack, unless `--no-slack`
is passed. For each Approved issue, find the newest `MERGE PROOF PACK` or
`LANDING PROOF PACK` comment within the fetched window. Extract its Slack
permalink and convert `p<10 digits><6 digits>` to `<10 digits>.<6 digits>`.
Skip missing permalinks and channels different from `slack_channel_id` in the
approval policy. Call only Slack `reactions.get`, at most once per issue per
tick, with `full=true` so the reactor list is complete.

A `rocket` whose users include `approver_slack_user_id` causes
`integrate: slack-approval WD-N -> dispatched(pid=...)`, unless that issue/ts is
already recorded in `approval_dispatched`. Record it only after a successful
launch. One tick consumes at most one approval; other approvals remain for later
passes. A newer proof pack with a new ts can dispatch again. Other users or
reactions do nothing. The integrate worker still independently verifies every
approval gate; the gatekeeper **never approves or merges**. Typed `APPROVE MERGE`
comments already change the Linear fingerprint and need no Slack check.

Missing `SLACK_FACTORY_BOT_TOKEN`, missing/invalid policy, HTTP errors and
`ok:false` print `integrate: slack-check-failed (<reason>)` every affected tick,
**do not dispatch on that account**, and do not stop other lanes. This is the
separate exception to fail-open: failing open on Slack outages would
restore the very per-tick cost this change eliminates. The two-hour safety net
and typed-comment path bound the approval delay. Error diagnostics exclude
remote response bodies and exception text that might echo credentials.

### Detached dispatch, in-flight guard and lane isolation

`hermes cron run` is synchronous for nested runs. The gatekeeper launches it
**detached** (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows,
`start_new_session` elsewhere), never waits, and never kills it on a timeout.
Child output goes to `cron\gatekeeper-logs\<lane>-<timestamp>.log` under Hermes
home. Each lane has its own exception boundary; one failure cannot starve others.

### Per-lane worker profiles (exec team, 2026-10-20)

Each lane's job lives in its own Hermes profile, one bot per stage:

| Lane | Linear state | Job | Profile (bot) |
|---|---|---|---|
| build | Todo | `9b7b5987bcd6` | `builder` (Mason) |
| build-crm | Todo | `1dd429fe03b2` | `builder` (Mason) |
| review | In Review | `8e5c37a23b91` | `reviewer` (Quinn) |
| integrate | Approved | `4e7a4d9c5fa4` | `integrator` (Harper) |

The gatekeeper dispatches `hermes -p <profile> cron run <job>` and reads that
lane's `last_run_at` from `profiles\<profile>\cron\jobs.json`. The gatekeeper job
itself, its state file and the factory bot token stay in the default profile.
A `--job-map` entry may be `[state, jobId]` (default profile) or
`[state, jobId, profile]`; profile names must match `[a-z0-9][a-z0-9_-]*`, and a
bad entry is a `LANE-ERROR` for that lane only. `--jobs-path`, when given,
overrides the jobs file for every lane (scratch testing).

The `build` state key keeps its existing meaning and in-flight marker.
`build-crm` has its own fingerprint and marker, and reads its own job ID from
the shared builder jobs file. Existing state needs no migration: a non-empty
CRM lane without a fingerprint reports `no-baseline` on its first ready tick.

A live in-flight marker produces `in-flight (skip)` before any Linear or Slack
probe for that lane, regardless of queue changes. As before, a changed job
`last_run_at` clears the marker and prints `confirmed-ran`; it preserves the
fingerprint. After 90 minutes without a run, `no-run-observed` clears both marker
and fingerprint, making a non-empty lane dispatch on the same tick. A malformed
marker also cannot wedge a lane indefinitely. The guard applies to fail-open
paths as well. The watchdog reads scheduler liveness, not these decision strings.

Both `LINEAR_FACTORY_BOT_TOKEN` and `SLACK_FACTORY_BOT_TOKEN` are read from the
Hermes `.env` **file**, never argv or process environment (the scheduler sanitizes
subprocess environments). No token is written to output, state or logs.

## Daily Done-without-integration audit (WD-580)

After all lane dispatch decisions, an independent read-only audit checks team `WD`,
label `Factory Queue`, state `Done`. It checks only for an **INTEGRATION REPORT**
heading at the beginning of a comment (optional Agent line / Markdown heading),
not its author or other stage evidence. A mid-text mention does not count.
Issues page at 50 and comments at 100, exhausting both; repeated cursors fail safely.
The existing token reader and 20-second Linear request timeout are reused.

Constants: `AUDIT_INTERVAL_HOURS = 24`, `AUDIT_FIRST_WINDOW_DAYS = 7`,
`AUDIT_GRACE_MINUTES = 60`, `AUDIT_OVERDUE_HOURS = 48`.
State lives only in the top-level `audit` key: `covered_until`, `last_ok_at`,
`pending_since` (UTC ISO timestamps or absent), and `found` (issue IDs).
No key means never run. The first window starts seven days ago; later windows
start at `covered_until`. The exclusive end is now minus 60 minutes, allowing
integration reports to arrive after Done. Success advances the checkpoint and
clears pending failure time. Failure retains findings/checkpoint and retries next
tick, setting `pending_since` only on a first failure with no previous success.
This is a windowed audit, not a persistent per-issue acknowledgement registry.

Every tick includes an `audit:` element so the watchdog's newest-file reader can
observe the stored result between daily queries:

```text
audit: done-without-integration: WD-371,WD-413 (window <start>..<end>)
audit: clean (window <start>..<end>)
audit: done-without-integration: WD-371,WD-413 (last audit <last_ok_at>)
audit: clean (last audit <last_ok_at>)
audit: failed (<safe error reason>)
audit: overdue (last ok <timestamp or never>)
audit: skipped (linear-auth-failed)
```

Failure/overdue also repeats the stored nonempty findings as a second element.
Overdue means at least 48 hours since last success (or first pending failure).
A lane-probe 401 skips the audit entirely. Audit failure never changes lane
decisions, `lanes`, `fingerprints`, `approval_dispatched`, or the exit code.

- `--no-audit`: no audit query or output element.
- `--audit-only`: no lane probe or dispatch; saves only audit changes alongside
  existing state, unless dry-run.
- `--audit-since <ISO-8601 with timezone>`: force a window start even when not due.
  Dates without a timezone exit 2; a start beyond the grace boundary fails without
  advancing state.
- `--dry-run`: runs due audits but writes nothing, appending ` (dry-run)` to each
  audit element.

Build/review run fake HTTP tests only, never the live token reader. After approved
merge and the normal backup/manual script copy, Jonathan may verify the live
GraphQL schema with this read-only backfill (no job or interval change):

```powershell
python "$env:LOCALAPPDATA\hermes\scripts\factory_gatekeeper.py" --dry-run --audit-only --audit-since 2026-09-01T00:00:00Z
```

Expected historical findings include WD-371 and WD-413, but not WD-424, WD-437,
WD-461 or WD-549, which have reports. Other IDs require inspection. Synthetic
tests do not prove the live schema. An operator-only non-dry backfill persists
the result for the next scheduled watchdog alert. Rollback ignores the added
audit key; preserve backups and do not change scheduler settings.

## Install / wiring — operator only, after approved merge

This remains a **manual copy**, not a new `deploy.ps1` installation feature.
Build and review do not install, launch real workers, restart services or change
scheduler files. The isolated `factory-integrate` profile is not in scope.

1. Back up the installed `scripts\factory_gatekeeper.py` and
   `cron\gatekeeper-state.json`; retain rollback artifacts.
2. From the merged repo, copy both files:
   ```powershell
   Copy-Item deploy\factory-gatekeeper.py "$env:LOCALAPPDATA\hermes\scripts\factory_gatekeeper.py"
   Copy-Item deploy\approval-policy.json "$env:LOCALAPPDATA\hermes\scripts\approval-policy.json"
   ```
3. Confirm existing gatekeeper job `1a9fb621f161` still references
   `factory_gatekeeper.py` with `no_agent: true`; confirm the existing interval
   in `cron\jobs.json`; do not change the gatekeeper schedule.
   After merge, Jonathan confirms the 120-minute worker safety net, runs
   `pwsh deploy/worker-schedules.ps1` to inspect the four PLAN/OK lines, then
   `pwsh deploy/worker-schedules.ps1 -Apply` and repeats it to confirm four OKs.
   Apply worker schedules before updating the watchdog checkout, or its new
   `worker-safety-net` check will alert on the old five-minute schedules.
   Each changed profile gets `jobs.json.bak-<yyyyMMdd-HHmmss>-pre-safety-net`.
   Missing/unparseable jobs or non-factory names refuse the whole operation;
   CLI failure or read-back mismatch exits 1. No gateway restart is required.
   Build/review runs must always supply a scratch `-HermesHome` and the fixture
   `-HermesCommand deploy/tests/fixtures/fake-hermes.ps1`, never the live CLI.
4. Inspect the next three real gatekeeper outputs under
   `cron\output\1a9fb621f161\`: the first non-empty lane without a baseline
   dispatches once; subsequent confirmed, unchanged lanes skip. This soak is a
   post-merge operator step, not build/review acceptance proof.

The default policy path is `approval-policy.json` beside the script. Override it
with `--approval-policy <path>`; `--no-slack` explicitly disables Slack checks.
Existing `--env-path`, `--jobs-path`, `--state-path`, `--job-map` and test-only
`--exec-override` remain available.

### Safe build/review verification

- Offline: `python -m unittest discover -s deploy/tests -v`. CI runs the same
  suite with `python3` on every PR, alongside parity, semantics and LF checks.
- Live read-only: `python deploy\factory-gatekeeper.py --dry-run`. Same decisions,
  `dispatched(dry-run)` instead of a pid; no process launch or state/log writes.
  Hash the real state before/after and ensure token values are absent from output.
- Live-data scratch test: make a scratch copy of `jobs.json` and use a new scratch
  state path with `--exec-override '["python", "-c", "pass"]'`. Pass BOTH scratch
  `--jobs-path` and `--state-path`. For complete log isolation, set `HERMES_HOME`
  only for the test subprocess to a scratch directory, supplying the real
  `.env` path via `--env-path` and the repo policy via `--approval-policy`.
  Run once, bump only scratch worker `last_run_at` fields, and repeat within two
  minutes: expect `confirmed-ran` then `unchanged (skip)` on unchanged lanes.
  Integrate may instead report Slack approval dispatch or a Slack check failure.
  Hash real state, real jobs and installed script before/after; all must match.

Never test without either `--dry-run` or the complete scratch/no-op combination.

## Rollback

After operator approval, restore the pre-change `factory_gatekeeper.py` from the
backup or `git show <pre-change-sha>:deploy/factory-gatekeeper.py` over the
installed copy, and remove the newly copied `approval-policy.json`. The previous
script ignores `fingerprints` and `approval_dispatched`, so no state migration or
schedule edit is needed. Keep backups and proof; do not delete log history.
Resetting `gatekeeper-state.json` is safe if explicitly chosen by the operator:
the next tick dispatches each non-empty lane once. The worker safety nets remain
unchanged. No skills, bot helper, secrets or approval rules change.
