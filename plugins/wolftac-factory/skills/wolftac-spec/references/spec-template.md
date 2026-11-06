# <Imperative feature title>

**Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

## Problem

What's broken or missing, for whom, why it matters now, and what not doing it costs.

## Goals

## Non-goals

## User flow

## Technical approach

- **Entry points / affected files:** real paths verified against the repository
- **Data model:** tables/fields, source of truth for each, and migrations; settle before interfaces
- **APIs / interfaces:** endpoints, parameters, authorization
- **Key decisions:** selected approach and rejected alternatives, including what was deleted or simplified instead of built; UI work follows `DESIGN.md` in `wolftac-brand`
- **Dependencies:** libraries, environment variables, external services

## Acceptance criteria

- [ ] Independently testable observable result
- [ ] At least one failure or edge case

## Quality gates

Answer substantively or use `N/A — <feature-specific reason>`.

- Accessibility:
- Security / privacy:
  - AI features: state the model fallback, the rate limit and how spend is monitored.
  - Auth features: list each testing setting to restore before launch, such as email confirmation.
  - Public claims: no compliance, certification or security claim without evidence the reviewer can check.
- Rollback / recoverability:
- Data / migration:
- Empty / error states:

## Evidence map

**UNATTENDED-PROOF-SELF-CHECK:** For new specs written after this rule merges, every
acceptance criterion must name an exact worker command or test that can prove it unattended,
or carry `[operator]` for Jonathan to perform after merge. Before the approval pass and
filing, stop and ask the user when any criterion has neither worker proof nor `[operator]`;
do not file until resolved. Record the proof in the Evidence map column "Proven by (worker
command or [operator])". Put each `[operator]` criterion, retaining its ID, in Build notes
under Post-merge operator steps and in the OPERATE follow-up when available. Operator steps
are not required for review PASS. Do not rewrite existing specs or relabel unmet worker
criteria as operator steps.

| Criterion | Test-plan step | Expected observable result | Proven by (worker command or [operator]) |
|---|---|---|---|
| AC-1 | <matching test-plan step> | <observable pass result> | <exact unattended worker command or test, or [operator]> |

## Test plan

Include exact commands, happy/edge/failure cases, manual checks, and expected results.

## Build notes

- Use the Linear-generated branch name.
- Keep secrets in environment variables.
- Follow repository instructions and document environment prerequisites.
