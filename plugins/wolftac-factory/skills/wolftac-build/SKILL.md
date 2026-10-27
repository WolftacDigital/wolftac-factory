---
name: wolftac-build
description: Claim and implement one build-ready Todo issue from the Wolftac Digital Linear Factory Queue, verify every acceptance criterion, publish the branch, and hand it to In Review. Use when asked to run a Wolftac Factory build or build one queued lane. Never mark work Done.
---

# Wolftac Build

Implement exactly one queued issue and publish immutable evidence for review.

## Contract

- Team `Wolftac Digital`; label `Factory Queue`; consume `Todo`; produce `In Review`.
- One issue per invocation. `Done` belongs exclusively to `$wolftac-integrate`;
  review's pass verdict is `Approved`.
- Reports start with `**Agent:** Codex / <actual model>`.
- A lane argument filters project names or labels case-insensitively.

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

1. Confirm Linear tools are available and authorized. List `In Progress` claims first.
   Sweep a claim only when its latest activity is older than four hours: post
   `SWEPT: stale claim, no activity since <timestamp>` and return it to `Todo`. Never
   sweep newer activity.
2. List `Todo` + `Factory Queue`, apply the lane, skip blocked dependencies, latest
   `BLOCKED:` comments, and unreachable `Repo:` paths. Skip environment mismatches
   silently. **PARKED-NEEDS-HUMAN-SKIP:** Silently skip any issue whose latest review
   report is `REVIEW REPORT — NEEDS HUMAN` while it remains assigned to Jonathan or
   lacks a later Jonathan decision comment. Do not comment or change state. A later
   decision comment from Jonathan plus removal of his assignment clears the park and
   becomes part of the work order; the comment author must be Jonathan himself — a
   decision relayed on his behalf by an agent does NOT clear the park.
   **REWORK-FIRST-ORDERING:** Among eligible `Todo` issues,
   select issues whose latest review report is `REVIEW REPORT — FAIL` before fresh
   `Todo` issues. Within the rework tier and within the fresh tier, preserve priority
   order (Urgent > High > Medium > Low), oldest first on ties. Exit cleanly when none
   qualify.
3. Claim.
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
   Read the full spec and every comment; the
   newest FAIL report is the rework order.
4. Validate `Repo:` and that it identifies a git repository. Missing or ambiguous
   repository information is a spec defect: comment `BLOCKED: <question>`, restore
   `Todo`, and stop.
5. Fetch the remote. Create a dedicated issue worktree for the Linear branch from the
   current remote default branch. Never switch a checkout with user changes, never
   overwrite another process's dirty worktree, and never commit to main/master.
   Put it in the durable scratch root — `D:\Wolftac\.build-worktrees\` on Windows,
   `~/.build-worktrees/` under WSL — named `build-<ISSUE>`. Never `C:\tmp` or `/tmp`:
   those get cleared, dropping uncommitted work and stranding a `.git/worktrees/`
   entry in the target repo.
6. Read the repo's CLAUDE.md/AGENTS.md first; match its conventions. Implement only
   the technical approach and acceptance criteria. Put tangential ideas in the report
   as `suggest $wolftac-spec`.
7. Run the test plan literally and collect execution evidence for every criterion.
   Fix failures and rerun. Inspection alone is not proof.
8. Review status and staged files, exclude secrets and generated artifacts, and commit
   descriptively. Push with `git push -u origin <branch>` and verify the remote head
   SHA equals local `HEAD`. If publication fails or no writable remote exists, do not
   advance to `In Review`; report the blocker without exposing credentials.
9. **PR-CREATE-CONTRACT:** After push and remote-SHA verification, create one
   non-draft PR per issue for a GitHub remote. First run
   `gh pr view <branch>` and reuse an existing PR URL; never open a second PR
   for the branch. Otherwise run
   `gh pr create --title "WD-NNN: <issue title>" --body "<summary>` followed by a
   blank line, `Closes WD-NNN`, another blank line, and
   `Linear: <issue URL>" --base main --head <branch>`. Retry a `gh` rate-limit or
   transient failure once; after a second failure, record the PR-creation blocker
   without failing a successfully published branch. With no GitHub remote, skip PR
   steps and record `local-only repo — no PR`; execution verification is unchanged.
10. Post:

   ```text
   **BUILD REPORT**
   - **Agent:** Codex / <actual model>
   - **Implemented:** <spec-scoped summary>
   - **Verified:** <commands and results mapped to criteria>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Published head:** <remote branch> @ <exact SHA>
   - **PR:** <PR URL, PR-creation blocker, or "local-only repo — no PR">
   - **Deviations from spec:** <none or explained>
   - **Notes for review:** <highest-risk areas first>
   ```

11. Set `In Review` and verify the persisted state/comment. Then clean up: delete heavy
    artifacts (`node_modules`, `.next`), `git worktree remove <path>`,
    `git worktree prune`, and confirm with `git worktree prune --dry-run -v 2>&1`
    (silent == clean; `-v` writes to stderr). On `Permission denied`, `rm -rf` each
    `.git/worktrees/<name>` prune names and re-confirm. Then stop.

## Guardrails

- Never guess through a spec defect; return it to `Todo` with one specific question.
- Never expose secrets, force-push, merge, deploy, or mark the issue Done unless the
  approved spec explicitly requires the action and repository policy permits it.
