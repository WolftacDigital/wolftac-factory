# Hermes Kanban bridge review pattern

Use this when a Wolftac Factory Queue spec promises a Hermes Kanban integration that reads `kanban.db` directly and routes writes through the `hermes kanban` CLI.

## What to verify

1. **Primary surface, not side route only.** The real Kanban page and primary `GET /api/agentic-os/kanban` route must consume the Hermes source when the bridge is enabled. A helper or `/kanban/hermes` side route is not enough.
2. **Read path is SQLite read-only.** Inspect the bridge helper for `node:sqlite`/SQLite open with read-only options and a bounded query/limit. The app must not write the DB directly.
3. **Write path is CLI-only.** Create/comment/move routes used by the UI must call a shared CLI write helper (`execFile`/equivalent) for supported Hermes transitions. Unsupported board columns should return an honest 409 or clear error before spawning the CLI.
4. **Offline fallback is visible on the surface.** With the bridge enabled but DB missing/unreadable, the primary API should return local JSON cards plus a source status detail, and the UI should render an honest “Hermes source offline/local board” note.
5. **Failure read does not crash.** Use a malformed SQLite fixture (for example a DB missing the `tasks` table) to prove the primary route catches read failures and falls back with `ok:true` rather than 500/hang.

## Safe runtime proof pattern

Use a detached WSL worktree when the repo is WSL-hosted. Create disposable fixtures rather than mutating the live Hermes board.

- Build a small fixture `kanban.db` with a `tasks` table and one known task.
- Run the app with:
  - `AGENTIC_OS_ENABLE_HERMES_BRIDGE=1`
  - `AGENTIC_OS_HERMES_KANBAN_DB=<fixture-db>`
  - `AGENTIC_OS_OPERATOR_TOKEN=<throwaway-token>` for API auth checks
- Verify tokenless API returns 401 when token auth is configured.
- Verify authorized `GET /api/agentic-os/kanban` returns `sourceMode:"hermes"`, the fixture task, and correct column summary.
- Browser-smoke the actual `/dashboard/agentic-os/kanban` page and confirm it renders source chips such as `HERMES SQLITE LIVE`, `CLI WRITES`, `DB READ-ONLY`, plus the fixture card and `SOURCE HERMES`.

For CLI writes, do **not** casually create/move live Hermes tasks. Prefer a stub CLI:

```sh
stub=/tmp/wd-review-hermes-stub
log=/tmp/wd-review-hermes-cli.log
rm -f "$log"
printf '%s\n' '#!/bin/sh' "printf '%s\n' \"\$*\" >> $log" 'exit 0' > "$stub"
chmod +x "$stub"
AGENTIC_OS_HERMES_CLI="$stub" AGENTIC_OS_HERMES_BOARD=review-board npm run dev -- --hostname 127.0.0.1 --port <port>
```

Then POST through the existing UI/API routes (not a side route) and verify the log contains `kanban --board ... create ...` / `complete ...` / `block ...` as appropriate. Also send an unsupported move (for example to `doing` if Hermes has no generic set-status CLI) and expect 409 with no CLI log entry.

## Reporting emphasis

Anchor the verdict to the contract:

- PASS only if the primary Kanban surface reads Hermes-backed cards, visible source/offline copy is present, and writes on primary routes go through the CLI.
- FAIL if the implementation only exposes a helper or side route while the actual surface still uses the local JSON store.
- Mention that live Hermes board mutation was avoided if using a stub; the stub proves route-to-CLI behavior without operational side effects.