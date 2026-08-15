# Pipeline single-gate review proof pattern

Use this when reviewing Agentic OS pipeline work that claims a one-human-gate Inbox/Shaping/Review/Building flow, classifier confidence, or bridge-gated build behavior.

## What to prove

Do not accept helper/unit tests alone as proof. Reproduce the public route behavior against a scratch store so the gate contract is exercised the same way the UI uses it.

1. Build and lint first, then start a scratch server with isolated state:
   ```sh
   AGENTIC_OS_PIPELINE_PATH=/tmp/wd-issue-review-pipeline.json \
   AGENTIC_OS_OPERATOR_TOKEN=review-token \
   npm run start -- -H 127.0.0.1 -p 31XX
   ```
2. Verify guarded routes reject tokenless mutation with `401`.
3. Capture an item through `/api/agentic-os/pipeline/capture`; record its returned `id` and initial `stage`.
4. Classify through `/api/agentic-os/pipeline/classify` with no classifier provider configured when testing honest-offline behavior. Expected: `ok: true`, `offline: true`, `triage.source: unclassified`, no crash, no auto-advance.
5. Attempt `/api/agentic-os/pipeline/build` before Review approval. Expected: `409`, with wording equivalent to "must be approved at the Review gate before building".
6. Move the item to the gate via `/shape`; approve via `/decide { approve: true }`; verify the stage becomes `build`.
7. Attempt `/build` after approval. Expected: still `409` bridge-gated unless the spec explicitly enabled a live builder; the important difference is that the item is now allowed past the human gate.
8. Capture a second item, shape it, reject it, then try to approve/decide it again. Expected: `409`; rejected items must not be able to re-enter Building by re-decide.
9. Inspect the UI diff for visible gate affordances: triage type/confidence or `unclassified — classify`, one Approve/Reject action at Review, and a `needs your call` or equivalent low-confidence flag.
10. Baseline scan the introduced diff for secrets/debug leftovers, but scope it to the changed files/diff so inherited fixtures do not create false positives.

## Evidence wording

In the Linear review report, call out both layers:
- Unit/contract tests covering classifier parse/threshold/offline/unreachable/gate helpers.
- Runtime API smoke proving the route-level path: 401, capture, classify offline, build-before-gate 409, shape, approve, build-after-gate bridge-gated, reject/re-decide 409.

## Pitfalls

- A passing helper test for `canEnterPipelineBuild` is not enough; the previous bug class is route/UI bypass, so smoke the routes.
- Do not treat post-approval builder `409` as a defect when the bridge is intentionally gated. Distinguish "gate passed" from "live generator invoked".
- Use a scratch `AGENTIC_OS_PIPELINE_PATH`; do not mutate the operator's normal pipeline store during review.
