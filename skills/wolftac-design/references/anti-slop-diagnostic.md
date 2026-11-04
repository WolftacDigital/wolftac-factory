# Anti-slop diagnostic

Run this against your own output before handing it over. Each pattern has a detection
heuristic you can actually apply, not a vibe.

## The line this house draws

**Fabricated proof gets deleted. Visible placeholders may stay.**

These are not the same thing and the distinction is the whole point:

- **Fabricated proof** asserts something false about the world — an invented client
  logo, a testimonial from a person who does not exist, a made-up metric, a fictional
  case study, a fake press mention. It reads as evidence. **Delete it.** There is no
  version of this that is acceptable "as a placeholder", because a reader cannot tell
  it apart from a real claim.
- **A visible bracket placeholder** announces its own emptiness — `[Client Name]`,
  `[Logo]`, `[00%]`, `[contact@example.org]`. It holds a visual slot open so the layout
  can be judged. **Leave it.** When the artifact's purpose is to show the slot, removing
  it destroys the thing being reviewed.

Detection: for every specific claim in the artifact, ask "could I point at where this
came from?" A named person with a job title and a quote needs a source. `[Name]` does
not, because it claims nothing.

## Named failure patterns

| Pattern | How to detect it | What to do |
|---|---|---|
| **Default gradient hero** | A purple-to-blue (or teal-to-indigo) linear gradient behind the first heading, unrelated to any brand token | Replace with a profile background; if the brand has no gradient, it has no gradient |
| **Emoji as icon** | Emoji characters used in nav, buttons, feature cards, or status indicators | Remove. Use a text label, or a real glyph the brand owns |
| **Centred everything** | `text-align: center` on the majority of blocks; no left edge to read down | Establish one alignment spine; centre only what is genuinely symmetric |
| **Three lorem cards** | Exactly three feature cards with interchangeable filler and no real distinction | Either write the real three, or cut to what is true |
| **Unearned glassmorphism** | `backdrop-filter: blur()` on panels that sit over a flat colour, so it blurs nothing | Remove. It costs paint and communicates nothing |
| **Mixed radii** | More than one `border-radius` value in the artifact, or any radius under a profile whose `shape_rules.border_radius_px` is 0 | Take the radius from the token. `brand-assert.mjs` fails this under Wolftac |
| **Grey rectangle content** | Placeholder boxes shaped like stock photography, standing in where real content belongs | Either bring real content or make the slot honest with a bracket label |
| **Fake density** | Numbers, chips, and sparklines invented to make a dashboard look busy | Bracket the values. A dashboard of `[00]` reads as a shell, which is what it is |
| **Contrast theatre** | Body copy in a mid-grey that fails AA at its rendered size but "looks premium" | Measure it. A colour that fails is not used, however well it looks |
| **Motion that moves content** | Transitions on layout properties, parallax on text, anything over 150ms in the reading path | Cut it. Effects are atmosphere; content does not move |

## Procedure

1. Read the artifact once as a reader, not as its author. Note every specific claim.
2. For each claim: sourced, or fabricated? Fabricated goes, immediately.
3. Sweep the pattern table above.
4. Report findings as a list, each naming the element and the verdict. Say explicitly
   which placeholders you are **leaving** and why — a diagnostic that silently strips
   the layout's placeholders has broken the artifact it was checking.
