# WD-180 Hermes kanban surface integration build pattern

Use this as a concrete example for factory-build sessions where a review failure says a new backend bridge exists but the primary UI/API surface is not wired to it.

## Situation

- Linear issue: WD-180, agentic lane, previously failed review.
- Existing branch already had a helper and side route for a Hermes-backed Kanban source.
- Review failure: the real Kanban surface still read/wrote only local JSON, and the offline source note was not visible.

## Fix pattern

1. Treat the latest `REVIEW REPORT — FAIL` as the primary work order after claiming the issue.
2. Use an isolated WSL worktree when the main checkout is dirty or on another issue branch.
3. Wire the primary route, not only a side/test route:
   - Existing `GET /api/agentic-os/kanban` should switch to the external read source when enabled/online.
   - On read failure, return `ok:true` with local fallback cards and an honest source/offline detail.
4. Wire existing mutation routes, not only a new bridge route:
   - Existing create/move/comment endpoints should route through the external CLI when the source is online.
   - Preserve the local JSON write path when the bridge is offline.
   - Return honest errors for unsupported external transitions; do not fake direct DB writes.
5. Wire the UI status surface:
   - Show source mode/status (`Hermes live`, `source offline`, `local file`).
   - Show write posture (`CLI writes` vs local writes).
   - Show read-only DB posture and per-card source.
6. Verify with both standard tests and a runtime smoke against the real read-only source when safe. Avoid live writes against operational state unless the spec explicitly authorizes mutation.

## WSL/Hermes editing pitfall

When invoking `wsl.exe ... bash -lc ...` from the Windows-hosted Hermes profile, shell variables inside nested commands may be expanded by the outer shell and arrive empty in WSL. Prefer literal absolute paths in short WSL commands. For complex multi-file patches, write a temporary Python/Node script and execute it under WSL. If the script partially applies before failing, make the next script idempotent or target only the remaining files.

## Proof wording pattern

In the build report, separate:

- Unit/build proof (`npm run lint`, `npm run test:*`, `npm run build`).
- Runtime read smoke (`GET /api/...` returned `sourceMode:"hermes"`, `source.online:true`).
- Explicit mutation restraint (`No live write issued against real Hermes board; write path verified with mock execFile and production route wiring`).

This keeps the handoff honest while still proving the acceptance criteria that can be safely exercised in automation.
