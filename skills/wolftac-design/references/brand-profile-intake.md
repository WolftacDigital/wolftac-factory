# Brand profile intake

How to build a profile from source material. Run this before any visual decision for a
brand not already in `brands/`.

## Sources, in order of authority

1. **A token file** the brand already publishes (`brand-tokens.json` or similar). Set it
   as `tokens_source` and mirror it into `snapshot`. This is the Wolftac case.
2. **A stylesheet** — a `:root` custom-property block is a token export in disguise.
   Extract value by value. Set `tokens_source` to the stylesheet path for provenance;
   the snapshot carries the values, and the provenance line says so. This is the
   OMAS/ATLAS case: that repo has no token file, so the profile *is* the extraction.
3. **A rendered page or screenshot.** Delegate to `design-dna` for extraction, then
   verify every value by eye against the source. This is the weakest source and the one
   most likely to invent a colour that was never in the brand.

## Procedure

1. **Collect.** Get the strongest source available. If you are handed a screenshot and a
   stylesheet exists, go get the stylesheet.
2. **Extract** the palette, the three font families (display, body, mono), the radius,
   and any signature shape treatment — OMAS's 14px cut corner is a good example of a
   signature that is neither colour nor type.
3. **Choose signature tokens.** The smallest set that identifies the brand. Exclude
   generic neutrals: near-white text and near-black backgrounds recur everywhere and
   would produce constant false contamination reports. Check disjointness against every
   other live profile.
4. **Record prohibitions.** Any retired value that still circulates. This is what keeps
   a superseded palette from creeping back.
5. **Write** the profile from `brands/_template.json` and validate it against
   `brand-profile-schema.md`.
6. **Prove it.** Generate one artifact under the new profile and run the full gate:

   ```
   node scripts/make-artifact.mjs --template landing-page --profile <id> --out <out.html>
   node scripts/render-proof.mjs <out.html>
   node scripts/brand-assert.mjs <out.html> --profile <id>
   ```

   A profile that has never rendered is a hypothesis.

## Do not

- **Do not invent a value to fill a slot.** If the source has no mono family, say so and
  fall back to `ui-monospace` in `font_families` — do not pick something that "fits".
- **Do not widen the palette to make an artifact pass.** If the assertion fails on a
  colour, either the artifact is wrong or the extraction missed a value. Go back to the
  source and find out which; a palette edited to silence a failure is a palette that no
  longer means anything.
- **Do not commit real client brand material to this repo.** Only `wolftac`,
  `omas-atlas`, `neutral`, and the blank template ship here. A client profile lives with
  that client's work.

## Test fixture

`testdata/acme-relief-source.html` is a checked-in saved page used to exercise this
procedure end to end. It is synthetic — invented for the purpose, not any real client —
so a reviewer can reproduce the onboarding byte-for-byte without depending on a live
site. `testdata/README.md` records what it is and where its values came from.
