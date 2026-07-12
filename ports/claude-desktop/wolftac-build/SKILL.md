---
name: wolftac-build
description: Factory stage 2 — pull the highest-priority Todo issue labeled Factory Queue from Linear (team Wolftac Digital), implement it per its spec, verify, and move it to In Review with a build report. One issue per invocation. NOTE - from Claude Desktop this stage can only run if the chat has file/terminal access to the target repo (e.g. via an MCP server); otherwise it reports what's queued and defers to a coding agent.
---

# /wolftac-build — Factory stage 2: execution

Take ONE specced issue from the Linear queue, implement it exactly to spec, prove it
works, hand it to review.

## Desktop reality check (do this first)
This stage needs to edit files, run git, and execute tests in the spec's **Repo**.
From Claude Desktop that requires an MCP server with filesystem/terminal access to
that repo (e.g. desktop-commander or a filesystem server). If this chat has no such
access: list what's buildable in the queue, tell the user to run /wolftac-build from Claude
Code, Codex, or Hermes, and STOP. Do not claim an issue you cannot actually build.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Todo`; you produce `In Review`. `Done` is set ONLY by the review
  stage — never by you.
- Reports open with `**Agent:** <platform / model>` (e.g. `Claude Desktop / Fable 5`).
- Tools: claude.ai Linear connector (`list_issues`, `get_issue`, `save_issue`,
  `save_comment`, `list_comments`). Not available → ask the user to enable it.

## Procedure

1. **Pick up work.** `list_issues`: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`. If the user gave a lane filter (e.g. "build the crm lane"), only
   consider issues whose Linear project name or labels contain it (case-insensitive).
   Highest priority, oldest on ties. Skip issues whose latest comment starts
   `BLOCKED:`, issues with unresolved blocking relations, and — SILENTLY, no comment
   or state change — issues whose spec **Repo:** path this chat cannot reach
   (`BLOCKED:` is reserved for genuine spec defects).
   Before choosing a Todo issue, perform a read-first stale-claim sweep: inspect
   `In Progress` issues with label `Factory Queue` (and the lane filter, if any).
   If the most recent comment, or `startedAt`/claim time when there are no comments,
   is OLDER than 4 hours, you may post exactly
   `SWEPT: stale claim, no activity since <timestamp>` and reset the issue to `Todo`.
   If there is ANY comment younger than 4 hours, the issue is UNTOUCHABLE — never
   sweep it. This sweep is the ONLY time build posts a comment during pickup; during
   a claim-race back off you post NOTHING.
   Queue empty → "Factory queue: nothing buildable", stop cleanly.

2. **Claim it.** `save_issue`: state `In Progress`, assignee `me`. Immediately
   re-fetch with `get_issue` and verify state is still `In Progress` AND
   assignee is still you. If another session won the race (assignee is someone else,
   or state is no longer `In Progress`), back off silently: no comment, no state
   change, stop this tick. Then read the FULL spec and ALL comments — a prior
   `REVIEW REPORT — FAIL` defect list is your primary work order on top of the
   original spec.

3. **Locate the repo.** The spec's **Repo:** header names the working directory.
   Verify you can reach it (see reality check). Header missing/ambiguous (a spec
   defect) → comment `BLOCKED: <what's unclear>`, state back to `Todo`, stop.
   Merely unreachable from this chat → should have been skipped at step 1; un-claim
   (state `Todo`, no comment) and stop.

4. **Branch safely.** Fetch the remote, then create a dedicated worktree for the
   issue's Linear-generated branch from the current remote default branch. Never switch
   branches in a checkout with user changes and never commit directly to main/master.
   A dirty worktree owned by another process or human is a fail-closed condition.

5. **Implement — guardrails.** Read the repo's CLAUDE.md first; match conventions.
   Build exactly to the spec's Technical approach and Acceptance criteria — NO scope
   creep (tangents go in the report as "suggest /wolftac-spec"). Secrets in .env, never
   hardcoded. Blocking ambiguity → `BLOCKED: <questions>` comment, state `Todo`, stop.

6. **Verify.** Execute the spec's Test plan literally. Every criterion needs evidence
   from running code — "the code looks right" is not verification.

7. **Commit.** Descriptive messages; record SHAs. Review the staged file list before
   committing: never commit artifacts (`__pycache__/`, `*.pyc`, `node_modules/`,
   `dist/`, logs); add .gitignore entries as needed. Push with
   `git push -u origin <branch>` and verify the remote head equals local `HEAD`. If no
   writable remote exists, do not advance the issue to `In Review`.

8. **Hand off.** Comment:

   ```
   **BUILD REPORT**
   - **Agent:** Claude Desktop / <model>
   - **Implemented:** <what was built, per spec section>
   - **Verified:** <exact commands run and results, per criterion>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Published head:** <remote branch> @ <exact remote SHA; PR URL when available>
   - **Deviations from spec:** <none, or what and why>
   - **Notes for review:** <what the reviewer should check first>
   ```

   Then `save_issue`: state `In Review`.

9. **Stop.** One issue per invocation.
