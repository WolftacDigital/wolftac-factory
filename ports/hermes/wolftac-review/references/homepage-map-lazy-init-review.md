# Homepage map lazy-init review pattern

Use this when a factory review touches homepage program links, map controls/filters, Mapbox fallback, or lazy-loaded map initialization.

## Why

A route/e2e suite can pass service navigation while missing a homepage runtime bug: map controls may be clickable before the lazy map engine has initialized. If handlers call `state.engine.setFilter(...)` or `state.engine.focus(...)` while `state.engine` is still null, users get uncaught page errors even though static route and detail-page tests pass.

Placeholder or unusable public Mapbox tokens can also cause noisy Mapbox startup failures instead of clean canvas fallback. Treat this as a functional homepage regression when the spec says service links must not break map interactions.

## Required probes

Run the normal spec tests first, then add a targeted browser probe on a scratch server:

```bash
PORT=4273 npm run serve
BASE_URL=http://127.0.0.1:4273 npm run test:e2e -- services
```

Then run or hand-author a Playwright check that:

1. Opens `/` on the scratch `BASE_URL`.
2. Captures `pageerror` events.
3. Clicks at least one operations map filter before/while the map section is lazily initializing.
4. Clicks a homepage program/service card link.
5. Waits for the expected `/services/<slug>` route.
6. Asserts exactly one H1 on the target route and no page errors.
7. Scrolls the map stage into view and waits briefly to prove fallback startup emits no page errors.

Example probe:

```js
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(process.env.BASE_URL + '/', { waitUntil: 'domcontentloaded' });
  await page.locator('button:has-text("TRAINING & READINESS")').click();
  await page.locator('.program__link').first().click();
  await page.waitForURL(process.env.BASE_URL + '/services/strategic-healthcare-solutions');
  assert.equal(await page.locator('h1').count(), 1);
  assert.deepEqual(errors, []);
  await browser.close();
})();
```

## Passing implementation signs

- Map filter and operation-selection handlers either no-op safely or initialize the engine before calling engine methods.
- Unusable placeholder tokens (for example `pk...` placeholders) route to the canvas fallback instead of trying Mapbox.
- Mapbox auth/style failure falls back without leaving a black box or throwing uncaught page errors.
- The review report names the targeted map probe separately from the generic service e2e suite.

## Port/scratch-server note

If a smoke test unexpectedly fails on the default dev port, verify whether an unrelated server is already bound there. Re-run against an explicitly started scratch port via `BASE_URL=http://127.0.0.1:<port>` before treating the failure as product evidence.