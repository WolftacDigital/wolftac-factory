---
name: wolftac-spec
description: Factory stage 1 — turn a feature idea into a build-ready spec through structured questioning, then file it as a Linear issue in the software-factory queue (team Wolftac Digital, state Todo, label Factory Queue). Use when the user wants to spec out a feature, fix, or task. Produces a Linear issue only; never writes code.
---

# /wolftac-spec — Factory stage 1: specification

Your only deliverable is a filed Linear issue containing a build-ready spec. You never
write code in this stage.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — stages ONLY touch issues carrying this label
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) → `Done` (set ONLY by the review stage). Failed reviews go back to `Todo` with a defect list.
- Reports open with `**Agent:** <platform / model>` (e.g. `Claude Desktop / Fable 5`).
- Tool access: this runs on the claude.ai **Linear connector** (tools like `list_issues`,
  `save_issue`, `save_comment`). If Linear tools aren't available in this chat, ask the
  user to enable the Linear connector in the tools menu, then retry. Tool names vary by
  platform (`save_issue` here vs `create_issue`/`update_issue` elsewhere).

## Procedure

1. **Understand the ask.** Interrogate the user in batches of ≤4 focused questions
   until you could defend every spec section without inventing anything: the problem
   and who has it, success criteria, scope boundaries, technical constraints,
   verification. Stop asking when further answers wouldn't change what gets built.
   Note: from Desktop you usually can't read the target repo — get file paths and
   technical grounding from the user, and say so in Key decisions if unverified.

2. **Draft the spec** using exactly this template (no placeholders, no TBD — if you
   can't fill a section, go back to step 1):

   ```
   **Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

   ## Problem
   ## Goals
   ## Non-goals
   ## User flow
   ## Technical approach
   - **Entry points / affected files:**
   - **Data model:**
   - **APIs / interfaces:**
   - **Key decisions:**
   - **Dependencies:**
   ## Acceptance criteria
   - [ ] (each independently testable by a reviewer who didn't build it; include ≥1 failure/edge case)
   ## Quality gates
   - Accessibility:
   - Security / privacy:
   - Rollback / recoverability:
   - Data / migration: (schema/persisted-state changes and migration/rollback path,
     or `N/A — <feature-specific reason>`)
   - Empty / error states:
   ## Evidence map
   | Criterion | Test-plan step | Expected observable result |
   |---|---|---|
   | <one row per acceptance criterion> | <matching test-plan step> | <observable pass result> |
   ## Test plan
   (exact commands + expected results)
   ## Build notes
   (branch = Linear's generated branch name; secrets in .env never hardcoded; match repo conventions)
   ```

   The **Repo** header line is mandatory — the build stage refuses work without it.
   Quality gates must be answered substantively or as `N/A — <reason>`; bare `N/A`
   is invalid. The Evidence map must include one row per acceptance criterion; a
   criterion with no row is a template violation. While filling it, check criteria for
   mutual contradiction and resolve conflicts before filing (WD-211 AC3/AC4 is the
   motivating example).

3. **One approval pass.** Show the draft, single go/no-go, accept edits, then file.

4. **File to Linear.** Duplicate-check by title keywords first (team Wolftac Digital).
   Then create: team `Wolftac Digital`, state `Todo`, label `Factory Queue`,
   imperative title, description = the full spec, priority (default Medium), estimate.

5. **Report the issue identifier + URL, then stop.** Building is the build stage's job.

## Guardrails
- One feature per issue; split anything larger than ~8 points into linked issues.
- Never file a spec with unresolved questions.
- This is the strategy stage — depth here pays for itself downstream.
