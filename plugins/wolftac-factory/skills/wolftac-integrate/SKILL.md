---
name: wolftac-integrate
description: Land one Approved issue from the Wolftac Digital Linear Factory Queue by rebasing the reviewed branch onto the remote default, re-running gates, posting a MERGE PROOF PACK, and merging only after Jonathan's valid human approval artifact. The only stage that sets Done. Use for Wolftac Factory integration.
---

# Wolftac Integrate

Land exactly one reviewed build. Done means the commits are ancestors of the remote
default branch — nothing less.

## Contract

- Team `Wolftac Digital`; label `Factory Queue`; consume `Approved`.
- Produce `Done` after an approved, ancestry-verified merge, or `Todo` on rebase
  conflict / gate failure.
- **The human approval gate is absolute:** merge only after a valid human
  approval artifact NEWER than the latest MERGE PROOF PACK — EITHER a `:rocket:`
  reaction on the NEWEST Slack approval message for the issue whose reactor's
  Slack user id equals `approver_slack_user_id` in `deploy/approval-policy.json`
  (verified by API read; a 🚀 from any other user id is ignored silently;
  reactions on older messages are void), OR a comment starting `APPROVE MERGE`
  or `APPROVED MERGE`, matched case-insensitively, authored by Jonathan (typed
  fallback covering Slack outages). Ids come from the policy file only, never
  hardcoded. No approval → no merge, ever. Workers never add reactions to
  factory notification messages.
- **Factory Slack identity is always bot-authored:** every factory Slack post,
  including the approval message and post-merge thread reply, uses
  `SLACK_FACTORY_BOT_TOKEN` from the environment with Slack Web API
  `chat.postMessage`; reaction verification uses `reactions.get` with the same
  bot token, never a human Slack session. Select `username` and `icon_emoji` from
  `deploy/approval-policy.json`'s `sender_identities` entry keyed by platform
  (Hermes resolves `hermes@<hostname>`), or `fallback_identity` when unmapped.
  Never post a factory message through the human's Slack identity. Missing,
  invalid, or expired bot token → post no Slack message, disclose the unavailable
  Slack lane in the proof pack, and use only the typed-comment gate for that pack.
- **Slack calls go through `slack_bot.py`.** Use the `slack_bot.py` helper that
  ships beside `linear_bot.py` for every Slack call: `check` (lane available?),
  `post --body-file - [--thread-ts TS]`, `reactions TS`, `delete TS`. It finds
  the bot token (profile `.env`, process env, then `%LOCALAPPDATA%\hermes\.env`)
  and the approval policy by absolute path, so it works from any target repo;
  never resolve either relative to the working directory. Exit code 2 means the
  Slack lane is unavailable: disclose it and use the typed-comment gate.
- **Slack posting encoding.** Never pass Slack message text as a command-line argument
  (`curl --data-urlencode 'text=...'`, `-d`, or PowerShell argv): on Windows, non-ASCII
   characters in argv are re-encoded through the legacy code page, Slack receives
  invalid UTF-8, and characters such as an em dash arrive as `BAD+97` (2026-10-20,
  WD-347). Write the JSON payload (`channel`, `text`, `username`, `icon_emoji`, and
  `thread_ts` for replies) to a temporary file as UTF-8 without a BOM, post it with
  `curl --data-binary @<file>` and header `Content-Type: application/json;
  charset=utf-8`, then delete the file. Keep factory Slack text ASCII anyway: use `-`
  for dashes. Check the returned `message.text`; if it differs from what you sent,
  delete that post with `chat.delete` (same bot token) before posting the corrected
  one, so only one approval message exists for the pack.
- **Approvals must be provably human:** workers connect to Linear through the
  dedicated factory bot identity; humans approve from their own accounts. Fetch your
  own Linear identity (`viewer`) and require that the approval's author id differs
  from your own Linear viewer id. Same identity → do NOT merge; report that the
  identity split is missing and stop.
- **Worker Linear identity is the bot, enforced at the connection:** all Linear
  reads/writes in this stage use the factory bot credentials
  (`LINEAR_FACTORY_BOT_TOKEN`, Linear GraphQL API) — never the session's default
  Linear connection. Token absent/invalid/expired → fail closed: claim nothing,
  post nothing, report the precondition, stop.
- **Reach Linear only through `scripts/linear_bot.py`** (see *Bot Linear access*
  below): never hand-roll a GraphQL call, and never put the token on a command
  line — no token export out of the environment file, no inline heredoc client.
  The helper loads the credential internally and exits non-zero on a
  missing/invalid/expired token, so fail-closed is automatic. Hand-rolled variants
  also re-trigger a security-review confirmation on every call, which is what
  makes looped runs unusable.
- **Notification lanes are notify-only:** no relay or automation ever posts the
  approval comment on the human's behalf — a relayed approval is authored by the
  relay's identity and is void.
- One issue per invocation. Reports start with `**Agent:** Codex / <actual model>`.

## Bot Linear access

All Linear traffic goes through the helper shipped beside this skill, which reads
`LINEAR_FACTORY_BOT_TOKEN` from the process env or the Hermes `.env` itself, prints
JSON on stdout, and exits non-zero on any failure (missing token, expired token,
GraphQL error, failed read-back verification):

Always invoke it with the literal path below — never via a shell variable you
define, an alias, or a `cd` into the directory. The stable prefix is what lets a
single permission rule cover every call; a variable form re-prompts on each one.

```bash
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py viewer
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py queue --state Approved [--lane crm]
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py issue WD-123
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py comment WD-123 --body-file -
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py claim WD-123
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py unclaim WD-123
python ~/.agents/skills/wolftac-integrate/scripts/linear_bot.py state WD-123 Done [--dry-run]
python ~/.agents/skills/wolftac-integrate/scripts/run_gate.py check-repo --repo-path "<spec Repo path>"
python ~/.agents/skills/wolftac-integrate/scripts/run_gate.py run --repo-path "<spec Repo path>" --cwd "<integrate worktree or deliverable folder>" [--timeout-sec <spec limit in seconds>] -- <gate argv>
```

Gate runner exits (separate from `linear_bot.py` exit codes):
- **0:** repo allowed (`check-repo`) or gate passed (`run`).
- **1:** gate failed; use the existing numbered defect report and `Todo` path.
- **3:** repo not allowlisted; at pickup skip silently, no comment or state change;
  at gate time use `PRECONDITION:` with `gate runner refused: repo allowlist`, keep `Approved`.
- **4:** allowlist unavailable or invalid; at pickup fail closed, claim nothing,
  post nothing, stop the tick; at gate time use the same allowlist `PRECONDITION:`, keep `Approved`.
- **124:** gate timed out; use `PRECONDITION:` with `timed out after <N> s`, keep `Approved`.

- `viewer` — the bot's own identity (the id every approval author is checked against)
- `queue` — Factory Queue in a state, oldest updated first, optional lane filter
- `issue` — spec description + all comments + delegate/assignee/labels/project
- `comment` — body on stdin via `--body-file -`, never on the command line
- `claim` — delegate := bot, with read-back; **exit 2 = lost claim race**
- `unclaim` — clear the delegate
- `state` — transition with read-back verification; `--dry-run` resolves only

Shells without `~` expansion (PowerShell) use
`python "$env:USERPROFILE\.agents\skills\wolftac-integrate\scripts\linear_bot.py" …`.

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

## Procedure

1. Before queue access, run `linear_bot.py viewer`. Do not load or use the session's
   default Linear MCP/toolset. A non-zero exit (missing/invalid/expired token,
   unsuccessful viewer query) → fail closed: claim nothing, post nothing, stop. Run
   `linear_bot.py queue --state Approved [--lane <arg>]` for the oldest `Approved` +
   `Factory Queue` issues with any lane filter applied, and silently skip unreachable
   `Repo:` paths. Before choosing or claiming any candidate, run
   `run_gate.py check-repo --repo-path "<spec Repo path>"` using this surface's
   literal helper path above. Exit 3 = skip silently (no comment, no state change).
   Exit 4 = fail closed: claim nothing, post nothing, stop the tick. Exit 0 permits
   the remaining pickup checks.
   Inspect delegate-held claims first: when latest activity is older
   than four hours, post
   `SWEPT: stale integrate claim, no activity since <timestamp>` with
   `linear_bot.py comment`, clear the stale delegate with `linear_bot.py unclaim` or
   replace it with your own `linear_bot.py claim`, and keep `Approved`. A human
   assignee is NOT a worker claim —
   reviewed issues routinely carry one; never sweep it, never clear it, and never read
   it as a competing claim. Worker claims live in `delegate` only.

   **Own claims and waiting issues (WD-371).** Never sweep a delegate claim whose
   `delegate.id` equals your own bot viewer id: it is your own earlier claim, not a
   stale foreign one, so keep it, post nothing, and go straight to the approval
   position. An issue whose newest proof pack has no valid approval newer than it is
   WAITING, not work: post nothing, keep your claim, and move to the next candidate in
   queue order. Stop the tick after the first issue that needed an action, or with
   "Factory queue: nothing awaiting integration" when every candidate is waiting.
   **Slack-less pack refresh:** if the newest proof pack disclosed `Slack lane
   unavailable` and the Slack lane is available now (bot token and approval policy
   present), produce ONE fresh proof pack for that issue (rebase, gates, Slack
   approval message); the staleness guard then voids approvals older than the new
   pack.

2. Claim with `linear_bot.py claim <ID>`: it sets the issue's **delegate** to the bot
   viewer (`issueUpdate` with `delegateId`) while preserving `Approved` and leaving
   `assignee` untouched, then re-reads and verifies `delegate.id` equals your bot
   viewer id. Exit 2 (`"claimed": false`) is the lost race — back off silently (no
   comment, no state change, stop this tick). Never claim via `assigneeId`: the
   factory bot is a
   Linear *app user*, and Linear never honors an app-user `assigneeId` as the
   assignee: it returns `success: true`, leaves the human assignee unchanged, and may
   instead populate `delegate` with the app user as a side effect. An assignee claim
   therefore can never verify and this stage would deadlock on its own claim check.
   Team membership does not change this translation — verified 2026-07-27 via
   twice-reproduced clean-state runs with and without the bot in the team. Explicit
   `delegateId` with `delegate.id` read-back verification is the only correct claim
   mechanism. Do not "repair" this back to `assigneeId`. If `delegateId` is itself
   rejected, fail closed: back off silently rather than proceed on an unverified
   claim, and report the blocker.
3. From `linear_bot.py issue <ID>` read the spec, all comments, the latest REVIEW
   REPORT (reviewed SHA), and any MERGE PROOF PACK. Three positions: no proof pack → produce one (steps 4–5);
   proof pack but no newer valid approval → stop silently; an approval newer than
   the latest proof pack whose author id differs from your own Linear viewer id →
   merge (step 6). **Staleness guard:** an approval older than the
   newest proof pack is void — never merge on it. A same-identity approval is void.
   **(d) Branchless deliverable (by-design):** before applying the three
   positions, check the latest review report. If — and only if — it is a PASS
   review whose `Reviewed head` line explicitly attests
   `no branch exists for this issue by design`, the issue has no git artifact to
   merge BY DESIGN: apply the same positions with the branchless variants in the
   "Branchless landing" section below, reading LANDING PROOF PACK wherever the
   positions say MERGE PROOF PACK — steps 4-alt–5-alt replace 4–5 and step 6-alt
   replaces 6. Detection is strict: a PASS review without that explicit
   attestation, or a branch that is merely missing, deleted, or SHA-mismatched,
   is NEVER branchless — those fall through unchanged to the existing behavior
   and failure paths.
4. Fetch the remote and verify the branch still resolves to the reviewed SHA (a
   mismatch or missing branch → defect report, `Todo`, push nothing). Create a
   detached integrate worktree at the current remote default; never touch the user
   checkout; never force-push. Rebase the reviewed branch onto default and re-run
   the spec's command gates through `run_gate.py run` (GATE-RUNNER). Conflict or gate failure →
   numbered defect report
   (wrong → repro → expected vs actual) via `linear_bot.py comment <ID> --body-file -`,
   `linear_bot.py state <ID> Todo`, push nothing, stop.

   **UNATTENDED-GATE-LIMITS** (applies to every gate re-run, including merge-time
   and branchless verification; takes precedence over the ordinary defect path):

   1. Never authenticate as a human. Do not use a password manager, a saved-login
      or vault tool (for example `browser_vault`), a login form, or any credential
      except the factory bot credentials this skill names. A spec may define a
      credential-free test session (for example WD-352's local `mc_session`
      cookie); use only that, never invent an authentication bypass.
   2. Each gate gets a wall-clock limit of 15 minutes, or the limit the spec states.
      Each browser step gets 3 minutes of no visible progress. Record the start
      time, bound tool waits to the remaining limit, and stop that gate when either
      limit is hit. Do not keep retrying or wait for a human in an unattended run.
   3. A gate that needs a human (login, MFA, missing credential, or interactive
      approval), or hits a limit, ends the run with exactly one Linear comment:
      `PRECONDITION: <gate / test-plan step> could not run unattended - <observed signal>`.
      Name only the step and observed status (for example HTTP 401 at
      `/api/content-machine`, redirect to `/login`, or the last visible state at
      timeout), never credential values. Read the existing comments first: if the
      same text already exists for the current proof position, post nothing new.
      Leave the state `Approved`, keep the delegate claim, merge nothing, and post
      nothing else (no proof pack or approval notification). End cleanly with that
      PRECONDITION line in the run output. On later ticks, treat the issue as
      WAITING under WD-371 before re-running gates: keep the claim, post nothing,
      and move to other Approved candidates. Revisit only after a newer human
      comment or a spec change.
   4. A `PRECONDITION:` is not a defect report. Do not move the issue to `Todo`
      for it. Ordinary code failures and rebase conflicts still use the existing
      numbered-defect / `Todo` path; do not relabel them as preconditions.

   **GATE-RUNNER:** Run every Test plan command gate through `run_gate.py run` with
   `--repo-path`, `--cwd` set to the integrate worktree (or the deliverable's folder
   in 4-alt/6-alt), `--timeout-sec` set to the spec's stated limit in seconds or
   omitted for the 15-minute default, and the gate's argv after `--`. Wrap a
   shell-syntax gate as `-- pwsh -NoProfile -Command "<gate>"` or
   `-- bash -c "<gate>"`. Never run a gate command directly. Exit 1 is a gate failure;
   exit 124 and exits 3/4 are `PRECONDITION:` outcomes under UNATTENDED-GATE-LIMITS.
   This rule covers steps 4, 6 (rebuild), 4-alt and 6-alt. Agent-driven browser and
   manual steps remain under UNATTENDED-GATE-LIMITS only, with no changed limits.
   For exit 124, the observed signal is `timed out after <N> s`; for exits 3/4 at
   gate time it is `gate runner refused: repo allowlist`. Keep `Approved` in both
   cases and follow the existing PRECONDITION comment/waiting rule, not the defect
   path. Record each gate's already-redacted `GATE-RESULT` line with its result for
   the proof pack's **Gates re-run** evidence.

5. Post the Slack approval message with `SLACK_FACTORY_BOT_TOKEN` and
   `chat.postMessage` to the `slack_channel_id` from
   `deploy/approval-policy.json`. Select `username` and `icon_emoji` from the
   policy's `sender_identities` entry for the current platform (Hermes resolves
   `hermes@<hostname>`), or `fallback_identity` when unmapped. Include the issue
   ID, reviewed SHA, risk, gates summary, and Linear link; the pack records the
   message link and the message carries the reviewed SHA. Never use the human's
   Slack identity. Missing, invalid, or expired bot token → skip every Slack
   post and DISCLOSE in the pack; the case-insensitive typed-comment gate
   (`APPROVE MERGE` or `APPROVED MERGE`) covers the outage. Then post the proof
   pack with `linear_bot.py comment <ID> --body-file -` (write the pack to a scratch
   file or pipe it in — never inline it as a shell argument) and stop, keeping
   `Approved`. Before writing the merge command, apply the
   **PR-MERGE-TRANSITION-RULE:** query for an existing PR whose head is the reviewed
   branch. If one exists, record `gh pr merge <n> --merge`; if none exists, record
   the existing direct fast-forward push for this pre-PR-era in-flight issue. The
   human approval gate is unchanged:

   ```text
   **MERGE PROOF PACK**
   - **Agent:** Codex / <actual model>
   - **Reviewed head:** <remote branch> @ <SHA>
   - **Default head:** origin/<default> @ <exact SHA the gates ran against>
   - **Rebased head:** <SHA> (or "fast-forward — no rebase needed")
   - **vs default:** <ahead/behind; conflicts and resolution, or none>
   - **Gates re-run:** <commands and results>
   - **Diffstat:** <files / insertions / deletions>
   - **Risk:** <low/moderate/high + why>
   - **Merge command (on approval):** <exact command>
   - **Slack approval message:** <link; or "Slack lane unavailable this pack — typed-comment gate only">
   - **Approval:** tap `:rocket:` on the Slack approval message (configured
     approver only), or reply `APPROVE MERGE` (also accepts `APPROVED MERGE`,
     case-insensitively) on this issue. No merge occurs without one.
   ```

6. On approval: re-read the approval artifact immediately before acting — for a
   Slack 🚀, call `reactions.get` on the NEWEST approval message using
   `SLACK_FACTORY_BOT_TOKEN` and require an exact `approver_slack_user_id` match
   (other reactors ignored silently; never use a human Slack session); for a
   typed comment, require a case-insensitive `APPROVE MERGE` or
   `APPROVED MERGE` prefix and re-verify the viewer-identity check. Back off
   if another session already posted an INTEGRATION REPORT. Fetch and compare the current `origin/<default>` SHA for EQUALITY against
   the proof pack's recorded **Default head** SHA — any inequality voids the
   approval: post a fresh proof pack and stop. **Never trust a SHA from a prior session** — rebuild:
   fresh detached integrate worktree at current default, re-verify the reviewed
   branch SHA, re-run the rebase and command gates through `run_gate.py run` (GATE-RUNNER) on
   the head you just built (failure →
   numbered defect report via `linear_bot.py comment`, `linear_bot.py state <ID> Todo`,
   push nothing).
   **PR-MERGE-TRANSITION-RULE:** Query the reviewed branch for a PR again. If it
   exists, merge with `gh pr merge <n> --merge`; if none exists, treat it as a
   pre-PR-era in-flight issue and use the existing direct fast-forward push of the
   verified rebuilt head. Never auto-merge — Jonathan's valid `:rocket:` or typed
   approval remains the ONLY merge trigger. Never force-push, delete branches, or
   deploy. Fetch and verify
   `git merge-base --is-ancestor <reviewed-SHA> origin/<default>` and
   `git merge-base --is-ancestor <branch-head> origin/<default>` both succeed, post
   the INTEGRATION REPORT (`linear_bot.py comment <ID> --body-file -`), run
   `linear_bot.py state <ID> Done` — it verifies the transition by read-back and
   exits non-zero if the state did not stick — and re-read with
   `linear_bot.py issue <ID>` to confirm the state and report. Close the Slack loop
   with `chat.postMessage`:
   thread-reply the report summary prefixed 🚀 on the approval message using
   `SLACK_FACTORY_BOT_TOKEN` and the same policy-selected sender identity (a
   message, never a reaction), then post a Linear back-reference comment
   (approval author, Slack message link, approval timestamp). If the bot token
   becomes unavailable, disclose the omitted thread reply in the Linear report;
   never substitute a human Slack session.

   ```text
   **INTEGRATION REPORT — MERGED**
   - **Agent:** Codex / <actual model>
   - **Merged:** <branch> @ <SHA> → <default> @ <new SHA>
   - **Ancestry verified:** merge-base --is-ancestor ✓
   - **Approved by:** Jonathan, <approval timestamp>
   - **Gates at merge:** <proof pack reference + anything re-run>
   ```

7. Clean up the detached worktree and stop. One issue per invocation. Secrets never
   appear in proof packs or reports.

## Branchless landing (by-design deliverables)

4-alt. **Verify the live deliverable.** There is no branch, no rebase, and no
   integrate worktree on this path — the deliverable the review attested lives
   outside git (deployed configuration, installed skills, provisioned services).
   Re-run the spec's **Test plan** (command gates via `run_gate.py run`, per GATE-RUNNER)
   against that live deliverable exactly as
   written; secret values never appear in output. If any verification step
   fails: post a numbered defect report (wrong → repro → expected vs actual) with
   `linear_bot.py comment`, run `linear_bot.py state <ID> Todo`, land NOTHING,
   stop — identical to the gate-failure path.

5-alt. **Post the LANDING PROOF PACK and notify.** Post the Slack approval
   message exactly as in step 5 — same `deploy/approval-policy.json` channel,
   same `SLACK_FACTORY_BOT_TOKEN` + `chat.postMessage`, same policy-selected
   sender identity, same outage-disclosure rule. Include the issue ID, the
   by-design attestation line (in place of a reviewed SHA), risk, one-line
   verification summary, and Linear link; the pack records the Slack message
   link and the message carries the pack's deliverable line — the same
   cross-binding, so a re-verified pack (new message) structurally invalidates
   prior reactions. Then post the pack with
   `linear_bot.py comment <ID> --body-file -` and stop, keeping `Approved`:

   ```text
   **LANDING PROOF PACK**
   - **Agent:** Codex / <actual model>
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
   landing on it. Re-run the spec's **Test plan** (command gates via `run_gate.py run`, per
   GATE-RUNNER) against the live deliverable
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
   - **Agent:** Codex / <actual model>
   - **Deliverable verified:** <evidence — each verification command/check and its result against the live deliverable>
   - **Approved by:** Jonathan, <approval comment timestamp>
   - **Gates at landing:** <reference the landing proof pack, plus anything re-run>
   ```
