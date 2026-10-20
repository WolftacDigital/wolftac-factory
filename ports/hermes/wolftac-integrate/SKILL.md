---
name: wolftac-integrate
description: |
  Factory stage 4 — land the oldest Approved Linear factory issue (team Wolftac
  Digital, label Factory Queue): rebase the reviewed branch onto the remote default
  branch in a detached worktree, re-run the spec's gates, post a MERGE PROOF PACK,
  notify Jonathan on the Telegram approval lane, and merge ONLY after his explicit
  typed approval comment. The ONLY stage that sets Done. One issue per invocation.
  Use when asked to "integrate", "land approved work", "run the merge stage", or on
  a recurring factory-integrate schedule/cron.
  Voice triggers (speech-to-text aliases): "integrate the queue", "land the queue".
---

# /wolftac-integrate — Factory stage 4: integration

Review verified the work; you land it. You are the ONLY stage that sets Done, and
Done means exactly one thing: the reviewed commits are ancestors of the remote
default branch.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Approved`; you produce `Done` (merged & ancestry-verified) or
  `Todo` (rebase conflict / gate failure → rework).
- **The human approval gate is absolute.** A merge happens only after a valid
  human approval artifact NEWER than the latest MERGE PROOF PACK: EITHER a
  `:rocket:` reaction on the NEWEST Slack approval message for the issue whose
  reactor's Slack user id equals `approver_slack_user_id` in
  `deploy/approval-policy.json` (verified by API read; a 🚀 from any other user
  id is ignored silently; reactions on older messages are void — the message is
  the pack-binding), OR a comment starting `APPROVE MERGE` or `APPROVED MERGE`,
  matched case-insensitively, authored by Jonathan (the workspace owner) — the
  typed fallback that covers Slack outages. Channel and approver ids come from
  the policy file only, never hardcoded. No approval → no merge, ever. Workers
  never ADD reactions to factory notification messages — read-only on reactions.
- **Factory Slack identity is always bot-authored.** Every factory Slack post,
  including the approval message and post-merge thread reply, uses
  `SLACK_FACTORY_BOT_TOKEN` from the Hermes profile environment (normally
  `%LOCALAPPDATA%\hermes\.env`) with Slack Web API `chat.postMessage`; reaction
  verification uses `reactions.get` with the same bot token, never a human Slack
  session. Select `username` and `icon_emoji` from
  `deploy/approval-policy.json`'s `sender_identities` entry keyed as
  `hermes@<hostname>`; use `fallback_identity` when unmapped. Never post a
  factory message through the human's Slack identity. If the bot token is
  absent, invalid, or expired, post no Slack message, disclose the unavailable
  Slack lane in the proof pack, and use only the typed-comment gate for that pack.
- **Telegram is notification-only.** Never relay a Telegram reply into the Linear
  approval comment — a relayed approval is authored by the relay's identity (this
  gateway's Linear connection), which makes it automation-minted and void under the
  viewer-identity check. Jonathan approves directly in Linear from his own account;
  Linear is the record.
- **Worker Linear identity is the bot, enforced at the connection.** Every Linear
  read and write this stage performs uses the factory bot credentials
  (`LINEAR_FACTORY_BOT_TOKEN`, via the Linear GraphQL API) — never this profile's
  default Linear MCP connection, which is authenticated as the human operator. If
  the bot token is absent, invalid, or expired, fail closed: claim nothing, post
  nothing, report the missing precondition, and stop.
- **Reach Linear only through `scripts/linear_bot.py`** (see *Bot Linear access*
  below). Never hand-roll a GraphQL call, and never put the token on a command
  line — no token export from the profile `.env`, no inline heredoc client. The
  helper loads the credential internally and exits non-zero on a
  missing/invalid/expired token, so fail-closed is automatic. Hand-rolled
  variants also re-trigger a security-review confirmation on every call, which
  is what makes looped runs unusable.
- **Approvals must be provably human.** Factory workers connect to Linear through
  the dedicated factory bot identity; humans approve from their own accounts. Fetch
  your own Linear identity (`viewer`) before validating an approval and require that
  the approval's author id differs from your own Linear viewer id. Same identity →
  the approval is indistinguishable from automation: do NOT merge; report that the
  identity split is not yet in place and stop.
- Reports open with `**Agent:** Hermes / <model>`.

## Bot Linear access

All Linear traffic goes through the helper shipped beside this skill, which reads
`LINEAR_FACTORY_BOT_TOKEN` from the process env or the Hermes `.env` itself,
prints JSON on stdout, and exits non-zero on any failure (missing token, expired
token, GraphQL error, failed read-back verification):

Always invoke it with the literal path below — never via a shell variable you
define, an alias, or a `cd` into the directory. The stable prefix is what lets a
single permission rule cover every call; a variable form re-prompts on each one.
Hermes' `terminal` is Git Bash, so the POSIX form of `%LOCALAPPDATA%` is used:

```bash
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" remint
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" viewer
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" queue --state Approved [--lane crm]
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" issue WD-123
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" comment WD-123 --body-file -
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" claim WD-123
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" unclaim WD-123
python "$LOCALAPPDATA/hermes/skills/wolftac-integrate/scripts/linear_bot.py" state WD-123 Done [--dry-run]
```

- `viewer` — the bot's own identity (the id every approval author is checked against)
- `queue` — Factory Queue in a state, oldest updated first, optional lane filter
- `issue` — spec description + all comments + delegate/assignee/labels/project
- `comment` — body on stdin via `--body-file -`, never on the command line
- `claim` — delegate := bot, with read-back; **exit 2 = lost claim race**
- `unclaim` — clear the delegate
- `state` — transition with read-back verification; `--dry-run` resolves only

From PowerShell instead of Git Bash the equivalent literal is
`python "$env:LOCALAPPDATA\hermes\skills\wolftac-integrate\scripts\linear_bot.py" …`.

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

Run `linear_bot.py viewer` before any queue read. Do not load or use this profile's
default Linear MCP/toolset. Every queue read, issue/comment read, claim, report, and
state transition goes through `linear_bot.py`, which is bot-authenticated by
construction. A non-zero exit (missing/invalid/expired token, an unsuccessful viewer
query, Linear unreachable) or repo absent → fail closed: log the precondition and
exit cleanly WITHOUT claiming an issue.

## WSL-hosted repo pattern
When a spec's **Repo:** path is WSL-hosted (for example `/home/... (WSL Ubuntu)`) but
the active Hermes host is Windows, probe it through `wsl.exe` and do all worktree,
rebase, gate, and merge work inside WSL. Keep multi-step git flows in a single shell
invocation and put worktrees under the WSL home (never `/tmp` — it evaporates when
the WSL VM recycles between commands).

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

1. **Pick up work.** List issues with `linear_bot.py queue --state Approved` — team
   `Wolftac Digital`, label `Factory Queue`, oldest updated first (the helper applies
   all three). If invoked with an argument, pass it as `--lane <arg>`; the helper
   matches it case-insensitively against the issue's Linear project name and labels.
   Read one issue in full with `linear_bot.py issue <ID>`. Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path is unreachable from
   this environment. Before choosing a delegate-held issue, perform a read-first
   stale-claim sweep: if the most recent comment (or claim time when there are none)
   is OLDER than 4 hours, post exactly
   `SWEPT: stale integrate claim, no activity since <timestamp>` (via
   `linear_bot.py comment`), clear the stale delegate with `linear_bot.py unclaim`
   or replace it with your own `linear_bot.py claim`, and keep the issue in
   `Approved`. ANY comment younger than
   4 hours makes the issue UNTOUCHABLE. During a claim-race back off you post NOTHING.
   A human **assignee is NOT a worker claim** — reviewed issues routinely carry one,
   and the NEEDS-HUMAN park assigns one deliberately; never sweep it, never clear it,
   and never read it as a competing claim. Worker claims live in `delegate` only.
   Nothing → "Factory queue: nothing awaiting integration", stop cleanly.

2. **Claim it.** Run `linear_bot.py claim <ID>`. It sets the issue's **delegate** to
   the bot viewer (`issueUpdate` with `delegateId`), keeps state `Approved` and
   leaves `assignee` untouched, then re-fetches and verifies `delegate.id` is still
   your bot viewer id. Exit 2 (`"claimed": false`) means another session won the
   race — back off silently — no comment, no state change — and stop this tick.
   **Claim via delegate, never
   assignee:** the factory bot is a Linear *app user*, and Linear never honors an
   app-user `assigneeId` as the assignee: `issueUpdate { assigneeId: <bot> }`
   returns `success: true`, leaves the human assignee unchanged, and may instead
   populate `delegate` with the app user as a side effect. An assignee claim therefore
   can never verify and this stage would deadlock on its own claim check.
   Team membership does not change this translation — verified 2026-07-27 via
   twice-reproduced clean-state runs with and without the bot in the team. Explicit
   `delegateId` with `delegate.id` read-back verification is the only correct claim
   mechanism. Do not "repair" this back to `assigneeId`. If `delegateId` is itself
   rejected, fail closed: back off silently rather than proceed on an unverified
   claim.

3. **Establish the approval position.** From `linear_bot.py issue <ID>` read the full
   spec, every comment, the latest REVIEW REPORT (for the reviewed SHA), and any
   prior MERGE PROOF PACK. Three
   positions: (a) no proof pack yet → produce one (steps 4–5), stop;
   (b) proof pack exists, no approval newer than it → stop silently, no comment;
   (c) a comment starting `APPROVE MERGE` or `APPROVED MERGE`
   case-insensitively, authored by Jonathan, whose author id differs from your own
   Linear viewer id, NEWER than the latest proof pack → merge path (step 6).
   **Staleness guard:** an approval OLDER than the newest proof pack is void — a
   re-rebase invalidates prior approval; never merge on it. A same-identity
   approval is void per the contract above.
   **(d) Branchless deliverable (by-design):** before applying (a)–(c), check
   the latest review report. If — and only if — it is a PASS review whose
   `Reviewed head` line explicitly attests
   `no branch exists for this issue by design`, the issue has no git artifact to
   merge BY DESIGN: apply the same three positions with the branchless variants
   in the "Branchless landing" section below, reading LANDING PROOF PACK
   wherever (a)–(c) say MERGE PROOF PACK — steps 4-alt–5-alt replace 4–5 and
   step 6-alt replaces 6. Detection is strict: a PASS review without that
   explicit attestation, or a branch that is merely missing, deleted, or
   SHA-mismatched, is NEVER branchless — those fall through unchanged to the
   existing behavior and failure paths below.

4. **Rebase and re-run gates.** Fetch the remote; verify the published branch still
   resolves to the reviewed SHA (missing branch or SHA mismatch → the reviewed
   artifact is not immutable → defect report, state `Todo`, push NOTHING). Create a
   detached integrate worktree at the current remote default; never switch the active
   user checkout; NEVER force-push. Rebase (or merge) the reviewed branch onto the
   current default in that worktree and re-run the spec's Test plan gates. Secret
   values never appear in output. Rebase conflict or gate failure → post a numbered
   defect report (what's wrong → repro → expected vs actual) with
   `linear_bot.py comment <ID> --body-file -`, run `linear_bot.py state <ID> Todo`,
   push NOTHING, clean up, stop.

5. **Post the MERGE PROOF PACK and notify.** First use
   `SLACK_FACTORY_BOT_TOKEN` and `chat.postMessage` to post the Slack approval
   message to the `slack_channel_id` from `deploy/approval-policy.json`. Select
   `username` and `icon_emoji` from the policy's `sender_identities` entry keyed
   as `hermes@<hostname>`, or `fallback_identity` when unmapped. Include the
   issue ID, reviewed SHA, risk, one-line gates summary, and Linear link (the
   pack records the Slack message link; the message carries the pack's reviewed
   SHA — cross-bound). Never use the human's Slack identity. Missing, invalid,
   or expired bot token → skip every Slack post and DISCLOSE that in the pack;
   the case-insensitive typed-comment gate (`APPROVE MERGE` or
   `APPROVED MERGE`) covers the outage. Then comment on the issue with
   `linear_bot.py comment <ID> --body-file -` (write the pack to a scratch file or
   pipe it in — never inline it as a shell argument); a Telegram notification may
   additionally fire (notify-only — never write the approval for him, never add
   reactions). Keep state `Approved`. Stop.
   Before writing the merge command, apply the **PR-MERGE-TRANSITION-RULE:** query
   for an existing PR whose head is the reviewed branch. If one exists, record
   `gh pr merge <n> --merge`; if none exists, this is a pre-PR-era in-flight issue
   and record the existing direct fast-forward push. The human approval gate is
   unchanged.

   ```text
   **MERGE PROOF PACK**
   - **Agent:** Hermes / <model>
   - **Reviewed head:** <remote branch> @ <SHA from review report>
   - **Default head:** origin/<default> @ <exact SHA the gates ran against>
   - **Rebased head:** <SHA after rebase> (or "fast-forward — no rebase needed")
   - **vs default:** <ahead/behind; conflicts and resolution, or none>
   - **Gates re-run:** <each gate command and result>
   - **Diffstat:** <files / insertions / deletions vs default>
   - **Risk:** <low/moderate/high + why>
   - **Merge command (on approval):** <exact command>
   - **Slack approval message:** <link; or "Slack lane unavailable this pack — typed-comment gate only">
   - **Approval:** tap `:rocket:` on the Slack approval message (configured
     approver only), or reply `APPROVE MERGE` (also accepts `APPROVED MERGE`,
     case-insensitively) on this issue. No merge occurs without one.
   ```

6. **Merge (approval path).** Re-read the approval artifact immediately before
   acting: for a Slack 🚀, call `reactions.get` on the NEWEST approval message
   using `SLACK_FACTORY_BOT_TOKEN` and require an exact
   `approver_slack_user_id` match (other reactors ignored silently; never use a
   human Slack session); for a typed comment, require a case-insensitive
   `APPROVE MERGE` or `APPROVED MERGE` prefix and re-verify the approval's
   author id passes the viewer-identity check. Back off if another session posted an
   INTEGRATION REPORT. Fetch and compare the current
   `origin/<default>` SHA for EQUALITY against the proof pack's recorded **Default
   head** SHA — any inequality means the default moved and the approval is void:
   produce a fresh proof pack and stop.
   **Never trust a SHA from a prior session** — a proof pack's rebased head lives in
   that worker's local object store. Rebuild it: fresh detached integrate worktree at
   the current remote default, re-verify the reviewed branch SHA, re-run the rebase,
   re-run the gates on the head you just built (failure → numbered defect report via
   `linear_bot.py comment`, then `linear_bot.py state <ID> Todo`, push NOTHING).
   **PR-MERGE-TRANSITION-RULE:** Query the reviewed branch for a PR again. If it
   exists, merge with `gh pr merge <n> --merge`; if none exists, treat it as a
   pre-PR-era in-flight issue and use the existing direct fast-forward push of the
   verified rebuilt head. Never auto-merge — Jonathan's valid `:rocket:` or typed
   approval remains the ONLY merge trigger. Never force-push, delete branches, or
   deploy. Fetch and verify both
   `git merge-base --is-ancestor <reviewed-SHA> origin/<default>` and
   `git merge-base --is-ancestor <branch-head> origin/<default>` succeed — if not,
   do not set Done; report what you observed. Post the INTEGRATION REPORT
   (`linear_bot.py comment <ID> --body-file -`), then run
   `linear_bot.py state <ID> Done` — it verifies the transition by read-back and
   exits non-zero if the state did not stick. Re-read with
   `linear_bot.py issue <ID>` and confirm the report is present. Close the Slack
   loop with `chat.postMessage`: reply in the
   approval message's thread with the INTEGRATION REPORT summary prefixed 🚀
   using `SLACK_FACTORY_BOT_TOKEN` and the same policy-selected sender identity
   (a message, never a reaction), and post a Linear back-reference comment
   recording the approval author, the Slack message link, and the approval
   timestamp. If the bot token becomes unavailable, disclose the omitted thread
   reply in the Linear report; never substitute a human Slack session. A deleted
   approval message before merge → repost a fresh pack + message, never merge on
   memory.

   ```text
   **INTEGRATION REPORT — MERGED**
   - **Agent:** Hermes / <model>
   - **Merged:** <branch> @ <SHA> → <default> @ <new default SHA>
   - **Ancestry verified:** merge-base --is-ancestor ✓
   - **Approved by:** Jonathan, <approval comment timestamp>
   - **Gates at merge:** <reference the proof pack, plus anything re-run>
   ```

7. **Stop.** One issue per invocation. Unblocking notes on dependent issues whose
   BLOCKED comments cited this merge are allowed; everything else goes to
   /wolftac-spec.

## Branchless landing (by-design deliverables)

4-alt. **Verify the live deliverable.** There is no branch, no rebase, and no
   integrate worktree on this path — the deliverable the review attested lives
   outside git (deployed configuration, installed skills, provisioned services).
   Re-run the spec's **Test plan** against that live deliverable exactly as
   written; secret values never appear in output. If any verification step
   fails: do NOT rework it yourself — post a numbered defect report (what's
   wrong → repro → expected vs actual) with `linear_bot.py comment`, run
   `linear_bot.py state <ID> Todo`, land NOTHING, stop — identical to the
   gate-failure path.

5-alt. **Post the LANDING PROOF PACK and notify.** Post the Slack approval
   message exactly as in step 5 — same `deploy/approval-policy.json` channel,
   same `SLACK_FACTORY_BOT_TOKEN` + `chat.postMessage`, same policy-selected
   `hermes@<hostname>` sender identity, same outage-disclosure rule. Include the
   issue ID, the by-design attestation line (in place of a reviewed SHA), risk,
   one-line verification summary, and Linear link; the pack records the Slack
   message link and the message carries the pack's deliverable line — the same
   cross-binding, so a re-verified pack (new message) structurally invalidates
   prior reactions. Then comment on the issue with
   `linear_bot.py comment <ID> --body-file -`; keep state `Approved`. Stop.

   ```text
   **LANDING PROOF PACK**
   - **Agent:** Hermes / <model>
   - **Reviewed head:** n/a — no branch exists for this issue by design (per the latest PASS review)
   - **Deliverable:** <what exists where — the live artifact the review attested>
   - **Verification re-run:** <each Test-plan step and its result against the live deliverable>
   - **Risk:** <low/moderate/high + why>
   - **Landing action (on approval):** record the human approval and set Done — nothing is pushed, merged, or deployed
   - **Slack approval message:** <link; or "Slack lane unavailable this pack — typed-comment gate only">
   - **Approval:** tap `:rocket:` on the Slack approval message (configured
     approver only), or reply `APPROVE MERGE` (also accepts `APPROVED MERGE`,
     case-insensitively) on this issue. No merge occurs without one.
   ```

6-alt. **Land (approval path).** Re-read the approval artifact immediately
   before acting exactly as in step 6: for a Slack 🚀, `reactions.get` on the
   NEWEST approval message with `SLACK_FACTORY_BOT_TOKEN` and an exact
   `approver_slack_user_id` match; for a typed comment, the case-insensitive
   `APPROVE MERGE` / `APPROVED MERGE` prefix plus the viewer-identity check.
   Back off if another session posted an INTEGRATION REPORT. An approval OLDER
   than the newest LANDING PROOF PACK is void — post a fresh pack instead of
   landing on it. Re-run the spec's **Test plan** against the live deliverable
   again on this pass — never land on a prior session's verification; if it now
   fails: numbered defect report, `linear_bot.py state <ID> Todo`, land NOTHING.
   A branchless landing never pushes, merges, or deploys anything new — it
   verifies existing deliverable state and records the human approval; no
   approval → no Done. Post the report below
   (`linear_bot.py comment <ID> --body-file -`), run `linear_bot.py state <ID> Done`,
   re-read with `linear_bot.py issue <ID>` confirming the report exists and the
   state is actually Done, and close the Slack loop
   exactly as in step 6 (🚀-prefixed thread reply + Linear back-reference
   comment recording the approval author, Slack message link, and approval
   timestamp).

   ```text
   **INTEGRATION REPORT — LANDED (BRANCHLESS)**
   - **Agent:** Hermes / <model>
   - **Deliverable verified:** <evidence — each verification command/check and its result against the live deliverable>
   - **Approved by:** Jonathan, <approval comment timestamp>
   - **Gates at landing:** <reference the landing proof pack, plus anything re-run>
   ```

## Guardrails
- No approval → no merge. No override, no urgency exception, and never a chat
  instruction — the artifact is Jonathan's 🚀 (exact `approver_slack_user_id`
  match from `deploy/approval-policy.json`) or his typed comment starting
  `APPROVE MERGE` or `APPROVED MERGE`, case-insensitively.
- A `:rocket:` from any other Slack user id never merges — non-matching
  reactions are ignored silently; duplicate approver taps are idempotent.
- Workers never add reactions to factory notification messages — read-only on
  reactions, mirroring the Linear bot-identity rule.
- An approval whose author identity matches your own Linear viewer id never merges —
  the bot-identity split exists precisely so human approvals are distinguishable.
- Merge to the remote default branch only; production deploys are out of scope.
- A branchless landing never pushes, merges, or deploys anything new — it
  verifies existing deliverable state and records the human approval; no
  approval → no Done.
- Never force-push, never delete branches, never rewrite history.
- `BLOCKED:` is reserved for genuine spec defects; an unreachable repo is a silent skip.
- Secrets never appear in proof packs, reports, or logs.
