# WSL WebGL build pitfalls from WD-200

Use this as a compact checklist when a Wolftac factory build touches a WSL-hosted Next.js/WebGL surface.

## Shell/file-writing pattern

- When generating TypeScript/TSX files from Hermes on Windows into WSL, prefer writing to the WSL UNC path (`//wsl.localhost/Ubuntu/...`) from the host shell or use a single-quoted WSL heredoc with outer quoting proven safe.
- Avoid nested `wsl.exe ... bash -lc "cat <<'EOF' ..."` commands containing template literals/backticks or `${...}`. Even with a quoted heredoc marker, the outer shell layer can still expand or break quoting before the WSL shell receives the body.
- If a file was written through Windows tooling, run a line-ending sanity check before commit (`git diff --check`; fix CRLF with `perl -pi -e 's/\r$//' <file>` inside WSL when needed).

## Three.js / Next.js TypeScript verification

- `three` may need matching `@types/three` for `next build` type checking even if lint/unit tests pass.
- For exact pinned Three.js builds, add `three` as a normal dependency and `@types/three` as an exact dev dependency.
- Run `npm run build` after `npm run lint` and unit tests; TypeScript-only failures can appear only at production build time.
- Three.js type predicates over filtered arrays often need concrete generics, e.g. `THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial>`, not bare `THREE.Mesh`.

## React state and fallback design

- Current React lint rules reject synchronous `setState` calls used only to mirror derived state inside an effect. Derive search focus directly from `query` plus the last manual selection, or schedule browser capability detection from an animation-frame/event callback rather than setting it synchronously in the effect body.
- Preserve user override behavior when search changes: store manual selection with the query value that produced it. If the query changes, use the new best match; after a node or linked-note click, let that manual selection win for the current query.
- WebGL feature detection is not sufficient by itself: wrap `new THREE.WebGLRenderer(...)` in a guarded path and notify the parent to switch to the 2D fallback if renderer construction fails.
- Hover isolation over one `LineSegments` mesh can be implemented without one object per edge by using a vertex `color` buffer attribute and updating the two vertices for each direct/non-direct edge.

## Honest browser proof

- An unlinked vault only proves the empty/no-WebGL-init path. Do not claim the live 3D interaction criteria from that smoke test.
- Do not mutate local config or secrets merely to manufacture a linked-vault demo. Record the exact empty-state/browser proof obtained and hand the linked-vault orbit/zoom/hover/click/search walkthrough to review when the environment lacks a safe fixture.

## Factory handoff priority

- In cron runs, do not let extended browser smoke consume the last available tool calls after commit/push. Reserve capacity for the Linear BUILD REPORT and state transition. If a manual/browser criterion remains incomplete, report that explicitly in the BUILD REPORT rather than leaving the issue claimed in `In Progress`.
