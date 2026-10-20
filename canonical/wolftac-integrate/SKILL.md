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
  (`LINEAR_FACTORY_BOT_TOKEN`, via the Linear GraphQL API) — never the session's
  default Linear connection, which may be authenticated as the human. If the bot
  token is absent, invalid, or expired, fail closed: claim nothing, post nothing,
  report the missing precondition, and stop.
- **Reach Linear only through `scripts/linear_bot.py`** (see *Bot Linear access*
  below). Never hand-roll a GraphQL call, and never put the token on a command
  line — no `export LINEAR_FACTORY_BOT_TOKEN=$(grep ... .env)`, no inline
  heredoc client. The helper loads the credential internally and exits non-zero
  on a missing/invalid/expired token, so fail-closed is automatic. Hand-rolled
  variants also re-trigger a security-review confirmation on every call, which
  is what makes looped runs unusable.
- **Notification lanes are notify-only.** No relay, bridge, or automation may post
  the approval comment on the human's behalf — a relayed approval is authored by
  the relay's identity and is void under the viewer-identity check. The human
  approves directly in Linear from their own account.
- Spec template reference: `~/.claude/skills/wolftac-spec/spec-template.md`
- Intended model: Opus. Judgment here is about git state and gate evidence, not code design.
- Cross-agent: this factory also runs on Claude Desktop, Codex, and Hermes — the Linear
  conventions above are the shared contract; any agent may integrate what any agent reviewed.

## Bot Linear access

All Linear traffic goes through the helper, which reads
`LINEAR_FACTORY_BOT_TOKEN` from the process env or the Hermes `.env` itself,
prints JSON on stdout, and exits non-zero on any failure (missing token,
expired token, GraphQL error, failed read-back verification):

Always invoke it with the literal path below — never via a shell variable,
alias, or `cd` into the directory. The stable prefix is what lets a single
permission rule cover every call; a variable form re-prompts on each one.

```bash
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py remint
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py viewer
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py queue --state Approved [--lane crm]
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py issue WD-123
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py comment WD-123 --body-file -
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py claim WD-123
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py unclaim WD-123
python ~/.claude/skills/wolftac-integrate/scripts/linear_bot.py state WD-123 Done [--dry-run]
```

- `viewer` — the bot's own identity (the id every approval author is checked against)
- `queue` — Factory Queue in a state, oldest updated first, optional lane filter
- `issue` — spec description + all comments + delegate/assignee/labels/project
- `comment` — body on stdin via `--body-file -`, never on the command line
- `claim` — delegate := bot, with read-back; **exit 2 = lost claim race**
- `unclaim` — clear the delegate
- `state` — transition with read-back verification; `--dry-run` resolves only

Agents without `~` expansion (Hermes/PowerShell) use:
`python "$env:USERPROFILE\.claude\skills\wolftac-integrate\scripts\linear_bot.py" …`

Notes that carry factory semantics:
- `queue` and `issue` include the bot `viewer` in their output — that is the id
  the viewer-identity check compares approval authors against; no separate call
  needed.
- **Never use the author FIELD to tell humans from bots.** Verified 2026-07-30:
  a comment posted by the factory bot comes back under `user` with the bot's id
  and a `botActor` of `null` — identical in shape to a human comment. The only
  valid discriminator is comparing the author id against the `viewer` id
  returned alongside the data: equal ⇒ self-authored ⇒ the approval is void.
- `claim` exits **2** with `"claimed": false` when the delegate read-back does not
  match — that is the lost-claim-race signal: back off silently, post nothing.
- `--body-file -` keeps proof packs off the command line; write the pack to a
  scratch file or pipe it in.

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

## Preflight

**TOKEN-RENEWAL-PREFLIGHT:** Run `linear_bot.py remint`, then
`linear_bot.py viewer`, using the literal helper path in Bot Linear access.
`remint` checks expiry and renews only when missing/invalid or within five days;
`viewer` also recovers once from HTTP 401. A new token is persisted atomically
with `LINEAR_FACTORY_BOT_TOKEN_EXPIRES_AT` only after its viewer matches the
non-overridable pinned bot id. Either command failing means fail closed: claim
nothing, post nothing, stop. Never copy credentials into a command or output.
Use `--no-mint` when credential writes are forbidden; it disables renewal and
keeps a rejected token failure read-only. `viewer` reports `token_source` and
`token_path` (path only). Missing app credentials exit 2; OAuth/network failure
exits 1; viewer mismatch exits 4. Failed renewal leaves the old file untouched.

## Procedure

### 1. Pick up work
- Before reading the queue, run `linear_bot.py viewer`. A non-zero exit (absent,
  invalid, or expired token; failed viewer query) → fail closed: claim nothing,
  post nothing, stop.
- Do not load or use the session's default Linear MCP/toolset. Every queue read,
  issue/comment read, claim, report, and state transition below goes through
  `linear_bot.py`.
- List issues: `linear_bot.py queue --state Approved` — team `Wolftac Digital`,
  label `Factory Queue`, oldest updated first (the helper applies all three).
- Lane filter: if this skill was invoked with an argument (e.g. `/wolftac-integrate crm`),
  pass it as `--lane crm` — the helper matches it case-insensitively against the
  issue's Linear project name and labels.
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
  (often the workspace owner), and the NEEDS-HUMAN park assigns one deliberately.
  Never sweep it, never clear it, and never read it as a competing claim — worker
  claims live in `delegate` only.
- **Nothing there → say "Factory queue: nothing awaiting integration" and stop cleanly.**

### 2. Claim it
Run `linear_bot.py claim <ID>`. It sets the issue's **delegate** to the bot viewer
(`issueUpdate` with `delegateId`), leaves `assignee` and state `Approved`
untouched, and re-reads the issue to verify `delegate.id` still equals your bot
viewer id. Exit 2 (`"claimed": false`) means another session won the race — back
off silently: no comment, no state change, stop this tick.

**Claim via delegate, never assignee.** The factory bot is a Linear *app user*, and
Linear never honors an app-user `assigneeId` as the assignee: `issueUpdate {
assigneeId: <bot> }` returns `success: true`, leaves the human assignee unchanged,
and may instead populate `delegate` with the app user as a side effect. An
assignee-based claim therefore can never verify and this stage would deadlock on its
own claim check. Team membership does not change this translation — verified
2026-07-27 via twice-reproduced clean-state runs with and without the bot in the
team. Explicit `delegateId` with `delegate.id` read-back verification is the only
correct claim mechanism. Do not "repair" this back to `assigneeId`.

If `delegateId` is itself rejected (permissions or an API change), fail closed: back
off silently rather than proceeding on an unverified claim, and report the blocker.

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

**(d) Branchless deliverable (by-design):** before applying (a)–(c), check the
latest review report. If — and only if — it is a PASS review whose `Reviewed
head` line explicitly attests `no branch exists for this issue by design`, the
issue has no git artifact to merge BY DESIGN: apply the same three positions
with the branchless variants, reading LANDING PROOF PACK wherever (a)–(c) say
MERGE PROOF PACK — steps 4-alt–5-alt replace 4–5 and step 6-alt replaces 6.
Detection is strict: a PASS review without that explicit attestation, or a
branch that is merely missing, deleted, or SHA-mismatched, is NEVER branchless —
those fall through unchanged to the existing behavior and failure paths below.

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
  actual), run `linear_bot.py state <ID> Todo`, push NOTHING, clean up the
  worktree, stop.

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

Then comment on the issue with `linear_bot.py comment <ID> --body-file -`
(write the pack to a scratch file or pipe it in — never inline it as a shell
argument).

Before writing the merge command, apply the **PR-MERGE-TRANSITION-RULE:** query for
an existing PR whose head is the reviewed branch. If one exists, record
`gh pr merge <n> --merge`; if none exists, this is a pre-PR-era in-flight issue and
record the existing direct fast-forward push command. The choice does not weaken or
bypass the human approval gate.

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
- **PR-MERGE-TRANSITION-RULE:** Query the reviewed branch for a PR again. If a PR
  exists, merge it with `gh pr merge <n> --merge` so GitHub creates the repository's
  merge commit. If no PR exists, treat it as a pre-PR-era in-flight issue and use the
  existing direct fast-forward push of the head you just rebuilt and verified. Never
  auto-merge: Jonathan's valid `:rocket:` or typed approval remains the ONLY merge
  trigger. Never force-push; never delete branches; never deploy.
- Fetch the remote and verify ancestry: both
  `git merge-base --is-ancestor <reviewed-SHA> origin/<default>` and
  `git merge-base --is-ancestor <branch-head> origin/<default>` must succeed. If
  either does not, something is wrong — do not set Done; report what you observed
  and stop.
- Post the INTEGRATION REPORT (`linear_bot.py comment <ID> --body-file -`), then
  run `linear_bot.py state <ID> Done` — it verifies the transition by read-back
  and exits non-zero if the state did not stick. Re-read with
  `linear_bot.py issue <ID>` and confirm the report is present.
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

### 4-alt. Verify the live deliverable (branchless proof-pack path)
- There is no branch, no rebase, and no integrate worktree on this path — the
  deliverable the review attested lives outside git (deployed configuration,
  installed skills, provisioned services). Re-run the spec's **Test plan**
  against that live deliverable exactly as written. Secret values must never
  appear in your output — report verification results, never raw env content.
- **If any verification step fails:** do NOT attempt the rework yourself. Post a
  numbered defect report (what's wrong → how to reproduce → expected vs actual),
  run `linear_bot.py state <ID> Todo`, land NOTHING, stop — identical to the
  gate-failure path.

### 5-alt. Post the LANDING PROOF PACK and notify
Post the Slack approval message exactly as in step 5 — same
`deploy/approval-policy.json` channel, same `SLACK_FACTORY_BOT_TOKEN` +
`chat.postMessage`, same policy-selected sender identity, same outage-disclosure
rule. Include the issue ID, the by-design attestation line (in place of a
reviewed SHA), risk line, one-line verification summary, and Linear issue link;
the pack records the Slack message link and the Slack message carries the pack's
deliverable line — the same cross-binding, so a re-verified pack (new message)
structurally invalidates prior reactions.

Then comment on the issue:

```
**LANDING PROOF PACK**
- **Agent:** Claude Code / <model>
- **Reviewed head:** n/a — no branch exists for this issue by design (per the latest PASS review)
- **Deliverable:** <what exists where — the live artifact the review attested>
- **Verification re-run:** <each Test-plan step and its result against the live deliverable>
- **Risk:** <low/moderate/high + one line why>
- **Landing action (on approval):** record the human approval and set Done — nothing is pushed, merged, or deployed
- **Slack approval message:** <link to the message posted above; or "Slack lane
  unavailable this pack — typed-comment gate only" when disclosed>
- **Approval:** tap `:rocket:` on the Slack approval message (configured approver
  only), or reply `APPROVE MERGE` (also accepts `APPROVED MERGE`,
  case-insensitively) on this issue. No merge occurs without one.
```

Keep state `Approved`. Stop.

### 6-alt. Land (branchless approval path)
- Re-read the approval artifact immediately before acting exactly as in step 6:
  for a Slack 🚀, `reactions.get` on the NEWEST approval message with
  `SLACK_FACTORY_BOT_TOKEN` and an exact `approver_slack_user_id` match; for a
  typed comment, the case-insensitive `APPROVE MERGE` / `APPROVED MERGE` prefix
  plus the viewer-identity check. Confirm no other session has posted an
  INTEGRATION REPORT. An approval OLDER than the newest LANDING PROOF PACK is
  void — post a fresh pack instead of landing on it.
- Re-run the spec's **Test plan** against the live deliverable again on this
  pass — never land on a prior session's verification. If it now fails: numbered
  defect report, state `Todo`, land NOTHING.
- A branchless landing never pushes, merges, or deploys anything new — it
  verifies existing deliverable state and records the human approval; no
  approval → no Done.
- Post the report below (`linear_bot.py comment <ID> --body-file -`), then run
  `linear_bot.py state <ID> Done`; re-read with `linear_bot.py issue <ID>` and
  confirm the report exists and the state is actually Done. Close the Slack loop exactly as in step 6
  (🚀-prefixed thread reply via `chat.postMessage` + Linear back-reference
  comment recording the approval author, Slack message link, and approval
  timestamp).

```
**INTEGRATION REPORT — LANDED (BRANCHLESS)**
- **Agent:** Claude Code / <model>
- **Deliverable verified:** <evidence — each verification command/check and its result against the live deliverable>
- **Approved by:** Jonathan, <approval comment timestamp>
- **Gates at landing:** <reference the landing proof pack, plus anything re-run>
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
- A branchless landing never pushes, merges, or deploys anything new — it
  verifies existing deliverable state and records the human approval; no
  approval → no Done.
- Never force-push, never delete branches, never rewrite history.
- `BLOCKED:` comments are reserved for genuine spec defects (missing/ambiguous
  **Repo:** header); an unreachable repo is a silent skip, not a comment.
- Secrets never appear in proof packs, reports, or logs.
