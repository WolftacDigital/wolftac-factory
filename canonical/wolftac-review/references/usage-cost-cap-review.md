# Usage / cost-cap review pattern

Use this when a Factory Queue spec adds token/cost ledgers, provider spend caps, or a user-facing Cost/Usage dashboard.

## What to verify

1. **Ledger + aggregation contract**
   - Use a scratch ledger/config path (for example `AGENTIC_OS_USAGE_PATH=/tmp/.../usage.jsonl` and `AGENTIC_OS_USAGE_CONFIG_PATH=/tmp/.../usage-config.json`) so review does not mutate the operator's real usage files.
   - Seed at least two records across the promised surfaces (for example paid `extra-model` and free/local `engine`) through the app's exported ledger helpers or through real route calls.
   - Verify `GET /api/.../usage` returns totals that match the ledger: total calls/cost, by-provider, by-model, and by-day buckets.

2. **Cap states and soft-block flow**
   - POST a provider rate/cap through the guarded usage API rather than editing config files directly.
   - Verify a non-zero estimated cost from token counts or text-length fallback.
   - Verify warning threshold and blocked threshold separately (for example cap `$2.50` on `$2.00` spend => warn at 0.8; cap `$1` => blocked).
   - Hit the real paid-call route without confirm and expect the cap response (usually HTTP 402 with `capped: true`).
   - Hit the same route with `confirmOverCap: true` and verify it passes the cap gate. If provider credentials are intentionally blank, an honest provider/config error after the gate (for example 503 missing API key) is valid evidence; do not spend real provider credits just to prove the override path.

3. **Free/local provider exemption**
   - Confirm local/free providers are recorded in the ledger and aggregate but `evaluatePaidCap` or equivalent returns not blocked even if a cap exists.

4. **User-facing surface integration**
   - Browser-open the actual dashboard route, not only the API/helper. Verify the Cost/Usage nav/page renders totals, provider rows, day buckets, cap statuses, and recent calls.
   - If HTML source omits client-rendered strings, use a browser snapshot/DOM view after hydration rather than treating raw `curl` HTML as failed evidence.

5. **Baseline checks**
   - Run the spec test plan from a clean worktree after installing dependencies from lockfile if needed.
   - Run a diff-scoped scan for new secrets/debug leftovers. Ignore pre-existing test fixtures; scan the introduced diff and changed files.
   - Verify route auth/input validation for new GET/POST endpoints.

## Reporting language

Mention the scratch paths and that the live operator ledger was not mutated. For confirm-over-cap tests, explicitly say whether a real external provider call was avoided and what honest downstream error proved the cap gate was bypassed.
