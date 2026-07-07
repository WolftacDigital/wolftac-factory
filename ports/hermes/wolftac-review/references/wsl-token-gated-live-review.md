# WSL token-gated live review checks

Use this when a Factory review needs live browser/API proof for auth-gated Agentic OS surfaces from a Windows-hosted Hermes session against a WSL repo.

## Pattern

1. Build and test in a detached WSL worktree first; do not use the source repo working tree for review.
2. Start throwaway `next start` servers from the worktree only, on scratch ports. Keep production PM2/gateway untouched.
   - One normal server is useful for SSR/page smoke checks.
   - One token-gated server can be started with a disposable `AGENTIC_OS_OPERATOR_TOKEN` to reproduce tokenless 401 behavior.
3. Verify tokenless API behavior with `curl` from Windows rather than trying to inject fetch/localStorage/cookie code through browser console. Hermes browser console may block sensitive primitives, and that block is a safety control, not a product defect.
4. For inline auth CTA checks, use real browser navigation/click snapshots: the evidence is that the page shows the operator-token CTA and does not show the network-error copy.
5. For command-palette or other UI behavior, prefer real browser clicks and snapshots over DOM eval.
6. Stop/kill all throwaway servers after Linear mutation and verify cleanup.
7. Remove the detached worktree with `git worktree remove --force <tmp-worktree>` and verify the path is absent.

## Evidence worth reporting

- Branch SHA and temp worktree path.
- Commands run: `npm ci`, `npm test`, targeted tests, `npm run build`, `npm run lint`.
- HTTP smoke status codes for representative pages.
- Tokenless API HTTP status and short non-secret body preview.
- Browser-observed CTA/dialog copy.
- Diff-scoped secret/debug scan result.
- Cleanup proof for throwaway servers and worktree.
