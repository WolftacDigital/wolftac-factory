---
name: wolftac-spec
description: |
  Factory stage 1 — turn a feature idea into a build-ready spec through structured
  questioning, then file it as a Linear issue in the software-factory queue
  (team Wolftac Digital, state Todo, label Factory Queue). Produces a Linear issue
  only; never writes code.
  Use when asked to "spec", "spec out", "spec this feature", or when Jonathan
  describes something he wants built and wants it queued for the factory.
  Voice triggers (speech-to-text aliases): "spec it", "spec this out", "add to the factory".
---

# /wolftac-spec — Factory stage 1: specification

Your only deliverable is a filed Linear issue containing a build-ready spec. You never
write code in this stage.

## Hermes model routing

This skill does not choose the model, and it deliberately names no model IDs, because they go stale. The sources of truth are:

- **Model IDs per stage:** `deploy/model-policy.yaml` in the wolftac-factory repo (`harnesses.hermes-moa` for the MoA presets; spec is interactive, so there is no scheduled spec worker). The policy's intent for this stage: the spec sets the quality ceiling for everything downstream and its volume is low, so it runs on the strongest model available.
- **Optional MoA preset** for launching this stage from Hermes: `wolftac-factory-spec`, defined in the profile's `config.yaml` under `moa.presets`:

```text
/model wolftac-factory-spec --provider moa
```

Roles when the MoA preset is active: the **aggregator** is the acting spec writer and owns the final Linear spec voice. The **reference models** are critics only (execution clarity, acceptance criteria and proof gates, architecture, repo-fit, implementation risk).

A skill document is loaded after the model call starts; it cannot change the active model for the current turn, so do not fake model switching. This stage is interactive: if the active model is not the one the model policy names for spec, tell Jonathan once which model is active and continue unless he chooses to relaunch on another — never stall on it. In unattended/scheduled runs, never pause to ask about model routing: if the job definition explicitly requires a stage preset and it is not active, log the mismatch and exit cleanly WITHOUT claiming any issue; otherwise proceed under the active model. The report's Agent line records what actually ran.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — stages ONLY touch issues carrying this label
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) → `Approved` (review PASS, awaiting merge) → `Done` (set ONLY by the integrate stage after an approved merge). Failed reviews go back to `Todo` with a defect list.
- Reports open with `**Agent:** <platform / model>`, using the model ID that actually ran this session (format: `Hermes / <model-id>`). Never copy a model name from a document.
- Linear tool names vary by platform (`create_issue`/`update_issue` vs `save_issue`) — use whatever Linear toolset this Hermes profile has (Linear MCP or Composio Linear).

## Preflight
Confirm you have Linear tools (list/create/update issues, comments). If not, STOP and
tell Jonathan: "No Linear tools in this profile — wire the Linear MCP server
(https://mcp.linear.app/mcp) or the Composio Linear toolkit into config.yaml
`mcp_servers`, then retry." Do not fake the filing or store specs anywhere else.

## Procedure

1. **Understand the ask.** The message after the trigger is the raw feature idea; if
   empty, ask. Interrogate in batches of ≤4 focused questions until you could defend
   every spec section without inventing anything: problem and who has it, success
   criteria, scope boundaries, technical constraints, verification. If you can read
   the target repo, ground the Technical approach in real file paths. Stop asking when
   further answers wouldn't change what gets built.

2. **Draft the spec** using exactly this template (no placeholders, no TBD):

   ```
   **Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

   ## Problem
   ## Goals
   ## Non-goals
   - NG-1: <out-of-scope item>
   ## User flow
   ## Technical approach
   - **Entry points / affected files:** (real paths, verified)
   - **Data model:**
   - **APIs / interfaces:**
   - **Key decisions:**
   - **Dependencies:**
   ## Acceptance criteria
   - [ ] AC-1: <independently testable criterion; prefer "user can X and sees Y" over "code does Z">
   - [ ] AC-2: <failure/edge case criterion>
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
   | AC-1 | <matching test-plan step> | <observable pass result> |
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
   motivating example). Assign stable `AC-N` IDs to acceptance criteria and stable
   `NG-N` IDs to non-goals. IDs are stable once filed — never renumber; deletions
   leave gaps. Before filing, compare every `AC-N` against every `NG-N`. No acceptance
   criterion may require a non-goal; resolve any conflict with the user before filing.
   The Non-goals section protects /wolftac-build from scope creep — be generous.

3. **One approval pass.** Show the draft, single go/no-go, accept edits, then file.

4. **File to Linear.** Duplicate-check by title keywords first. Create: team
   `Wolftac Digital`, state `Todo`, label `Factory Queue`, imperative title,
   description = the full spec, priority (default Medium), estimate in points.

5. **Report the issue identifier + URL, then stop.** Building is /wolftac-build's job.

## Guardrails
- One feature per issue; split >~8 points into linked issues.
- Never file a spec with unresolved questions.
