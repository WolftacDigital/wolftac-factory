# <Imperative feature title>

**Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

## Problem
What's broken or missing, for whom, why it matters now, and what not doing it costs. One tight paragraph.

## Goals
- Concrete outcomes that define success. Each one observable after shipping.

## Non-goals
This section protects /wolftac-build from scope creep — be generous here.
- NG-1: <Out-of-scope item>

## User flow
Step-by-step of what the user sees and does once this ships. Number the steps.

## Technical approach
- **Entry points / affected files:** real paths verified against the repo, not guesses
- **Data model:** tables/fields added or changed and the source of truth for each; migrations needed? Settle this before interfaces.
- **APIs / interfaces:** endpoints, params, auth requirements
- **Key decisions:** chosen approach and why; alternatives considered and rejected, including what was deleted or simplified instead of built. UI work: follows `DESIGN.md` in `wolftac-brand`.
- **Dependencies:** libraries, env vars, external services

## Acceptance criteria
- [ ] AC-1: <Independently testable criterion; prefer "user can X and sees Y" over "code does Z">
- [ ] AC-2: <Failure/edge case criterion>

IDs are stable once filed — never renumber; deletions leave gaps.
No acceptance criterion may require a non-goal. If one does, resolve it with the user before filing.

## Quality gates
Answer each gate substantively or with `N/A — <reason>`; bare `N/A` is non-compliant.
- Accessibility:
- Security / privacy:
  - AI features: state the model fallback, the rate limit and how spend is monitored.
  - Auth features: list each testing setting to restore before launch, such as email confirmation.
  - Public claims: no compliance, certification or security claim without evidence the reviewer can check.
- Rollback / recoverability:
- Data / migration: describe schema or persisted-state changes and the migration/rollback
  path, or use `N/A — <feature-specific reason>` when none are involved.
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

A criterion with no row is a template violation. Filling this table is where the author checks criteria for mutual contradiction. Note: the WD-211 AC3/AC4 conflict is the motivating example.

| Criterion | Test-plan step | Expected observable result | Proven by (worker command or [operator]) |
|---|---|---|---|
| AC-1 | <matching test-plan step> | <observable pass result> | <exact unattended worker command or test, or [operator]> |

## Test plan
How /wolftac-build proves it works and /wolftac-review re-proves it independently: exact commands to run,
cases to cover (happy path, edge, failure), and any manual checks with expected results.

## Build notes
- Branch: use the Linear-generated branch name for this issue.
- Secrets go in .env / env vars — never hardcoded (this is checked at review).
- Match repo conventions: read the repo's CLAUDE.md before writing code.
- <anything environment-specific: ports, services that must be running, test data>
