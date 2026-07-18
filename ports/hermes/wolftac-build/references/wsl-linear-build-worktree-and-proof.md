# WSL Linear build worktree + proof pattern

Use this when a Factory Queue issue's **Repo:** header names a WSL path while Hermes is running on Windows.

## Pattern

1. Verify repo reachability through WSL, not the Windows shell:
   ```bash
   wsl.exe -d Ubuntu -- bash -lc 'test -d /home/logan_wolftac/repo && git -C /home/logan_wolftac/repo rev-parse --is-inside-work-tree'
   ```
2. If the main checkout is dirty or on another branch, create a detached build worktree from the Linear-generated branch:
   ```bash
   wsl.exe -d Ubuntu -- bash -lc 'repo=/home/logan_wolftac/repo; branch=jonathan/wd-123-branch; wt=$(mktemp -d /tmp/wolftac-build-WD-123-XXXXXX); rmdir "$wt"; git -C "$repo" worktree add "$wt" "$branch"; printf "%s" "$wt"'
   ```
3. Run `npm ci` in the worktree before Node tests if dependencies are absent.
4. Execute the spec tests literally, then add a runtime/API smoke when route behavior was changed.
5. Commit only the intended source/test files from the worktree. Do not commit generated build artifacts, logs, `.next/`, `node_modules/`, or runtime config artifacts.
6. Before Linear handoff, re-query the exact issue, post the BUILD REPORT, move to `In Review`, then re-query again to verify both the state and the report comment.

## Nested WSL shell pitfall

Hermes' Windows `terminal` runs through a POSIX shell before `wsl.exe`; unescaped `$repo`, `$branch`, `$wt`, etc. can be expanded by the outer shell before WSL receives the script. In nested `wsl.exe -- bash -lc '...'` commands, either avoid variables or escape variables as `\$repo` / `\$wt` when the outer shell might expand them.

## Runtime smoke detail from WD-178

For an API action that creates state before discovering an honest-offline condition, persist that state before returning the offline response if the manual fallback depends on it. WD-178's `auto-run` created a pending round, discovered no verifier providers, and needed to write the store before returning so the manual `verify` fallback could pass the same round.