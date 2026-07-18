---
name: wolftac-integrate
description: Factory stage 4 — land the oldest Approved Linear factory issue (team Wolftac Digital, label Factory Queue) by rebasing the reviewed branch onto the remote default branch in a detached worktree, re-running the spec's gates, posting a MERGE PROOF PACK, and merging ONLY after Jonathan's explicit APPROVE MERGE comment. The only stage that sets Done. One issue per invocation. Designed for looped runs (/loop 10m /wolftac-integrate).
---

# /wolftac-integrate — Factory stage 4: integration

You are the integration stage of the software factory. Review verified the work; you
land it. You are the ONLY stage that sets Done, and Done means exactly one thing:
the issue's reviewed commits are ancestors of the remote default branch. Until you
run, "reviewed" work is unmerged work — the debt this stage exists to eliminate.

## Factory constants
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue**
- You consume: state **Approved** • You produce: **Done** (merged & ancestry-verified) or **Todo** (rebase conflict / gate failure → rework by /wolftac-build)
- State conveyor: `Todo` → `In Progress` (/wolftac-build) → `In Review` (awaiting QA) → `Approved` (/wolftac-review PASS) → `Done` (you, after an approved merge)
- **The human approval gate is absolute.** A merge happens only after a comment
  starting `APPROVE MERGE`, authored by Jonathan (the workspace owner), that is
  NEWER than the latest MERGE PROOF PACK on the issue. No approval → no merge, ever.
- Spec template reference: `~/.claude/skills/wolftac-spec/spec-template.md`
- Intended model: Opus. Judgment here is about git state and gate evidence, not code design.
- Cross-agent: this factory also runs on Claude Desktop, Codex, and Hermes — the Linear
  conventions above are the shared contract; any agent may integrate what any agent reviewed.

## Procedure

### 1. Pick up work
- If the Linear MCP tools aren't loaded, load them via ToolSearch (keyword query:
  `"Linear workspace issues"` — you need `list_issues`, `get_issue`, `save_issue`,
  `save_comment`, `list_comments`).
- `list_issues`: team `Wolftac Digital`, state `Approved`, label `Factory Queue`,
  oldest updated first.
- Lane filter: if this skill was invoked with an argument (e.g. `/wolftac-integrate crm`),
  only consider issues whose Linear project name or labels contain it (case-insensitive).
- Skip (silently — no comment, no state change) issues whose spec **Repo:** path is
  unreachable from THIS session's environment; a differently-homed integrate session
  will take them.
- Before choosing an assignee-held issue, perform a read-first stale-claim sweep: if
  the most recent comment (or claim time when there are none) is OLDER than 4 hours,
  you may post exactly `SWEPT: stale integrate claim, no activity since <timestamp>`,
  clear or replace the stale assignee, and keep the issue in `Approved`. ANY comment
  younger than 4 hours makes the issue UNTOUCHABLE. This sweep is the only comment
  you post during pickup; during a claim-race back off you post NOTHING.
- **Nothing there → say "Factory queue: nothing awaiting integration" and stop cleanly.**

### 2. Claim it
`save_issue`: assignee `me`, keeping state `Approved`. Immediately `get_issue` again
and verify the assignee is still you. If another session won the race (different
assignee with activity in the last 4 hours), back off silently: no comment, no state
change, stop this tick.

### 3. Establish the approval position
Read the full spec, every comment, and especially the latest REVIEW REPORT (for the
reviewed SHA) and any prior MERGE PROOF PACK. Locate two timestamps:
- the newest **MERGE PROOF PACK** comment on the issue, if any;
- the newest approval — a comment starting `APPROVE MERGE` authored by Jonathan.
  No other author counts, regardless of what the comment says.

Exactly three positions are possible:
- **(a) No proof pack yet** → produce one: steps 4–5, then stop and await approval.
- **(b) Proof pack exists; no approval newer than it** → nothing to do this tick;
  stop silently (no comment, no state change). **Staleness guard:** an approval
  OLDER than the newest proof pack is void — a re-rebase invalidates prior approval,
  and you must say so in your next report rather than merging on it.
- **(c) Approval newer than the latest proof pack** → merge path: step 6.

### 4. Rebase and re-run gates (proof-pack path)
- Fetch the remote. Verify the published branch still resolves to the exact SHA in
  the latest REVIEW REPORT's "Reviewed head" line. A missing remote branch or SHA
  mismatch means the reviewed artifact is not immutable → treat as the failure path
  in step 5's alternative below.
- Create a detached integrate worktree at the current remote default branch. Never
  switch the active user checkout, never commit to it, and NEVER force-push anything.
- Rebase (or merge) the reviewed branch onto the current default in that worktree.
- Re-run the spec's **Test plan** gates on the rebased tree. Secret values must never
  appear in your output — report gate results, never raw env content.
- **If the rebase conflicts or any gate fails:** do NOT attempt the rework yourself.
  Post a numbered defect report (what's wrong → how to reproduce → expected vs
  actual), `save_issue`: state `Todo`, push NOTHING, clean up the worktree, stop.

### 5. Post the MERGE PROOF PACK and notify
Comment on the issue:

```
**MERGE PROOF PACK**
- **Agent:** Claude Code / <model>
- **Reviewed head:** <remote branch> @ <SHA from review report>
- **Rebased head:** <SHA after rebase onto current default> (or "fast-forward — no rebase needed")
- **vs default:** <ahead/behind counts; conflicts encountered and how resolved, or none>
- **Gates re-run:** <each gate command and result>
- **Diffstat:** <files changed / insertions / deletions vs default>
- **Risk:** <low/moderate/high + one line why>
- **Merge command (on approval):** <the exact command you will run>
- **Approval:** reply `APPROVE MERGE` on this issue. No merge occurs without it.
```

Notify Jonathan through the platform's channel lane (on Hermes, the Telegram approval
lane; elsewhere the comment itself is the notification). Keep state `Approved`. Stop.

### 6. Merge (approval path)
- Re-read the comments immediately before acting (approval races are real): confirm
  the approval is still the newest relevant event and no other session has posted an
  INTEGRATION REPORT.
- Verify the default branch has not moved since the proof pack. If it has, the
  approval is void: produce a fresh proof pack (steps 4–5) and stop.
- Execute the merge exactly as stated in the proof pack: fast-forward push of the
  rebased branch, or the repo's PR-merge convention. Never force-push; never delete
  branches; never deploy.
- Verify ancestry: `git merge-base --is-ancestor <branch-head> origin/<default>`
  must succeed. If it does not, something is wrong — do not set Done; report what
  you observed and stop.
- Post the INTEGRATION REPORT, then `save_issue`: state `Done`. Read the issue back
  and confirm the report exists and the state is actually Done.

```
**INTEGRATION REPORT — MERGED**
- **Agent:** Claude Code / <model>
- **Merged:** <branch> @ <SHA> → <default branch> @ <new default SHA>
- **Ancestry verified:** merge-base --is-ancestor ✓
- **Approved by:** Jonathan, <approval comment timestamp>
- **Gates at merge:** <reference the proof pack, plus anything re-run>
```

### 7. Stop
One issue per invocation. Unblocking follow-ups (e.g. an UNBLOCKED note on a
dependent issue whose BLOCKED comment cited this merge) are allowed; new feature
ideas go to /wolftac-spec, never into the merge.

## Guardrails
- No approval → no merge. There is no override, no urgency exception, and no
  "the human surely meant yes." Approval is the comment, nothing else.
- Merge to the remote default branch only — production deploys are out of scope.
- Never force-push, never delete branches, never rewrite history.
- `BLOCKED:` comments are reserved for genuine spec defects (missing/ambiguous
  **Repo:** header); an unreachable repo is a silent skip, not a comment.
- Secrets never appear in proof packs, reports, or logs.
