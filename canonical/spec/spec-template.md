# <Imperative feature title>

**Repo:** <absolute path or git URL> | **Priority:** <Urgent/High/Medium/Low> | **Estimate:** <points>

## Problem
What's broken or missing, for whom, and why it matters now. One tight paragraph.

## Goals
- Concrete outcomes that define success. Each one observable after shipping.

## Non-goals
- Explicitly out of scope. This section protects /build from scope creep — be generous here.

## User flow
Step-by-step of what the user sees and does once this ships. Number the steps.

## Technical approach
- **Entry points / affected files:** real paths verified against the repo, not guesses
- **Data model:** tables/fields added or changed; migrations needed?
- **APIs / interfaces:** endpoints, params, auth requirements
- **Key decisions:** chosen approach and why; alternatives considered and rejected
- **Dependencies:** libraries, env vars, external services

## Acceptance criteria
- [ ] Each criterion must be independently testable by a reviewer who didn't build it
- [ ] Prefer "user can X and sees Y" over "code does Z"
- [ ] Include at least one failure/edge case criterion

## Test plan
How /build proves it works and /review re-proves it independently: exact commands to run,
cases to cover (happy path, edge, failure), and any manual checks with expected results.

## Build notes
- Branch: use the Linear-generated branch name for this issue.
- Secrets go in .env / env vars — never hardcoded (this is checked at review).
- Match repo conventions: read the repo's CLAUDE.md before writing code.
- <anything environment-specific: ports, services that must be running, test data>
