# Theme/brand review proof pattern

Use this when a Wolftac factory issue claims brand/theme compliance: no legacy colors, sharp-corner/no-rounded rules, token drift-locks, font loading, favicon/logo assets, or palette usage rules.

## What to verify

1. **Diff and rendered UI both matter.** Inspect `git diff <base>...HEAD`, then run the app and verify representative routes with browser/visual evidence.
2. **Scan all producers of rendered or persisted UI values.** Do not stop at `app/` and `components/` if UI components render values from data fields. Include:
   - `lib/` mock/seed data.
   - DB schemas and migrations.
   - import/export, staging, smoke, and production scripts.
   - tests/fixtures that may become seeded defaults.
3. **Trace dynamic style fields.** If components use `style={{ backgroundColor: stage.color }}` or similar, search every assignment/default for that field. Legacy colors there are blocking even when UI source files scan clean.
4. **Test the guard, not just the happy path.** For a token drift-lock criterion, temporarily mutate the exact mapped CSS variable/source value the criterion names and run the targeted test. The test must fail. A test that only checks token strings appear somewhere in a file can pass while the actual variable is wrong.
5. **Runtime font proof.** In browser, inspect computed font families and resource entries. Next/font may source from `next/font/google` at build time but runtime should load local `/_next/static/media/*.woff2` and not request `fonts.googleapis.com`.
6. **Asset cleanup proof.** List the public asset directory, confirm default framework SVGs are gone, confirm logo/favicon resources are referenced by rendered HTML/browser resources.

## Example failure evidence

A branch passed its normal test suite and rendered a correct-looking mock dashboard, but review failed because:

- Changing `app/globals.css` `--background` from `#050706` to `#050707` did not fail `tests/brand-theme.test.ts`; the test only asserted that token values appeared somewhere in the CSS file.
- `#7CFF98` survived in schema/migration/import/smoke paths that created `pipeline_stages.color`, while components rendered `stage.color` directly.

Report this as an acceptance defect: the current pages may look correct, but the system can still create or import non-compliant branded data and the drift-lock is not enforcing the contract.
