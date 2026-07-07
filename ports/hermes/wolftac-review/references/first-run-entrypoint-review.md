# First-run entrypoint review pattern

Use this when reviewing onboarding, setup wizard, guided first-run, or default dashboard behavior.

## Why this matters

A build can satisfy the direct route/component route while missing the real user entrypoint. In WD-177, `/dashboard/agentic-os/setup` passed direct wizard checks, but `/dashboard/agentic-os` with empty setup progress stayed on the normal dashboard and exposed no `Start here`, setup progress, or resume affordance. That failed the acceptance criterion even though build/lint/unit tests passed.

## Review recipe

1. Identify the browser-local key or persisted state used for first-run detection.
2. Start the app from a clean worktree and real dev/prod command from the spec.
3. In Playwright or browser QA, clear the relevant localStorage/session state.
4. Visit the actual route a first-time operator would open, not just the feature's direct route.
5. Verify one of the explicit first-run entry signals appears:
   - full guided wizard,
   - `Start here`,
   - setup progress summary,
   - resume affordance with the last step.
6. Then visit the direct feature route to verify step controls, persistence, and degraded/error states.
7. If the entrypoint fails but the direct route works, classify as FAIL unless the spec explicitly scoped direct-route-only behavior.

## Minimal Playwright probe shape

```js
await page.goto("http://127.0.0.1:PORT/dashboard/agentic-os", { waitUntil: "networkidle" });
await page.evaluate(() => localStorage.removeItem("<setup-progress-key>"));
await page.reload({ waitUntil: "networkidle" });
const text = await page.locator("body").innerText();
if (!/Start here|Setup \d+\/\d+|resume|Setup is a buffet/i.test(text)) {
  throw new Error("First-run entrypoint missing");
}
```

## Reporting wording

Anchor the defect to the actual user route:

> Clear `<storage-key>`, open `<root/dashboard route>`. Expected first-run wizard or setup resume affordance. Actual normal dashboard/no setup affordance. Direct `<feature route>` works, but the first-run entrypoint is missing.
