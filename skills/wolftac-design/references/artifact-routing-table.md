# Artifact routing table

Type × mode → template, delegates, gate. Consult this after intake, before generating.

## Web and fixed-canvas artifacts

| Artifact type | Mode | Template | Delegates | Gate |
|---|---|---|---|---|
| Landing page | generate | `landing-page` | `ui-ux-pro-max` (palette, pairing), `brand-voice:brand-voice-enforcement` (copy) | render proof + brand assert |
| Landing page | restyle | existing HTML, tokens swapped | none required | render proof + brand assert |
| Visual identity board | generate | `visual-identity-board` | `anthropic-skills:canvas-design` (fixed-canvas variants) | render proof + brand assert |
| Dashboard shell | generate | `dashboard-shell` | `dataviz` (any real chart) | render proof + brand assert |
| Published web page | generate | `landing-page` as the draft | `artifact-design` + the Artifact tool | render proof + brand assert **before** publishing |
| Logo / mark export | export | none | none | `render-svg-png.mjs` + `verify-png.mjs` pixel scan |
| Brand token spec | extract-tokens | none — JSON out | `design-dna` (from a screenshot or reference site) | schema validation against `brand-profile-schema.md` |
| Deck | generate | none — out of this skill's templates | `anthropic-skills:pptx` | delegate's own output check |
| Document | generate | none | `anthropic-skills:docx` | delegate's own output check |
| Figma design | generate | none | `figma:figma-generate-design`, `figma:figma-use` | delegate's own output check |
| Figma → code | restyle | none | `figma:figma-design-to-code` | render proof + brand assert on the result |

## Rules

- **Every HTML artifact takes the full gate.** There is no "quick" path; a page that
  skipped the assertion is a page nobody has verified.
- **A delegate's output still gets our gate** when it lands as HTML we own. Figma-to-code
  output is not exempt because Figma produced it.
- **Deck and document output** is gated by the delegate that writes the file — we do not
  render `.pptx` headlessly. Say so in the report rather than implying a proof we did
  not run.
- **Publishing is the last step, never the first.** Assert, then publish. A published
  artifact that fails its own brand check has already reached its audience.

## Mode definitions

| Mode | Means |
|---|---|
| `generate` | New artifact from a template plus a resolved profile. |
| `restyle` | Existing structure, tokens replaced. Structure is not redesigned. |
| `extract-tokens` | Source material in, a validated brand profile out. No visual artifact. |
| `export` | An existing asset rendered to another format at a requested size. |
