---
name: wolftac-integrate
description: Land one Approved issue from the Wolftac Digital Linear Factory Queue by rebasing the reviewed branch onto the remote default, re-running gates, posting a MERGE PROOF PACK, and merging only after Jonathan's APPROVE MERGE comment. The only stage that sets Done. Use for Wolftac Factory integration.
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
  reactions on older messages are void), OR a comment starting `APPROVE MERGE`,
  authored by Jonathan (typed fallback covering Slack outages). Ids come from
  the policy file only, never hardcoded. No approval → no merge, ever. Workers
  never add reactions to factory notification messages.
- **Approvals must be provably human:** workers connect to Linear through the
  dedicated factory bot identity; humans approve from their own accounts. Fetch your
  own Linear identity (`viewer`) and require that the approval's author id differs
  from your own Linear viewer id. Same identity → do NOT merge; report that the
  identity split is missing and stop.
- **Worker Linear identity is the bot, enforced at the connection:** all Linear
  reads/writes in this stage use the factory bot credentials
  (`LINEAR_FACTORY_BOT_TOKEN` env var, Linear GraphQL API) — never the session's
  default Linear connection. Token absent/invalid/expired → fail closed: claim
  nothing, post nothing, report the precondition, stop.
- **Notification lanes are notify-only:** no relay or automation ever posts the
  approval comment on the human's behalf — a relayed approval is authored by the
  relay's identity and is void.
- One issue per invocation. Reports start with `**Agent:** Codex / <actual model>`.

## Procedure

1. Before queue access, require `LINEAR_FACTORY_BOT_TOKEN` and query `viewer` through
   the Linear GraphQL API with that credential. Do not load or use the session's
   default Linear MCP/toolset. Missing/invalid/expired token or unsuccessful viewer
   query → fail closed: claim nothing, post nothing, stop. Through the bot-authenticated
   connection, list oldest `Approved` + `Factory Queue`, apply any lane filter, and
   silently skip unreachable `Repo:` paths. Inspect assignee-held claims first: when
   latest activity is older than four hours, post
   `SWEPT: stale integrate claim, no activity since <timestamp>`, clear or replace
   the stale assignee, and keep `Approved`.
2. Claim by assigning `me` while preserving `Approved`; immediately re-read and back
   off silently on a race.
3. Read the spec, all comments, the latest REVIEW REPORT (reviewed SHA), and any
   MERGE PROOF PACK. Three positions: no proof pack → produce one (steps 4–5);
   proof pack but no newer valid approval → stop silently; an approval newer than
   the latest proof pack whose author id differs from your own Linear viewer id →
   merge (step 6). **Staleness guard:** an approval older than the
   newest proof pack is void — never merge on it. A same-identity approval is void.
4. Fetch the remote and verify the branch still resolves to the reviewed SHA (a
   mismatch or missing branch → defect report, `Todo`, push nothing). Create a
   detached integrate worktree at the current remote default; never touch the user
   checkout; never force-push. Rebase the reviewed branch onto default and re-run
   the spec's gates. Conflict or gate failure → numbered defect report
   (wrong → repro → expected vs actual), `Todo`, push nothing, stop.
5. Post the Slack approval message to the `slack_channel_id` from
   `deploy/approval-policy.json` (issue ID, reviewed SHA, risk, gates summary,
   Linear link; the pack records the message link, the message carries the
   reviewed SHA). No authenticated Slack path → skip and DISCLOSE in the pack
   (typed-comment gate covers the outage). Then post the proof pack and stop,
   keeping `Approved`:

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
     approver only), or reply `APPROVE MERGE` on this issue. No merge occurs
     without one.
   ```

6. On approval: re-read the approval artifact immediately before acting — for a
   Slack 🚀, re-fetch reactions on the NEWEST approval message via API read and
   require an exact `approver_slack_user_id` match (other reactors ignored
   silently); for a typed comment, re-verify the viewer-identity check. Back off
   if another session already posted an INTEGRATION REPORT. Fetch and compare the current `origin/<default>` SHA for EQUALITY against
   the proof pack's recorded **Default head** SHA — any inequality voids the
   approval: post a fresh proof pack and stop. **Never trust a SHA from a prior session** — rebuild:
   fresh detached integrate worktree at current default, re-verify the reviewed
   branch SHA, re-run the rebase and gates on the head you just built (failure →
   numbered defect report, `Todo`, push nothing). Merge the head you just built
   (fast-forward push or repo PR convention); never force-push, delete branches,
   or deploy. Verify `git merge-base --is-ancestor <branch-head>
   origin/<default>` succeeds, post the INTEGRATION REPORT, set `Done`, and read
   back the state and report. Close the Slack loop: thread-reply the report
   summary prefixed 🚀 on the approval message (a message, never a reaction) and
   post a Linear back-reference comment (approval author, Slack message link,
   approval timestamp).

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
