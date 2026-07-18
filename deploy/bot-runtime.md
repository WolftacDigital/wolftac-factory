# Bot-only integrate runtime (WD-281)

The `/wolftac-integrate` stage must run in a runtime whose ONLY Linear capability
is the Wolftac Factory Bot GraphQL credential. The runtime is the dedicated
Hermes profile **`factory-integrate`**; the executable gate is
`deploy/integrate-preflight.py`, which every integrate invocation runs FIRST and
which fails closed (non-zero exit, zero side effects) when the runtime is not
correctly isolated.

## The `factory-integrate` profile (created 2026-07-17, WD-281)

Created with `hermes profile create factory-integrate` (fresh — no clone, so
nothing inherited from the human operator's profile). Root:
`%LOCALAPPDATA%\hermes\profiles\factory-integrate\`. Provisioned with:

1. **`config.yaml`** — model pin only (`openai-codex` / `gpt-5.6-sol`), and by
   design NO `mcp_servers` block of any kind. The preflight scans EVERY
   `*.yaml`/`*.yml` in the profile root and rejects any occurrence of the tokens
   `linear` or `composio` — quoted, inline, block, or commented forms all count
   (fail-closed; Composio counts because it can proxy Linear tools). Keep those
   tokens out of this profile's yaml entirely, including comments.
2. **`.env`** — the bot credentials only: `LINEAR_FACTORY_APP_ID`,
   `LINEAR_FACTORY_APP_SECRET`, `LINEAR_FACTORY_BOT_TOKEN`. Values are never
   printed, logged, or committed.
3. **`skills\wolftac-integrate\`** — the Hermes integrate skill (deployed by
   `deploy.ps1`, which targets this profile's skills directory).

All Linear I/O in this runtime goes through GraphQL with the bot token
(`POST https://api.linear.app/graphql`, `Authorization: Bearer $LINEAR_FACTORY_BOT_TOKEN`).
Writes are authored by "Wolftac Factory Bot" — the identity split that makes
human approvals distinguishable. The expected bot id is PINNED inside the
preflight script and is not overridable by flag or environment; rotating the bot
identity is a reviewed change to that constant.

## Preflight (REQUIRED first step of every integrate invocation)

```
python D:\Wolftac\wolftac-factory\deploy\integrate-preflight.py
```

The default `--profile-root` is the `factory-integrate` profile; the scanned
root and file list are printed so the audit trail records what was checked.
Exit codes: `0` proceed · `2` credentials missing/unmintable · `3` a
Linear-capable transport token found in profile yaml (dual-capable runtime) ·
`4` viewer is not the pinned factory bot · `5` profile root/env unreadable.
Any non-zero exit → the worker stops without claiming, commenting, or touching
anything.

## Token re-mint

App-actor tokens expire (~30 days). When the stored token no longer
authenticates, the preflight mints a fresh one via the client-credentials
grant, VERIFIES the fresh token resolves to the pinned factory bot id, and only
then persists it to the profile `.env`. A fresh token that resolves to any
other identity is discarded unpersisted (exit 4). Mint failure → exit 2. Output
contains status and `expires_in` only — never credential values.

## Remaining operator steps (spend/infrastructure gates)

1. **Inference keys for the worker profile:** run `factory-integrate setup` (or
   let the profile inherit provider keys from the shell environment) so the
   profile can run its pinned model. The Linear side needs nothing more.
2. **Create the scheduled worker** — verified against
   `hermes -p factory-integrate cron create --help` on 2026-07-17:

```
hermes -p factory-integrate cron create --name "Wolftac Factory Integrate Worker" \
  --skill wolftac-integrate \
  --workdir D:\Wolftac\MissionControl \
  --deliver local \
  "every 10m" \
  "Run one wolftac-integrate tick: FIRST run 'python D:\Wolftac\wolftac-factory\deploy\integrate-preflight.py' and stop immediately on any non-zero exit; otherwise work at most one Approved Factory Queue issue per the skill; merge only on a valid APPROVE MERGE; if nothing is awaiting integration or any precondition fails, exit cleanly."
```

   The preflight is invoked by ABSOLUTE path so the Mission Control workdir
   never matters.
3. **Pin the job's provider/model immediately after creation** through the
   documented Hermes control plane — the agent's own `cronjob` tool, invoked
   from a one-shot session under the isolated profile (proven live 2026-07-18,
   session `20260718_105210_c45f27`):

```
hermes -p factory-integrate chat -q "Use the cronjob tool: action='update', job_id='<JOB_ID>', provider='openai-codex', model='gpt-5.6-sol'. Then action='list' and report the job's provider and model fields verbatim."
```

   Verify the readback shows both fields. Offline fallback (no session
   possible): `python D:\Wolftac\wolftac-factory\deploy\pin-cron-model.py
   --job-name "Wolftac Factory Integrate Worker" --provider openai-codex
   --model gpt-5.6-sol` — backup + atomic write + readback, but prefer the
   control-plane route above.
4. The profile's scheduler/gateway must be running for cron ticks to fire —
   operator-managed, same as the existing factory workers.

## Honest residual trust

The preflight proves the profile's on-disk configuration wires no
Linear-capable transport and that the runtime's credential is the bot; it
cannot prove what an arbitrary process could do with credentials found
elsewhere on the host. Isolation is by profile construction; the preflight
verifies that construction on every tick, and the Linear comment audit trail
(author ids) verifies conduct after the fact.
