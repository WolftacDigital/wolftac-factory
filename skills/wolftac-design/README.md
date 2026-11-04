# Design Command System (`/wolftac-design`)

One screen of orientation for a human.

## What this is

A general, brand-agnostic design capability that Wolftac owns. Wolftac is **one
selectable brand profile among several** — the thing that is always applied is the
quality doctrine, never the visual style.

It exists because every visual deliverable used to be improvised per session, so quality
was a function of which model ran and how much context it happened to have. Worse, two
installed Hermes skills still encode the retired v3 green system and will emit it with
full confidence.

## The two rules

1. **Brand isolation.** Wolftac values are emitted only under the `wolftac` profile. No
   brand specified means `neutral`, never Wolftac.
2. **Proof before done.** Every artifact passes a headless render proof and a
   machine-checked brand assertion. A clean render is not proof; a failed assertion is a
   failed build.

## Quick start

```
cd skills/wolftac-design
npm install                     # first use only: puppeteer-core, sharp

node scripts/make-artifact.mjs --template landing-page --profile neutral --out out/page.html
node scripts/render-proof.mjs  out/page.html
node scripts/brand-assert.mjs  out/page.html --profile neutral
```

Asset export:

```
node scripts/render-svg-png.mjs "D:\Wolftac\Brand\assets\logo\web\svg\mark-white.svg" out/mark.png --size 500
node scripts/verify-png.mjs     out/mark.png --size 500 --expect-color "#E8EAE9"
```

## Layout

- `SKILL.md` — the procedure the agent follows.
- `references/` — doctrine, brand isolation, profile schema and intake, routing table,
  delegation map (with the adoption protocol), proof gates.
- `brands/` — `wolftac`, `omas-atlas`, `neutral`, `_template`.
- `templates/` — `landing-page`, `visual-identity-board`, `dashboard-shell`.
- `scripts/` — token resolution, artifact generation, render proof, brand assertion,
  SVG→PNG export, PNG pixel verification.
- `testdata/` — synthetic fixtures for brand intake and the anti-slop diagnostic.

## What it does not do

It is **not** a factory stage. It never touches Linear issue state and is not wired into
the factory queue — `/wolftac-spec`, `/wolftac-build`, `/wolftac-review`, and
`/wolftac-integrate` are the stages; this is a capability they can call.

It does not reimplement installed design skills. Palettes, font pairings, UX rule sets,
Figma I/O and charting are reached by delegation — see `references/delegation-map.md`.

It never writes to `D:\Wolftac\Brand`. It is a read-only consumer of tokens and logo
assets.

## Deployment

This issue (WD-339) builds the package only. Discoverability and invocation from Claude
Code, ports to Hermes / Codex / Claude Desktop, `deploy.ps1` wiring, and retirement of
the two stale v3-green brand skills are WD-340.
