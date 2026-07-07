# Memory Galaxy / Obsidian vault graph review pattern

Use this for Factory Queue reviews where an Agentic OS Memory Galaxy, Obsidian graph, vault search, or read-only note-navigation surface must prove real vault data rather than placeholder graph data.

## Review pattern

1. **Use a detached WSL worktree when the repo is WSL-hosted.** Follow `references/wsl-linear-factory-review-proof.md` first so the active branch and local artifacts stay untouched.
2. **Inspect both the transform and the real surface.** Helper/unit tests are not enough. Read the graph transform, the Memory Galaxy component, and the actual Memory page/view that wires the vault path, fetch, search, and selected-note panel.
3. **Create a scratch vault with known links.** Use a temporary vault containing at least three Markdown notes, one `[[wikilink]]`, and one markdown link so the reviewer can identify known nodes and edges. Example shape:
   - `Alpha.md` links to `[[Beta Note]]` and `[Gamma](sub/Gamma.md)`.
   - `Beta Note.md` links back to `[[Alpha Note]]`.
   - `sub/Gamma.md` is reachable by markdown link.
4. **Smoke the API before the browser.** Hit the public graph route with the scratch vault and loopback/same-origin auth posture, for example:
   ```sh
   curl -sS -H "Origin: http://127.0.0.1:<port>" \
     "http://127.0.0.1:<port>/api/agentic-os/memory/graph?vaultPath=<scratch-vault>&maxFiles=20"
   ```
   Verify `ok: true`, the expected note titles, and the expected `wikilink` / `markdown` edges.
5. **Browser-smoke the actual Memory route.** Open `/dashboard/agentic-os/memory`, first confirm the no-vault state is honest (`No vault graph yet` / waiting/offline copy), then enter the scratch vault path in the real `Vault path` field. The page may auto-scan after a debounce; otherwise click `Scan vault`.
6. **Verify every interactive criterion visibly.** Confirm the rendered graph shows the expected note count and link count; type a known search term and verify the match count/label; click a graph node and verify the selected-note panel shows title/path/group/link count and linked-note navigation.
7. **Check the browser console.** Zero JavaScript errors is part of the runtime proof for graph surfaces.
8. **Run baseline scans on the introduced diff only.** Scan changed graph/component/test files for debug leftovers and likely secrets (`console.log`, `debugger`, TODO/FIXME, API keys/tokens/password patterns). Do not report inherited repository-wide fixtures as new findings.

## Pass evidence to include

- Worktree path and reviewed branch/commit.
- Commands: dependency install if needed, `npm run lint`, `npm run test:agentic-os`, `npm run build`.
- API smoke result: expected titles and edge labels.
- Browser smoke result: empty/offline state, real node/link counts, search match evidence, click-to-detail evidence, console status.
- Whether any new visualization dependency was added; specs often require offline/local-first posture and dependency-light rendering.

## Fail patterns

- The graph transform works in isolation, but the real Memory page does not consume it.
- API route returns graph data, but the dashboard still shows placeholder/static nodes.
- Search only filters an API query field but does not visibly highlight/focus rendered graph nodes.
- Clicking a node only selects an SVG element visually but does not expose title/path/detail or linked-note navigation.
- No-vault/unreachable API leaves a blank canvas or crashes instead of honest empty/offline copy.
