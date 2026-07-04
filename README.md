# wolftac-factory

Canonical source for the Wolftac software factory — the `/wolftac-spec` → `/wolftac-build` → `/wolftac-review`
pipeline that runs across Claude Code, Claude Desktop, Codex, and Hermes.
Born from VCA Bootcamp #21 (2026-07-03); built and packaged via WD-147/WD-159/WD-160.

**This repo is the source of truth.** The copies under `~/.claude/skills`,
`~/.codex/prompts`, and `%LOCALAPPDATA%\hermes\skills` are deploy targets.
Edit here, commit, then deploy.

## The factory contract

- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — stages only touch issues carrying this label
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) → `Done`
  — `Done` is set **only** by the review stage; failed reviews go back to `Todo` with a
  numbered defect list that becomes the next build's work order
- Specs follow `canonical/wolftac-spec/spec-template.md`; the **Repo:** header line is mandatory
- BUILD/REVIEW REPORTs open with `**Agent:** <platform / model>`; reviewers prefer work
  built by a different agent than themselves
- Model economics: spec on the strongest model (Fable), build/wolftac-review looped on cheaper
  executors (`/loop 5m /wolftac-build`, `/loop 5m /wolftac-review`)

## Layout

```
canonical/            Claude Code skills (the reference implementation)
ports/codex/          Codex custom prompts (~/.codex/prompts/)
ports/hermes/         Hermes skills (%LOCALAPPDATA%\hermes\skills\)
ports/claude-desktop/ Sources for the Desktop upload bundles (zipped by -Deploy)
deploy/deploy.ps1     -Check (drift detection) / -Deploy (push to all targets)
deploy/linear-mcp.toml.example  Codex config.toml snippet for Linear MCP
```

## Usage

```powershell
pwsh deploy\deploy.ps1 -Check    # diff repo vs deployed copies (exit 1 on drift)
pwsh deploy\deploy.ps1 -Deploy   # copy repo -> all agent locations, rebuild Desktop zips
```

Drift reconciliation: if the *deployed* copy is the improvement (e.g. Hermes `/learn`
edited a skill in place), copy it back into the repo and commit; otherwise `-Deploy`
overwrites the target with the repo version.

Multi-environment queues: build/wolftac-review sessions **silently skip** issues whose spec
`Repo:` path they can't reach (a WSL repo from a Windows session, another machine's
repo) — the right-homed session picks them up. Optional lane filter: `/wolftac-build agentic`
or `/wolftac-review crm` works only issues whose Linear project or labels match. On machines
with WSL, `-Deploy` also installs the canonical skills into the Linux home
(`~/.claude/skills`) so WSL Claude Code sessions get `/wolftac-spec` `/wolftac-build` `/wolftac-review` —
restart any session that was open during a deploy to pick up skill changes.

New machine setup: clone, run `-Deploy`, then do each platform's one-time step
(Codex: Linear MCP OAuth on first use; Desktop: upload the zips from
`Downloads\factory-skills`; Hermes: wire Linear MCP/Composio into `config.yaml`).

## Provenance

- WD-147 — canonical skills built; E2E-proven on WD-158 (review stage caught a real
  defect on the factory's first run)
- WD-159 — cross-agent ports (Codex, Hermes, Claude Desktop) + agent-identity contract
- WD-160 — this repo
