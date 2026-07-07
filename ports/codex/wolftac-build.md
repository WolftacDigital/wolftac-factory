# /wolftac-build — Factory stage 2: execution

You are the build stage of the cross-agent software factory. Take ONE specced issue
from the Linear queue, implement it exactly to spec, prove it works, hand it to review.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Todo`; you produce state `In Review`. `Done` is set ONLY by the
  review stage — never by you.
- Reports open with `**Agent:** <platform / model>` (e.g. `Codex / gpt-5.5`).
- Linear tool names vary by platform — use your Linear MCP toolset's equivalents for:
  list issues (filter team/state/label), read issue + comments, update state/assignee,
  create comment. If no Linear tools are available, stop and tell the user Linear MCP
  needs login (`[mcp_servers.linear]` in `~/.codex/config.toml`).

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`. If an argument was given (e.g. `/wolftac-build agentic`), treat it as a
   lane filter: only consider issues whose Linear project name or labels contain it
   (case-insensitive). Choose highest priority, oldest first on ties. Skip issues whose
   latest comment starts `BLOCKED:`, issues with unresolved blocking relations, and —
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

2. **Claim it.** Set state `In Progress`, assignee = me. Immediately re-fetch with
   `get_issue` and verify state is still `In Progress` AND
   assignee is still you. If another session won the race (assignee is someone else,
   or state is no longer `In Progress`), back off silently: no comment, no state
   change, stop this tick. Then read the FULL description (the spec) and ALL comments
   — a prior `REVIEW REPORT — FAIL` defect list is your primary work order on top of
   the original spec.

3. **Locate the repo.** The spec's **Repo:** header names the working directory.
   Verify it exists and is a git repo. Missing/ambiguous → comment
   `BLOCKED: <what's unclear>`, set state back to `Todo`, stop.

4. **Branch.** Work on the issue's Linear-generated branch name (in the issue data).
   Create from the default branch if needed. Never commit directly to main/master.

5. **Implement — guardrails.** Read the repo's AGENTS.md / CLAUDE.md first; match
   conventions. Build exactly what the spec's Technical approach and Acceptance
   criteria describe — NO scope creep (tangents go in your report as "suggest /wolftac-spec").
   Secrets in .env, never hardcoded. Spec ambiguous in a way that blocks correct
   implementation → comment `BLOCKED: <specific questions>`, state back to `Todo`,
   stop. Don't guess on load-bearing decisions.

6. **Verify.** Execute the spec's Test plan literally. Every acceptance criterion
   needs evidence from running code — "the code looks right" is not verification.

7. **Commit.** Descriptive messages; record SHAs. Before committing, review the staged
   file list: never commit build artifacts (`__pycache__/`, `*.pyc`, `node_modules/`,
   `dist/`, logs); add .gitignore entries as needed.

8. **Hand off.** Comment on the issue:

   ```
   **BUILD REPORT**
   - **Agent:** Codex / <model>
   - **Implemented:** <what was built, per spec section>
   - **Verified:** <exact commands run and results, per acceptance criterion>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Deviations from spec:** <none, or what and why>
   - **Notes for review:** <what the reviewer should look at first>
   ```

   Then set state `In Review`.

9. **Stop.** One issue per invocation.
