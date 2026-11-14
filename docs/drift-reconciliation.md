# Drift reconciliation

How to fold live-edited (`/learn`) skill content back into this repo without losing
anything — and how not to repeat WD-358.

## The one rule

**`deploy/deploy.ps1` `$Pairs` is the authoritative source→target mapping. Resolve every
deployed path through it. Never assume a repo directory from the file's name or shape.**

The mapping is **not uniform**:

| Surface | Repo source | Pair shape | Covers `references/`? |
| --- | --- | --- | --- |
| Hermes (default profile) | `ports\hermes\<skill>` | whole-directory **tree** | **yes** |
| Hermes worker profiles (`builder`, `reviewer`, `integrator`) | `ports\hermes\<skill>` | junctioned whole-directory **tree** | **yes** |
| Hermes `factory-integrate` profile (retired, not deployed since WD-579) | `ports\hermes-factory-integrate\...` retained for WD-547 | no live deploy target | n/a |
| Claude Code (`~/.claude/skills`) | `canonical\<skill>\SKILL.md` | individual **files** | **no** |
| Claude Code in WSL | `canonical\<skill>\SKILL.md` | individual files | **no** |
| Codex (`~/.codex/prompts`) | `ports\codex\<skill>.md` | individual files | n/a (flat) |

Two consequences that are easy to get wrong:

- `/learn` edits land in the **Hermes** tree, so their repo home is **`ports\hermes\`** —
  not `canonical\`.
- Files placed under `canonical\<skill>\references\` are deployed **nowhere**. No pair
  references them. They are dead weight that can silently diverge from `ports\hermes\`.

## Procedure

1. **Get the authoritative drift list.** Run `pwsh deploy\deploy.ps1 -Check`. Its `DRIFT`
   lines are the complete, correct set. Do not substitute a hand-rolled comparison.
2. **Resolve each drifted path to its source** via `$Pairs` in `deploy\deploy.ps1`.
3. **Classify each entry by direction**, by hashing the deployed file against both the
   repo source and `origin/main`:
   - deployed == `origin/main` → **repo-newer**, stale deploy; a `-Deploy` fixes it.
   - deployed has unique content → **live-newer**; reconcile into the repo *first*.
4. **Reconcile live-newer files into the repo source** the pair actually names.
5. **Prove no loss before deploying.** For every target in `$Pairs`, the deployed file's
   whitespace-normalized line set must be a **subset** of its repo source. A non-empty
   "deployed-only" result is a **stop**, not a warning.
6. **Only then deploy.** Back up the live tree first.

## Skills-only post-merge deployment (WD-632)

`pwsh deploy/deploy.ps1 -Deploy -SkillsOnly` copies only mapped skill Markdown,
flat Codex prompt Markdown, `spec-template.md`, and Markdown references within the
existing owned skill roots. Script helpers, metadata, credentials, schedules and
other targets print `SKILLS-ONLY-SKIPPED` and remain operator work. It does not
build Desktop/plugin archives, delete files, restart sessions, or write through
any reparse-point ancestor (including foreign/unregistered junctions).

For an existing unequal target, without `-Force`:

- **Stale:** its LF-normalized content hash matches the **same source path** at an
  earlier commit reachable from checked-out `HEAD`. The script reads that path's
  history, not `origin/main`, another path, or a deployed-only-line heuristic.
  It makes a byte-verified backup in a unique sibling `.deploy-backup-<UTC>-<id>`
  directory **before** writing, then prints `STALE-UPDATED <Dst> (was <sha7>)`.
  The backup filename is `<original-name>.bak` (for example `SKILL.md.bak`),
  never `SKILL.md` or a `.md` file: recursive skill/prompt discovery must not load
  a second copy of the old skill. The `BACKUP <Dst> -> <backup-path>` line identifies
  the byte-preserved restore source. Repeated updates keep separate backups;
  existing backups are never renamed or deleted automatically.
- **Live-edited or unknown:** print `DRIFT-SKIPPED`, preserve the target and return
  nonzero. No repository, shallow history, failed Git lookup or an untracked path
  cannot establish stale content. A local deletion with zero deployed-only lines
  is still protected unless its complete content actually matches history.
- **Identical or missing:** the normal copy behavior applies. A repeat deploy
  creates no new stale backup for a target already updated.

Plain `-Deploy`, `-Force`, and `-Check` retain their existing behavior. Integrate
never passes `-Force`. `-SkillsOnly` is valid only with `-Deploy`, not `-Check` or
`-Package`. `-Check` still checks all ordinary targets, so script/live-edit drift
may keep its overall exit nonzero even when every updated skill is clean.

Only after a verified `WolftacDigital/wolftac-factory` merge, the six integrate
surfaces check the source checkout identity/default branch/cleanliness, then run:

1. `git -C D:/Wolftac/Factory pull --ff-only`
2. `pwsh -NoProfile -File D:/Wolftac/Factory/deploy/deploy.ps1 -Deploy -SkillsOnly`
3. `pwsh -NoProfile -File D:/Wolftac/Factory/deploy/deploy.ps1 -Check`

A failure stops this sequence, not the already-completed merge. The INTEGRATION
REPORT records each step's exit/result, later steps not run, remaining files and
reasons, and operator work. Other repositories and branchless landings do not run
this sequence. The existing verified Discord approval gate is unchanged.

**Junction gap:** `-Check` does not cover junctioned targets. When the merge changes
`ports/hermes/`, the report must retain `fast-forward D:\Wolftac\.factory-live` as
an operator step, requiring a clean tree and no worker in flight. This feature
never fast-forwards that worktree or claims that a clean copy check proves workers
are current. Scripts, schedules, credentials and non-skill targets remain manual.

For rollback, keep the backup and repository evidence. A previous checkout is not
permission to overwrite a newer or live-edited target: if that content is not in
its reachable history, the same fail-closed skip still applies. An operator must
reconcile/restore the verified backup explicitly; integrate does not force it.

Tests: `python -B -m unittest discover -s deploy/tests -p test_deploy_skills_only.py -v`
executes the real entry point on synthetic three-commit repositories and scratch
homes. WSL probing is shadowed by an inert executable. It never deploys to live homes.

## Check the invariant patterns before reflowing text

`deploy/invariants.json` patterns differ per surface. Some are wrap-tolerant
(`[\s\S]{0,N}`, `\s+`), some require a literal contiguous phrase. Test the *live* text
against the *target file's* pattern before rewrapping anything — the `ports/hermes`
patterns for `one-issue-per-tick` and `review-ci-red-blocks-pass` accept the deployed
wrapping as-is, while the `canonical` patterns do not.

## What went wrong in WD-358 (worked example)

The live tree was compared **only against `canonical\`**, which tracked no `references/`
at the time. That produced the false conclusion that 35 reference docs were untracked
orphans. In fact `ports\hermes\` already tracked all 35, and 32 were byte-identical to
live. The real drift was always the 5 files `-Check` had been reporting.

The 35 "orphans" were committed to `canonical\*\references\`, where nothing deploys them,
while `ports\hermes\*\SKILL.md` — the file the Hermes surface actually loads — was left
stale. A `-Deploy -Force` at that point would have overwritten the live skills from those
stale sources and destroyed 40+ lines of `/learn` content plus three reference docs.

It was caught by step 5, and only by step 5.

Two process notes worth keeping:

- Review recorded "all changes confined to `canonical/`" as a **clean** scope result. That
  confinement *was* the defect. A reviewer who shares the builder's mental model of where
  files live will not catch this class — use a reviewer with fresh context.
- The step-5 pre-diff is the last line of defence. Keep a **negative test** for it: revert
  one reconciled file to its stale source and confirm the check fails. A safety check that
  cannot fail is not a safety check.
