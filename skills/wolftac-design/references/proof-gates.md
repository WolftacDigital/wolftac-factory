# Proof gates

An artifact is not done because it looks done. It is done when it has rendered under a
clean browser and passed a machine-checked assertion.

## The gate

```
node scripts/render-proof.mjs  <artifact.html>
node scripts/brand-assert.mjs  <artifact.html> --profile <id>
```

Both must pass. A failed assertion is a failed build, not a caveat in the report.

## Why a clean browser profile is mandatory

`render-proof.mjs` launches with a fresh `--user-data-dir` and `--disable-extensions`.
Daily-driver Chrome extensions on this machine have already produced false verification
verdicts (WD-218: extensions intermittently broke hydration and produced a FAIL against
working code). A verification run that inherits the operator's browser profile is not
evidence.

The launch args are returned in the script's JSON output so a reviewer can assert on
them rather than taking the script's word for it.

## What `brand-assert.mjs` checks

1. **Colour** — every computed `color`, `background-color`, and `border-color` is in the
   profile palette or the documented neutral set.
2. **Type** — every computed `font-family` resolves to a profile family, **and** each
   profile webfont is verified actually loaded via `document.fonts.check()`.
   `getComputedStyle().fontFamily` returns the authored list even when the font file
   404s, so the computed value alone proves nothing about what rendered.
3. **Shape** — profile shape rules hold. Under `wolftac`, computed `border-radius` is
   `0px` on every element.
4. **Revealed end-state** — any element behind a reveal gate is asserted visible *in its
   revealed state*: non-zero opacity, non-zero box, not `visibility: hidden`.
5. **Contrast** — every element that paints its own text is measured against its
   effective (composited) background and must clear WCAG AA: 4.5:1 normal, 3:1 large
   (>=24px, or >=18.66px bold). Translucent colours are composited before measuring.
6. **Cross-brand contamination** — no other profile's signature tokens appear.
7. **Console** — any console error during load fails the gate.

## Revealed end-state, in particular

WD-217 shipped invisible content to production. A gate's specificity beat the reveal
class, so content that existed in the DOM never became visible, and the spot check only
proved the element was there. Existing is not the same as being seen.

So the assertion samples computed opacity and box dimensions *after* the reveal has had
time to run, and names the hidden element when it fails. Verify both sides: an artifact
with a deliberately broken gate must fail, and the repaired one must pass. A gate that
only ever passes is not a gate.

## Only rendered content is evidence

The assertion walks `body` and its descendants and skips `script`, `style`, `link`,
`meta`, `title`, `template`, `noscript`, and `display: none` subtrees. Head-level nodes
inherit the UA default font and have no box; including them reports "Times New Roman"
violations for a page that renders entirely in its profile families.

## Two ways this gate was fail-open, and what closed them

Both were found by review, and both had passed a page that was actually wrong:

- **Alpha was an escape hatch.** The colour check skipped *every* `rgba(...)` value on
  the theory that alpha is just compositing of an allowed hue. It is not: a trailing
  `color: rgba(1, 2, 3, .8)` passed cleanly. The check is now alpha-aware — it parses
  the hue and validates that, exempting only fully transparent values and translucent
  white/black hairlines, which are documented neutrals.
- **`document.fonts.check()` is not proof of loading.** It returns `true` when no
  matching `FontFace` exists at all and a fallback satisfies the request, so an
  artifact with every webfont `<link>` deleted still reported its fonts loaded. The
  check now requires an actual `FontFace` entry for the family in status `loaded`, and
  the failure message reports what `check()` claimed so the trap stays visible.

A third gap was absence rather than fail-open: there was **no contrast measurement at
all**, and shipped templates failed AA at 3.78:1 while every assertion exited 0.

## Horizontal overflow is a render failure

`render-proof.mjs` compares `documentElement.scrollWidth` against `clientWidth` at each
viewport and fails on overflow, naming the widest element and its right edge. A
screenshot proves a render happened, not that it is usable: the 390px dashboard
overflowed to 512px and clipped its entire Status column while the command reported
success.

## Asset exports

```
node scripts/render-svg-png.mjs <in.svg> <out.png> --size 500
node scripts/verify-png.mjs     <out.png> --size 500 --expect-color "#RRGGBB"
```

A clean render return is never success. `verify-png.mjs` reads raw RGBA and reports
dimensions, target-colour pixel count, transparent pixel count, and the content bounding
box with its offset from centre. An empty PNG, a white-matted PNG, and a wrong-sized PNG
all "render successfully".

## Token-spec mode

Token-spec output is JSON, not HTML, so its gate is schema validation against
`brand-profile-schema.md` rather than a render.
