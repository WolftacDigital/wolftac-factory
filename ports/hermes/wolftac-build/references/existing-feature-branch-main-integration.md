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
- When regression tests extract a baseline script to scratch, pass identical explicit dependency paths to baseline and branch executions; defaults based on script location otherwise compare different inputs. For cross-platform PowerShell tests, pass an explicit profile path rather than assuming Ubuntu defines Windows USERPROFILE. Keep drift checks enabled and assert their output rather than filtering unexpected findings away. Tests using `git show origin/main:<path>` require the CI checkout to fetch that ref (for example actions/checkout with fetch-depth: 0); never replace a missing baseline with the feature tree or a skip. Set TMPDIR explicitly to the profile scratch root before test execution instead of assuming the terminal inherited it.
- Verify auto-merged tests as carefully as conflicted production code. When main adds per-lane context (such as a worker profile) and a feature splits probing from dispatch, carry that context through the intermediate records and test actual dispatch argv across lane-order permutations. Auto-merge can leave old tuple unpacking or shared-loop-variable reuse that routes every lane to the last profile. Compare unchanged baseline functions structurally to prove existing tests and helpers were preserved.
- Do not create a no-op replacement commit when the existing implementation is already correct; integrate and re-verify it. If the fetched default is already a parent/ancestor of the feature head and a normal merge reports `Already up to date`, record the exact parents, ancestry check, base-relative diff and fresh execution proof. A plain rebase can replay an earlier pre-merge conflict even though the published tree already resolves it; do not force-rewrite history merely to satisfy that replay. Explicitly hand this integration-method mismatch to review.
- For byte-exact baseline fixtures extracted with `git archive` on Windows, use command-scoped `-c core.autocrlf=false` and compare extracted bytes with `git show <ref>:<path>`. Git archive can otherwise apply CRLF conversion; do not misclassify that conversion as a source regression or weaken the byte-equality assertion.
- When current main adds a check to an existing watchdog, keep that check enabled in feature regression fixtures. Provide valid scratch inputs (for example a real clean Git root for `/learn`), assert new cannot-check states explicitly, and compare all findings plus diagnostic sections against main. Do not filter new rows or skip the check to preserve old expected counts. Test pending input and prove a scratch mutation removing the dispatch fails the regression; assert DryRun produces no state, snapshots or alert-outbox files.
- Pin the fetched default SHA used for a merge and record MERGE_HEAD while the merge is pending. Remote-tracking refs are shared across worktrees and another worker's fetch can advance origin/main during tests. Compare staged scope to the pinned merge parent, then separately detect default-branch movement; apparent deletions against a newly advanced ref are not proof that the conflict resolution deleted those features. Integrate the newer default and reverify before claiming current-main proof.
- Do not assume a reported rebase conflict implies a normal merge needs manual resolution. A previously merged feature can conflict when its original commits are replayed even though merging current main is clean. Use the authorized normal merge, compare the three issue files against the previously reviewed head, rerun the full proof plan, and report unchanged implementation honestly rather than manufacturing a code fix.
