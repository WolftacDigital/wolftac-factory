# Bot-only integrate runtime (WD-281)

The `/wolftac-integrate` stage must run in a runtime whose ONLY Linear capability
is the Wolftac Factory Bot GraphQL credential. This document is the recipe; the
executable gate is `deploy/integrate-preflight.py`, which every integrate
invocation runs FIRST and which fails closed (non-zero exit, zero side effects)
when the runtime is not correctly isolated.

## The isolated Hermes profile

Create (or designate) a Hermes profile for the integrate worker with:

1. **No Linear MCP.** The profile's `config.yaml` must contain NO
   `mcp_servers.linear` entry (or an explicitly `enabled: false` one). The
   preflight scans the config and refuses to proceed if an enabled entry exists
   — a runtime that can also write Linear as the human operator is dual-capable
   and violates the approval gate's provenance guarantee (WD-276 review, round 4).
2. **Bot credentials via the profile env only.** The profile's `.env` carries:
   - `LINEAR_FACTORY_APP_ID` / `LINEAR_FACTORY_APP_SECRET` — the Linear OAuth
     application ("Wolftac Factory Bot", client-credentials grant enabled)
   - `LINEAR_FACTORY_BOT_TOKEN` — the current app-actor token (managed by the
     preflight; see re-mint below)
   Values are never printed, logged, or committed anywhere.
3. **All Linear I/O via GraphQL with the bot token** — `POST
   https://api.linear.app/graphql`, `Authorization: Bearer $LINEAR_FACTORY_BOT_TOKEN`.
   Comments and state changes made this way are authored by "Wolftac Factory Bot"
   (`b8fece8e-8ec5-4125-b4b2-d7fa882db137`), which is what makes human approvals
   distinguishable: the merge gate accepts only `APPROVE MERGE` comments whose
   author id differs from the worker's viewer id.

## Preflight (REQUIRED first step of every integrate invocation)

```
python deploy/integrate-preflight.py [--env <path>] [--config <path>] [--expected-bot-id <id>]
```

Defaults: `%LOCALAPPDATA%\hermes\.env`, `%LOCALAPPDATA%\hermes\config.yaml`, and
the known factory bot id. Override via `FACTORY_BOT_ENV`, `FACTORY_PROFILE_CONFIG`,
`LINEAR_FACTORY_BOT_ID` env vars or flags when the worker profile lives elsewhere.

Exit codes: `0` proceed · `2` credentials missing/unmintable · `3` an enabled
Linear MCP is wired into the profile (dual-capable runtime) · `4` viewer is not
the factory bot · `5` env/config unreadable. Any non-zero exit → the worker
stops without claiming, commenting, or touching anything.

## Token re-mint

App-actor tokens from the client-credentials grant expire (~30 days). The
preflight re-mints automatically when the stored token no longer authenticates,
using the app id/secret, and persists the new token back to the env file. It
prints only status and `expires_in` — never credential values. If re-mint fails
(revoked app, bad secret), the preflight fails closed with exit 2.

## Creating the scheduled worker (operator-executed — spend gate)

Verified against `hermes cron create --help` on 2026-07-17:

```
hermes cron create --name "Wolftac Factory Integrate Worker" \
  --skill wolftac-integrate \
  --workdir D:\Wolftac\MissionControl \
  --deliver local \
  "every 10m" \
  "Run one wolftac-integrate tick: run deploy/integrate-preflight.py FIRST and stop immediately on any non-zero exit; otherwise work at most one Approved Factory Queue issue per the skill; merge only on a valid APPROVE MERGE; if nothing is awaiting integration or any precondition fails, exit cleanly."
```

Preconditions before creating it:
1. The isolated profile exists per this document and the preflight exits 0 under it.
2. Pin the job's provider/model immediately after creation by editing
   `%LOCALAPPDATA%\hermes\cron\jobs.json` (back it up first): set `provider`,
   `model`, and refresh the snapshots — `hermes cron edit` has no model flags,
   and unpinned jobs skip every run after any global model drift.

## Honest residual trust

The preflight proves the profile does not WIRE a human Linear path; it cannot
prove what an arbitrary process could do with credentials found elsewhere on
the host. Isolation is by profile construction; the preflight verifies that
construction on every tick and the Linear audit trail (comment author ids)
verifies it after the fact.
