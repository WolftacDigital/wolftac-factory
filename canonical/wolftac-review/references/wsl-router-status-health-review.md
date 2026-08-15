# WSL router/status health review proof pattern

Use this when a Factory Queue issue adds or changes a guarded status endpoint that claims to report a resolved provider/model/router chain.

## Lesson from WD-201

A status endpoint can pass the auth matrix and still fail the acceptance criterion if the authenticated payload only lists routing candidates. When the spec says "per-provider health," require observable health/state/detail fields for each provider, not just `{kind,label,baseUrl,model,tier}`.

## Review steps

1. Start the branch in a detached WSL worktree on scratch ports only. Never touch prod PM2.
2. Use Windows curl for the endpoint matrix:
   - tokenless request must return `401`.
   - token request must return `200`.
3. Inspect the `200` JSON shape, not just the status code. For each provider/candidate in the chain, confirm there is a health signal such as `state`, `health`, `ready`, `offline`, `detail`, or equivalent evidence tied to that provider.
4. If the endpoint only reports static chain metadata (`kind`, `label`, `baseUrl`, `model`, `tier`) and no health/status/detail, mark the health criterion failed even if build/tests pass and live fallback works.
5. For fallback routers, separately prove:
   - forced-primary-down + stub/known fallback returns a successful routed response naming the answering provider;
   - all providers down returns an honest offline result with no crash/raw top-level error.
6. For router work that changes both the status endpoint and the chat/fallback route, verify all three live behaviors before PASS: authenticated status payload shape, forced-primary-down fallback success, and all-providers-down honest-offline response with no top-level raw error string.
7. Parse the scratch HTTP responses as JSON and assert shape, not just grep text. A small inline Node script can split `curl -w "\nHTTP_STATUS:%{http_code}"` output, enforce 401/200 status codes, and require every chain entry to include non-empty `state`/`detail` (or equivalent) fields.
8. Kill scratch servers, clear ports, remove heavy artifacts from the worktree, then remove the detached worktree before writing Linear. Immediately after killing Next/stub processes, a Windows curl check can briefly race with shutdown; if it reports a port still open, confirm with WSL `ss`/`lsof` and retry the curl once before treating it as a leaked process.

## Suggested defect wording

`GET /api/.../status` omits per-provider health. Expected each provider in the resolved chain to carry health/state/detail (ready/offline/configured or equivalent). Actual response only lists routing candidates and tiers, so the operator cannot see per-provider health from the guarded status endpoint.
