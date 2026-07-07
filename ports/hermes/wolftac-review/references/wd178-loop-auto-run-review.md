# WD-178 loop auto-run review pitfall

Use this as a reference when reviewing specs that require an autonomous loop, scheduler, or "run until gate/cap/stall" behavior.

## Durable lesson

A test that proves a cap/stall helper works is not enough to satisfy an **auto-run** acceptance criterion. The review must verify that the exposed action/control actually advances the workflow without manual operator steps.

## Repro pattern

For a loop feature claiming `build round → council verify → if fail and not stalled, next round → until pass/stall/cap`:

1. Create a scratch loop with a small `maxRounds` (for example `2`) and a gate that will not pass.
2. Trigger the public API/UI auto-run action, not just internal helper functions.
3. After a failing verifier/council result, inspect persisted state:
   - round count should advance automatically when rounds remain;
   - status should become `stalled` exactly at the cap;
   - the loop must not require manual `add-round` calls between failed verification cycles.
4. If the result is still `running` with `canAdvance=true` and the round count unchanged, the auto-run criterion is not met even if majority voting, builder≠verifier, and cap helper tests pass.

## Review report wording

Call this an acceptance-scope defect, not a minor fix, when the spec explicitly promised autonomous progression. Return the issue to Todo with the repro state values and expected behavior.

## Re-review after an auto-run fix

When a builder returns with an `auto-run` action after this failure, do not pass on helper tests alone. Reproduce both layers:

1. **Unit/helper proof:** run the focused loop tests and confirm one test closes on pass after multiple rounds, and another reaches `stalled` at exactly `maxRounds` without manual `add-round` calls. Record `rounds.length`, final status, and `canAdvanceLoop`.
2. **Public route proof:** start a scratch dev server with a temporary loop store and verifier provider keys blank. Call the exposed `POST /api/agentic-os/loop` `auto-run` action. Expected no-provider behavior is `ok: true`, `offline: true`, one persisted pending round, and no crash/spin; then use the manual `verify` action on that round to prove the fallback path still passes.
3. **Council aggregation proof:** use `council-verify` with at least three verifier verdicts and a failing majority (for example 2 fail / 1 pass) and assert the aggregate round verdict is `fail` with all three verifier notes persisted.
4. **Builder self-review proof:** call `council-verify` with `builder` equal to one verifier; expect HTTP 400 and the builder-cannot-grade-itself error.
5. **UI evidence:** open the real Loop dashboard route and verify the auto-run control is visible for running loops and that every verifier's note is visible. If a compact browser/accessibility snapshot hides chip labels, request a full snapshot or inspect the rendered text before concluding notes are missing.

A PASS is reasonable once the route-level no-provider behavior, manual fallback, aggregation, self-review rejection, full test plan, build, and diff-scoped baseline checks all pass.