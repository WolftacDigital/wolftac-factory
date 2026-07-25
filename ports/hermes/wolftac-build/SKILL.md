---
name: wolftac-build
description: |
  Factory stage 2 — pull the highest-priority Todo issue labeled Factory Queue from
  Linear (team Wolftac Digital), implement it on a feature branch per its spec, verify
  by executing the test plan, and move it to In Review with a build report. One issue
  per invocation.
  Use when asked to "build", "run the build stage", "work the factory queue", or on a
  recurring factory-build schedule/cron.
  Voice triggers (speech-to-text aliases): "build from the queue", "work the queue".
---

# /wolftac-build — Factory stage 2: execution

Take ONE specced issue from the Linear queue, implement it exactly to spec, prove it
works, hand it to review.

## Hermes MoA routing

Preferred Hermes execution preset: `wolftac-factory-build`.

When launching this stage from Hermes, run it under:

```text
/model wolftac-factory-build --provider moa
```

Model roles inside the preset:
- **GPT-5.6 Sol** is the aggregator / acting builder-verifier. It owns tool calls, code edits, tests, commits, Linear state changes, and the build report.
- **Claude Opus 4.8** advises on architecture, implementation plan, repo-fit, and hidden risks.
- **Claude Sonnet 5** advises on implementation mechanics and likely code changes.
- **Gemma 4 31B QAT via LM Studio** is a low-cost local advisor for code-review sanity checks and defect spotting. Treat Gemma as advisory only; if the LM Studio reference fails or disagrees with cloud models, continue with cloud consensus and real execution proof.

Use MoA reference output as private counsel, not as proof. Proof still comes only from running the repo's commands/tests and inspecting real diffs. If this skill is invoked while Hermes is not already on `provider=moa`, `model=wolftac-factory-build`, do not fake model switching. Continue only if Jonathan explicitly wants the current model to run it, or ask him to relaunch/switch the session to the preset above. A skill document is loaded after the model call starts; it can prescribe the correct model but cannot itself change the active model for the current turn. In unattended/scheduled runs, never pause to ask about model routing: if the job definition requires this stage preset and it is not active, log the mismatch and exit cleanly WITHOUT claiming any issue; otherwise proceed under the active model — the report's Agent line records what actually ran.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — only touch issues carrying this label
- You consume state `Todo`; you produce `In Review`. `Done` is set ONLY by the integrate stage after an approved merge — never by you (review's pass verdict is `Approved`).
- Reports open with `**Agent:** <platform / model>` (e.g. `Hermes / gpt-5.6-sol`).
- Linear tool names vary by platform — use this profile's Linear toolset equivalents.

## Preflight
Confirm Linear tools exist (see /wolftac-spec skill's preflight). No tools → STOP and say so.

### Completion-budget gate
Before claiming an issue, estimate the change surface and the remaining execution/tool budget. Reserve the final 20% of the run for at least one fix cycle, the full test plan, diff review, commit/push verification, the Linear build report, and the `In Review` transition. If that reserve cannot be protected, skip the candidate without claiming it rather than leaving partially edited work in `In Progress`.

For broad multi-file work, follow `references/build-execution-budget-and-batching.md`: batch independent reads, use coherent multi-file edits, never edit the same file concurrently, and run the cheapest compile/type check after each logical batch.

## Procedure

1. **Pick up work.** List issues: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`. Exhaust pagination before concluding the queue or stale-claim set is
   empty. If the listing omits project names, first filter to active `Factory Queue`
   issues, then enrich those candidates before applying a lane filter; do not infer a
   project lane from the issue title. If invoked with an argument (e.g. "build agentic"), treat it as a
   lane filter: only consider issues whose Linear project name or labels contain it
   (case-insensitive).
   **PARKED-NEEDS-HUMAN-SKIP:** Silently skip any issue whose latest review report is
   `REVIEW REPORT — NEEDS HUMAN`; it is parked awaiting Jonathan. Do not comment or
   change state.
   **REWORK-FIRST-ORDERING:** Among eligible `Todo` issues, select issues whose latest
   review report is `REVIEW REPORT — FAIL` before fresh `Todo` issues. Within the
   rework tier and within the fresh tier, preserve priority order
   (Urgent > High > Medium > Low), oldest first on ties.
   Skip issues whose latest comment starts `BLOCKED:`, issues with unresolved blocking relations, and —
   SILENTLY, no comment or state change — issues whose spec **Repo:** path is
   unreachable from this environment (`BLOCKED:` is reserved for genuine spec defects).
   Before choosing a Todo issue, perform a read-first stale-claim sweep: inspect
   `In Progress` issues with label `Factory Queue` (and the lane filter, if any).
   Before resetting one, resolve its Linear-generated branch against `git worktree
   list --porcelain` and inspect any owning issue worktree. A dirty issue worktree is
   preserved operator-attention work, not proof of abandonment: do not sweep, reset,
   claim, edit, commit, or discard it, even when Linear activity is older than four
   hours. Report the worktree path and diff stat in the cron result and follow
   `references/stale-claim-dirty-worktree-safety.md`. If no dirty issue worktree exists
   and the most recent comment, or `startedAt`/claim time when there are no comments,
   is OLDER than 4 hours, you may post exactly
   `SWEPT: stale claim, no activity since <timestamp>` and reset the issue to `Todo`.
   If there is ANY comment younger than 4 hours, the issue is UNTOUCHABLE — never
   sweep it. This sweep is the ONLY time build posts a comment during pickup; during
   a claim-race back off you post NOTHING.

   Before claiming each selected `Todo` candidate, resolve its Linear-generated branch
   against `git worktree list --porcelain` and inspect any owning/reused issue worktree.
   For WSL repos, make this a repository-identity-verified, explicit-path probe: pass the
   literal spec repo path and branch ref to WSL (do not rely on nested shell variables),
   and confirm `git rev-parse --show-toplevel` plus `git remote get-url origin` identify
   the intended repo before trusting the worktree list. If output resolves to the host
   current checkout or any unexpected repo, treat the probe as invalid and rerun it
   before any Linear claim mutation. The claim is permitted only after this explicit
   probe and the target worktree dirty check complete successfully.
   A dirty dedicated worktree is operator-attention work even while Linear says `Todo`:
   do not claim, edit, commit, reset, discard, comment, or change state. Report its path
   and complete dirty summary, then consider the next lane-eligible candidate in normal
   priority order. Use `git diff --stat` for unstaged changes, `git diff --cached --stat`
   for staged changes, and `git status --short` for untracked paths; a branch-range diff
   such as `git diff --stat origin/main...HEAD` does **not** include uncommitted work.
   Follow `references/stale-claim-dirty-worktree-safety.md` for both stale `In Progress`
   claims and dirty reused worktrees on `Todo` candidates. In scheduled runs, this is an
   actionable operator-attention result, not a generic empty queue, so normal
   nothing-buildable suppression does not hide it.

   Queue empty → "Factory queue: nothing buildable", stop cleanly.

2. **Claim it.** State `In Progress`, assignee: the operator (`me`). Immediately
   re-fetch with `get_issue` and verify state is still `In Progress` AND
   assignee is still you. If another session won the race (assignee is someone else,
   or state is no longer `In Progress`), back off silently: no comment, no state
   change, stop this tick. Then read the FULL spec and ALL comments — a prior
   `REVIEW REPORT — FAIL` defect list is your primary work order. If comments show
   the issue was previously built and later reset to `Todo`, verify the existing
   branch/commits before coding. When the approved implementation is already present
   and no FAIL report changed the work order, re-run the test plan, publish current
   proof, and return it to `In Review` without manufacturing a no-op commit. See
   `references/stale-reset-existing-branch-reverification.md`.

3. **Locate the repo.** The spec's **Repo:** header names the working directory.
   Verify it exists and is a git repo. Missing/ambiguous → comment
   `BLOCKED: <what's unclear>`, state back to `Todo`, stop. Reserve `BLOCKED:` for
   spec defects only — a repo that is merely unreachable from this environment
   should have been silently skipped at step 1.

   **WSL repo note:** from Windows/Git Bash, disable path conversion for Linux paths,
   for example `MSYS2_ARG_CONV_EXCL='*' wsl --cd /home/... sh -lc '<cmd>'`.

   Verify any prerequisite that the spec says must already be merged to `main` before
   coding. Linear `Done` is not proof of git integration. If the prerequisite is absent
   from the remote default branch, post a specific `BLOCKED:` comment, restore `Todo`,
   and stop without merging dependency branches or expanding scope.

4. **Branch safely.** Fetch the remote, then create a dedicated worktree for the
   issue's Linear-generated branch from the current remote default branch. Never switch
   branches in a checkout with user changes and never commit directly to main/master.
   A dirty *target/reused issue worktree* owned by another process or human is a
   fail-closed condition. A dirty source checkout does not by itself block creating a
   separate clean issue worktree from the fetched remote default branch: leave the dirty
   checkout untouched, do not switch it, and verify the new worktree starts clean.

   If the Linear-generated branch already contains approved implementation commits but
   predates current `origin/main`, inspect its branch-only history before changing it.
   Without explicit history-rewrite approval, preserve those commits and merge
   `origin/main` into the branch in its dedicated worktree rather than resetting and
   force-pushing. During a merge, `git status` may list many current-main paths; prove
   issue scope with `git diff --cached origin/main` before commit and the final diff
   against `origin/main` afterward. See
   `references/existing-feature-branch-main-integration.md`.

   For WSL-hosted Next.js/WebGL work, also use
   `references/wsl-webgl-build-pitfalls.md` for line endings, React state/fallback
   design, build verification, and honest browser-proof boundaries.

5. **Implement — guardrails.** **Before editing anything**, when the spec uses
   `AC-N`/`NG-N` IDs, compare every `AC-N` against every `NG-N`. Non-goals are
   binding. An acceptance criterion that requires a non-goal is a spec defect:
   comment `BLOCKED: AC-x requires NG-y (<detail>)`, set state back to `Todo`, and
   stop. Old-format (unnumbered) specs proceed under the existing rules.
   If it is not in the Linear issue, it does not exist — no side-channel instructions.
   Read the repo's CLAUDE.md/AGENTS.md first; match conventions. Build exactly to the
   spec's Technical approach and Acceptance criteria. NO scope creep — tangents go in
   the report as "suggest /wolftac-spec". Secrets in .env, never hardcoded. Blocking
   ambiguity → `BLOCKED: <questions>` comment, state `Todo`, stop. Don't guess on
   load-bearing decisions.

6. **Verify.** Execute the spec's Test plan literally. Every criterion needs evidence
   from running code — "looks right" is not verification. Classify proof precisely:
   automated command output, live browser/API behavior, or an unavailable fixture path.
   If a live criterion needs config, credentials, or a fixture that is not safely
   available, do not mutate secrets/config merely to manufacture proof. Run every safe
   check, describe the unverified interaction under **Deviations from spec**, and make
   it the reviewer's first explicit check; never imply that an empty/fallback smoke
   proved the configured live path.

7. **Commit.** Descriptive messages; record SHAs. Review the staged file list before
   committing: never commit artifacts (`__pycache__/`, `*.pyc`, `node_modules/`,
   `dist/`, logs); add .gitignore entries as needed. Push with
   `git push -u origin <branch>` and verify the remote head equals local `HEAD`. If no
   writable remote exists, do not advance the issue to `In Review`.

   **PR-CREATE-CONTRACT:** After push and remote-SHA verification, create one
   non-draft PR per issue for a GitHub remote. Run `gh pr view <branch>` first
   and reuse the existing PR URL when present; never open a second PR for the branch.
   Otherwise run
   `gh pr create --title "WD-NNN: <issue title>" --body "<summary>` followed by a
   blank line, `Closes WD-NNN`, another blank line, and
   `Linear: <issue URL>" --base main --head <branch>`. Retry a `gh` rate-limit or
   transient failure once; if it fails again, record the PR-creation blocker without
   failing a build whose branch published successfully. With no GitHub remote, skip
   PR steps and record `local-only repo — no PR`; execution verification is
   unchanged.

8. **Hand off.** Comment:

   ```
   **BUILD REPORT**
   - **Agent:** Hermes / <model>
   - **Implemented:** <what was built, per spec section>
   - **Verified:** <exact commands run and results, per criterion>
   - **Branch / commits:** <branch> @ <SHAs>
   - **Published head:** <remote branch> @ <exact remote SHA>
   - **PR:** <PR URL, PR-creation blocker, or "local-only repo — no PR">
   - **Deviations from spec:** <none, or what and why>
   - **Notes for review:** <what the reviewer should check first>
   ```

   Then state `In Review`.

9. **Stop.** One issue per invocation.
