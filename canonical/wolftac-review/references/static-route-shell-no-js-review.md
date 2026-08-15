# Static route shell + no-JS review proof

Use when a factory issue claims a static/public site route foundation, shared shell, clean URLs, route manifest, metadata, progressive enhancement, or no-JS primary content.

## Lesson captured

Passing route-manifest, smoke, and e2e tests is not enough if those tests skip the homepage or only exercise generated routes. In WD-267, all published tests passed, but `/` still depended on client-side population for primary navigation and showed a noscript message that the experience required JavaScript. That violated “every route uses the shared shell” and “primary content/navigation remains available without JavaScript.”

## Required extra probes

1. Identify every route covered by the wording “every route” or “all routes,” including `/`, `/404`, utility pages, and any legacy route that is not generated from the same template pipeline.
2. Run the repo’s declared tests first, but inspect the tests for exclusions such as `if (route.path !== "/")` or homepage-specific bypasses.
3. With JavaScript disabled, open `/` and at least one generated content route. Record observed counts/evidence:
   - one `<h1>`
   - primary `nav a` links present
   - shared footer/legal links present
   - conversion CTA route is a real link, not only a hash/client binding unless the spec permits it
   - active-route indication where applicable (`aria-current="page"` or equivalent)
   - no noscript copy claiming the route requires JavaScript when the acceptance criterion says progressively enhanced
4. For shared-shell criteria, compare `/` against generated routes for the same shell contracts: official logo, skip link, navigation, footer, mobile menu trigger, active state, and CTA.
5. For branded 404 criteria, request an unknown route and require both HTTP 404 and recovery actions (search, contact, get-involved/home as specified).
6. If an included route fails while generated routes pass, fail the review and call out the test blind spot as a separate defect.

## Minimal Playwright probe pattern

```js
const context = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
const page = await context.newPage();
await page.goto(`${base}/`, { waitUntil: "domcontentloaded" });
const proof = {
  title: await page.title(),
  h1: await page.locator("h1").count(),
  navLinks: await page.locator("nav a").count(),
  footer: await page.locator("footer, .page-footer").count(),
  current: await page.locator('[aria-current="page"]').count(),
  ctas: await page.locator('a[href="/get-involved"], a[href="#involved"]').count(),
  noscript: (await page.locator("noscript").innerText().catch(() => "")).slice(0, 200),
};
console.log(JSON.stringify(proof, null, 2));
```

Treat `navLinks=0`, no shared footer, no active state where required, or noscript text saying JavaScript is required as blocking when the spec requires progressively enhanced/indexable content.
