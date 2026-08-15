# Static-site CTA contrast review note

Use when a factory review covers a static marketing route with shared button styles and page-specific light/dark sections.

## Lesson

DOM presence is not enough for CTA acceptance. A link can exist, have the correct text, and pass route tests while its label is visually unreadable because page-specific color overrides do not override the shared button background/fill.

## Review pattern

1. Run the route/e2e tests from a scratch server with the same `BASE_URL` the tests expect.
2. Open the route in the browser and inspect the CTA section visually, not just through accessibility snapshots.
3. For each primary/secondary CTA, verify:
   - label is visibly readable on its rendered background,
   - hover/focus treatment does not erase contrast,
   - href points to the approved route contract,
   - the CTA remains readable in both light and dark page bands.
4. If a low-risk CSS-only contrast issue is found during review, it is eligible for PASS WITH FIXES when:
   - the fix is isolated to styling,
   - no design/content scope changes are introduced,
   - route/e2e/smoke tests are rerun,
   - browser/vision proof confirms the label is visible after the fix,
   - the branch is pushed and remote head is reverified.

## Concrete failure shape

Shared `.btn--ghost` styles may define a dark background and light label for dark hero sections. A light-section override that changes only `color` can produce a black button body with a nearly hidden or absent-looking label. Fix by overriding the actual fill/background/box-shadow for the light section, then recheck the rendered page.
