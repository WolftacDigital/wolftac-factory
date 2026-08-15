# WD-201 main-merge dependency gate

Session lesson from a scheduled `/wolftac-build` pass on WD-201.

## What happened

WD-201 matched the `agentic` lane and was buildable by label/state/repo reachability, but its spec also said: build only after WD-181 is merged to `main`. Linear showed WD-181 as `Done`, yet the repo check showed the WD-181 implementation commit was not an ancestor of `origin/main`.

The build worker briefly claimed WD-201, then discovered the dependency gate and returned it to Todo with a `BLOCKED:` comment.

## Durable procedure update

Before claiming an issue, parse the full specced issue for explicit build gates such as:

- `Build after <issue> merges`
- `<issue> must be merged to main first`
- `Depends on <issue>` when the dependency is a repo-state requirement, not merely a Linear relation

If the gate says a dependency must be on main, verify it in the target repo before claiming where possible:

```bash
git fetch origin --prune
git merge-base --is-ancestor <dependency_commit_or_branch_tip> origin/main
```

If the dependency issue is Linear Done but the required commit/branch is not on `origin/main`, treat it as a genuine spec/build gate: post `BLOCKED: <dependency not merged to main proof>`, return/leave the issue in Todo, and make no implementation changes.

## Why this matters

Linear `Done` is not sufficient proof that a dependency is present on the build base. The build branch should start from the target default branch and should not silently stack unmerged dependency work unless the spec explicitly calls for a stacked branch.