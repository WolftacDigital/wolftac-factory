# Factory gatekeeper — free eligibility pre-checks for the worker loops

Worker cost must scale with **work**, not with **ticks**. Before the gatekeeper,
three agent workers on 5-minute intervals paid full model fare ~700×/day mostly
to discover the queue was idle — which exhausted the Codex plan's usage window
on 2026-08-12 and stalled real work behind idle burn (the WD-357 incident). The
gatekeeper inverts the economics: a zero-token script tick checks Linear, and
the paid agents run only when there is something to do.

## How it works

- `deploy/factory-gatekeeper.py` (canonical, this repo) is installed to
  `%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py` and runs as a Hermes
  `no_agent` cron job every 5 minutes. `no_agent` script ticks make **no model
  calls**; the script's only I/O is one cheap Linear GraphQL count per lane,
  a read of `cron\jobs.json`, its own state sidecar, and — when a lane is
  eligible — one detached `hermes cron run <job-id>` launch.
- Lane probes: build ← any `Todo` in the Factory Queue; review ← any
  `In Review`; integrate ← any `Approved`. These are deliberate **supersets**
  of true eligibility (blocked/parked filtering stays in the skills): a false
  positive costs one agent tick; a false negative would strand the queue.

### Why dispatch is detached (WD-357 D1)

`hermes cron run` executes **synchronously** — `_try_dispatch_background_run`
returns `None` for a nested cron run and falls back to inline execution. A real
worker pass takes minutes. Waiting on it would stall the 5-minute tick, and
capping it with a timeout would **SIGKILL a live worker mid-agent**, orphaning
an issue already moved to In Progress and skipping the remaining lanes. The
dispatch is therefore launched detached (`DETACHED_PROCESS |
CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session` elsewhere), never
waited on, with child output captured to
`%LOCALAPPDATA%\hermes\cron\gatekeeper-logs\<lane>-<timestamp>.log`.

### In-flight guard (WD-357 D2)

Hermes' own re-entry protection is not sufficient here: the in-memory guard is
process-local (and `hermes cron run` executes in the CLI's own process), and the
persisted `fire_claim` TTL is 300 s — which Hermes' docstring notes is
"routinely outlived by real jobs." Two agents on one lane would claim the same
issue. The gatekeeper therefore keeps its own marker per lane in
`%LOCALAPPDATA%\hermes\cron\gatekeeper-state.json`, recording the dispatch time
and the job's `last_run_at` as observed at dispatch. A lane is skipped while
marked; the marker clears when `last_run_at` advances (reported
`confirmed-ran`) or after `INFLIGHT_TTL_MINUTES` (90, reported
`no-run-observed`) so a lost dispatch cannot wedge a lane.

### Honest reporting (WD-357 D4)

A detached launch is reported as `dispatched(pid=…)` — never "triggered", which
would overstate it, and never counted as a run. The *following* tick reports
`confirmed-ran` or `no-run-observed` by comparing `last_run_at`. Tick logs are
therefore a truthful basis for the soak counts.

### Fail-open and lane isolation

Unreadable `.env`, network failure, or an unparseable response dispatches the
lane and prints why — the gatekeeper must never fail quiet, since quiet failure
is the disease it treats (same doctrine as the WD-345 watchdog, which also
monitors this job's liveness). Every lane runs inside its own `try`, so one
lane's failure can never prevent later lanes from being probed. The in-flight
guard still applies during fail-open, so a provider outage cannot stack
dispatches on a lane that is already working.

The three agent jobs keep slow **safety-net** interval schedules (every 2 h) so
the conveyor still self-heals if the gatekeeper dies entirely. The cron
scheduler sanitizes script subprocess environments, so the script reads
`LINEAR_FACTORY_BOT_TOKEN` from the Hermes `.env` **file**; the token never
appears in output, and the script is read-only against Linear.

## Install / wiring

1. `Copy-Item deploy\factory-gatekeeper.py "$env:LOCALAPPDATA\hermes\scripts\factory_gatekeeper.py"`
2. Back up `%LOCALAPPDATA%\hermes\cron\jobs.json`, then add the gatekeeper job
   (`no_agent: true`, `script: factory_gatekeeper.py`, interval 5 m,
   `deliver: local`) and relax the three worker jobs to 120 m — setting **both**
   `schedule.minutes` and `schedule_display`, since `hermes cron list` renders
   the stored display string and a stale one makes the mandated post-update
   verification lie (WD-357 D3).
3. `hermes gateway restart` in an idle window; confirm with `hermes cron list`
   (expect four `[active]` jobs and `Schedule: every 2h` on the workers).

Dry run any time (no dispatch): `python deploy\factory-gatekeeper.py --dry-run`
Scratch testing: `--env-path`, `--jobs-path`, `--state-path`, `--job-map`, and
the test-only `--exec-override` point every side effect away from the live
install.

## Rollback

Restore the `jobs.json` backup (worker cadence returns, gatekeeper job
disappears), delete `%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py`, the
state sidecar, and the `gatekeeper-logs` directory, then restart the gateway.
The agent workers were never modified beyond their schedules; no skill, policy,
or approval-gate content is involved.
