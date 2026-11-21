---
name: wolftac-design
description: Produce a fixed-canvas or web visual artifact — landing page, identity board, dashboard shell, or logo asset export — under an explicitly resolved brand profile, and prove it renders correctly before calling it done. Brand is resolved before any visual decision; with no brand supplied the output is neutral, never Wolftac. Every artifact passes a headless render proof and a machine-checked brand-conformance assertion. Use when asked to design, mock up, restyle, or export any visual deliverable.
---

# /wolftac-design — the Design Command System

You produce visual artifacts. The quality doctrine is always applied; the visual style
never is — that comes from a brand profile you resolve first, explicitly.

This skill orchestrates. It does not reimplement what installed design skills already
do well: palettes, font pairings, UX rule sets, Figma I/O and charting are reached by
delegation (`references/delegation-map.md`), never absorbed.

## The two rules that are not negotiable

1. **Brand isolation.** Wolftac token values may be emitted *only* when the resolved
   profile id is `wolftac`. Absent an explicit brand, use `neutral` — a deliberately
   plain system — so that "no brand specified" produces something honest rather than
   Wolftac-by-accident. See `references/brand-isolation.md`.
2. **Proof before done.** No artifact is complete without a headless render proof and a
   passing brand assertion. A clean render return is not proof. A failed assertion is a
   failed build, not a note in the report. See `references/proof-gates.md`.

## Procedure

### 1. Intake — before any visual decision

Resolve four things. Do not write a line of HTML until all four are settled:

| Field | Question | Default if the user does not say |
|---|---|---|
| **brand** | Whose identity is this? | `neutral` — never Wolftac |
| **artifact type** | landing page, identity board, dashboard shell, asset export? | ask |
| **mode** | generate, restyle, extract-tokens, or export? | `generate` |
| **source material** | is there a reference site, screenshot, Figma file, or token file? | none |

If the ask is ambiguous ("make me something for the new client"), ask. Guessing the
brand is the one failure this skill exists to prevent.

Then print the provenance header — resolved brand, the token source **actually used**,
and the mode — before emitting output:

```
PROVENANCE
  BRAND: wolftac (Wolftac Digital)
  TOKENS: D:\Wolftac\Brand\tokens\brand-tokens.json (canonical, v4.0)
  TYPE:  landing-page
  MODE:  generate
```

`node scripts/resolve-tokens.mjs --profile <id>` prints exactly this and the resolved
bundle. `scripts/make-artifact.mjs` prints it as it generates.

### 2. Resolve tokens — and degrade loudly

Read the profile's `tokens_source` first. If it is unreadable, fall back to the
profile's `snapshot` **and say so**:

```
TOKENS: snapshot v4.0 (canonical source unreachable)
```

Never degrade silently. Silent staleness is exactly how two Wolftac brand skills
survived into 2026 still emitting the retired v3 green palette with full confidence.

### 3. Generate

```
node scripts/make-artifact.mjs --template <landing-page|visual-identity-board|dashboard-shell> \
  --profile <id> --out <file.html>
```

Templates are brand-agnostic: structure and layout only, every colour, family and shape
reached through a CSS custom property filled from the resolved profile. Consult
`references/core-design-doctrine.md` for the quality bar and
`references/artifact-routing-table.md` for type × mode → template, delegates, gate.

For a brand not yet profiled, follow `references/brand-profile-intake.md` and validate
the result against `references/brand-profile-schema.md`.

### 4. Prove it — both halves, every time

```
node scripts/render-proof.mjs  <artifact.html>
node scripts/brand-assert.mjs  <artifact.html> --profile <id>
```

`scripts/render-proof.mjs` launches headless Chrome with a **fresh `--user-data-dir` and
`--disable-extensions`**. This is mandatory, not tidy: daily-driver extensions have
already produced false verification verdicts on this machine (WD-218).

`scripts/brand-assert.mjs` walks the **rendered** DOM and fails on an off-palette colour, an
off-profile font family, a shape-rule violation, a webfont that did not actually load,
a foreign brand's signature token, a console error, or — the one people forget —
**content behind a reveal gate that never revealed**. WD-217 shipped invisible content
to production because a gate's specificity beat the reveal class and the spot check
only proved the element existed in the DOM.

For asset exports:

```
node scripts/render-svg-png.mjs <in.svg> <out.png> --size 500
node scripts/verify-png.mjs     <out.png> --size 500 --expect-color "#E8EAE9"
```

The pixel scan is the proof. A successful render says nothing about colour,
transparency, or centring.

Token-spec mode produces JSON rather than HTML, so its gate is schema validation
against `references/brand-profile-schema.md` instead of a render.

### 5. Review your own output before handing it over

Run `references/anti-slop-diagnostic.md` against what you made. It draws the line this
house has drawn before: **fabricated proof gets deleted** — invented client logos, fake
testimonials, made-up metrics — while **visible bracket placeholders may stay**
(`[Client Name]`, `[Logo]`) when the visual slot itself is the point.

### 6. Report

State the brand, the token source actually used, the templates rendered, the screenshot
paths, and the assertion result. If a delegate was unavailable, say so — the skill
completes on its own doctrine, degraded and visibly so:

```
DELEGATE-UNAVAILABLE: ui-ux-pro-max
```

## Files

| Path | Purpose |
|---|---|
| `references/core-design-doctrine.md` | The brand-independent quality bar |
| `references/anti-slop-diagnostic.md` | Named failure patterns and detection |
| `references/brand-isolation.md` | The hard rule and its enforcement |
| `references/brand-profile-schema.md` | The contract a profile satisfies |
| `references/brand-profile-intake.md` | Building a profile from source material |
| `references/artifact-routing-table.md` | Type × mode → template, delegates, gate |
| `references/delegation-map.md` | Sub-job → installed skill, and the adoption protocol |
| `references/proof-gates.md` | The blocking proof procedure |
| `brands/*.json` | `wolftac`, `omas-atlas`, `neutral`, `_template` |
| `templates/*.html` | `landing-page`, `visual-identity-board`, `dashboard-shell` |
| `scripts/resolve-tokens.mjs` | Token resolution with the loud degrade |
| `scripts/make-artifact.mjs` | Template + profile → artifact |
| `scripts/render-proof.mjs` | Clean-profile headless render, both viewports |
| `scripts/brand-assert.mjs` | Machine-checked conformance on the rendered DOM |
| `scripts/render-svg-png.mjs` | SVG → transparent PNG via sharp |
| `scripts/verify-png.mjs` | Raw-RGBA pixel scan of an exported PNG |
| `testdata/` | Fixtures: brand intake source, sloppy artifact sample |

## Scope

This skill is **not** a factory stage. It never touches Linear issue state, and it is
never wired into the factory queue. It is a design capability the factory's stages — and
Jonathan directly — can call.

Dependencies (`puppeteer-core`, `sharp`) live in this directory's `package.json` and are
installed on first use: `npm install` from `skills/wolftac-design/`. Chrome or Edge is
discovered via `PUPPETEER_EXECUTABLE_PATH` or the standard Windows install paths.
