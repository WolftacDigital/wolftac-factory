# You are being onboarded onto the Wolftac Factory

This file is written **to you**, the agent — Claude Code, Claude Desktop, Codex, or
Hermes. Someone handed you this repo (or a copy of its files) so you could become a
worker in the Wolftac software factory: a `/wolftac-spec` → `/wolftac-build` →
`/wolftac-review` → `/wolftac-integrate` pipeline where multiple agents share one
Linear queue and hand work to each other through it. Read this file fully before
installing anything.

If you are Codex, you likely loaded this file automatically (Codex reads `AGENTS.md`
from the repo root by convention) — good, that's the point.

## What this factory is, in one paragraph

Instead of one agent doing everything in one session, work moves through four
stages, each with a different agent/model: **spec** turns an idea into a detailed,
testable plan and files it as a Linear issue; **build** picks up specced issues,
implements them on a branch, and *proves* they work by running the code; **review**
re-verifies independently and marks passing work Approved; **integrate** rebases the
approved branch onto the default branch, re-runs the gates, and — only after the
human's explicit approval (a 🚀 reaction by the configured approver on the newest
Slack approval message, or the case-insensitive typed `APPROVE MERGE` /
`APPROVED MERGE` fallback) — merges it and
marks it Done. Done means merged:
the only stage allowed to set it is integrate, and only after ancestry is verified.
Any agent on any platform can run any stage — the Linear issue itself carries all
the context, so a Codex session can build what a Fable session specced, and a Hermes
session can review what Codex built.

## The factory contract (memorize this — it's the only thing that has to be shared)

- **Linear team:** Wolftac Digital (key `WD`) — confirm you can reach this team via
  whatever Linear tools you have before doing anything else.
- **Queue label:** `Factory Queue` — you only ever touch issues carrying this label.
  Ignore everything else in the workspace.
- **States, in order:** `Todo` (specced, ready to build) → `In Progress` (a build
  session has claimed it) → `In Review` (built, awaiting QA) → `Approved` (review
  passed, awaiting merge) → `Done` (merged to the remote default branch — **only the
  integrate stage may set this, and only after the human's approval: Jonathan's
  `:rocket:` reaction on the newest Slack approval message (reactor id must equal
  `approver_slack_user_id` in `deploy/approval-policy.json`, verified by API read;
  any other reactor is ignored) or his typed comment starting `APPROVE MERGE` or
  `APPROVED MERGE`, case-insensitively, as the fallback**). Workers never add
  reactions to factory notification messages. Every factory Slack post uses
  `SLACK_FACTORY_BOT_TOKEN` with `chat.postMessage`, selecting `username` and
  `icon_emoji` from `deploy/approval-policy.json`'s platform/hostname-keyed
  `sender_identities`, or `fallback_identity` when unmapped; `reactions.get`
  uses the same bot token, never a human Slack session. A
  failed review — or a failed rebase/gate at integration — sends the issue back to
  `Todo` with a numbered defect list, which becomes the next build's work order.
- **Spec template:** every spec follows `canonical/wolftac-spec/spec-template.md`.
  The **`Repo:`** header line at the top of every spec is mandatory — it's the
  working directory for that issue, and build/review refuse to guess at it.
- **Issue is the instruction boundary:** If it is not in the Linear issue, it does
  not exist — no side-channel instructions.
- **One PR per published issue branch:** after build pushes and verifies the remote
  SHA, it creates one non-draft GitHub PR and records the URL in the BUILD REPORT.
  Reuse an existing PR for the branch; never create a duplicate. A local-only repo
  records `local-only repo — no PR` and keeps the execution-verified flow.
- **Linear identities are split:** factory workers connect to Linear through the
  dedicated factory bot identity; humans comment and approve from their own
  accounts. The integrate stage refuses any typed approval comment
  (`APPROVE MERGE` or `APPROVED MERGE`, case-insensitively) authored by the same
  identity it writes with — until the split exists in a workspace, integrate can
  prepare proof packs but will never merge. Integrate performs ALL of its Linear
  I/O with the bot credentials (`LINEAR_FACTORY_BOT_TOKEN`), failing closed when
  they are missing, and notification lanes (e.g. Telegram) are notify-only: no
  relay ever posts an approval on the human's behalf.
- **Every report opens with an identity line:** `**Agent:** <platform / model>`
  (e.g. `Codex / gpt-5.6-sol`, `Hermes / gpt-5.6-sol`, `Claude Code / Opus 4.8`). This is how
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

### Review verdicts and parked work

Review has exactly four outcomes:

| Verdict | Meaning | Conveyor action |
|---|---|---|
| `PASS` | Every criterion and baseline check passes. | Set `Approved`. |
| `PASS WITH FIXES` | The reviewer applies and verifies only minor, low-risk fixes. | Set `Approved`. |
| `FAIL` | One or more ordinary decidable defects, failed CI checks, security findings, or unmet criteria require rework. | Post the tagged defect list and set `Todo`. |
| `NEEDS HUMAN` | A `[SCOPE-CONFLICT]` or other product decision requires Jonathan's verdict. | Assign Jonathan and keep `In Review`; do not move state. |

Every review finding carries exactly one tag: `[AC-N]`, `[DEFECT]`, `[SECURITY]`,
`[CI]`, or `[SCOPE-CONFLICT]`. `[CI]` means a GitHub check failed and the
finding names the failing job. `NEEDS HUMAN` is forbidden for ordinary decidable defects:
decidable defects are `FAIL`; only product decisions park an issue. A parked report
must name the exact decision in one tagged `[SCOPE-CONFLICT]` line and instruct
Jonathan to comment his decision, then remove his assignment. Once unassigned, the
issue re-enters the review queue with his decision comment as part of the work order.

Review pickup silently skips an issue whose latest review report is `NEEDS HUMAN`
while it remains assigned to Jonathan or lacks his later decision comment. Build
pickup applies the identical test to `Todo` issues. Neither stage comments or changes
state while skipping parked work.

**Clearing a park is a two-part action, and both parts are required:** Jonathan
comments his decision *from his own Linear account*, and his assignment is removed.
Only then does the issue become pickable again, with that comment as part of the work
order. A decision relayed on Jonathan's behalf by an agent does **not** clear the park
— the comment author must be Jonathan himself, for the same identity reason integrate
refuses a bot-authored approval. Relaying a chat decision into Linear as an agent
records the intent but leaves the issue parked; say so plainly rather than implying
the issue is unblocked.
For buildable `Todo` work, issues whose latest review report is `FAIL` form the
rework tier and are selected before fresh issues; priority
(`Urgent` > `High` > `Medium` > `Low`) and oldest-first ordering apply within each
tier.

Before any PASS verdict, review enumerates every check on the published branch's PR
with `gh pr checks <n>`: any red check, required or advisory, is a `[CI]` FAIL; any
pending/queued/outage result gets `waiting on CI` and no verdict; only a PR with no
checks records `no CI checks present — execution-verified only`. Required-ness affects
only GitHub's server-side merge enforcement, never the Factory verdict; with no GitHub
remote or no PR, review records `local-only repo` and keeps the execution-verified flow.

The full procedure for each stage — exactly what to do, in what order, what to check,
what to write in your report — lives in the canonical skill files. This file gets you
installed and oriented; **`canonical/wolftac-{spec,build,review,integrate}/SKILL.md`
is the actual spec you execute against.** Read the one(s) for the stage(s) you'll run
before your first invocation.

## Shared doctrine skill (not a stage)

This repo also ships `wolftac-agent-command-patterns`: shared Wolftac operating
doctrine, **not a fourth pipeline stage**. The pipeline remains exactly
spec → build → review → integrate. Load the doctrine skill before strategy,
implementation, review, or an agent-to-agent handoff to get consistent operating-mode
selection, decision-complete handoff prompts, memory boundaries, tool-schema-first
discipline, subagent delegation limits, verification gates, and final reporting. It
never replaces a stage skill and changes no Linear state, label, queue order, approval
rule, or merge authority. It is available at
`canonical/wolftac-agent-command-patterns/SKILL.md`,
`ports/hermes/wolftac-agent-command-patterns/SKILL.md`,
`ports/codex/wolftac-agent-command-patterns.md`, and
`ports/claude-desktop/wolftac-agent-command-patterns/SKILL.md`; install it exactly the
way you install a stage skill for your platform. Whether your persona loads it
automatically is a separate, deliberate decision — this repo does not patch it in.

**Naming note:** the deployed commands in this repo are `/wolftac-*`. If you find
`/factory-*` names in `share/build-your-own-agent-factory.html`, those are
sanitized public examples for clients/community — templates, not installed skills.
Do not mix the two.

## Which one are you? Install yourself.

Pick your platform below. Every path ends the same way: all four stage skills
present where your platform looks for them, Linear reachable, and a restart if
you're a long-running session.

### Claude Code (CLI or IDE)

1. Copy `canonical/wolftac-spec/`, `canonical/wolftac-build/`, `canonical/wolftac-review/`,
   `canonical/wolftac-integrate/` into your skills directory:
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
5. Verify: `/wolftac-spec`, `/wolftac-build`, `/wolftac-review`, `/wolftac-integrate`
   should appear as invocable skills.

### Codex (CLI, Desktop, or IDE plugin)

1. Install the native skills from `plugins/wolftac-factory/skills/` into
   `~/.agents/skills/` (or install the `plugins/wolftac-factory` plugin). The deploy
   script does this locally. Files under `ports/codex/` are deprecated compatibility
   prompts only; when retained, their CLI/IDE names are `/prompts:wolftac-*`.
2. Add Linear as an MCP server. Open `~/.codex/config.toml` and add the block from
   `deploy/linear-mcp.toml.example` — **append it, don't overwrite the file**, since
   other MCP servers are likely already configured there:
   ```toml
   [mcp_servers.linear]
   url = "https://mcp.linear.app/mcp"
   ```
3. Start a new session (or restart). The first Linear-touching call will trigger an
   OAuth browser login — or run `codex mcp login linear` explicitly.
4. Verify: `$wolftac-spec`, `$wolftac-build`, `$wolftac-review`, and
   `$wolftac-integrate` appear as skills
   in CLI/IDE or the desktop Skills UI; then run one read-only Linear dry run (for example,
   list your Linear teams or list Factory Queue issues) and confirm it reaches
   Linear without error.

### Hermes

1. Copy `ports/hermes/wolftac-spec/`, `wolftac-build/`, `wolftac-review/`,
   `wolftac-integrate/` into your Hermes profile's skills directory (Windows default:
   `%LOCALAPPDATA%\hermes\skills\`).
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

1. Each of `ports/claude-desktop/wolftac-spec/`, `wolftac-build/`, `wolftac-review/`,
   `wolftac-integrate/` is a skill folder — zip each one and upload it in
   Settings → Capabilities → Skills. (If you were handed a package that already
   includes `wolftac-{spec,build,review,integrate}.zip`, use those directly.)
2. Enable the Linear connector for the chat (tools menu).
3. **Know your limits:** the build, review, and integrate stages need filesystem +
   terminal access to the target repo (an MCP server like desktop-commander) to
   actually do anything — and integrate additionally needs the factory bot
   credentials in that environment. Without them, they'll report what's in the queue
   and tell the human to run that stage from a coding agent instead — they will
   never fake a build, claim completion they can't verify, or merge anything.

## Operating rules (apply regardless of platform)

- **One issue per invocation.** Don't chain multiple issues in a single build/review
  call — the platform's scheduler or loop runner handles cadence.
- **Verify by execution, always.** "The code looks right" is not verification. Build
  proves acceptance criteria by running the code; review reproduces that from
  scratch and does not trust the build report's claims.
- **Secrets never appear in code, logs, or reports.** `.env` / environment variables
  only. If a spec deals with tokens or credentials, redact them in every report.
- **No scope creep.** Build exactly what the spec's Technical Approach and Acceptance
  Criteria describe. Ideas for more get noted as "suggest /wolftac-spec," not built.
- **Only the integrate stage sets `Done`, and only after the human's explicit
  approval artifact.** Build ends at `In Review`; review's passing verdict is
  `Approved`. If you think something is finished, hand it forward — never mark it
  Done yourself from build or review.
- **Integrate uses the issue PR when one exists.** After the unchanged human approval
  gate and rebuilt-head gates, run `gh pr merge <n> --merge` for the branch's PR.
  Never auto-merge. If no PR exists for a pre-PR-era in-flight issue, use the existing
  direct fast-forward path. After either path, verify the reviewed SHA is an ancestor
  of the remote default branch before setting `Done`.
- **If a spec is genuinely wrong or ambiguous**, don't guess on load-bearing
  decisions — post `BLOCKED: <specific question>` and return the issue to `Todo`.

## Running unattended

Interactive use needs none of this. For scheduled workers and Claude Code loops, use only the
recipes below — **every command here was verified against the live CLI on
2026-07-05.** If you need a flag not shown, verify it against `--help` yourself
before relying on it; never schedule aspirational syntax.

**Shared safeguards (all platforms):**
- One issue per tick. Exit cleanly — and quietly — on an empty queue.
- Fail closed: if any precondition is missing (Linear unreachable, repo absent,
  a required model preset not active), log why and exit WITHOUT claiming an issue.
- Claim-race rule: list→claim is not atomic. The claim-race recheck and review
  claim convention are encoded in the build/review skills' pickup + claim steps:
  after claiming, re-read the issue's state and assignee; if another session got
  there first, back off silently. Prefer one build worker per lane.
- Stale-claim recovery: the stale-claim sweep is encoded in the build/review
  skills' pickup steps. A claimed issue with no comment activity for more than
  4 hours may be returned to `Todo` with the `SWEPT` comment; any issue with
  activity younger than 4 hours is untouchable.
- Claim identity split: Build and Review act as the operator through the
  platform's own Linear toolset (`assignee: me`, verified by read-back);
  Integrate acts as the Wolftac Factory Bot through `linear_bot.py` and
  `delegate`. Three rules bind every Build and Review port, checked by the
  `build-review-claim-identity` parity invariant: CLAIM-IDENTITY (claim only
  with the platform Linear tool as the operator; a Build/Review claim lives in
  `assignee`, never `delegate`); NO-BOT-TOKEN (never read or use the factory
  bot token, never hand-roll a Linear GraphQL or HTTP call, never import the
  gatekeeper or `linear_bot.py` for a credential, never write a helper that
  fetches one; if the Linear write tool is unavailable, stop without claiming
  and say so); CLAIM-DID-NOT-TAKE (if your own mutation applied but read-back
  shows the assignee is not you, restore only what this run changed, post
  nothing on the issue, stop, and report `CLAIM-DID-NOT-TAKE <ID>` with the
  read-back state, assignee and delegate; losing a race still means back off
  with no side effects).
- Never grant blanket auto-approval to an unattended worker. Scope permissions
  to exactly what the stage needs; a destructive operation outside that scope
  should fail, not auto-approve.

### Claude Code

```
/loop 5m /wolftac-build [lane]     # build line, session in/reaching the target repo
/loop 5m /wolftac-review           # review line
/loop 10m /wolftac-integrate       # integrate line — merges only on a valid approval artifact
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

`codex exec` runs a skill non-interactively. Select the model before the skill starts,
pin the target repo, and use the policy in `deploy/model-policy.yaml`:

```powershell
# Windows scheduled task (PowerShell)
codex exec -C <ABSOLUTE_PATH_TO_TARGET_REPO> -m gpt-5.6-sol `
  -c 'model_reasoning_effort="high"' 'Use $wolftac-build to run one Factory tick.'
```
```sh
# cron (bash)
codex exec -C <ABSOLUTE_PATH_TO_TARGET_REPO> -m gpt-5.6-sol \
  -c 'model_reasoning_effort="high"' 'Use $wolftac-build to run one Factory tick.'
```

Sandbox/approvals: choose an explicit `--sandbox <mode>` appropriate to the repo.
Codex's own help marks `--dangerously-bypass-approvals-and-sandbox` as
"EXTREMELY DANGEROUS … intended solely for running in environments that are
externally sandboxed" — treat that as the rule for any unattended worker, not
a suggestion.

### Claude Desktop

No unattended mode. Desktop is an interactive surface; its build/review/integrate
skills already refuse work they can't execute (see the reality check above).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Codex `$wolftac-build` doesn't appear after install | Native plugin/skills were not deployed, the session needs restart, or the user is looking for the deprecated direct slash name instead of the Skills UI/`$` mention |
| Legacy Codex `/wolftac-build` doesn't appear | Custom prompts are deprecated and namespaced as `/prompts:wolftac-build` in CLI/IDE; use `$wolftac-build` instead |
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
- `canonical/wolftac-integrate/SKILL.md`

Platform-specific prompt/skill text (`ports/*`) carries the same procedure adapted to
each platform's tools and constraints — read your own if you want the exact wording
you're operating under.
