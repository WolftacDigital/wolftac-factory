# WSL-hosted Linear factory review proof pattern

Use this when a Wolftac Factory Queue issue's spec says `Repo: /home/... (WSL Ubuntu)` but the active Hermes host is Windows.

## Pattern

1. **Do not mark the issue unreviewable just because the POSIX path is missing from Git Bash.** First check the WSL path via `wsl.exe -e sh -lc 'test -d /home/... && echo reachable || echo missing'`.
2. **Avoid disturbing the active branch or local uncommitted artifacts.** If the repo is reachable, create a detached review worktree from the build-report branch:
   ```sh
   wsl.exe -e sh -lc 'cd /home/logan_wolftac/wolftac-agentic-os && d=$(mktemp -d /tmp/wolftac-review-ISSUE-XXXXXX) && git worktree add --detach "$d" BRANCH && printf "%s\n" "$d"'
   ```
   Some Windows/WSL hosts clear `/tmp` between separate `wsl.exe` invocations. If the returned path vanishes while `git worktree list` shows a prunable entry, run `git worktree prune --verbose`, then create the detached review worktree at a stable sibling path such as `/home/logan_wolftac/wolftac-review-ISSUE`; remove that stable path with `git worktree remove --force` during cleanup.
3. **Gather real evidence from the worktree:** `git status --short`, `git log --oneline -5`, and `git diff main...HEAD --stat` plus targeted diff inspection for changed files.
4. **Re-run the spec test plan from scratch in the worktree.** If dependencies are absent in the fresh worktree, install from the lockfile (`npm ci`) before running build/lint/tests. Treat install warnings as evidence, not defects, unless they fail the requested verification.
5. **Run baseline checks on the introduced diff, not the entire inherited test file.** For secret/debug scans, prefer `git diff main...HEAD -G'pattern' -- changed-files` or added-line scans so pre-existing fixtures that mention `token` or `secret` are not misreported as new defects. When the scan flags static route metadata or tests for existing surfaces (for example labels like `Secret Inventory`, `Operator Token`, `tokens`, or navigation assertions), inspect the added values and classify them as clean only if they are non-secret labels/descriptions/test selectors and not credential material, bearer strings, env values, debug calls, or runtime leaks.
6. **Concurrent-review race check before side effects:** after long installs/tests/builds, re-read the issue and latest comments before posting. If no newer REVIEW REPORT or state move exists, post the report; otherwise follow the main skill's race guidance and avoid duplicate/overwriting updates.
7. **Linear pass flow:** post the REVIEW REPORT comment first, tick acceptance checkboxes in the issue description, set the issue to Done, then re-query Linear by short identifier and verify: state is Done, the review comment ID is present, and all acceptance lines are checked. Linear may normalize Markdown checkboxes to uppercase `- [X]`; verification scripts should accept both `- [x]` and `- [X]` rather than falsely reporting zero checked criteria after a successful update.

## Notes

- Keep the review to one issue per cron tick.
- Do not merge, deploy, restart services, or edit secrets during review.
- Detached worktrees make the review reproducible while preserving any active builder branch and untracked local artifacts in the source repo.
