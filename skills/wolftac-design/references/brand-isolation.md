# Brand isolation

## The rule

Wolftac token values may be emitted **only** when the resolved profile id is `wolftac`.
Absent an explicit brand, the profile is `neutral`.

This is not a style preference. A client artifact carrying Wolftac's tactical HUD look
is a delivery defect: it tells the client's audience something false about whose work
they are looking at.

## Why `neutral` exists

If "no brand supplied" fell back to house style, every unspecified request would emit
Wolftac. `neutral.json` is deliberately plain — system font stack, greyscale plus one
restrained accent, modest radius — so that the absence of a brand produces something
honest rather than something branded by accident.

Neutral is not a lesser artifact. It gets the same doctrine, proof gate, and states
treatment as any other profile. It just says nothing about who made it.

## Enforcement, both directions

`scripts/brand-assert.mjs` runs against the **rendered** DOM, not the source text:

- **Outbound:** every computed colour must be in the resolved profile's palette or the
  documented neutral set; every computed font family must resolve to a profile family;
  shape rules hold.
- **Inbound:** the artifact must not contain the **signature tokens** of any *other*
  profile in `brands/`.

## Signature tokens, and why the check is narrow

Contamination is checked against `signature_tokens` only — never full palettes.

Generic neutrals legitimately recur across brands. Near-white body text and near-black
backgrounds appear in Wolftac, OMAS, and half the brands anyone will ever onboard.
Wolftac's `#E2E8F0` body text turning up in a neutral artifact is not leakage; `#FF5E00`
is. Checking full palettes would produce constant false positives, and a check that
cries wolf gets disabled — which is how the rule dies.

So a signature token is the small set of values distinctive enough to *identify* the
brand:

| Profile | Signature tokens |
|---|---|
| `wolftac` | `#FF5E00`, `#00F0FF`, `#050706`, `Rajdhani`, `Chakra Petch` |
| `omas-atlas` | `#C43B3B`, `#EDEAE3`, `#D9A441`, `Space Grotesk`, `IBM Plex Mono` |
| `neutral` | `#2F5B8C` |

## Prohibitions

A profile may also list values that must never appear even though they are not another
live profile's signature. Wolftac's prohibitions carry the retired v3 system —
`#7CFF98`, `#8FF0BC`, `Archivo` — precisely because two installed Hermes skills still
emit them.
