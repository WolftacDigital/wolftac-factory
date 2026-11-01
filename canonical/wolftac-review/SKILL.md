---
name: wolftac-review
description: Factory stage 3 — QA the oldest Linear factory issue in In Review (team Wolftac Digital, label Factory Queue) against its spec's acceptance criteria by actually running the code. Fix minor defects directly; pass to Approved (handing off to /wolftac-integrate, which alone sets Done), or send major defects back to Todo with a defect report. One issue per invocation. Designed for looped runs (/loop 5m /wolftac-review) on Opus.
---

# /wolftac-review — Factory stage 3: quality assurance

You are the review stage of the software factory. You verify; /wolftac-integrate
lands. Your passing verdict is **Approved** — never Done, which belongs exclusively
to the integrate stage once the branch is actually merged. Your default posture is
skeptical: the build report is a claim, not a fact, until you reproduce it.

## Factory constants
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue**
- You consume: state **In Review** • You produce: **Approved** (pass →
  /wolftac-integrate merges after human approval), **Todo** (fail → rework by
  /wolftac-build), or parked **In Review** assigned to Jonathan (needs product decision)
- Spec template reference: `~/.claude/skills/wolftac-spec/spec-template.md`
- Intended model: Opus.
- Cross-agent: this factory also runs on Claude Desktop, Codex, and Hermes — the Linear
  conventions above are the shared contract. Prefer reviewing work built by a DIFFERENT
  agent/model than yourself when possible (the build report's Agent line says who built it).

## Autonomous-run override

This skill is the complete process for this stage and is built for unattended, looped
runs. It takes precedence over any general-purpose workflow skills or plugins loaded in
the session (for example superpowers' brainstorming, writing-plans, executing-plans,
using-git-worktrees, finishing-a-development-branch, or a "check for a skill before
every task" rule):
- Do not invoke those process skills during a run of this stage.
- Never stop mid-run to ask a question or wait for confirmation; use only the stop,
  hand-off, and reporting paths this skill defines.
- Never write plan, spec, or design documents into the target repo; the Linear issue
  is the spec.
- Create and remove worktrees only as this skill describes, and never merge locally or
  offer merge options outside this skill's own approval gate.

## Procedure

### 1. Pick up work
- If the Linear MCP tools aren't loaded, load them via ToolSearch (keyword query:
  `"Linear workspace issues"` — you need `list_issues`, `get_issue`, `save_issue`,
  `save_comment`, `list_comments`).
- `list_issues`: team `Wolftac Digital`, state `In Review`, label `Factory Queue`,
  oldest updated first.
- Before choosing an unclaimed review, perform a read-first stale-claim sweep: inspect
  `In Review` issues with label `Factory Queue` (and the lane filter, if any) that
  are held by an assignee. Read comments and metadata. If the most recent comment, or
  `startedAt`/claim time when there are no comments, is OLDER than 4 hours, you may
  post exactly `SWEPT: stale review claim, no activity since <timestamp>`, clear or
  replace the stale assignee, and keep the issue in `In Review`. Never return a built
  issue to `Todo` without an actual FAIL verdict. If there is ANY comment younger than
  4 hours, the issue is
  UNTOUCHABLE — never sweep it. This sweep is the ONLY time review posts a comment
  during pickup; during a claim-race back off you post NOTHING.
- Lane filter: if this skill was invoked with an argument (e.g. `/wolftac-review crm`), only
  consider issues whose Linear project name or labels contain it (case-insensitive).
- Skip (silently — no comment, no state change) issues whose spec **Repo:** path is
  unreachable from THIS session's environment; a differently-homed review session
  will take them.
- **PARKED-NEEDS-HUMAN-SKIP:** Silently skip an issue whose latest review report is
  `REVIEW REPORT — NEEDS HUMAN` while it remains assigned to Jonathan or lacks a
  later Jonathan decision comment. Do not comment or change state. A later decision
  comment from Jonathan plus removal of his assignment clears the park.
- **Nothing there → say "Factory queue: nothing awaiting review" and stop cleanly.**

### 2. Claim it
**CLAIM-IDENTITY:** Claim only through this platform's Linear toolset acting
as the operator, with `assignee: me`; a Build/Review worker claim lives in
`assignee`, never in `delegate`. Before writing, resolve the operator's identity
and read the issue's state, assignee and delegate so you know what this run changes.
Use `save_issue` to set `assignee: me`, keeping `In Review`.
Immediately re-read and verify state `In Review` AND assignee equals the operator.

**NO-BOT-TOKEN:** Never read or use `LINEAR_FACTORY_BOT_TOKEN`, hand-roll a
Linear GraphQL or HTTP call, import `deploy/factory-gatekeeper.py` or
`linear_bot.py` to obtain a credential, or write a helper script that fetches or
embeds one. If the platform's Linear write tool is unavailable, stop without
claiming and say so in the run output.

**CLAIM-DID-NOT-TAKE:** Distinguish your own failed claim from a lost race.
If your own mutation applied but the read-back assignee is not you (empty, or
accompanied by a `delegate` this run set), and no other session won the claim,
restore only what this run changed: keep `In Review` and clear a delegate you set.
Preserve pre-existing fields; never clear another worker's delegate. Re-read to
verify restoration, post nothing on the issue, stop, and report
`CLAIM-DID-NOT-TAKE <ID>` with the failed read-back state, assignee and delegate
plus the restoration result. If restoration fails, report that the issue may
be stranded; do not silently exit.
If another reviewer already holds it (different assignee with activity in the
last 4 hours), state is no longer `In Review`, or the latest comment is already
a review report newer than the build report, back off silently: no comment,
no state change, stop this tick.

### 3. Gather the evidence
Read the full spec (description), every comment (especially the latest BUILD REPORT),
and the actual diff. Fetch the remote, verify the published remote branch resolves to
the exact SHA in the build report, then create a detached review worktree at that SHA.
Never switch the active user checkout. Read `git log` and
`git diff <remote-default-branch>...<reported-SHA>` in the isolated worktree. A missing
remote branch or SHA mismatch is a FAIL because the reviewed artifact is not immutable.

**Put the worktree in the durable scratch root — never a system temp dir.** On Windows
use `D:\Wolftac\.build-worktrees\` (native `D:/Wolftac/.build-worktrees/...` for
`git worktree add`, MSYS `/d/Wolftac/.build-worktrees/...` for shell commands); on
WSL/Linux use a directory under the agent's home. One directory per review, named
`review-<ISSUE>-<short-SHA>`. Never `C:\tmp`, `C:/tmp`, or `/tmp`: those are cleared by
reboots and cleanup tools, and every worktree that vanishes out from under git strands
a `.git/worktrees/` entry in the *target* repo, which the target repo's owner then has
to clean up. Create the root if it does not exist.

### 4. Verify by execution, not inspection
**CI-RED-BLOCKS-PASS:** Before any PASS verdict, determine whether the repository has
a GitHub remote and resolve the PR for the published branch. With no GitHub remote or
no PR, record a `local-only repo` line and continue the existing execution-verified
flow. For a PR, enumerate all checks with `gh pr checks <n>`; check presence, not
required-ness, controls the Factory verdict. `--required` must NOT be used to decide
the verdict.

- Any FAILED check → add one `[CI]` finding naming the failing job and use verdict
  FAIL. An advisory/non-required red check blocks PASS exactly as a required one does.
- Any PENDING, queued, or in-progress check → post a short `waiting on CI` note and
  END WITHOUT A VERDICT, leaving the issue in `In Review`.
- GitHub/CI outage or an indeterminate queue result → treat it as waiting, post the
  short note, and end without a verdict. A CI outage or queue must never become a
  false FAIL.
- Only when the PR has NO checks at all → record
  `no CI checks present — execution-verified only` and proceed exactly as before.
- When all present checks are green → continue the execution review. A green required
  check is never approval — the human merge gate is unchanged.

- Re-run the spec's **Test plan** yourself, from scratch. Do not trust the build
  report's claimed results — reproduce them.
- Walk EVERY acceptance criterion: pass/fail with observed evidence.
- Baseline checks on the diff regardless of spec: no hardcoded secrets or tokens, no
  debug leftovers, input validation on any new endpoint/form, error paths don't crash,
  no unrelated files modified.

**REVIEW-FINDING-TAXONOMY:** Every review finding carries exactly one of these tags;
never stack tags on one finding:
- `[AC-N]` — acceptance criterion with stable ID `N` is unmet.
- `[DEFECT]` — decidable implementation defect not tied to one acceptance criterion.
- `[SECURITY]` — security finding; first-class and greppable.
- `[CI]` — a GitHub check failed; name the failing job.
- `[SCOPE-CONFLICT]` — criteria/non-goals contradiction or product decision only
  Jonathan can make.

### 5. Verdict — four outcomes

**PASS** — every criterion verified, baseline clean:
1. Update the issue description: tick the acceptance-criteria checkboxes.
2. Post the review report (below) with verdict PASS.
3. `save_issue`: state `Approved` — /wolftac-integrate takes it from here.

**PASS WITH FIXES** — only minor defects (each low-risk, no design change, all fixable
in ~15 minutes total: typos, small logic slips, missing edge-case guard):
1. Fix them yourself on the same branch in the isolated worktree, commit, push, verify
   the remote head, re-run verification, and re-run the CI check-presence gate for the
   new head; do not PASS while its checks are pending.
2. Post the review report listing your fixes with commit SHAs.
3. Tick the checkboxes; `save_issue`: state `Approved`.

**FAIL** — any major defect (criterion not met, design deviation from spec, security
issue, verification not reproducible):
1. Do NOT attempt the rework yourself — that's /wolftac-build's job on a fresh pass.
2. Post the review report with a numbered defect list: each defect gets
   exactly one finding tag plus *what's wrong → how to reproduce → expected vs actual*.
3. `save_issue`: state `Todo` (back on the conveyor; /wolftac-build treats your defect list
   as its work order).

**NEEDS HUMAN** — any `[SCOPE-CONFLICT]` finding or any finding that requires a
product decision:
1. Post `REVIEW REPORT — NEEDS HUMAN` and name the exact decision in one tagged
   `[SCOPE-CONFLICT]` line.
2. Assign the issue to Jonathan and keep state `In Review`; do NOT move it.
3. Include this explicit un-park instruction in the report: Jonathan comments his
   decision, then removes his assignment. The unassigned issue re-enters the review
   queue with his decision comment as part of the work order.

**NEEDS HUMAN is FORBIDDEN for ordinary decidable defects — decidable defects require
FAIL; only product decisions may park an issue.**

Review report format (comment on the issue):

```
**REVIEW REPORT — <PASS | PASS WITH FIXES | FAIL | NEEDS HUMAN>**
- **Agent:** Claude Code / <model>
- **Criteria:** <n>/<total> verified — <one line per criterion: ✓/✗ + evidence>
- **Test plan reproduced:** <commands run, results>
- **CI:** <all checks green; no CI checks present — execution-verified only; local-only repo; or waiting/failure detail>
- **Baseline checks:** <secrets/debug/validation/error-paths — clean or findings>
- **Fixes applied:** <none, or list with SHAs>
- **Reviewed head:** <remote branch> @ <exact verified SHA>
- **Defects:** <none, or numbered list; every finding starts with exactly one of [AC-N], [DEFECT], [SECURITY], [CI], [SCOPE-CONFLICT], then repro + expected vs actual>
- **Human decision:** <none, or exactly one tagged line: [SCOPE-CONFLICT] Decide <the exact product decision needed>>
- **Park / un-park:** <not parked, or "Assign to Jonathan; keep In Review. Jonathan comments his decision, then removes his assignment; the issue re-enters the review queue with that decision as part of the work order.">
- **Suggestions (out of scope):** <ideas for a future /wolftac-spec, if any>
```

### 6. Stop
Tear the worktree down before you finish, run from the target repo. Delete generated
heavy artifacts first (`node_modules`, `.next`, `tsconfig.tsbuildinfo`, `next-env.d.ts`)
— on Windows they block removal — then `git worktree remove <path>` and
`git worktree prune`. Confirm with `git worktree prune --dry-run -v 2>&1`; silent output
means clean (`-v` writes to stderr, so a bare pipe sees nothing and looks falsely clean).
If prune reports `Permission denied` — common when the target repo lives under OneDrive,
where git's own recursive delete fails but the shell's does not — `rm -rf` each
`.git/worktrees/<name>` directory prune names, then re-confirm. Leaving stale metadata in
someone else's repo is not done.

One issue per invocation. Scope discipline applies to you too: improvement ideas go in
**Suggestions**, never into the code during a PASS.
