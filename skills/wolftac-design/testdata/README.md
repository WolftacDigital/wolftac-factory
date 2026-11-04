# Test data

Both fixtures are **synthetic** — invented for testing. Neither is real client material,
and neither is derived from any real organisation's site. They ship with the skill so a
reviewer can reproduce the build's results byte-for-byte without a network fetch.

## `acme-relief-source.html`

Source material for brand-profile intake (`references/brand-profile-intake.md`). A saved
page carrying a `:root` custom-property block, which is the second-strongest intake
source: a token export in disguise.

Its values — the ones a correct extraction should recover:

| Role | Value |
|---|---|
| Surface / raised / high | `#14181F` / `#1C222B` / `#262E3A` |
| Text / muted | `#F2F0EB`, `#DDD9D0` / `#8892A0` |
| Signal (accent) | `#2E8B74`, deep `#1F6153` |
| Alert | `#C8622F` |
| Display / body / mono | Barlow Condensed / Source Sans 3 / Roboto Mono |
| Radius | `3px` |

Signature tokens for the resulting profile should be `#2E8B74`, `#C8622F`, and
`Barlow Condensed` — distinctive enough to identify it, and disjoint from `wolftac`,
`omas-atlas`, and `neutral`. The near-white `#F2F0EB` and near-black `#14181F` are
deliberately **excluded**: generic neutrals recur across brands and would produce false
contamination reports.

## `sloppy-sample.html`

Input for the anti-slop diagnostic. Deliberately bad, and specifically constructed so a
correct diagnostic has to distinguish two things that look superficially similar:

- **Fabricated proof, must be flagged for deletion:** the testimonial attributed to
  "Sarah Chen, VP Engineering, Acme", and the "Trusted by 40,000+ teams" claim. Both
  assert something false about the world.
- **Visible placeholders, must be left in place:** `[Client Name]`, `[Logo]`, `[00]`.
  Each announces its own emptiness and holds a slot open for review.

It also carries, for the pattern sweep: a default purple-blue gradient hero, emoji as
icons, centred everything, three lorem cards, unearned glassmorphism over a flat ground,
mixed radii (`18px` and `4px`), and a grey rectangle standing in for content.
