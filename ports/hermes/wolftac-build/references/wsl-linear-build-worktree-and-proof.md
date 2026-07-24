# WSL Linear build worktree + proof pattern

Use this when a Factory Queue issue's **Repo:** header names a WSL path while Hermes is running on Windows.

## Pattern

1. Verify repo reachability **and identity** through WSL, not the Windows shell. For any
   ownership check that gates a Linear mutation, use literal repo/branch paths rather
   than nested shell variables, and inspect the worktree list before claiming:
   ```bash
   MSYS2_ARG_CONV_EXCL='*' wsl.exe -d Ubuntu -- bash -lc 'test -d /home/logan_wolftac/repo && git -C /home/logan_wolftac/repo rev-parse --is-inside-work-tree && git -C /home/logan_wolftac/repo rev-parse --show-toplevel && git -C /home/logan_wolftac/repo remote get-url origin && git -C /home/logan_wolftac/repo worktree list --porcelain'
   ```
   Confirm the reported top-level and remote match the spec. If the output instead
   describes the host current checkout, discard the probe result and rerun it before
   changing Linear.
2. If the generated branch already owns a worktree, inspect that literal worktree path
   with `status --short --branch`, `diff --stat`, `diff --cached --stat`, and
   `status --short`. A dirty target is operator-attention work: do not claim or edit it.
3. If no worktree owns the branch, create a deterministic dedicated path without nested
   variables or command substitutions:
   ```bash
   MSYS2_ARG_CONV_EXCL='*' wsl.exe -d Ubuntu -- bash -lc 'test ! -e /tmp/wolftac-build-WD-123 && git -C /home/logan_wolftac/repo worktree add /tmp/wolftac-build-WD-123 jonathan/wd-123-branch'
   ```
4. Run `npm ci` in the worktree before Node tests if dependencies are absent.
5. Execute the spec tests literally, then add a runtime/API smoke when route behavior was changed.
6. Commit only the intended source/test files from the worktree. Do not commit generated build artifacts, logs, `.next/`, `node_modules/`, or runtime config artifacts.
7. Before Linear handoff, re-query the exact issue, post the BUILD REPORT, move to `In Review`, then re-query again to verify both the state and the report comment.

## Nested WSL shell pitfall

Hermes' Windows `terminal` runs through a POSIX shell before `wsl.exe`; `$repo`,
`$branch`, `$wt`, and `$(...)` inside a nested script may be expanded or reparsed before
WSL receives them. A failed variable expansion can silently turn `git -C "$repo"` into
`git -C ""`, causing Git to inspect the host current checkout and return plausible but
wrong worktree data. For safety gates, prefer literal paths and verify repository
identity in the same call. If variables are unavoidable for non-mutating work, escape
them as `\$repo` / `\$wt` and verify the resolved top-level before trusting output.

For compound Git probes, prefer a single WSL-owned shell and change directory inside it:

```bash
MSYS2_ARG_CONV_EXCL='*' wsl.exe -e bash -lc 'cd /home/logan_wolftac/repo && git fetch origin --prune && git rev-parse origin/main'
```

Avoid packing outer-shell command substitutions (`$(...)`) and Git pathspec magic such as `:(exclude)` into the same nested command when a simple fixed ref or separate probe works. Those tokens can be reparsed before WSL receives them and produce misleading Bash syntax errors. Split complex discovery into sequential WSL commands or use literal paths throughout safety-critical discovery.

## Runtime smoke detail from WD-178

For an API action that creates state before discovering an honest-offline condition, persist that state before returning the offline response if the manual fallback depends on it. WD-178's `auto-run` created a pending round, discovered no verifier providers, and needed to write the store before returning so the manual `verify` fallback could pass the same round.