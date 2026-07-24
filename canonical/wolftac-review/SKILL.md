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
`save_issue`: assignee `me` while keeping state `In Review`. Immediately `get_issue`
again and verify assignee is still you. If another reviewer already holds it
(different assignee with activity in the last 4 hours), or the latest comment is
already a review report newer than the build report, back off silently: no comment,
no state change, stop this tick.

### 3. Gather the evidence
Read the full spec (description), every comment (especially the latest BUILD REPORT),
and the actual diff. Fetch the remote, verify the published remote branch resolves to
the exact SHA in the build report, then create a detached review worktree at that SHA.
Never switch the active user checkout. Read `git log` and
`git diff <remote-default-branch>...<reported-SHA>` in the isolated worktree. A missing
remote branch or SHA mismatch is a FAIL because the reviewed artifact is not immutable.

### 4. Verify by execution, not inspection
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
   the remote head, and re-run verification.
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
- **Baseline checks:** <secrets/debug/validation/error-paths — clean or findings>
- **Fixes applied:** <none, or list with SHAs>
- **Reviewed head:** <remote branch> @ <exact verified SHA>
- **Defects:** <none, or numbered list; every finding starts with exactly one of [AC-N], [DEFECT], [SECURITY], [SCOPE-CONFLICT], then repro + expected vs actual>
- **Human decision:** <none, or exactly one tagged line: [SCOPE-CONFLICT] Decide <the exact product decision needed>>
- **Park / un-park:** <not parked, or "Assign to Jonathan; keep In Review. Jonathan comments his decision, then removes his assignment; the issue re-enters the review queue with that decision as part of the work order.">
- **Suggestions (out of scope):** <ideas for a future /wolftac-spec, if any>
```

### 6. Stop
One issue per invocation. Scope discipline applies to you too: improvement ideas go in
**Suggestions**, never into the code during a PASS.
