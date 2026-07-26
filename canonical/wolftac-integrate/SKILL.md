---
name: wolftac-integrate
description: Factory stage 4 — land the oldest Approved Linear factory issue (team Wolftac Digital, label Factory Queue) by rebasing the reviewed branch onto the remote default branch in a detached worktree, re-running the spec's gates, posting a MERGE PROOF PACK, and merging ONLY after Jonathan's valid human approval artifact. The only stage that sets Done. One issue per invocation. Designed for looped runs (/loop 10m /wolftac-integrate).
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
- **The human approval gate is absolute.** A merge happens only after a valid
  human approval artifact NEWER than the latest MERGE PROOF PACK on the issue.
  Two artifacts qualify, either alone suffices:
  1. **Slack 🚀 (primary lane):** a `:rocket:` reaction on the NEWEST Slack
     approval message for the issue, whose reactor's Slack user id equals the
     `approver_slack_user_id` in `deploy/approval-policy.json` — verified by a
     Slack API read (e.g. `reactions.get`) at decision time, never assumed. A
     🚀 from any other user id does not merge; non-matching reactions are
     ignored silently. Reactions on OLDER approval messages are void — the
     message is the pack-binding, so a re-rebase (new pack → new message)
     structurally invalidates prior reactions.
  2. **Typed comment (silent fallback):** a comment starting `APPROVE MERGE` or
     `APPROVED MERGE`, matched case-insensitively, authored by Jonathan (the
     workspace owner), on the Linear issue — the original gate, retained so a
     Slack outage never blocks a merge.
  Channel and approver ids come from `deploy/approval-policy.json` only — never
  hardcode them in skill text or prompts. No approval → no merge, ever.
- **Factory Slack identity is always bot-authored.** Every factory Slack post,
  including the approval message and post-merge thread reply, uses
  `SLACK_FACTORY_BOT_TOKEN` from the environment with Slack Web API
  `chat.postMessage`; reaction verification uses `reactions.get` with that same
  bot token, never a human Slack session. Select `username` and `icon_emoji` from
  `deploy/approval-policy.json`'s `sender_identities` entry keyed by platform;
  resolve Hermes as `hermes@<hostname>`. Use `fallback_identity` when the key is
  unmapped. Never post a factory message through the human's Slack identity. If
  the bot token is absent, invalid, or expired, post no Slack message, disclose
  the unavailable Slack lane in the proof pack, and use only the typed-comment
  gate for that pack.
- **Workers may not add reactions to factory notification messages.** A worker
  reacting to its own (or any) approval message would blur the human-artifact
  boundary exactly the way a self-authored `APPROVE MERGE` would. Read
  reactions; never write them.
- **Approvals must be provably human.** Factory workers connect to Linear through
  the dedicated factory bot identity; humans approve from their own accounts. Before
  treating any approval as valid, fetch your own Linear identity (`viewer`) and
  require that the approval's author id differs from your own Linear viewer id. If
  they are the same identity, the approval is indistinguishable from automation:
  do NOT merge — report that the identity split is not yet in place and stop.
- **Worker Linear identity is the bot, enforced at the connection.** Every Linear
  read and write this stage performs uses the factory bot credentials
  (`LINEAR_FACTORY_BOT_TOKEN` from the environment, via the Linear GraphQL API) —
  never the session's default Linear connection, which may be authenticated as the
  human. If the bot token is absent, invalid, or expired, fail closed: claim
  nothing, post nothing, report the missing precondition, and stop.
- **Notification lanes are notify-only.** No relay, bridge, or automation may post
  the approval comment on the human's behalf — a relayed approval is authored by
  the relay's identity and is void under the viewer-identity check. The human
  approves directly in Linear from their own account.
- Spec template reference: `~/.claude/skills/wolftac-spec/spec-template.md`
- Intended model: Opus. Judgment here is about git state and gate evidence, not code design.
- Cross-agent: this factory also runs on Claude Desktop, Codex, and Hermes — the Linear
  conventions above are the shared contract; any agent may integrate what any agent reviewed.

## Procedure

### 1. Pick up work
- Before reading the queue, require `LINEAR_FACTORY_BOT_TOKEN` and query `viewer`
  through the Linear GraphQL API with that credential. Missing/invalid/expired token
  or an unsuccessful viewer query → fail closed: claim nothing, post nothing, stop.
- Do not load or use the session's default Linear MCP/toolset. Every queue read,
  issue/comment read, claim, report, and state transition below is performed through
  the bot-authenticated Linear GraphQL connection.
- List issues: team `Wolftac Digital`, state `Approved`, label `Factory Queue`, oldest
  updated first.
- Lane filter: if this skill was invoked with an argument (e.g. `/wolftac-integrate crm`),
  only consider issues whose Linear project name or labels contain it (case-insensitive).
- Skip (silently — no comment, no state change) issues whose spec **Repo:** path is
  unreachable from THIS session's environment; a differently-homed integrate session
  will take them.
- Before choosing a delegate-held issue, perform a read-first stale-claim sweep: if
  the most recent comment (or claim time when there are none) is OLDER than 4 hours,
  you may post exactly `SWEPT: stale integrate claim, no activity since <timestamp>`,
  clear or replace the stale delegate, and keep the issue in `Approved`. ANY comment
  younger than 4 hours makes the issue UNTOUCHABLE. This sweep is the only comment
  you post during pickup; during a claim-race back off you post NOTHING.
- A human **assignee is NOT a worker claim.** Reviewed issues routinely carry one
  (often the workspace owner). Never sweep it, never clear it, and never read it as a
  competing claim — worker claims live in `delegate` only.
- **Nothing there → say "Factory queue: nothing awaiting integration" and stop cleanly.**

### 2. Claim it
Through the bot-authenticated GraphQL connection, set the issue's **delegate** to the
bot viewer (`issueUpdate` with `delegateId`) while keeping state `Approved`. Leave
`assignee` untouched. Immediately read the issue again through that connection and
verify `delegate.id` still equals your bot viewer id. If another session won the race
(a different delegate with activity in the last 4 hours), back off silently: no
comment, no state change, stop this tick.

**Claim via delegate, never assignee.** The factory bot is a Linear *app user*, and
Linear silently refuses app users as `assignee`: `issueUpdate { assigneeId: <bot> }`
returns `success: true` and changes nothing, so an assignee-based claim can never
verify and this stage would deadlock on its own claim check. Team membership does not
change this — verified 2026-07-26 by adding the bot to the team, re-testing (still a
no-op), and removing it again; `delegateId` succeeded both with and without
membership. `delegate` is Linear's supported mechanism for agent ownership. Do not
"repair" this back to `assigneeId`.

### 3. Establish the approval position
Read the full spec, every comment, and especially the latest REVIEW REPORT (for the
reviewed SHA) and any prior MERGE PROOF PACK. Locate two timestamps:
- the newest **MERGE PROOF PACK** comment on the issue, if any;
- the newest approval — EITHER a `:rocket:` reaction on the NEWEST Slack approval
  message for this issue whose reactor id equals `approver_slack_user_id` from
  `deploy/approval-policy.json` (verify by API read; reactions from any other
  user id are ignored silently; reactions on older messages are void), OR a
  comment starting `APPROVE MERGE` or `APPROVED MERGE` (case-insensitive)
  authored by Jonathan, whose author id differs from your own Linear viewer id
  (fetch `viewer` first). No other author or reactor counts, regardless of what
  the comment says; a same-identity approval is void per the constants above. If
  this environment lacks a valid `SLACK_FACTORY_BOT_TOKEN`, evaluate the
  typed-comment lane only and disclose the unavailable Slack lane in the pack.

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
  actual), update the issue to state `Todo` through the bot-authenticated GraphQL
  connection, push NOTHING, clean up the worktree, stop.

### 5. Post the MERGE PROOF PACK and notify
Post a **Slack approval message** to the `slack_channel_id` from
`deploy/approval-policy.json` using `SLACK_FACTORY_BOT_TOKEN` and
`chat.postMessage`. Select the message's `username` and `icon_emoji` from the
policy's `sender_identities` entry for the current platform (Hermes uses
`hermes@<hostname>`), or `fallback_identity` when unmapped. Include the issue ID,
reviewed SHA, risk line, one-line gates summary, and Linear issue link. The
message and the proof pack cross-bind: the pack records the Slack message link,
and the Slack message carries the pack's reviewed SHA. Never use the human's
Slack identity. If the bot token is absent, invalid, or expired, skip every
Slack post and DISCLOSE that in the pack — the case-insensitive typed-comment
gate (`APPROVE MERGE` or `APPROVED MERGE`) covers the outage (fail-graceful,
never fail-open).

Then comment on the issue:

```
**MERGE PROOF PACK**
- **Agent:** Claude Code / <model>
- **Reviewed head:** <remote branch> @ <SHA from review report>
- **Default head:** origin/<default> @ <exact SHA the gates ran against>
- **Rebased head:** <SHA after rebase onto current default> (or "fast-forward — no rebase needed")
- **vs default:** <ahead/behind counts; conflicts encountered and how resolved, or none>
- **Gates re-run:** <each gate command and result>
- **Diffstat:** <files changed / insertions / deletions vs default>
- **Risk:** <low/moderate/high + one line why>
- **Merge command (on approval):** <the exact command you will run>
- **Slack approval message:** <link to the message posted above; or "Slack lane
  unavailable this pack — typed-comment gate only" when disclosed>
- **Approval:** tap `:rocket:` on the Slack approval message (configured approver
  only), or reply `APPROVE MERGE` (also accepts `APPROVED MERGE`,
  case-insensitively) on this issue. No merge occurs without one.
```

Notify Jonathan through the platform's channel lane (the Slack approval message
is the primary notification; on Hermes a Telegram notification may additionally
fire; elsewhere the comment itself is the notification). Notification lanes are
notify-only, and workers never add reactions to factory notification messages —
the approval artifact must come from Jonathan himself (his 🚀 or his typed
comment). Keep state `Approved`. Stop.

### 6. Merge (approval path)
- Re-read the approval artifact immediately before acting (approval races are
  real): for a Slack 🚀, call `reactions.get` on the NEWEST approval message
  using `SLACK_FACTORY_BOT_TOKEN` and confirm a reaction whose reactor id equals
  the policy file's `approver_slack_user_id` is present (any other reactor is
  ignored silently); never use a human Slack session. For a typed comment,
  confirm it is still the newest relevant event, starts `APPROVE MERGE` or
  `APPROVED MERGE` case-insensitively, and its author id passes the
  viewer-identity check. Either way, confirm no other session has posted an
  INTEGRATION REPORT.
- Verify the default branch has not moved since the proof pack: fetch, then compare
  the current `origin/<default>` SHA for EQUALITY against the proof pack's recorded
  **Default head** SHA. Any inequality means the approval is void: produce a fresh
  proof pack (steps 4–5) and stop.
- **Never trust a SHA from a prior session** — a proof pack's rebased head lives in
  that worker's local object store and may not exist anywhere you can reach. Rebuild
  it: create a fresh detached integrate worktree at the current remote default,
  re-verify the reviewed branch still resolves to the reviewed SHA, re-run the
  rebase, and re-run the gates on the head you just built. If the rebase or gates
  now fail, take the failure path (numbered defect report, state `Todo`, push
  NOTHING).
- Merge the head you just built and verified: fast-forward push, or the repo's
  PR-merge convention. Never force-push; never delete branches; never deploy.
- Verify ancestry: `git merge-base --is-ancestor <branch-head> origin/<default>`
  must succeed. If it does not, something is wrong — do not set Done; report what
  you observed and stop.
- Post the INTEGRATION REPORT, then update the issue to state `Done` through the
  bot-authenticated GraphQL connection. Read the issue back through that connection
  and confirm the report exists and the state is actually Done.
- Close the Slack loop: reply in the approval message's thread with the
  INTEGRATION REPORT summary prefixed 🚀 using `chat.postMessage`,
  `SLACK_FACTORY_BOT_TOKEN`, and the same policy-selected sender identity (a
  message, not a reaction — workers never add reactions), and post a
  back-reference comment on the Linear issue recording the approval author, the
  Slack message link, and the approval timestamp, so the on-issue audit trail
  stays complete when the approval was a reaction rather than a comment. If the
  bot token becomes unavailable, disclose the omitted thread reply in the
  Linear report; never substitute a human Slack session. If a Slack approval
  message was deleted before merge, repost a fresh pack + message rather than
  merging on memory.

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
  "the human surely meant yes." Approval is Jonathan's 🚀 on the newest Slack
  approval message (exact `approver_slack_user_id` match from
  `deploy/approval-policy.json`) or his typed comment starting `APPROVE MERGE`
  or `APPROVED MERGE`, case-insensitively — nothing else, and never a chat
  instruction.
- A `:rocket:` from any other Slack user id never merges — non-matching
  reactions are ignored silently, and duplicate taps by the approver are
  idempotent (one valid reaction suffices; extras change nothing).
- Workers never add reactions to factory notification messages — read-only on
  reactions, mirroring the Linear bot-identity rule.
- An approval whose author identity matches your own Linear viewer id never merges —
  the factory writes through its bot identity precisely so human approvals are
  distinguishable from automation.
- Merge to the remote default branch only — production deploys are out of scope.
- Never force-push, never delete branches, never rewrite history.
- `BLOCKED:` comments are reserved for genuine spec defects (missing/ambiguous
  **Repo:** header); an unreachable repo is a silent skip, not a comment.
- Secrets never appear in proof packs, reports, or logs.
