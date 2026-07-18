# Existing feature branch behind current main

Use this when the Linear-generated branch already contains approved implementation commits but its merge base predates required work now present on `origin/main`.

## Safe integration pattern

1. Fetch and verify the prerequisite commit is an ancestor of `origin/main`; Linear state is not git proof.
2. Inspect the existing branch before changing it: branch-only log, merge base, diff of each implementation commit, remote head, and whether another worktree owns the branch.
3. Create a dedicated worktree for the existing feature branch. Do not switch the user's checkout and do not discard the existing implementation.
4. Prefer a normal merge of `origin/main` into the feature branch when history rewriting or force-push approval is absent. Resolve conflicts by preserving current-main contracts while carrying forward only the issue's approved feature delta.
5. Before committing the merge, compare the staged tree to `origin/main`:

   ```sh
   git diff --cached --name-status origin/main
   git diff --cached --stat origin/main
   git diff --check --cached
   ```

   The resulting delta must contain only issue-scope files even though `git status` during the merge may list many current-main paths as staged.
6. Run the complete current test plan after conflict resolution. Commit the merge, push normally, and verify `git ls-remote` equals local `HEAD`.
7. In the BUILD REPORT, list both the original implementation commit(s) and the final integration commit. State whether a PR exists; do not manufacture one unless the workflow calls for it.

## Pitfalls

- Do not reset the feature branch to `origin/main` and cherry-pick unless branch rewrite/force-push is explicitly allowed.
- Do not treat the large merge-status list as scope proof; the authoritative scope check is the staged or final diff against `origin/main`.
- Do not create a no-op replacement commit when the existing implementation is already correct; integrate and re-verify it.
