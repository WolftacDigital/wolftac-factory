# WSL live maintenance-proxy review proof

Use this when a WSL-hosted Agentic OS factory issue implements a deploy/ops fallback such as a PM2 maintenance proxy, and the acceptance criteria depend on live tailnet/PM2 behavior.

## Key pattern

1. **Do not repeat destructive/live-ops actions from cron.** A live `pm2 reload`, `pm2 stop`, service restart, Tailscale Serve mutation, or deliberate bad-build simulation is an ops action. If an operator already posted live evidence, treat that comment as operator-run evidence and independently verify all non-destructive parts.
2. **Still reproduce the mechanism.** Create a detached WSL worktree, install dependencies from the lockfile, run lint/build/tests, and run a local harness that starts a fake upstream plus the proxy on scratch ports. Verify:
   - normal upstream -> `200` + upstream body;
   - upstream down/refused -> branded `503` + `Retry-After`, no raw 502;
   - upstream recovers -> `200` again without manual proxy restart;
   - upstream returns 502/503/504 -> proxy converts it to branded `503` instead of passing the raw gateway page through.
3. **Check current live posture without restarting services.** Read-only commands are acceptable proof: `pm2 ls`, `tailscale serve status`, direct curls to app/proxy ports, and tailnet curl. These confirm the installed shape while avoiding gateway/service restarts.
4. **When the branch is already merged to `origin/main`, `git diff origin/main...HEAD` may be empty.** Use the build report's commit range or first feature commit parent (for example `git diff <first-feature-commit>^..<head>`) to inspect the actual introduced files and run diff-scoped secret/debug scans.
5. **Validate PM2 ecosystem filenames.** PM2 recognizes ecosystem config files by suffix such as `.config.cjs` / `ecosystem.config.cjs`; a custom `*.ecosystem.cjs` filename may be executed as a plain script instead of parsed as an app definition. During review, inspect docs/config for the recognized suffix if PM2 process startup is part of the acceptance criteria.

## Report posture

In the review report, explicitly separate:
- operator-run live proof you did not repeat because of safety gates;
- reviewer-run mechanism proof from a scratch harness;
- read-only current live health checks.

This lets review pass when all acceptance criteria are proved without violating the no-restart/no-destructive-ops gate in unattended cron.
