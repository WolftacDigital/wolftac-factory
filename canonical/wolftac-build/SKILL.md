---
name: wolftac-build
description: Factory stage 2 — pull the highest-priority Todo issue labeled Factory Queue from Linear (team Wolftac Digital), implement it on a feature branch per its spec, verify it, and move it to In Review with a build report. One issue per invocation. Use when asked to build from the factory queue; designed for looped runs (/loop 5m /wolftac-build) on Opus or Sonnet.
---

# /wolftac-build — Factory stage 2: execution

You are the build stage of the software factory. You take ONE specced issue from the
Linear queue, implement it exactly to spec, prove it works, and hand it to review.

## Factory constants
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue**
- You consume: state **Todo** • You produce: state **In Review**
- State conveyor: `Todo` → `In Progress` (you, while working) → `In Review` (you, when done) → `Approved` (/wolftac-review PASS) → `Done` (/wolftac-integrate after an approved merge — never yours)
- Spec template reference: `~/.claude/skills/wolftac-spec/spec-template.md`
- Intended model: Opus or Sonnet. Fable should be orchestrating, not executing.
- Cross-agent: this factory also runs on Claude Desktop, Codex, and Hermes — the Linear
  conventions above are the shared contract; any agent may build what any agent specced.

## Autonomous-run override

This skill is the complete process for this stage and is built for unattended, looped
runs. It takes precedence over any general-purpose workflow skills or plugins loaded in
the session (for example superpowers' brainstorming, writing-plans, executing-plans,
using-git-worktrees, finishing-a-development-branch, or a "check for a skill before
every task" rule):
- Do not invoke those process skills during a run of this stage.
- Never stop mid-run to ask a question or wait for confirmation; use only the stop,
  hand-off, and reporting paths this skill defines.
- Never write plan, spec, or design documents into the target repo; the Linear issue
  is the spec.
- Create and remove worktrees only as this skill describes, and never merge locally or
  offer merge options outside this skill's own approval gate.

## Procedure

### 1. Pick up work
- If the Linear MCP tools aren't loaded, load them via ToolSearch (keyword query:
  `"Linear workspace issues"` — you need `list_issues`, `get_issue`, `save_issue`,
  `save_comment`, `list_comments`).
- `list_issues`: team `Wolftac Digital`, state `Todo`, label `Factory Queue`.
- Before choosing a Todo issue, perform a read-first stale-claim sweep: list issues
  in state `In Progress` with label `Factory Queue` (and the lane filter, if any).
  For each claimed issue, read comments and metadata. If the most recent comment, or
  `startedAt`/claim time when there are no comments, is OLDER than 4 hours, you may
  post exactly `SWEPT: stale claim, no activity since <timestamp>` and reset the
  issue to `Todo`. If there is ANY comment younger than 4 hours, the issue is
  UNTOUCHABLE — never sweep it. This sweep is the ONLY time build posts a comment
  during pickup; during a claim-race back off you post NOTHING.
- Lane filter: if this skill was invoked with an argument (e.g. `/wolftac-build agentic`), only
  consider issues whose Linear project name or labels contain it (case-insensitive).
- **PARKED-NEEDS-HUMAN-SKIP:** Silently skip any issue whose latest review report is
  `REVIEW REPORT — NEEDS HUMAN` while it remains assigned to Jonathan or lacks a
  later Jonathan decision comment. Do not comment or change state. A later decision
  comment from Jonathan plus removal of his assignment clears the park; the issue is
  then buildable and that comment is part of its work order. The comment author must
  be Jonathan himself — a decision relayed on his behalf by an agent does NOT clear
  the park.
- **REWORK-FIRST-ORDERING:** Among eligible `Todo` issues, select issues whose latest
  review report is `REVIEW REPORT — FAIL` before fresh `Todo` issues. Within the
  rework tier and within the fresh tier, preserve priority order
  (Urgent > High > Medium > Low), oldest first on ties.
- Skip:
  - issues whose most recent comment is a `BLOCKED:` comment (they need a human/respec),
  - issues with unresolved blocking relations (check with `get_issue`),
  - issues whose spec **Repo:** path is unreachable from THIS session's environment
    (test the path — e.g. a WSL path from a Windows session, or another machine's
    repo). Skip these SILENTLY: no comment, no state change — they belong to a
    differently-homed session.
- **Queue empty or everything skipped → say "Factory queue: nothing buildable" and stop
  cleanly.** (A looped run will simply check again next cycle.)

### 2. Claim it
`save_issue`: state `In Progress`, assignee `me`. Immediately `get_issue` again and
verify state is still `In Progress` AND assignee is still you. If another session won
the race (assignee is someone else, or state is no longer `In Progress`), back off
silently: no comment, no state change, stop this tick. Then read the FULL description
(the spec) and ALL comments — a prior `REVIEW REPORT (FAIL)` comment means this is a
rework pass and its defect list is your primary work order, on top of the original
spec.

### 3. Locate the repo
The spec's **Repo** header line names the working directory. Verify it exists and is a
git repo. If the **Repo:** header is missing, ambiguous, or names a path that exists
nowhere (a genuine spec defect): post a comment `BLOCKED: <what's unclear>`, set state
back to `Todo`, and stop. Reserve `BLOCKED:` for spec defects only — a repo that is
merely unreachable from this environment should have been silently skipped at step 1.

### 4. Branch
Work on the issue's `gitBranchName` (Linear generates it; it's in the issue payload).
Fetch the remote, then create a dedicated worktree for this issue from the current
remote default branch. Never switch branches in a checkout that has user changes and
never commit directly to main/master. If an existing worktree is dirty and clearly
belongs to another process or human, fail closed without modifying it.

**Put the worktree in the durable scratch root — never a system temp dir.** On Windows
use `D:\Wolftac\.build-worktrees\` (native `D:/Wolftac/.build-worktrees/...` for
`git worktree add`, MSYS `/d/Wolftac/.build-worktrees/...` for shell commands); on
WSL/Linux use `~/.build-worktrees/`. One directory per issue, named `build-<ISSUE>`.
Create the root if it does not exist. Never `C:\tmp`, `C:/tmp`, or `/tmp`: those are
cleared by reboots, cleanup tools, and — under WSL — between separate `wsl.exe`
invocations. A build worktree is *reused across ticks*, so losing it mid-issue drops
uncommitted work and strands a `.git/worktrees/` entry in the target repo that its
owner then has to clean up. `/wolftac-review` uses the same root with a `review-`
prefix; the two never collide.

### 5. Implement — with guardrails
- **Before editing anything**, when the spec uses `AC-N`/`NG-N` IDs, compare every
  `AC-N` against every `NG-N`. Non-goals are binding. An acceptance criterion that
  requires a non-goal is a spec defect: post
  `BLOCKED: AC-x requires NG-y (<detail>)`, set state back to `Todo`, and stop.
  Old-format (unnumbered) specs proceed under the existing rules.
- If it is not in the Linear issue, it does not exist — no side-channel instructions.
- Read the repo's CLAUDE.md/AGENTS.md first; match its conventions.
- Build exactly what the spec's Technical approach and Acceptance criteria describe.
  **No scope creep**: tangential improvements get noted in your build report as
  "suggest /wolftac-spec", not built.
- Secrets go in .env/env vars, never hardcoded — review checks this.
- If the spec turns out ambiguous or wrong in a way that blocks correct implementation:
  post `BLOCKED: <specific questions>`, set state back to `Todo`, stop. Do not guess
  on load-bearing decisions.

### 6. Verify
Execute the spec's **Test plan** literally. Every acceptance criterion needs evidence
from running code (tests passing, app behavior observed) — "the code looks right" is
not verification. If verification fails, fix and re-verify before proceeding.

### 7. Commit
Commit with descriptive messages. Record the SHAs and branch name.
Before committing, review `git status` and the staged file list: never commit build
artifacts (`__pycache__/`, `*.pyc`, `node_modules/`, `dist/`, logs); add .gitignore
entries as needed. (Guardrail added after the factory's first review pass caught
committed bytecode — WD-158.)

Publish the branch with `git push -u origin <branch>` and verify the remote head SHA
equals the local head SHA. If the repository has no writable remote, do not move the
issue to `In Review`; report the publication blocker without exposing credentials.

**PR-CREATE-CONTRACT:** After the push and remote-SHA verification, create one
non-draft PR per issue when the repository has a GitHub remote. First run
`gh pr view <branch>`; if a PR already exists for that branch, reuse its URL
and never open a second. Otherwise run:

```text
gh pr create --title "WD-NNN: <issue title>" --body "<summary>

Closes WD-NNN

Linear: <issue URL>" --base main --head <branch>
```

If `gh` reports a rate-limit or transient failure, retry once. If the retry fails,
record the PR-creation blocker in the BUILD REPORT but do not fail a build whose
branch published successfully. For a local-only repository with no GitHub remote,
skip PR commands and record `local-only repo — no PR`; execution verification and
the rest of the handoff are unchanged.

### 8. Hand off
Post a comment on the issue:

```
**BUILD REPORT**
- **Agent:** Claude Code / <model>
- **Implemented:** <what was built, per spec section>
- **Verified:** <exact commands run and their results, per acceptance criterion>
- **Branch / commits:** <branch> @ <SHAs>
- **Published head:** <remote branch> @ <exact remote SHA>
- **PR:** <PR URL, PR-creation blocker, or "local-only repo — no PR">
- **Deviations from spec:** <none, or what and why>
- **Notes for review:** <anything the reviewer should look at first>
```

Then `save_issue`: state `In Review`. **Do not mark anything Done — that is /wolftac-integrate's
exclusive right.**

### 9. Stop
Once the branch is pushed and the remote head verified, the worktree has served its
purpose — `/wolftac-review` builds its own from the published SHA. Remove it: delete
generated heavy artifacts (`node_modules`, `.next`, `tsconfig.tsbuildinfo`,
`next-env.d.ts`) — on Windows they block removal — then `git worktree remove <path>` and
`git worktree prune`. Confirm with `git worktree prune --dry-run -v 2>&1`; silent output
means clean (`-v` writes to stderr, so a bare pipe sees nothing and looks falsely clean).
If prune reports `Permission denied` — common when the target repo lives under OneDrive,
where git's own recursive delete fails but the shell's does not — `rm -rf` each
`.git/worktrees/<name>` directory prune names, then re-confirm. Keep the worktree only
if you are handing the issue back unfinished; then say so in the report.

One issue per invocation. Do not pick up a second issue; the loop handles cadence.
