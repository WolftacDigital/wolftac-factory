# Wolftac Factory

This is your `/wolftac-spec` → `/wolftac-build` → `/wolftac-review` software factory:
a repeatable pipeline where one agent turns an idea into a detailed spec, another
builds it and proves it works, and a third independently re-verifies before anything
is marked done. It runs identically across **Claude Code, Claude Desktop, Codex, and
Hermes** — any agent on any of those platforms can spec, build, or review, because
the whole hand-off happens through one shared Linear queue, not through this repo.

**This repo is the source of truth for the skill files themselves.** The copies
under `~/.claude/skills`, `~/.codex/prompts`, and `%LOCALAPPDATA%\hermes\skills` (on
whichever machines run those agents) are deploy targets — edit here, then push the
change out to wherever it needs to run.

## What's in the box

```
AGENTS.md              Read this first if you're an agent being onboarded
README.md              This file — for you, the human
canonical/              Claude Code skills (the reference implementation)
  wolftac-spec/          spec stage: idea -> Linear issue
  wolftac-build/          build stage: issue -> working, tested code
  wolftac-review/         review stage: independent re-verification, the only stage that marks Done
ports/codex/            Codex custom prompts (same three stages, Codex's format)
ports/hermes/           Hermes skills (same three stages, Hermes' format)
ports/claude-desktop/   Sources for Claude Desktop's uploadable skill bundles
deploy/deploy.ps1       -Check (drift) / -Deploy (push to all local targets) / -Package (zip for handoff)
deploy/hermes-moa-presets.yaml.example   Optional Hermes MoA presets for model-team routing
deploy/linear-mcp.toml.example   Codex config.toml snippet for connecting Linear
```

## Giving this factory to another agent

You have two ways to hand the factory to a Hermes or Codex agent (on this machine,
another machine, or a fresh session) — pick whichever fits where that agent runs.

### Option A — give the repo

Best when the agent has git access (its own SSH key, or you're driving it on a
machine that already has one). The repo is private on GitHub:
`git@github.com:WolftacDigital/wolftac-factory.git`.

Tell the agent (paste this, adjusted to name the platform):

> You're being set up as a build/review/spec agent for the Wolftac software factory.
> Clone `git@github.com:WolftacDigital/wolftac-factory.git`, then read `AGENTS.md` at
> its root and follow the setup instructions for your platform (you're running as
> **Codex** / **Hermes**). Confirm Linear access once you're done.

### Option B — give the files directly

Best for a chat-based agent with no filesystem/git access, or anywhere simplest is
best. Generate a portable zip:

```powershell
pwsh deploy\deploy.ps1 -Package
```

This writes `wolftac-factory-package.zip` to your Downloads folder — the whole repo
minus `.git`, ready to attach or upload. Tell the agent:

> Attached is the Wolftac Factory (`wolftac-factory-package.zip`). Unzip it, read
> `AGENTS.md` at the root, and follow the setup instructions for your platform (you're
> running as **Codex** / **Hermes**). Confirm Linear access once you're done.

Either way, `AGENTS.md` does the actual onboarding — it tells the agent the shared
contract (Linear team, queue label, state machine, spec format) and walks it through
installing itself for whichever platform it's running on. You don't need to explain
any of that yourself.

## Running the factory yourself, on this machine

If you're setting up (or maintaining) the skills locally rather than handing them
off, the deploy tool does it in one step:

```powershell
pwsh deploy\deploy.ps1 -Deploy   # installs canonical skills + all ports, rebuilds Desktop zips
pwsh deploy\deploy.ps1 -Check    # confirms every deployed copy still matches the repo
```

`-Deploy` also detects WSL and installs the canonical skills into the Linux home so
WSL Claude Code sessions get the commands too. **Restart any open agent session
after deploying** — skills are loaded at startup, not picked up live.

One-time setup per platform still applies even after `-Deploy` copies the files:
Codex needs a Linear OAuth login on first use (or `codex mcp login linear`), Hermes
needs Linear MCP/OAuth wired in its profile and a fresh session/gateway reload, and
Claude Desktop needs the `wolftac-*.zip` bundles uploaded as Skills in its settings.

## Hermes MoA model-team routing

The Hermes port can optionally run each factory stage through a Mixture-of-Agents
model preset instead of a single active model. The source skills under `ports/hermes/`
now document the preferred stage presets:

| Stage | Preferred Hermes preset | Acting / aggregator model | Reference advisors |
|---|---|---|---|
| `/wolftac-spec` | `wolftac-factory-spec` | Claude Fable 5 | GPT 5.5, Claude Opus 4.8 |
| `/wolftac-build` | `wolftac-factory-build` | GPT 5.5 | Claude Opus 4.8, Claude Sonnet 5, Gemma 4 31B QAT via LM Studio |
| `/wolftac-review` | `wolftac-factory-review` | GPT 5.5 | Claude Fable 5, Gemma 4 31B QAT via LM Studio |

Use `deploy/hermes-moa-presets.yaml.example` as the non-secret config template. Merge
it into the target Hermes profile's `config.yaml` under `moa.presets` after backing up
the live config. It intentionally contains no API keys or OAuth tokens.

Important: a Hermes skill is an instruction package, not a model switch. The model or
MoA preset is selected before the skill's `SKILL.md` is loaded into the turn. Launch
or switch Hermes to the right preset first, then invoke the matching skill:

```text
/model wolftac-factory-spec --provider moa
/skill wolftac-spec

/model wolftac-factory-build --provider moa
/skill wolftac-build

/model wolftac-factory-review --provider moa
/skill wolftac-review
```

If a skill is invoked under the wrong active model, the skill can warn and tell the
operator which preset to use, but it cannot retroactively rerun the already-started
turn under a different model. Gemma is advisory only; build/review proof still comes
from real repo commands, test output, diffs, and Linear state.

## The contract, in brief

- Linear team **Wolftac Digital** (`WD`), queue label **Factory Queue**.
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) →
  `Done` — set only by the review stage. A failed review returns the issue to `Todo`
  with a defect list the next build treats as its work order.
- Every spec has a mandatory **`Repo:`** header naming where the work happens; every
  build/review report opens with **`Agent: <platform / model>`** so you can always
  tell who did what.
- Model economics: spec on your strongest model or the `wolftac-factory-spec` MoA
  preset, loop build/review on the stage presets or cheaper workers (`/loop 5m
  /wolftac-build`, `/loop 5m /wolftac-review`) — optionally scoped with a lane
  filter, e.g. `/loop 5m /wolftac-build agentic`.

Full detail — the exact procedure each stage follows — lives in
`canonical/wolftac-{spec,build,review}/SKILL.md`. `AGENTS.md` is the shorter version
written for an agent seeing this for the first time.

## Provenance

- WD-147 — canonical skills built; proven end-to-end on WD-158 (the review stage
  caught a real defect on the factory's very first run)
- WD-159 — cross-agent ports (Codex, Hermes, Claude Desktop) + agent-identity contract
- WD-160 — this repo, private on GitHub
- WD-161 — multi-environment support (lane filters, silent-skip routing, WSL targets)
- WD-167 — renamed skills to the `wolftac-` prefix, enforced LF line endings
- This handoff packaging (`AGENTS.md`, `-Package`) — so the factory can travel to
  any Hermes or Codex agent, not just the ones already deployed on this machine
- Hermes MoA routing — stage-specific factory presets documented for spec/build/review
  model teams, with Gemma 4 31B QAT as an optional local advisor
