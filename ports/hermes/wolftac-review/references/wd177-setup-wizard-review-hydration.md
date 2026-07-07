# WD-177 setup wizard review: persisted browser state + hydration proof

Use this as a review reference when a Wolftac Factory issue adds a client-side first-run/onboarding flow that persists progress in `localStorage` or other browser-only stores.

## Durable lesson

A Next/React client component can pass `npm run build` and SSR smoke while still throwing a recoverable hydration mismatch once the browser has persisted state. If initial `useState(() => readLocalStorage())` changes visible text between server render and client hydration, the dev overlay reports:

```text
Hydration failed because the server rendered text didn't match the client.
```

For persisted browser progress, initialize render-visible state from a server-stable default, then load persisted browser state after hydration. In WD-177 the review fix changed setup progress from localStorage-backed initializer state to default state plus a post-hydration timer/effect that reads `setup-progress` and updates the active step.

## Review pattern

1. Test an empty browser store first: open the page and verify the first-run default UI.
2. Create persisted state through the UI, not by assuming reducer/unit tests are sufficient: mark one step complete, skip another, then reload the page.
3. Inspect browser console / Next dev overlay for hydration errors after reload. A page that visually resumes but logs hydration mismatch is a real defect.
4. If browser tool JavaScript evaluation blocks `localStorage` or network monkey-patching, use the repo's Playwright dependency from the WSL worktree for deterministic probes:

```js
const { chromium } = require('playwright');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto('http://127.0.0.1:PORT/dashboard/agentic-os/setup', { waitUntil: 'networkidle' });
```

5. For API-unreachable acceptance criteria, Playwright route interception is a clean proof path:

```js
await page.route('**/api/agentic-os/doctor', route => route.abort('failed'));
await page.goto('http://127.0.0.1:PORT/dashboard/agentic-os/setup', { waitUntil: 'networkidle' });
const text = await page.locator('body').innerText();
console.log({
  hasFallback: text.includes('Graceful fallback active'),
  hasNoBlank: text.includes('Setup is a buffet') && text.includes('Dashboard shell'),
});
```

## PASS WITH FIXES notes

When a hydration bug is minor, low-risk, and localized, it fits PASS WITH FIXES:

- attach the detached review to the actual branch/worktree before committing;
- commit the fix on the builder branch;
- re-run the full requested test plan (`test`, `lint`, `build`);
- re-run browser proof that originally exposed the issue;
- include the fix SHA in the Linear review report.
