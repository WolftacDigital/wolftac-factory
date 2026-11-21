# wolftac-design regression tests

Run from `skills/wolftac-design`:

```
node tests/gates.test.mjs
node tests/package.test.mjs
node tests/regressions.test.mjs
```

Each exits non-zero on the first failing assertion set and prints a `RESULT` line.

| File | What it holds down |
|---|---|
| `gates.test.mjs` | The proof gates end to end: seeded palette/font/shape/console defects, the reveal gate, loud degradation when a canonical token source is unreachable, brand isolation between profiles, and the three-template x two-profile matrix. |
| `package.test.mjs` | The package contract: frontmatter shape, LF endings, every path a reference doc cites, delegate degradation, onboarding a new brand from the checked-in fixture, and the SVG-to-PNG pixel scan. |
| `regressions.test.mjs` | The four defects the WD-339 review found in a suite that was already green — RGBA as a palette escape hatch, `document.fonts.check()` mistaken for proof of loading, no contrast measurement at all, and a screenshot mistaken for a usable render. |

## Both directions, always

Every gate is asserted twice: the broken case must fail *and name the offender*, and
the legitimate case it resembles must still pass. Three of the four defects in
`regressions.test.mjs` shipped green because only the firing half was ever checked —
and the alpha exemption in particular is load-bearing, since Wolftac's own
`rgba(255, 255, 255, 0.05)` hairline must keep passing.

Where a threshold is involved the assertion checks the **number**, not the label. The
contrast case reproduces the exact pre-fix ratio (3.78:1 for red-500 on OMAS ink) and
the same colour pair is asserted *not* to fire at 28px, which is what proves the gate
measures rather than pattern-matching a known-bad hex.

## Seeding defects

Inject CSS as a **trailing** override immediately before `</style>`:

```js
const override = (html, css) => html.replace("</style>", css + "\n  </style>");
```

Prepending into an existing rule is beaten by the template's own later declaration in
the same cascade, so the defect never reaches the rendered page and the gate correctly
reports conformance. Two seeded defects went undetected exactly this way.

## Environment

- **Chrome or Edge** at a standard Windows path, or `PUPPETEER_EXECUTABLE_PATH`.
  Renders use a fresh `--user-data-dir` with `--disable-extensions`; daily-driver
  extensions have produced false verification verdicts on this machine before (WD-218).
- `npm install` in `skills/wolftac-design` for `puppeteer-core` and `sharp`.

Two blocks reach outside this repo and **skip loudly** when their dependency is absent —
a skip is counted separately and never folded into the pass count:

| Variable | Default | Used by |
|---|---|---|
| `OMAS_SITE_ROOT` | `D:/Wolftac/omas-relief-site` | `gates.test.mjs` — checks every OMAS palette value against the source `:root` |
| `WOLFTAC_BRAND_ROOT` | `D:/Wolftac/Brand` | `package.test.mjs` — AC-14 logo SVG |
| `WOLFTAC_DELEGATE_PATH` | Hermes `ui-ux-pro-max/scripts/search.py` | `package.test.mjs` — delegate degradation |
| `WOLFTAC_DESIGN_TEST_OUT` | a temp directory | all three — generated artifacts and screenshots |

## Things these suites touch

- `gates.test.mjs` briefly rewrites `brands/wolftac.json` to break its `tokens_source`,
  and restores it in a `finally`.
- `package.test.mjs` briefly **renames the live Hermes delegate script** to prove the
  `DELEGATE-UNAVAILABLE` path, and restores it in a `finally`. If a run is killed
  mid-block, look for a leftover `*.wd339-bak` beside it.
- `package.test.mjs` writes `brands/meridian.json` to test brand onboarding and removes
  it on exit. It should never survive a run, passing or failing.
