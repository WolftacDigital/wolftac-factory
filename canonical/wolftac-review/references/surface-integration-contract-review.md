# Surface integration contract review

Use this when a spec says a product **surface**, **UI**, **dashboard**, **existing route**, or **operator flow** must use a new backend/source/bridge.

## Review rule

A helper, side route, fixture test, or isolated API endpoint does **not** satisfy a surface-level acceptance criterion unless the actual user-facing surface consumes it.

If the build report says the new mechanism lives behind a new route and wiring the view is a follow-up, treat that as a likely acceptance failure when the spec promised the surface itself.

## Evidence to gather

1. Identify the real user entrypoint or component named by the spec (for example a dashboard tab or `*-view.tsx`).
2. Search/read the entrypoint for its fetches, actions, POST targets, feature flags, and rendered status notes.
3. Inspect the existing primary API routes the entrypoint already calls; verify they route to the new implementation when the gate/env is enabled.
4. Inspect mutation paths separately from read paths. A read-only bridge can pass read tests while create/move/comment still use the old local store.
5. Verify user-visible fallback/offline copy is rendered on the actual surface, not merely returned by an unused side endpoint.

## Fail patterns

- New `GET /api/.../feature-source` works, but the view still calls only `GET /api/.../feature`.
- New write helper uses the correct CLI/service, but UI buttons still post to old local mutation routes.
- New status helper returns honest-offline text, but no rendered component consumes that text.
- Tests validate the helper directly but never assert the surface or primary API route uses it.

## Reporting language

Anchor defects to the surface contract:

```text
Expected: with the feature gate enabled, the real <surface> renders/uses <new source> through the existing operator flow.
Actual: <new source> is available only through <side route/helper>; <surface> still calls <old route/store>.
```
