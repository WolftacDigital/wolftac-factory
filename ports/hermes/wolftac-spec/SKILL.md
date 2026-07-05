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

## Hermes MoA routing

Preferred Hermes execution preset: `wolftac-factory-spec`.

When launching this stage from Hermes, run it under:

```text
/model wolftac-factory-spec --provider moa
```

Model roles inside the preset:
- **Claude Fable 5** is the aggregator / acting spec writer and owns the final Linear spec voice.
- **GPT 5.5** critiques execution clarity, acceptance criteria, and proof gates.
- **Claude Opus 4.8** critiques architecture, repo-fit, and implementation risk.

If this skill is invoked while Hermes is not already on `provider=moa`, `model=wolftac-factory-spec`, do not fake model switching. Continue only if Jonathan explicitly wants the current model to run it, or ask him to relaunch/switch the session to the preset above. A skill document is loaded after the model call starts; it can prescribe the correct model but cannot itself change the active model for the current turn. In unattended/scheduled runs, never pause to ask about model routing: if the job definition requires this stage preset and it is not active, log the mismatch and exit cleanly WITHOUT claiming any issue; otherwise proceed under the active model — the report's Agent line records what actually ran.

## The factory contract (shared across Claude Code, Claude Desktop, Codex, Hermes)
- Linear team: **Wolftac Digital** (key `WD`)
- Queue label: **Factory Queue** — stages ONLY touch issues carrying this label
- States: `Todo` (specced) → `In Progress` (building) → `In Review` (awaiting QA) → `Done` (set ONLY by the review stage). Failed reviews go back to `Todo` with a defect list.
- Reports open with `**Agent:** <platform / model>` (e.g. `Hermes / gpt-5.5`).
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
   ## User flow
   ## Technical approach
   - **Entry points / affected files:** (real paths, verified)
   - **Data model:**
   - **APIs / interfaces:**
   - **Key decisions:**
   - **Dependencies:**
   ## Acceptance criteria
   - [ ] (each independently testable by a reviewer who didn't build it; include ≥1 failure/edge case)
   ## Test plan
   (exact commands + expected results)
   ## Build notes
   (branch = Linear's generated branch name; secrets in .env never hardcoded; match repo conventions)
   ```

   The **Repo** header line is mandatory — the build stage refuses work without it.

3. **One approval pass.** Show the draft, single go/no-go, accept edits, then file.

4. **File to Linear.** Duplicate-check by title keywords first. Create: team
   `Wolftac Digital`, state `Todo`, label `Factory Queue`, imperative title,
   description = the full spec, priority (default Medium), estimate in points.

5. **Report the issue identifier + URL, then stop.** Building is /wolftac-build's job.

## Guardrails
- One feature per issue; split >~8 points into linked issues.
- Never file a spec with unresolved questions.
