# Non-repo artifact review proof pattern

Use this when a factory-review spec requires a non-tracked proof artifact (for example a copy in `Downloads/`) to match the reviewed branch.

## When this applies

- The implementation branch/diff is otherwise correct.
- The acceptance criteria explicitly mention a refreshed convenience copy, exported bundle, generated preview, or other file outside the git repository.
- The artifact can be derived deterministically from the reviewed branch without changing design or tracked code.

## Review sequence

1. Verify the branch/diff first in an isolated worktree and confirm tracked repo scope.
2. Run the normal test plan against the repo file.
3. Compare the non-repo artifact against the repo source by SHA-256 or byte-for-byte compare.
4. If the artifact is stale, copy from the reviewed branch to the specified artifact path.
5. Re-run the compare and record both hashes, file sizes, and `identical=True`/equivalent.
6. Classify as **PASS WITH FIXES** because the reviewer changed a required proof artifact, but note that no repo commit/SHA is needed when no tracked file changed.
7. Tick the criterion only after the post-copy comparison passes.

## Report language

In **Fixes applied**, be explicit:

> Refreshed the non-git convenience copy from the reviewed repo file, then re-ran hash verification. No repo commit/SHA was needed because no tracked repo file changed.

In **Test plan reproduced**, include the exact hash compare result, not just "copied".

## Guardrails

- Do not treat this as license to mutate deployment targets, production files, secrets, or service state. Convenience copies and proof artifacts are low-risk only when the spec explicitly names them and they are deterministic copies from the reviewed branch.
- If the artifact refresh would overwrite user-edited content whose source of truth is unclear, fail back to Todo with a defect/blocker instead of guessing.
- Do not merge or deploy as part of review unless Jonathan explicitly approved that separate action.
