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
   `LINEAR_FACTORY_APP_SECRET`, `LINEAR_FACTORY_BOT_TOKEN` — PLUS blank
   overrides that neutralize any human Linear-capable credentials inherited
   from the root env or shell (`LINEAR_API_KEY=`, `LINEAR_OAUTH_TOKEN=`,
   `COMPOSIO_API_KEY=`). The preflight exits 3 if any such credential is
   non-empty, so a profile missing these overrides cannot start work. Values
   are never printed, logged, or committed. Note this credential scan is
   defense in depth only — see the executing-runtime attestation below for
   why it cannot be the primary gate.
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
Exit codes: `0` proceed · `2` credentials missing/unmintable · `3` the executing
runtime is not this profile, or a Linear-capable transport token was found in
profile yaml · `4` viewer is not the pinned factory bot · `5` profile root/env
unreadable.

### Executing-runtime attestation (the primary gate)

The preflight requires `HERMES_HOME` to **be** the scanned profile root. This is
a positive attestation, and it replaced an earlier credential blacklist that
checked only `LINEAR_API_KEY` / `LINEAR_OAUTH_TOKEN` / `COMPOSIO_API_KEY`.

That blacklist was unsound: an agent session reaching Linear over **MCP** holds
no Linear environment variable at all, so it passed the gate while retaining a
full human write path — the exact defect this issue exists to close. No
blacklist can cover transports it has never heard of; only "you are the isolated
profile" is a claim a foreign runtime cannot make.

Hermes makes this attestation meaningful: cron is per-profile by design, so a
profile's jobs are executed under that profile's home
(`cron/scheduler.py::_get_hermes_home`), and `HERMES_HOME` is one of Hermes'
explicitly global env vars (`agent/secret_scope.py::_GLOBAL_ENV_EXACT`) rather
than a per-profile secret that a multiplex gateway could confuse.

**Consequence:** running the preflight by hand from an operator shell or an
agent session correctly exits 3. That is the gate working, not a bug — the green
path is observed in the worker's own cron output, not from a human terminal.

**Residual trust (stated honestly):** this proves the runtime is the isolated
profile and that the profile's on-disk config wires no Linear-capable transport.
It cannot prove what an arbitrary process elsewhere on the host could do with
credentials it finds. The Linear comment audit trail (author ids) remains the
after-the-fact check on conduct.
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
2. **Create the scheduled worker** — flags re-verified against
   `hermes cron create --help` on 2026-08-08 (`--name`, `--skill`, `--workdir`,
   `--deliver`, plus the `schedule` and `prompt` positionals all still exist):

```
hermes -p factory-integrate cron create --name "Wolftac Factory Integrate Worker" \
  --skill wolftac-integrate \
  --workdir D:\Wolftac\MissionControl \
  --deliver local \
  "every 10m" \
  "Run one wolftac-integrate tick: FIRST run 'python C:/Users/logan_vwagmmf/AppData/Local/hermes/profiles/factory-integrate/deploy/integrate-preflight.py' and stop immediately on any non-zero exit; otherwise work at most one Approved Factory Queue issue per the skill; merge only on a valid APPROVE MERGE; if nothing is awaiting integration or any precondition fails, exit cleanly."
```

The prompt's preflight path is the **profile-deployed copy** (shipped by
`deploy/deploy.ps1` into `profiles\factory-integrate\deploy\`), NOT a repo
checkout or review worktree path: worktrees get deleted and branch paths die at
merge, which is exactly how the round-4 scheduled worker broke. The deployed
copy survives all of that and stays in sync with the repo on every deploy.
To repair an existing job's prompt or workdir, use the supported CLI editor and
read it back:

```
hermes -p factory-integrate cron edit <job-id> --prompt "<the prompt above>" --workdir D:\Wolftac\MissionControl
hermes -p factory-integrate cron list
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

   Verify the readback shows both fields. Note `cron create --help` describes
   `--model` as "user-owned; the agent's cronjob tool cannot set this" — that
   wording covers job *creation*; the `cronjob` tool's `update` action does set
   provider/model, which is why pinning is a post-creation step. The live job
   (`934f17571eb0`) reads back `openai-codex` / `gpt-5.6-sol`, confirming the
   route works. Offline fallback (no session
   possible): `python D:\Wolftac\wolftac-factory\deploy\pin-cron-model.py
   --job-name "Wolftac Factory Integrate Worker" --provider openai-codex
   --model gpt-5.6-sol` — backup + atomic write + readback, but prefer the
   control-plane route above.
4. The profile's scheduler/gateway must be running for cron ticks to fire —
   operator-managed, same as the existing factory workers.

## Honest residual trust

The preflight proves three things: the executing runtime IS the isolated
profile (`HERMES_HOME` attestation), the profile's on-disk configuration wires
no Linear-capable transport, and the runtime's credential is the bot. It cannot
prove what an arbitrary process could do with credentials found elsewhere on
the host. Isolation is by profile construction; the preflight
verifies that construction on every tick, and the Linear comment audit trail
(author ids) verifies conduct after the fact.
