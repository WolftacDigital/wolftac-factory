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
