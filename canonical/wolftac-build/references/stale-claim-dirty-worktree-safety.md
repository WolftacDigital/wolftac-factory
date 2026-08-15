# Dirty issue-worktree safety

Use this in two places:

- during the stale-claim sweep when an `In Progress` Factory issue already has a dedicated worktree or checked-out generated branch;
- before claiming a `Todo` candidate whose Linear-generated branch may already own a reused worktree.

## Durable rule

Linear state and inactivity are not proof that filesystem work is abandoned. Before posting `SWEPT:`, resetting, or claiming:

1. Resolve the Linear-generated branch and inspect `git worktree list --porcelain`.
2. Also inspect the deterministic durable issue path (`build-<ISSUE>` under the configured build-worktree root), even when `git worktree list` does not mention it. A prior failed cleanup can leave an unregistered/orphaned directory containing uncommitted work while the local branch still exists.
3. If either a registered worktree owns the branch or the deterministic issue directory exists, run `git status --short --branch --untracked-files=all` there before any claim/reset or `git worktree add`. If Git no longer recognizes an orphaned directory as a worktree, inspect its files without registering, deleting, overwriting, or reusing it; report the path for operator recovery.
4. If the issue worktree or orphaned issue directory is dirty/non-empty, do **not** sweep, reset, claim, edit, commit, discard, comment, change Linear state, or run `git worktree add` against that path. Treat it as operator-attention work whether Linear says `In Progress` or `Todo`.
5. Report the worktree path and complete dirty summary in the cron result:
   - `git diff --stat` for unstaged tracked changes;
   - `git diff --cached --stat` for staged tracked changes;
   - `git status --short --untracked-files=all` for untracked paths and the authoritative changed-path list.
   Do not substitute `git diff --stat origin/main...HEAD`: that range reports committed branch differences and can be empty while the worktree contains substantial uncommitted work.
6. Do not post `BLOCKED:` because preserved worktree state is an execution conflict, not a spec defect.
7. Continue to the next lane-eligible candidate in normal priority order. If none remains, emit the operator-attention report; do not collapse this condition into a generic empty-queue or `[SILENT]` result.
8. Resume only after an operator has deliberately preserved, committed, transferred, or cleared the work. Re-run the normal pickup checks afterward.

File modification times can help explain recency, but they do not establish ownership and never authorize taking over or deleting dirty work. The safe signal is the dirty issue worktree itself.

## Why this matters

A prior worker can modify files without reaching the Linear report step. Sweeping solely from Linear timestamps creates a reset/claim/fail loop, while claiming a dirty `Todo` branch creates the same ownership collision one step later. Both paths risk a second worker overwriting uncommitted implementation. Worktree inspection belongs before stale sweeping **and** before claiming a selected candidate—not only after the claim.