# Public map / premium homepage review proof

Use this reference when a Factory review acceptance plan includes a public Mapbox/globe/map homepage, responsive hero checks, fallback behavior, or Lighthouse gates.

## Review posture

Do not accept a builder report that says “map controls passed” or “fallback passed” unless the review reproduces the live failure modes. Mapbox can partially succeed: runtime/style may load while terrain DEM, tiles, sprites, or events fail. Partial failures can leave a blank canvas, misleading controls, and uncaught errors while a simple “Mapbox loaded” assertion passes.

## Minimum evidence to collect

1. Verify the branch/commit first:
   - `git ls-remote --heads <repo> <branch>` must resolve to the reported SHA.
   - Review in a detached worktree at that SHA.
2. Reproduce static gates:
   - JS syntax checks.
   - JSON parse checks.
   - Regression scans from the spec, including legacy/prototype copy and synthetic brand markers.
   - Sample local asset HTTP 200 checks for hero poster, icons, CSS, JS, and key images.
3. Browser viewport proof:
   - Test every required viewport with DOM geometry: `document.documentElement.scrollWidth <= window.innerWidth`.
   - Assert one H1, visible hero copy/actions, required brand/logo counts, and no synthetic brand marks.
4. Map proof:
   - Scroll the map section into view and wait for lazy/intersection loading.
   - Record whether Mapbox runtime/style loaded, whether map canvas exists, marker count, selected state, filter button behavior, tour state, reset behavior, terrain toggle text/state, and console/page errors.
   - Inspect resource statuses by class, not full URLs, to avoid leaking tokens: mapbox runtime/style/terrain/status counts are enough.
   - A 403/401 on terrain or tiles is a criterion failure when the spec requires terrain/hillshade/globe, even if fallback later appears.
5. Failure-mode proof:
   - Block the whole Mapbox runtime and verify the branded 2D fallback is usable and controls are honest.
   - Separately simulate/observe partial Mapbox source failure (style succeeds, terrain/tile source fails). Fail if controls still claim terrain is on, map is blank/unstable, or uncaught/page errors continue.
   - Block one content JSON request and verify branded recovery/nav/conversion actions.
   - Block or abort the hero video and verify poster/headline/actions remain usable.
6. Lighthouse:
   - Run against the same local server used for browser QA.
   - If the spec sets a numeric threshold, treat a miss as blocking unless a clean `origin/main` baseline proves the same miss is unrelated.

## Safe reporting

- Never print Mapbox token values. Report only prefix (`pk.` vs `sk.`), presence, and short hashes/fingerprints.
- For Mapbox resource logs, mask query strings and access tokens; aggregate counts by resource class/status.
- Record observed browser/page errors as evidence, but keep them concise and scrubbed.

## Common blocking defects

- Runtime/style loads but terrain DEM returns 403/401 and the UI still says “Terrain on.”
- Canvas exists but the map area is visually blank or unstable.
- Fallback only works when the entire Mapbox runtime is blocked, not when a dependent terrain/source request fails.
- Map controls can be clicked but do not produce visible state change or leave uncaught page errors.
- Lighthouse performance is below an explicit acceptance threshold.
