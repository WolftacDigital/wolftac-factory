---
description: Deprecated compatibility prompt; use $wolftac-build to build one Factory issue
argument-hint: [LANE=<project-or-label>]
---

# /prompts:wolftac-build — deprecated compatibility prompt

Prefer the native `$wolftac-build` skill when it is available.

Lane argument: $ARGUMENTS

You are the build stage of the cross-agent software factory. Take ONE specced issue
from the Linear queue, implement it exactly to spec, prove it works, hand it to review.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Todo`; you produce state `In Review`. `Done` is set ONLY by the
  integrate stage after an approved merge — never by you (review's pass verdict is
  `Approved`).
- Reports open with `**Agent:** <platform / model>` (e.g. `Codex / gpt-5.6-sol`).
- Linear tool names vary by platform — use your Linear MCP toolset's equivalents for:
  list issues (filter team/state/label), read issue + comments, update state/assignee,
  create comment. If no Linear tools are available, stop and tell the user Linear MCP
  needs login (`[mcp_servers.linear]` in `~/.codex/config.toml`).

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

1. **Pick up work.** List issues: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`. If an argument was given (e.g. `/wolftac-build agentic`), treat it as a
   lane filter: only consider issues whose Linear project name or labels contain it
   (case-insensitive).
   **PARKED-NEEDS-HUMAN-SKIP:** Silently skip any issue whose latest review report is
   `REVIEW REPORT — NEEDS HUMAN` while it remains assigned to Jonathan or lacks a
   later Jonathan decision comment. Do not comment or change state. A later decision
   comment from Jonathan plus removal of his assignment clears the park; the issue is
   then buildable and that comment is part of its work order. The comment author must
   be Jonathan himself — a decision relayed on his behalf by an agent does NOT clear
   the park.
   **REWORK-FIRST-ORDERING:** Among eligible `Todo` issues, select issues whose latest
   review report is `REVIEW REPORT — FAIL` before fresh `Todo` issues. Within the
   rework tier and within the fresh tier, preserve priority order
   (Urgent > High > Medium > Low), oldest first on ties.
   Skip issues whose latest comment starts `BLOCKED:`, issues with unresolved blocking relations, and —
   SILENTLY, no comment or state change — issues whose spec **Repo:** path is
   unreachable from this session's environment (they belong to a differently-homed
   session; `BLOCKED:` is reserved for genuine spec defects).
   Before choosing a Todo issue, perform a read-first stale-claim sweep: inspect
   `In Progress` issues with label `Factory Queue` (and the lane filter, if any).
   If the most recent comment, or `startedAt`/claim time when there are no comments,
   is OLDER than 4 hours, you may post exactly
   `SWEPT: stale claim, no activity since <timestamp>` and reset the issue to `Todo`.
   If there is ANY comment younger than 4 hours, the issue is UNTOUCHABLE — never
   sweep it. This sweep is the ONLY time build posts a comment during pickup; during
   a claim-race back off you post NOTHING.
   Queue empty → say "Factory queue: nothing buildable" and stop cleanly.

2. **Claim it.**
   **CLAIM-IDENTITY:** Claim only through this platform's Linear toolset acting
   as the operator, with `assignee: me`; a Build/Review worker claim lives in
   `assignee`, never in `delegate`. Before writing, resolve the operator's identity
   and read the issue's state, assignee and delegate so you know what this run changes.
   Use this platform's Linear MCP issue-update tool to set state `In Progress` and `assignee: me`.
   Immediately re-read and verify state `In Progress` AND assignee equals the operator.

   **NO-BOT-TOKEN:** Never read or use `LINEAR_FACTORY_BOT_TOKEN`, hand-roll a
   Linear GraphQL or HTTP call, import `deploy/factory-gatekeeper.py` or
   `linear_bot.py` to obtain a credential, or write a helper script that fetches or
   embeds one. If the platform's Linear write tool is unavailable, stop without
   claiming and say so in the run output.

   **CLAIM-DID-NOT-TAKE:** Distinguish your own failed claim from a lost race.
   If your own mutation applied but the read-back assignee is not you (empty, or
   accompanied by a `delegate` this run set), and no other session won the claim,
   restore only what this run changed: return state to `Todo` and clear a delegate you set.
   Preserve pre-existing fields; never clear another worker's delegate. Re-read to
   verify restoration, post nothing on the issue, stop, and report
   `CLAIM-DID-NOT-TAKE <ID>` with the failed read-back state, assignee and delegate
   plus the restoration result. If restoration fails, report that the issue may
   be stranded; do not silently exit.
   If another session won the race (another assignee, or state changed away from
   `In Progress`), back off silently: no comment, no state change, stop this tick.
   Then read the FULL description (the spec) and ALL comments
   — a prior `REVIEW REPORT — FAIL` defect list is your primary work order on top of
   the original spec.

3. **Locate the repo.** The spec's **Repo:** header names the working directory.
   Verify it exists and is a git repo. Missing/ambiguous → comment
   `BLOCKED: <what's unclear>`, set state back to `Todo`, stop.

4. **Branch safely.** Fetch the remote, then create a dedicated worktree for the
   issue's Linear-generated branch from the current remote default branch. Never switch
   branches in a checkout with user changes and never commit directly to main/master.
   A dirty worktree owned by another process or human is a fail-closed condition.
   Put the worktree in the durable scratch root — `D:\Wolftac\.build-worktrees\` on
   Windows, `~/.build-worktrees/` under WSL — one directory per issue, named
   `build-<ISSUE>`. Never `C:\tmp` or `/tmp`: those get cleared, which drops
   uncommitted work and strands a `.git/worktrees/` entry in the target repo.

5. **Implement — guardrails.** **Before editing anything**, when the spec uses
   `AC-N`/`NG-N` IDs, compare every `AC-N` against every `NG-N`. Non-goals are
   binding. An acceptance criterion that requires a non-goal is a spec defect:
   comment `BLOCKED: AC-x requires NG-y (<detail>)`, set state back to `Todo`, and
   stop. Old-format (unnumbered) specs proceed under the existing rules.
   If it is not in the Linear issue, it does not exist — no side-channel instructions.
   Read the repo's CLAUDE.md/AGENTS.md first; match conventions. Build exactly what
   the spec's Technical approach and Acceptance criteria describe — NO scope creep
   (tangents go in your report as "suggest /wolftac-spec").
   Secrets in .env, never hardcoded. Spec ambiguous in a way that blocks correct
   implementation → comment `BLOCKED: <specific questions>`, state back to `Todo`,
   stop. Don't guess on load-bearing decisions.

6. **Verify.** Execute the spec's Test plan literally. Every acceptance criterion
   needs evidence from running code — "the code looks right" is not verification.

7. **Commit.** Descriptive messages; record SHAs. Before committing, review the staged
   file list: never commit build artifacts (`__pycache__/`, `*.pyc`, `node_modules/`,
   `dist/`, logs); add .gitignore entries as needed. Push with
   `git push -u origin <branch>` and verify the remote head equals local `HEAD`. If no
   writable remote exists, do not advance the issue to `In Review`; report the
   publication blocker without exposing credentials.

   **PR-CREATE-CONTRACT:** After push and remote-SHA verification, create one
   non-draft PR per issue for a GitHub remote. First run
   `gh pr view <branch>` and reuse its URL if present; never create a second
   PR for the branch. Otherwise run
   `gh pr create --title "WD-NNN: <issue title>" --body "<summary>` followed by a
   blank line, `Closes WD-NNN`, another blank line, and
   `Linear: <issue URL>" --base main --head <branch>`. Retry a `gh` rate-limit or
   transient failure once; after a second failure, record the PR-creation blocker
   without failing a build whose branch published successfully. With no GitHub
   remote, skip PR steps and record `local-only repo — no PR`; execution verification
   is unchanged.

8. **Hand off.** Comment on the issue:

   ```
   **BUILD REPORT**
   - **Agent:** Codex / <model>
   - **Implemented:** <what was built, per spec section>
   - **Verified:** <exact commands run and results, per acceptance criterion>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Published head:** <remote branch> @ <exact remote SHA>
   - **PR:** <PR URL, PR-creation blocker, or "local-only repo — no PR">
   - **Deviations from spec:** <none, or what and why>
   - **Notes for review:** <what the reviewer should look at first>
   ```

   Then set state `In Review`.

9. **Stop.** Once the branch is pushed and the remote head verified, remove the
   worktree — review rebuilds its own from the published SHA. Delete generated heavy
   artifacts (`node_modules`, `.next`, `tsconfig.tsbuildinfo`), then
   `git worktree remove <path>` and `git worktree prune`. Confirm with
   `git worktree prune --dry-run -v 2>&1` — silent means clean (`-v` writes to stderr,
   so a bare pipe looks falsely clean). On `Permission denied`, `rm -rf` each
   `.git/worktrees/<name>` it names and re-confirm. Then: one issue per invocation.
