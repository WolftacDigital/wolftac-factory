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
| Fixed-canvas composition (poster, cover, social) | `anthropic-skills:canvas-design` | skill invocation | bundled |

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

### Adoption record — `anthropic-skills:canvas-design` (2026-08-30)

1. **Classified:** *fills a gap*. The registry routed web artifacts (landing page,
   dashboard, identity board) but had no delegate for a fixed-canvas composition — a
   poster, cover, or social graphic at an exact pixel size.
2. **Decided:** delegate. It ships with the host, is maintained upstream, and holds no
   brand opinion of its own — it composes at a canvas size and takes colour direction
   from the caller, so it cannot become a second source of brand truth.
3. **Registered:** the "Fixed-canvas composition" row above.
4. **Verified:** an identity-board artifact was generated under both the `wolftac` and
   `omas-atlas` profiles and passed the full proof gate — two viewports rendered, zero
   console errors, `brand-assert.mjs` exit 0 for each. The `DELEGATE-UNAVAILABLE` path
   was exercised separately against `ui-ux-pro-max` with its `search.py` path made
   unreachable: the run emitted the line and completed on doctrine.
5. **Recorded:** this entry.
