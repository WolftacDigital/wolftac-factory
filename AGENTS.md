# You are being onboarded onto the Wolftac Factory

This file is written **to you**, the agent — Claude Code, Claude Desktop, Codex, or
Hermes. Someone handed you this repo (or a copy of its files) so you could become a
worker in the Wolftac software factory: a `/wolftac-spec` → `/wolftac-build` →
`/wolftac-review` pipeline where multiple agents share one Linear queue and hand work
to each other through it. Read this file fully before installing anything.

If you are Codex, you likely loaded this file automatically (Codex reads `AGENTS.md`
from the repo root by convention) — good, that's the point.

## What this factory is, in one paragraph

Instead of one agent doing everything in one session, work moves through three
stages, each with a different agent/model: **spec** turns an idea into a detailed,
testable plan and files it as a Linear issue; **build** picks up specced issues,
implements them on a branch, and *proves* they work by running the code; **review**
re-verifies independently and is the only stage allowed to mark work Done. Any agent
on any platform can run any stage — the Linear issue itself carries all the context,
so a Codex session can build what a Fable session specced, and a Hermes session can
review what Codex built.

## The factory contract (memorize this — it's the only thing that has to be shared)

- **Linear team:** Wolftac Digital (key `WD`) — confirm you can reach this team via
  whatever Linear tools you have before doing anything else.
- **Queue label:** `Factory Queue` — you only ever touch issues carrying this label.
  Ignore everything else in the workspace.
- **States, in order:** `Todo` (specced, ready to build) → `In Progress` (a build
  session has claimed it) → `In Review` (built, awaiting QA) → `Done` (reviewed and
  accepted — **only the review stage may set this**). A failed review sends the issue
  back to `Todo` with a numbered defect list, which becomes the next build's work
  order.
- **Spec template:** every spec follows `canonical/wolftac-spec/spec-template.md`.
  The **`Repo:`** header line at the top of every spec is mandatory — it's the
  working directory for that issue, and build/review refuse to guess at it.
- **Every report opens with an identity line:** `**Agent:** <platform / model>`
  (e.g. `Codex / gpt-5.5`, `Hermes / gpt-5.5`, `Claude Code / Opus 4.8`). This is how
  the human (and other agents) can tell who did what. Reviewers should prefer
  reviewing work built by a *different* agent than themselves when the queue allows
  it — cross-agent review catches more.
- **Multi-environment safety:** if a spec's `Repo:` path isn't reachable from where
  you're running (a WSL path from a Windows session, another machine's repo), skip it
  **silently** — no comment, no state change. A differently-homed session will pick
  it up. Reserve the `BLOCKED:` comment for genuine spec defects (missing/ambiguous
  `Repo:`, contradictory requirements) — never for "this isn't my environment."
- **Lane filters (optional):** if you're invoked with an argument — `/wolftac-build
  agentic`, `/wolftac-review crm` — only work issues whose Linear project name or
  labels contain that word (case-insensitive). No argument = work the whole queue.

The full procedure for each stage — exactly what to do, in what order, what to check,
what to write in your report — lives in the canonical skill files. This file gets you
installed and oriented; **`canonical/wolftac-{spec,build,review}/SKILL.md` is the
actual spec you execute against.** Read the one(s) for the stage(s) you'll run before
your first invocation.

**Naming note:** the deployed commands in this repo are `/wolftac-*`. If you find
`/factory-*` names in `share/build-your-own-agent-factory.html`, those are
sanitized public examples for clients/community — templates, not installed skills.
Do not mix the two.

## Which one are you? Install yourself.

Pick your platform below. Every path ends the same way: the three skills present
where your platform looks for them, Linear reachable, and a restart if you're a
long-running session.

### Claude Code (CLI or IDE)

1. Copy `canonical/wolftac-spec/`, `canonical/wolftac-build/`, `canonical/wolftac-review/`
   into your skills directory:
   - Windows: `%USERPROFILE%\.claude\skills\`
   - macOS/Linux/WSL: `~/.claude/skills/`
2. **Line endings matter.** These files must be LF, not CRLF — a stray `\r` in the
   YAML frontmatter breaks skill discovery silently on Linux (the name parses as
   `wolftac-build\r` and the command never registers). If you copied from a Windows
   checkout, verify with `cat -A canonical/wolftac-build/SKILL.md | head -3` — you
   should see `$` line endings, never `^M$`. Re-save as LF if you see `^M`.
3. Restart your session — skills load at startup, not live.
4. Confirm the Linear MCP connector is authorized for the Wolftac Digital workspace
   (claude.ai connector settings, or `claude mcp` / `/mcp` in an interactive session).
5. Verify: `/wolftac-spec`, `/wolftac-build`, `/wolftac-review` should appear as
   invocable skills.

### Codex (CLI, Desktop, or IDE plugin)

1. Copy `ports/codex/wolftac-spec.md`, `wolftac-build.md`, `wolftac-review.md` into
   `~/.codex/prompts/`.
2. Add Linear as an MCP server. Open `~/.codex/config.toml` and add the block from
   `deploy/linear-mcp.toml.example` — **append it, don't overwrite the file**, since
   other MCP servers are likely already configured there:
   ```toml
   [mcp_servers.linear]
   url = "https://mcp.linear.app/mcp"
   ```
3. Start a new session (or restart). The first Linear-touching call will trigger an
   OAuth browser login — or run `codex mcp login linear` explicitly.
4. Verify: `/wolftac-spec` should be offered as a custom prompt; a dry invocation
   should reach Linear without error.

### Hermes

1. Copy `ports/hermes/wolftac-spec/`, `wolftac-build/`, `wolftac-review/` into your
   Hermes profile's skills directory (Windows default: `%LOCALAPPDATA%\hermes\skills\`).
2. Wire Linear into `config.yaml` under `mcp_servers:` (back up `config.yaml` first —
   this is a live agent config):
   ```yaml
   mcp_servers:
     linear:
       url: https://mcp.linear.app/mcp
       auth: oauth
       enabled: true
   ```
3. Optional but recommended for Wolftac's Hermes profile: merge
   `deploy/hermes-moa-presets.yaml.example` into that profile's `config.yaml` under
   `moa.presets`. Back up the live config first; the example contains model names only,
   never secrets. The intended stage routing is:
   - `/wolftac-spec` → `/model wolftac-factory-spec --provider moa`
   - `/wolftac-build` → `/model wolftac-factory-build --provider moa`
   - `/wolftac-review` → `/model wolftac-factory-review --provider moa`
4. Start a fresh Hermes session or have the operator reload the gateway/service so
   config and skills are re-read. If you are currently running inside that Hermes
   gateway, do not restart it yourself unless the human/operator explicitly told you
   to manage the service lifecycle.
5. Complete the Linear OAuth flow (Hermes will surface an auth link on next use).
6. Verify: `hermes tools list | grep -i linear` should list Linear tools — this is
   also what each skill's own preflight checks before doing anything.

Hermes MoA note: the skill cannot switch models after the turn already started. Select
the MoA preset first, then invoke the matching skill. If invoked under the wrong active
model, the Hermes skill should warn and tell the operator which preset to use rather
than pretending the model team ran.

### Claude Desktop

1. Each of `ports/claude-desktop/wolftac-spec/`, `wolftac-build/`, `wolftac-review/`
   is a skill folder — zip each one and upload it in Settings → Capabilities →
   Skills. (If you were handed a package that already includes
   `wolftac-{spec,build,review}.zip`, use those directly.)
2. Enable the Linear connector for the chat (tools menu).
3. **Know your limits:** the build and review stages need filesystem + terminal
   access to the target repo (an MCP server like desktop-commander) to actually do
   anything. Without it, they'll report what's in the queue and tell the human to run
   that stage from a coding agent instead — they will never fake a build or claim
   completion they can't verify.

## Operating rules (apply regardless of platform)

- **One issue per invocation.** Don't chain multiple issues in a single build/review
  call — a looped session (`/loop 5m /wolftac-build`) handles cadence.
- **Verify by execution, always.** "The code looks right" is not verification. Build
  proves acceptance criteria by running the code; review reproduces that from
  scratch and does not trust the build report's claims.
- **Secrets never appear in code, logs, or reports.** `.env` / environment variables
  only. If a spec deals with tokens or credentials, redact them in every report.
- **No scope creep.** Build exactly what the spec's Technical Approach and Acceptance
  Criteria describe. Ideas for more get noted as "suggest /wolftac-spec," not built.
- **Only the review stage sets `Done`.** If you are the build stage and you think
  something is finished, the correct end state is `In Review` — never `Done`.
- **If a spec is genuinely wrong or ambiguous**, don't guess on load-bearing
  decisions — post `BLOCKED: <specific question>` and return the issue to `Todo`.

## Running unattended

Interactive use needs none of this. For scheduled/looped workers, use only the
recipes below — **every command here was verified against the live CLI on
2026-07-05.** If you need a flag not shown, verify it against `--help` yourself
before relying on it; never schedule aspirational syntax.

**Shared safeguards (all platforms):**
- One issue per tick. Exit cleanly — and quietly — on an empty queue.
- Fail closed: if any precondition is missing (Linear unreachable, repo absent,
  a required model preset not active), log why and exit WITHOUT claiming an issue.
- Claim-race rule: list→claim is not atomic. After claiming, re-read the issue's
  state and assignee — if another session got there first, back off silently.
  Prefer one build worker per lane.
- Stale-claim recovery: an issue sitting `In Progress` with no comment activity
  for more than ~4 hours is presumed orphaned — the operator (or a sweep job)
  may reset it to `Todo`.
- Never grant blanket auto-approval to an unattended worker. Scope permissions
  to exactly what the stage needs; a destructive operation outside that scope
  should fail, not auto-approve.

### Claude Code

```
/loop 5m /wolftac-build [lane]     # build line, session in/reaching the target repo
/loop 5m /wolftac-review           # review line
```

Permission precondition: an unattended loop stalls at the first tool call that
needs approval. Pre-approve the specific tools the stage uses (project
`.claude/settings.json` allowlist or a scoped permission profile) **before**
starting the loop. Do not "fix" a stalling loop with a blanket bypass.

### Hermes

Hermes ships a scheduler (`hermes cron`). Verified recipe — a build tick every
30 minutes with the skill attached and repo context injected:

```
hermes cron create --name wolftac-build-tick \
  --skill wolftac-build \
  --workdir <ABSOLUTE_PATH_TO_TARGET_REPO> \
  "every 30m" \
  "Run one wolftac-build tick: work at most one Factory Queue issue; if the queue is empty or any precondition fails, exit cleanly."
```

Manage with `hermes cron list | status | pause | resume | remove`. For non-TTY
runs that would hit unseen shell hooks, the CLI offers `--accept-hooks` — enable
it deliberately per job, not as a default.

MoA note (routing + cost): cron ticks run under the profile's active/default
model, and MoA presets fan out to **every** reference model on each user turn —
an idle-queue tick on an MoA preset pays the full fan-out for nothing. Prefer
single-model ticks for polling; reserve the stage MoA presets for interactive
runs, or accept the cost knowingly.

### Codex

`codex exec` runs a prompt non-interactively (verified:
`codex exec --skip-git-repo-check "<prompt>"` completes and prints the reply).
Feed it the stage prompt file, pinned to the target repo:

```powershell
# Windows scheduled task (PowerShell)
codex exec -C <ABSOLUTE_PATH_TO_TARGET_REPO> (Get-Content ~\.codex\prompts\wolftac-build.md -Raw)
```
```sh
# cron (bash)
codex exec -C <ABSOLUTE_PATH_TO_TARGET_REPO> "$(cat ~/.codex/prompts/wolftac-build.md)"
```

Sandbox/approvals: choose an explicit `--sandbox <mode>` appropriate to the repo.
Codex's own help marks `--dangerously-bypass-approvals-and-sandbox` as
"EXTREMELY DANGEROUS … intended solely for running in environments that are
externally sandboxed" — treat that as the rule for any unattended worker, not
a suggestion.

### Claude Desktop

No unattended mode. Desktop is an interactive surface; its build/review skills
already refuse work they can't execute (see the reality check above).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/wolftac-build` (etc.) doesn't appear after install | Session wasn't restarted (skills load at startup), or the skill files have CRLF line endings (Linux/WSL only) |
| Skill loads but Linear calls fail | The platform's Linear MCP block isn't wired yet, or OAuth hasn't been completed — see the per-platform steps above |
| Hermes skill loads but the expected model team did not run | The active model was not switched to the stage MoA preset before invocation; use `/model wolftac-factory-<stage> --provider moa`, then rerun the skill |
| A build/review session grabs the wrong issue or nothing at all | Check the `Factory Queue` label and state filters; check whether the spec's `Repo:` path is reachable from where you're running |
| An issue seems stuck bouncing between agents | Confirm nobody is using `BLOCKED:` for "this isn't my environment" — that must be a silent skip, not a comment (see Multi-environment safety above) |

## Where the real detail lives

This file is orientation, not the spec. Before running a stage for the first time,
read its canonical definition:

- `canonical/wolftac-spec/SKILL.md` + `canonical/wolftac-spec/spec-template.md`
- `canonical/wolftac-build/SKILL.md`
- `canonical/wolftac-review/SKILL.md`

Platform-specific prompt/skill text (`ports/*`) carries the same procedure adapted to
each platform's tools and constraints — read your own if you want the exact wording
you're operating under.
