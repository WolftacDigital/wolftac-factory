---
name: wolftac-integrate
description: |
  Factory stage 4 — land the oldest Approved Linear factory issue (team Wolftac
  Digital, label Factory Queue): rebase the reviewed branch onto the remote default
  branch in a detached worktree, re-run the spec's gates, post a MERGE PROOF PACK,
  notify Jonathan on the Telegram approval lane, and merge ONLY after his explicit
  APPROVE MERGE comment. The ONLY stage that sets Done. One issue per invocation.
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
- **The human approval gate is absolute.** A merge happens only after a comment
  starting `APPROVE MERGE`, authored by Jonathan (the workspace owner), NEWER than
  the latest MERGE PROOF PACK on the issue. No approval → no merge, ever. A Telegram
  "approve" reply is relayed into that Linear comment; Telegram is the notification
  lane, Linear is the record.
- Reports open with `**Agent:** Hermes / <model>`.
- Linear tool names vary by platform — use this profile's Linear toolset equivalents.

## Preflight
Confirm Linear tools exist (see /wolftac-spec skill's preflight). No tools → STOP and
say so. In unattended/scheduled runs, if any precondition is missing (Linear
unreachable, repo absent), log why and exit cleanly WITHOUT claiming an issue.

## WSL-hosted repo pattern
When a spec's **Repo:** path is WSL-hosted (for example `/home/... (WSL Ubuntu)`) but
the active Hermes host is Windows, probe it through `wsl.exe` and do all worktree,
rebase, gate, and merge work inside WSL. Keep multi-step git flows in a single shell
invocation and put worktrees under the WSL home (never `/tmp` — it evaporates when
the WSL VM recycles between commands).

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `Approved`, label
   `Factory Queue`, oldest first. If invoked with an argument, treat it as a lane
   filter (match Linear project name or labels, case-insensitive). Skip — SILENTLY,
   no comment or state change — issues whose spec **Repo:** path is unreachable from
   this environment. Before choosing an assignee-held issue, perform a read-first
   stale-claim sweep: if the most recent comment (or claim time when there are none)
   is OLDER than 4 hours, post exactly
   `SWEPT: stale integrate claim, no activity since <timestamp>`, clear or replace
   the stale assignee, and keep the issue in `Approved`. ANY comment younger than
   4 hours makes the issue UNTOUCHABLE. During a claim-race back off you post NOTHING.
   Nothing → "Factory queue: nothing awaiting integration", stop cleanly.

2. **Claim it.** Set assignee `me`, keeping state `Approved`. Immediately re-fetch and
   verify the assignee is still you; on a race, back off silently and stop this tick.

3. **Establish the approval position.** Read the full spec, every comment, the latest
   REVIEW REPORT (for the reviewed SHA), and any prior MERGE PROOF PACK. Three
   positions: (a) no proof pack yet → produce one (steps 4–5), stop;
   (b) proof pack exists, no approval newer than it → stop silently, no comment;
   (c) an `APPROVE MERGE` comment authored by Jonathan NEWER than the latest proof
   pack → merge path (step 6). **Staleness guard:** an approval OLDER than the newest
   proof pack is void — a re-rebase invalidates prior approval; never merge on it.

4. **Rebase and re-run gates.** Fetch the remote; verify the published branch still
   resolves to the reviewed SHA (missing branch or SHA mismatch → the reviewed
   artifact is not immutable → defect report, state `Todo`, push NOTHING). Create a
   detached integrate worktree at the current remote default; never switch the active
   user checkout; NEVER force-push. Rebase (or merge) the reviewed branch onto the
   current default in that worktree and re-run the spec's Test plan gates. Secret
   values never appear in output. Rebase conflict or gate failure → post a numbered
   defect report (what's wrong → repro → expected vs actual), state `Todo`, push
   NOTHING, clean up, stop.

5. **Post the MERGE PROOF PACK and notify.** Comment on the issue, then notify
   Jonathan via the Telegram approval lane. Keep state `Approved`. Stop.

   ```text
   **MERGE PROOF PACK**
   - **Agent:** Hermes / <model>
   - **Reviewed head:** <remote branch> @ <SHA from review report>
   - **Rebased head:** <SHA after rebase> (or "fast-forward — no rebase needed")
   - **vs default:** <ahead/behind; conflicts and resolution, or none>
   - **Gates re-run:** <each gate command and result>
   - **Diffstat:** <files / insertions / deletions vs default>
   - **Risk:** <low/moderate/high + why>
   - **Merge command (on approval):** <exact command>
   - **Approval:** reply `APPROVE MERGE` on this issue. No merge occurs without it.
   ```

6. **Merge (approval path).** Re-read the comments immediately before acting; back
   off if another session posted an INTEGRATION REPORT. If the default branch moved
   since the proof pack, the approval is void — produce a fresh proof pack and stop.
   Execute the merge exactly as stated in the proof pack (fast-forward push or the
   repo's PR-merge convention). Never force-push, never delete branches, never
   deploy. Verify `git merge-base --is-ancestor <branch-head> origin/<default>`
   succeeds — if not, do not set Done; report what you observed. Post the
   INTEGRATION REPORT, set state `Done`, then read the issue back and confirm the
   report exists and the state is actually Done.

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

## Guardrails
- No approval → no merge. No override, no urgency exception.
- Merge to the remote default branch only; production deploys are out of scope.
- Never force-push, never delete branches, never rewrite history.
- `BLOCKED:` is reserved for genuine spec defects; an unreachable repo is a silent skip.
- Secrets never appear in proof packs, reports, or logs.
