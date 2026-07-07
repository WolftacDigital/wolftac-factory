# Creative studio style-guide / prompting review pattern

Use this when a Factory Queue spec changes creative studio prompting, shared style guides, or UI prompt-composer surfaces across thumbnails/video/music/games/SEO.

## Review focus

A helper or pure prompt builder is not enough by itself if the acceptance criteria promise a studio surface. Verify both:

1. The shared prompt builder/unit contract produces the required prompt shapes.
2. The real studio routes render the shared controls and consume the shared style guide.

## Evidence to gather

1. In a clean detached worktree, inspect the diff against `main...HEAD` and identify all touched studio views plus the shared prompt/style-guide modules.
2. Run the spec test plan from scratch (`npm ci` if needed, then `npm run build`, `npm run lint`, and the relevant node test target such as `npm run test:agentic-os`).
3. Use a scratch persisted style-guide path (for example `AGENTIC_OS_STYLE_GUIDE_PATH=/tmp/<issue>-style-guide.json`) when starting the dev server so review writes do not mutate the operator's real profile.
4. Smoke every promised studio route, not just one reference route. For WD-style Agentic OS studios, check `/dashboard/agentic-os/{thumbnails,video,music,games,seo}` and verify visible `Creative prompt`, `Edit`, and `Redesign` controls.
5. Prove mode difference in the browser: Redesign should produce fresh-direction language; Edit should expose source/change inputs and produce `recreate` + `change ONLY`/single-change wording.
6. Prove shared guide injection across at least two studios by POSTing a scratch guide to the guarded style-guide API, then opening two different studio routes and confirming the same voice/channel/palette/do/don't text appears in each composed prompt.
7. Verify default/failure behavior: no persisted style-guide file returns neutral defaults without crash; invalid/non-loopback origin or missing token is rejected when applicable; UI shows a usable default/offline state rather than blanking.
8. Run a diff-scoped scan for secrets/debug leftovers and hardcoded creator/brand strings introduced by the shared prompt source. It is acceptable for existing unrelated studio fixtures to contain brand defaults; anchor the scan to newly introduced or changed files.

## PASS wording

Include concrete browser/API evidence, for example:

```text
Manual/API/browser smoke used `npx next dev -H 127.0.0.1 -p <port>` with scratch style-guide path. Verified all five studio routes render Creative prompt/Edit/Redesign; thumbnail Edit mode generated recreate/change-only wording; saved scratch guide rendered in both thumbnail and music prompts; default guide GET worked with no persisted file; non-loopback Origin rejected with 401.
```

## Pitfalls

- Do not let unit tests for `buildCreativePrompt` substitute for route-level proof when the spec says every studio exposes a toggle.
- Do not write review test data to the real `~/.agentic-os/style-guide.json`; use a scratch env path.
- If the build report says server libs were not rewired because studio data models differ, judge against the acceptance criteria: a shared surface composer can satisfy prompt-shaping criteria only if the real studio routes render it and the shared guide changes visible prompt output.
