# Build comment noise

## CI-capacity hold

When GitHub cannot start a failed check on the published head, inspect its
check-run annotations with
`gh api repos/<owner>/<repo>/check-runs/<id>/annotations`. A capacity annotation
starts with `The job was not started because`. A job that ran steps and failed is
an ordinary failure; unreadable annotations are not proof of capacity failure.

Allow at most one `gh run rerun --failed <run-id>` per tick and wait up to five
minutes. Started steps mean capacity is back; follow the normal flow. A new
attempt failing with the same annotation is a capacity blocker. Still queued at
five minutes means follow the normal flow, where review handles `waiting on CI`.
Do not change review's gate or try to restore capacity, billing or workflows.

On the repeated capacity failure, use the normal BUILD REPORT fields with the
exact heading `**BUILD REPORT — INCOMPLETE: CI CAPACITY BLOCKER**` and this line:

```text
- **Next:** a human comment on this issue after CI capacity is restored ends the CI-CAPACITY-HOLD.
```

Post once, return the issue to `Todo`, clear only the assignee the run set, and
stop. Do not advance to `In Review` or call this a spec-defect `BLOCKED:` report.
Quote only the fixed annotation prefix, not arbitrary annotation text.

At pickup, consider every lane-eligible Todo candidate before claiming any.
For each, ignore comment bodies starting with `SWEPT:` and inspect the newest
remaining comment. Its heading is the first non-blank line after skipping
`**Agent:**` lines, optionally prefixed with `- `. Both `BUILD REPORT` and
`CI CAPACITY BLOCKER` must occur on that heading line, case-sensitively. This
includes the old `EXISTING CI CAPACITY BLOCKER` heading.

Derive a repo key from the spec's `**Repo:**` header: prefer its first backticked
`owner/name` token and lowercase it. Otherwise lowercase the backticked path and
remove trailing `/` or `\`. For example, `WolftacDigital/wolftac-crm` becomes
`wolftacdigital/wolftac-crm`; `D:\Wolftac\Factory\` becomes
`d:\wolftac\factory`. Missing headers still follow the existing spec-defect path.

A blocker that is the newest non-SWEPT comment on a lane-eligible Todo issue
holds every Todo candidate with the same repo key in that lane, regardless of
priority or rework tier. Skip held candidates without claims, comments (including
NO-SILENT-SKIP diagnostics for this hold), or state/assignee writes. Print the
hold line in the run output, never in Linear. Other repositories remain eligible.
If none is buildable, also print `Factory queue: nothing buildable`.

Any newer non-SWEPT comment from anyone ends that issue's hold, as does the issue
leaving Todo. This is readiness, not approval. There is no time-based expiry or
GitHub pickup probe. A lane-excluded issue cannot impose a hold in this run.
The hold is computed afresh from comments, not stored. Existing park, dependency,
BLOCKED, priority, sweep and claim protections still apply to unheld candidates.
No historical comments are edited or deleted.

### Fixture walkthrough

These are synthetic replay inputs, not live Linear actions. All timestamps are
UTC. Each row starts with F1 unless it states a change. The repo header in F1 is
`**Repo:** ` followed by backticked `WolftacDigital/wolftac-crm`. WD-461 is Todo,
in lane, and its latest review is FAIL. Its comments are:

- 2026-10-16T03:55:00Z: `**BUILD REPORT**`.
- 2026-10-16T04:29:00Z: `**REVIEW REPORT — FAIL**`.
- 2026-10-16T05:33:04Z: an optional `**Agent:**` identity line, blank line, then
  `**BUILD REPORT — INCOMPLETE: CI CAPACITY BLOCKER**`.

The exact hold line, **H**, is:

```text
CI-CAPACITY-HOLD wolftacdigital/wolftac-crm: WD-461 since 2026-10-16T05:33:04Z
```

`H` in the table always expands to that exact line. A dash means no hold line.
"Normal claim" means the existing claim/read-back protocol for the picked issue,
not permission to change a held issue. Synthetic WD-900 is a fresh High-priority
Todo in the same repo; WD-901 is an eligible Todo in `wolftacdigital/wolftac-factory`.

| Fixture | Comments / state / lane at tick | Tick time | Expected decision | Expected run-output line | Expected Linear writes |
| --- | --- | --- | --- | --- | --- |
| F1 | Base comments above | 2026-10-16T06:06:00Z | Hold CRM; skip WD-461. Repeated ticks with these same inputs still skip. | H; `Factory queue: nothing buildable` | Zero. The 51 later repeated blocker reports are not produced. |
| F2 | F1 plus WD-900 with no comments, same repo | 2026-10-16T06:06:00Z | Skip both WD-461 and WD-900, despite the fresh issue's High priority. | H; `Factory queue: nothing buildable` | Zero on both issues. |
| F3 | F1 plus WD-901 with no comments, different repo | 2026-10-16T06:06:00Z | Skip WD-461; pick WD-901 normally. | H | Zero on WD-461; normal claim on WD-901 only. |
| F4 | Jonathan adds `Actions budget raised` at 2026-10-20T13:00:00Z; WD-900 also present | 2026-10-20T13:01:00Z | Hold cleared; pick WD-461 before WD-900 in the rework tier. If a bounded retry still proves capacity failure, post one new blocker and hold from its timestamp next tick. | No hold line at this pickup | Normal claim on WD-461; only if still blocked, one fixed-heading report, Todo and clearing the run's own assignee. |
| F5 | Add `SWEPT: stale claim, no activity since 2026-10-16T05:33:04Z` at 2026-10-16T06:00:00Z | 2026-10-16T06:06:00Z | Ignore SWEPT for this test; hold still active. | H; `Factory queue: nothing buildable` | Zero. |
| F6 | Replace only the base blocker's heading with `**BUILD REPORT — INCOMPLETE: EXISTING CI CAPACITY BLOCKER**`; use a leading `- **Agent:**` identity line | 2026-10-16T06:06:00Z | Legacy heading recognized; hold active. | H; `Factory queue: nothing buildable` | Zero. |
| F7 | WD-461 moves to Backlog at 2026-10-16T06:00:00Z; WD-900 remains Todo | 2026-10-16T06:06:00Z | No hold from WD-461; pick WD-900. | No hold line | Normal claim on WD-900 only; zero on WD-461. |
| F8 | Lane filter excludes WD-461; WD-900 is independently lane-eligible | 2026-10-16T06:06:00Z | No hold from excluded WD-461; pick WD-900. | No hold line | Normal claim on WD-900 only; zero on excluded WD-461. |

Apply both the canonical and Hermes skill text separately to every row and record
the decisions and output lines in the BUILD REPORT and independent REVIEW REPORT.
A manual text walkthrough proves this skill-text contract, not live CI recovery.

### Parity negative control

`build-ci-capacity-hold` has exactly five checks, one for each build surface.
Its first lookahead requires the actual bold `**CI-CAPACITY-HOLD:**` pickup
marker, not a mention in the stop rule's Next line. This is the WD-582 amendment.
In an isolated mutation worktree, remove the pickup paragraph from one surface,
run `pwsh -NoProfile -File deploy/parity-check.ps1 -RepoRoot <scratch>` and require
exit 1 naming that file and invariant. Restore it and require a pass. Repeat for
all five surfaces. Never weaken the expected failure or mutate a live installed
skill. Semantic checks and the existing invariants must still pass.
