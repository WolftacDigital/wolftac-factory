---
name: wolftac-spec
description: Turn a feature, fix, or task idea into a build-ready Wolftac Factory specification and file one Todo issue in the Wolftac Digital Linear team with the Factory Queue label. Use for requests to spec, plan, define acceptance criteria, or enqueue Factory work. Never implement code.
---

# Wolftac Spec

Create one approved, build-ready Linear specification. Do not write code.

## Contract

- Team: `Wolftac Digital` (`WD`); label: `Factory Queue`; initial state: `Todo`.
- One feature per issue. Split work larger than about 8 points into independently
  buildable, linked issues.
- Require `**Repo:** <absolute path or git URL>` in the first metadata line.
- Use the Linear MCP tools available in the current Codex session. If they are missing
  or unauthorized, stop before filing and explain the Linear setup needed.

## Procedure

1. Treat the skill arguments or surrounding user request as the raw idea. If empty,
   ask for the idea. Ask at most four focused questions per batch until the problem,
   user, success measures, boundaries, constraints, and verification are unambiguous.
2. When the target repository is accessible, read its `AGENTS.md`/`CLAUDE.md` and the
   affected code. Use verified paths and interfaces; do not invent architecture.
3. Read [references/spec-template.md](references/spec-template.md) and fill every
   section. No placeholders or bare `N/A`. Every acceptance criterion must be
   independently testable and have one matching evidence-map row. Include an edge or
   failure criterion and exact test commands with expected observable results.
4. Check open Linear issues for a live duplicate before creating anything.
5. Show the complete draft once and request one go/no-go approval. Apply requested
   edits; do not file without approval.
6. Create one issue with an imperative title, full spec description, state `Todo`,
   label `Factory Queue`, selected priority (default Medium), estimate, and project
   when named.
7. Return the issue identifier and URL, then stop.

## Guardrails

- Never file unresolved contradictions or unanswered load-bearing questions.
- Never change code or start the build stage.
- Use the strongest available reasoning setting selected before this skill starts;
  the skill does not claim it can switch the active model.
