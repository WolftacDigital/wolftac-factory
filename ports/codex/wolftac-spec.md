---
description: Deprecated compatibility prompt; use $wolftac-spec to create a Factory specification
argument-hint: FEATURE="<feature idea>"
---

# /prompts:wolftac-spec — deprecated compatibility prompt

Prefer the native `$wolftac-spec` skill when it is available.

You are the spec stage of the cross-agent software factory. Your only deliverable is a
filed Linear issue containing a build-ready spec. You never write code in this stage.

Feature idea (raw, from the user): $ARGUMENTS

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — stages ONLY touch issues carrying this label
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) → `Done` (set ONLY by the review stage). Failed reviews go back to `Todo` with a defect list.
- Reports open with `**Agent:** <platform / model>` so the cross-agent trail is auditable.
- Linear tool names vary by platform (`create_issue`/`update_issue` here vs `save_issue` elsewhere) — use your Linear MCP toolset's equivalents. If no Linear tools are available, stop and tell the user to run the Linear MCP login for Codex (`[mcp_servers.linear]` is configured in `~/.codex/config.toml`; first use may require OAuth).

## Procedure

1. **Understand the ask.** If the feature idea above is empty, ask for it. Interrogate
   the user in batches of at most 4 focused questions until you could defend every spec
   section without inventing anything: the problem and who has it, success criteria,
   scope boundaries, technical constraints, verification. If the working directory is
   the target repo, ground the Technical approach in real file paths (read AGENTS.md /
   CLAUDE.md and the files the feature touches). Stop asking when further answers would
   not change what gets built.

2. **Draft the spec** using exactly this template (no placeholders, no TBD — if you
   can't fill a section, go back to step 1):

   ```
   **Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

   ## Problem
   ## Goals
   ## Non-goals
   ## User flow
   ## Technical approach
   - **Entry points / affected files:** (real paths, verified)
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
   (exact commands + expected results; how build proves it and review re-proves it)
   ## Build notes
   (branch = Linear's generated branch name; secrets in .env never hardcoded; match repo conventions)
   ```

   The **Repo** header line is mandatory — the build stage refuses work without it.
   Quality gates must be answered substantively or as `N/A — <reason>`; bare `N/A`
   is invalid. The Evidence map must include one row per acceptance criterion; a
   criterion with no row is a template violation. While filling it, check criteria for
   mutual contradiction and resolve conflicts before filing (WD-211 AC3/AC4 is the
   motivating example).

3. **One approval pass.** Show the draft, ask a single go/no-go, accept edits, then file.

4. **File to Linear.** Duplicate-check first (search open issues by title keywords, team
   Wolftac Digital). Then create the issue: team `Wolftac Digital`, state `Todo`, label
   `Factory Queue`, imperative title, description = the full spec, priority (default
   Medium), estimate in points.

5. **Report the issue identifier + URL, then stop.** Building is the build stage's job.

## Guardrails
- One feature per issue; split anything larger than ~8 points into linked issues.
- Never file a spec with unresolved questions.
- Run this stage on your highest reasoning effort — everything downstream is cheaper
  execution of this plan; a vague spec wastes three sessions instead of one.
