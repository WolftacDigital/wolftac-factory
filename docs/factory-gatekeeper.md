# Factory gatekeeper — free eligibility pre-checks for the worker loops

Worker cost must scale with **work**, not with **ticks**. Before the gatekeeper,
three agent workers on 5-minute intervals paid full model fare ~700×/day mostly
to discover the queue was idle — which exhausted the Codex plan's rolling usage
window on 2026-08-12 and stalled real work behind idle burn (the WD-357
incident). The gatekeeper inverts the economics: a zero-token script tick
checks Linear, and the paid agents run only when there is something to do.

## How it works

- `deploy/factory-gatekeeper.py` (canonical, this repo) is installed to
  `%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py` and runs as a Hermes
  `no_agent` cron job every 5 minutes. `no_agent` script ticks make **no model
  calls**; the script's only I/O is one cheap Linear GraphQL count per lane and,
  when a lane is eligible, one `hermes cron run <job-id>` invocation (which goes
  through the scheduler's locked job store — the script never edits
  `jobs.json` itself).
- Lane probes: build ← any `Todo` in the Factory Queue; review ← any
  `In Review`; integrate ← any `Approved`. These are deliberate **supersets**
  of true eligibility (blocked/parked filtering stays in the skills): a false
  positive costs one agent tick; a false negative would strand the queue.
- **Fail-open:** unreadable `.env`, network failure, or an unparseable response
  triggers **all** lanes and prints why. The gatekeeper must never fail quiet —
  quiet failure is the disease it treats (same doctrine as the WD-345 watchdog,
  which also monitors this job's own liveness).
- The three agent jobs keep slow **safety-net** interval schedules (every 2 h)
  so the conveyor still self-heals if the gatekeeper dies entirely.
- The cron scheduler sanitizes script subprocess environments, so the script
  reads `LINEAR_FACTORY_BOT_TOKEN` from the Hermes `.env` **file**; the token
  never appears in output, and the script is read-only against Linear.

## Install / wiring

1. `Copy-Item deploy\factory-gatekeeper.py "$env:LOCALAPPDATA\hermes\scripts\factory_gatekeeper.py"`
2. Back up `%LOCALAPPDATA%\hermes\cron\jobs.json`, then add the gatekeeper job
   (`no_agent: true`, `script: factory_gatekeeper.py`, interval 5 m,
   `deliver: local`) and relax the three worker jobs' intervals to 120 m.
3. `hermes gateway restart` in an idle window; confirm with `hermes cron list`
   (expect four `[active]` jobs).

Dry run any time (no triggers): `python deploy\factory-gatekeeper.py --dry-run`
Scratch testing: `--env-path` and `--job-map '{"build":["Todo","<scratch-id>"]}'`
point every side effect away from the live install.

## Rollback

Restore the `jobs.json` backup (worker intervals return to their previous
cadence, gatekeeper job disappears), delete
`%LOCALAPPDATA%\hermes\scripts\factory_gatekeeper.py`, restart the gateway.
The agent workers were never modified beyond their schedules; no skill,
policy, or approval-gate content is involved.
