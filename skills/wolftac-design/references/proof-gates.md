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
5. **Cross-brand contamination** — no other profile's signature tokens appear.
6. **Console** — any console error during load fails the gate.

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
