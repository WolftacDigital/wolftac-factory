# Production DB backfill review proof pattern

Use this when a factory issue claims it backfilled or repaired production data through an already-authorized database tool (for example Supabase MCP/Composio) instead of a checked-in runner, or when a spec acceptance criterion depends on live production rows.

## Goals

- Verify live data outcomes read-only without exposing PII or secrets.
- Distinguish code correctness from operator credential availability (`DATABASE_URL`, Vercel-sensitive env, etc.).
- Confirm safety invariants such as no fabricated foundation records, idempotency, consent-only-if-true, and test/rejected rows untouched.

## Pattern

1. Re-run all offline proof first: targeted tests, full suite, typecheck/lint/build, and diff checks.
2. Inspect the checked-in runner or route for fail-closed behavior:
   - Missing required production env or credentials should block before writes.
   - Output should be IDs/counts/statuses only, not PII or connection strings.
   - Synthetic/test exclusions should be explicit when the spec requires test rows untouched.
3. Use read-only production SQL aggregates for live verification. Prefer counts and booleans over row dumps:
   - foundation invariants: workspaces, live pipelines, live stages, users.
   - graph writes: promoted submissions, contacts, deals, tasks, activities, audit events.
   - untouched rows: rejected/test rows still unpromoted.
   - idempotency evidence: already-promoted guard plus unchanged counts if a live rerun was claimed.
4. For consent/suppression criteria, run a separate aggregate that joins promoted submissions to consent/suppression records and verifies both sides:
   - records exist when the captured payload explicitly opted in.
   - records do not exist for false/missing consent.
5. If a runner cannot be executed locally because credentials are intentionally unavailable, run a fail-closed local check without secrets and verify the production effect through read-only aggregates. Do not fail solely because a protected secret could not be pulled non-interactively.
6. Report the SQL proof as aggregates only. Never include lead names, emails, phone numbers, raw payloads, `DATABASE_URL`, or env file contents.

## Example aggregate checklist

- `accepted_real_promoted = expected real accepted count`
- `accepted_test_unpromoted = expected synthetic/test count`
- `rejected_unpromoted = expected rejected count`
- `promotion_audit_events = promoted count`
- foundation counts unchanged from the build report or known invariant
- consent true rows equal matching consent records; false/missing consent rows have zero records

## Review nuance

A code path can still PASS when the checked-in runner cannot perform the live backfill locally, if all of these are true:

- the runner fails closed before writes when credentials are absent,
- the same promotion logic is covered offline,
- production state is verified read-only after the supervised operator-authorized backfill,
- no secrets/PII were exposed,
- the review report clearly states which live action was reproduced by read-only evidence rather than rerun.
