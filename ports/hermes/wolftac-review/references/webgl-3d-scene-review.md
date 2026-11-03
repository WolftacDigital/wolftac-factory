# WebGL / react-three-fiber scene review (Windows)

Use for acceptance criteria about a three.js / R3F scene: render-on-demand frame budgets,
tab-hidden pausing, scene clicks, orbit/zoom limits, RESET VIEW, touch, WebGL fallback.

## Genuine tab-hide proof
- Headless Chrome (including the browser_exec session) and Playwright-launched Chromium keep
  `document.hidden === false` after a tab switch or minimize. Do not accept a synthetic
  `visibilitychange` as proof.
- What works: launch the cached Playwright `chrome.exe` HEADED yourself (fresh
  `--user-data-dir` under reviewer scratch, `--remote-debugging-port=0`, read
  `DevToolsActivePort`) and drive it over raw CDP with Node's global `WebSocket`. Then
  `Target.createTarget({url:'about:blank', newWindow:false})` + `Target.activateTarget` makes the
  console tab report `hidden` for real. `Browser.setWindowBounds({windowState:'minimized'})`
  also works. Attaching/enabling Page on the second tab first can keep the first visible, so
  create it bare.
- Count frames by wrapping `WebGL2RenderingContext.prototype.clear` in an
  `addScriptToEvaluateOnNewDocument` hook (R3F autoClear = one clear per render). Also count
  clears while `document.hidden`.

## Scene clicks without engine internals (production build)
- Project world points yourself from the shipped default camera (fov, position, target,
  canvas aspect) and click with `Input.dispatchMouseEvent`. Verify with `elementFromPoint`: a
  non-CANVAS hit means a DOM overlay is swallowing the click.
- drei `<Html pointerEvents="none">` only honors that prop in `transform` mode. Without
  `transform`, labels (and hover tooltips) intercept clicks on the meshes beneath. Probe the
  top face of a crate/unit while its hover label is showing.
- `scroll-behavior`/focus-return after closing a drawer can scroll the page after your
  `scrollIntoView`; re-scroll with `behavior:'instant'` right before each click.

## Camera / OrbitControls
- Read the persisted pose (e.g. localStorage camera key) after each gesture; a zero-length
  press/release on empty canvas fires `end` and persists the current pose.
- Run the test plan literally: drag AGAINST a polar/zoom limit, then RESET VIEW. With damping
  on, the residual `sphericalDelta` survives the clamp (no change event) and the reset's
  `controls.update()` replays it, snapping back to the limit. Fix pattern: flush with
  `enableDamping=false; update(); enableDamping=prev` before applying the pose.

## Scene access in a production build
- To sample world positions or the camera without importing app/engine modules (which a
  hashed prod bundle does not expose), use an `addInitScript` that sets
  `window.__THREE_DEVTOOLS__ = new EventTarget()` and listens for `observe`. three.js
  dispatches it for each `Scene` and `WebGLRenderer`. Wrap the renderer instance's `render` to
  capture `(scene, camera)`, then read `scene.children` groups and `camera.position` and
  `quaternion` every rAF. This works against the shipped `dist` served by the real server.
- Run the same probe against clean `origin/main` as a negative control. A probe that also
  passes on main proves nothing about the fix.
- To speed up fixture-driven SSE tests, a scratch wrapper can shorten one server poll interval
  by patching `globalThis.setInterval` before it imports the unchanged server. Measure all
  timing from the SSE event and disclose the acceleration in the report.

## Other proofs that worked
- Fixture dynamics: mutate a scratch fixture copy; track a crate label's screen position every
  rAF/50 ms from the moment its zone count changes (glide timing, reduced-motion jump).
- Throttled load order: `Network.emulateNetworkConditions` + a DOMContentLoaded poller that
  timestamps org-chart vs canvas appearance.
- Dev-only crash flags: run Vite programmatically (`createServer` from the worktree's
  node_modules, `configFile:false`, proxy `/api` to the scratch server, self-closing timer).
- In Git Bash, `$TMPDIR` may resolve to the system `/tmp`; use the explicit native reviewer
  scratch path for every artifact and Chrome profile.
- Size self-exiting server lifetimes tightly: a server whose cwd is the review worktree blocks
  worktree removal until it exits.
