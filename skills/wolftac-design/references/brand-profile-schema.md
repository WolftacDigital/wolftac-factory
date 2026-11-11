# Brand profile schema

A profile is JSON, not markdown, because `brand-assert.mjs` has to read it mechanically.
A profile a human can read but a script cannot enforce is a style guide, and style
guides drift.

## Required fields

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Filename stem and the value passed to `--profile`. Lowercase, hyphenated. |
| `name` | string | Human display name. |
| `tokens_source` | string or `null` | Absolute path to the canonical token file. `null` means the profile's own snapshot **is** canon. |
| `snapshot_version` | string | Version of the embedded snapshot. |
| `snapshot` | object | Fallback token bundle: `color`, `typography`, `layout`, `effects`, optionally `logo`. |
| `voice` | string | One line on how the brand sounds. Used for copy direction, never for visuals. |
| `artifact_defaults` | object | Per artifact type: background, max width, whether reveal is used. |
| `signature_tokens` | string[] | The small set of values distinctive enough to identify this brand. Used **only** for cross-profile contamination. |
| `prohibitions` | string[] | Values that must never appear, even if no live profile claims them (e.g. a retired palette). |
| `palette` | string[] | Every hex the profile may emit. `brand-assert.mjs` checks computed colours against this. |
| `font_families` | string[] | Families in order: display, body, mono. The assertion checks computed families and `document.fonts.check()` against these. |
| `shape_rules` | object | `border_radius_px`: a number to enforce exactly, or `null` for no shape rule. |

## Validation rules

1. `id` matches the filename stem.
2. `palette` contains every hex referenced anywhere in `snapshot.color`.
3. `signature_tokens` is a strict subset of `palette` plus `font_families` — you cannot
   declare a signature the profile never emits.
4. `signature_tokens` contains no generic neutral (`#FFFFFF`, `#000000`, near-white body
   text, near-black backgrounds). A signature must *identify* the brand; a shared
   neutral produces false contamination reports and gets the check disabled.
5. `signature_tokens` of any two live profiles are disjoint.
6. `font_families[0..2]` map to display, body, and mono in that order. `make-artifact.mjs`
   reads them positionally.
7. `shape_rules.border_radius_px` is `0`, a positive integer, or `null`. `0` is enforced
   strictly — every computed corner must be `0px`.
8. If `tokens_source` is non-null it is an absolute path. It may point at a non-token
   file (e.g. a CSS file the profile was extracted from); in that case the snapshot
   carries the values and the provenance line says so.

## The `_template.json` blank

`brands/_template.json` is the starting point for a new profile. It is excluded from
contamination checks — it has no signature tokens and emits nothing.

## Validating a profile

```
node scripts/resolve-tokens.mjs --profile <id>
```

Prints the provenance header and the resolved bundle. A profile that resolves, plus an
artifact that passes `brand-assert.mjs --profile <id>`, is a validated profile —
schema conformance that is never exercised by a render proves nothing.
