# Delegation map

`/wolftac-design` orchestrates. Each sub-job below belongs to a skill someone else
maintains; we reach it by name rather than reimplementing it. Absorbing a maintained
skill means owning its staleness — that is how the two v3-green Wolftac brand skills
happened.

When a delegate is unavailable on the host, emit exactly:

```
DELEGATE-UNAVAILABLE: <skill>
```

then complete on this skill's own doctrine. Degraded, and visibly so — never silently
skipped, never a hard failure.

## Registry

| Sub-job | Delegate | Invocation | Host availability |
|---|---|---|---|
| Tokens from a reference site or screenshot | `design-dna` | skill invocation | check before use |
| Palette lookup | `ui-ux-pro-max` | `python "C:/Users/logan_vwagmmf/AppData/Local/hermes/skills/creative/ui-ux-pro-max/scripts/search.py" "<query>"` | absolute Windows path — that skill's own note warns MSYS mangles `/c/...` |
| Font pairing | `ui-ux-pro-max` | as above | as above |
| UX rule lookup | `ui-ux-pro-max` | as above | as above |
| Figma read → code | `figma:figma-design-to-code` | skill invocation | needs Figma auth |
| Figma write / generate | `figma:figma-generate-design`, `figma:figma-use` | skill invocation | needs Figma auth |
| Charts and data visuals | `dataviz` | skill invocation | bundled |
| Accessibility review | `design:accessibility-review` | skill invocation | check before use |
| Motion and animation | `hyperframes-animation` | skill invocation | bundled |
| Copy in a brand voice | `brand-voice:brand-voice-enforcement` | skill invocation | needs brand guidelines |
| Deck file output | `anthropic-skills:pptx` | skill invocation | bundled |
| Document file output | `anthropic-skills:docx` | skill invocation | bundled |
| Published web page | `artifact-design` + the Artifact tool | load skill, then publish | bundled |
| Fixed-canvas composition (poster, cover, social) | `anthropic-skills:canvas-design` | skill invocation | **NOT installed on this host** — see the adoption record below |

## Rejected candidates

Recorded so they are not re-evaluated from scratch next time.

| Candidate | Decision | Reason |
|---|---|---|
| `gstack-design-*` (all variants) | **Reject** | Auto-generated wrappers around a gstack binary runtime. Delegating to a generated wrapper couples us to a binary we do not control and cannot inspect; absorbing it means reimplementing it. |
| `anthropic-skills:theme-factory` | **Reject** | Produces its own theme system with its own colour decisions. Overlaps `brands/*.json`, which is this skill's contract with `brand-assert.mjs`. A second source of theme truth is exactly the drift this skill exists to prevent. |

## Adopting a new design skill

Follow this whenever a design skill appears on a host that is not already in the
registry. Adoption is a one-file edit — once WD-340's deploy wiring lands,
`deploy.ps1 -Deploy` propagates the change to all four harnesses with no code change.

1. **Classify** the candidate as one of: *fills a gap* (no current delegate does this
   sub-job), *overlaps an existing delegate* (another registry row already covers it),
   or *duplicates engine doctrine* (it re-decides something `core-design-doctrine.md` or
   a brand profile already owns).
2. **Decide by rule.**
   - Delegate by default when the skill is installed and maintained by someone else.
   - Absorb only small, static, brand-critical assets that would otherwise drift out of
     sync with `D:\Wolftac\Brand`.
   - Reject binary-backed or auto-generated skills — the `gstack-design-*` precedent.
   - Reject anything that becomes a second source of brand truth — the
     `theme-factory` precedent.
3. **Register.** Add one row to the table above: sub-job, skill name, exact invocation,
   host availability. Update `artifact-routing-table.md` only if artifact routing
   actually changes.
4. **Verify.** Generate one artifact that exercises the new delegate through the full
   proof gate, and confirm the `DELEGATE-UNAVAILABLE` degradation path still works when
   the delegate is absent.
5. **Record** the decision inline with a one-line reason — **including rejections**, in
   the table above — so the reasoning survives the session that made it.

### Adoption record — `anthropic-skills:canvas-design` (2026-08-30) — **RETRACTED**

An earlier version of this file recorded canvas-design as adopted and "verified" by
identity-board artifacts passing the proof gate. **That record was wrong and has been
retracted.** Two things were untrue:

1. **The skill is not installed on this host.** A search of
   `%LOCALAPPDATA%\hermes\skills` and `%USERPROFILE%\.claude\skills` returns no
   `canvas-design` package.
2. **The cited artifacts did not exercise it.** Those identity boards are generated
   entirely by `scripts/make-artifact.mjs` from `templates/visual-identity-board.html`.
   The skill was named in this file and never consulted; a passing gate on an
   internally-generated artifact proves the gate works, not that a delegate ran.

Recorded rather than deleted, per step 5 of the protocol: a retraction is a decision,
and the next session should not re-evaluate this from scratch.

### Host inventory finding (2026-08-30)

The adoption protocol requires a candidate that is **installed** and can be **exercised**.
On this host, neither example named in WD-339's AC-15 exists, and every installed design
skill that is not already a registry row is guidance-class — a `SKILL.md` plus
`references/`, with no executable:

`design-audit`, `css-native`, `compose-graphics`, `canvas-generative`,
`brand-asset-production`, `creative-artifact-production`, `motion-design`,
`premium-b2b-website-conversion`, the `threejs-*` family, and the rest under
`%LOCALAPPDATA%\hermes\skills\creative\`.

The single exception is `claude-design-system-operations`, which ships a real
executable (`scripts/validate_claude_design_bundle.py`). It was invoked against this
package and returned genuine output — `files=2417 html=7 cards=0`, `FAIL`, missing
roots `assets, components, guidelines, readme.md, styles.css, tokens, ui_kits` — but
that is a **category mismatch**, not a defect: it validates a `claude.ai/design` bundle,
which a wolftac-design package is not. It is a validator, not a generator, so it cannot
"produce or materially contribute to" an artifact either.

**Conclusion:** the adoption protocol below is sound and unchanged, but it cannot be
demonstrated end-to-end on this host today, because no installed design skill outside
the registry is of a class that can contribute bytes to an artifact. Naming one anyway
is what produced the retracted record above.
