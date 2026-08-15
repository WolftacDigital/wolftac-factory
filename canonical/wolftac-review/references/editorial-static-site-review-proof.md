# Editorial static-site review proof pattern

Use this reference when reviewing a static editorial/blog issue where the build report claims migrated articles, unique metadata, preserved legacy copy, responsive article layout, and newsletter/provider-failure behavior.

## Durable review moves

1. **Verify the stacked PR base before judging the diff.** If the PR is stacked, inspect `gh pr view <pr> --json baseRefName,headRefName,headRefOid` and diff against the PR base branch, not always `origin/main`, so unrelated upstream foundation/conversion work does not pollute the review.
2. **Run static-contract tests and browser tests against an explicit review server.** Start a throwaway local server on a scratch port, health-check it, then pass `BASE_URL=http://127.0.0.1:<port>` to smoke/e2e commands. This avoids accidentally testing a stale server already bound to the default port.
3. **Treat an unqualified local smoke failure as suspect until rerun against the controlled server.** If a smoke script defaults to `localhost:4173`, rerun with `BASE_URL` aimed at the review server before calling it a product defect.
4. **Crawl metadata independently.** For `/blog` plus article routes, fetch each route and record status, H1 count, title, description length, canonical, OG image, and same-site link candidates.
5. **Compare migrated copy to live legacy pages with normalization.** Fetch the legacy URLs, strip scripts/styles/tags/entities, normalize punctuation/spacing, and compare meaningful article segments. Webflow exports often contain spacing artifacts (`becalled`, `ahigh-energy`, punctuation spacing); documented cleanup is acceptable when medical/tactical substance remains intact.
6. **Use browser/vision proof for editorial layout.** DOM checks do not prove readable measure, card imagery, TOC usability, contrast, or occlusion. Capture `/blog` and a representative article route at desktop/mobile where possible.
7. **Scan for secrets/debug leftovers with specific patterns.** Avoid broad `token|secret` scans as the static route shell itself may legitimately mention public Mapbox `pk.` tokens or test fixture text. Use focused patterns such as `\bsk-[A-Za-z0-9]{20,}|debugger\b|\bTODO\b|\bFIXME\b` plus any issue-specific sensitive terms.

## Cleanup note

On Windows, `git worktree remove` can unregister the worktree while leaving an empty temp directory that reports `Device or resource busy`, especially after browser/file handles touched the directory. If `git worktree list` is clean, background processes are stopped, heavy artifacts are deleted, and the directory is empty, report the cleanup limitation honestly rather than retrying destructive process kills.