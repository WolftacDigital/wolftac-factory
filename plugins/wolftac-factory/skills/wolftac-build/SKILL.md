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

## Procedure

1. Confirm Linear tools are available and authorized. List `In Progress` claims first.
   Sweep a claim only when its latest activity is older than four hours: post
   `SWEPT: stale claim, no activity since <timestamp>` and return it to `Todo`. Never
   sweep newer activity.
2. List `Todo` + `Factory Queue`, apply the lane, skip blocked dependencies, latest
   `BLOCKED:` comments, and unreachable `Repo:` paths. Skip environment mismatches
   silently. Choose highest priority, oldest first. Exit cleanly when none qualify.
3. Claim by setting `In Progress` and assignee `me`; immediately re-read state and
   assignee. Back off silently on any race. Read the full spec and every comment; the
   newest FAIL report is the rework order.
4. Validate `Repo:` and that it identifies a git repository. Missing or ambiguous
   repository information is a spec defect: comment `BLOCKED: <question>`, restore
   `Todo`, and stop.
5. Fetch the remote. Create a dedicated issue worktree for the Linear branch from the
   current remote default branch. Never switch a checkout with user changes, never
   overwrite another process's dirty worktree, and never commit to main/master.
6. Read repository instructions. Implement only the technical approach and acceptance
   criteria. Put tangential ideas in the report as `suggest $wolftac-spec`.
7. Run the test plan literally and collect execution evidence for every criterion.
   Fix failures and rerun. Inspection alone is not proof.
8. Review status and staged files, exclude secrets and generated artifacts, and commit
   descriptively. Push with `git push -u origin <branch>` and verify the remote head
   SHA equals local `HEAD`. If publication fails or no writable remote exists, do not
   advance to `In Review`; report the blocker without exposing credentials.
9. Post:

   ```text
   **BUILD REPORT**
   - **Agent:** Codex / <actual model>
   - **Implemented:** <spec-scoped summary>
   - **Verified:** <commands and results mapped to criteria>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Published head:** <remote branch> @ <exact SHA; PR URL when available>
   - **Deviations from spec:** <none or explained>
   - **Notes for review:** <highest-risk areas first>
   ```

10. Set `In Review`, verify the persisted state/comment, clean up the worktree when
    safe, and stop.

## Guardrails

- Never guess through a spec defect; return it to `Todo` with one specific question.
- Never expose secrets, force-push, merge, deploy, or mark the issue Done unless the
  approved spec explicitly requires the action and repository policy permits it.
