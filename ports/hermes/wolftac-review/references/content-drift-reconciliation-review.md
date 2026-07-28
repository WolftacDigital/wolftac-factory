# Content-drift reconciliation review proof

Use this when a factory issue reconciles live/deployed text, skills, runbooks, or generated documentation back into a version-controlled source of truth. The build report's “identical” or “all content preserved” claim is not proof; reproduce the accounting from the authoritative drift input and the published branch.

## Evidence sequence

1. **Freeze the inputs.** Record the authoritative drift report or source snapshot, the remote default SHA, the reported branch SHA, and the live/deployed roots. Review the exact published SHA in a detached worktree; do not compare against a dirty user checkout.
2. **Inspect branch scope.** Capture `git diff <default>...<head> --stat`, `--name-only`, and the full changed-text diff. Confirm only expected source and reference files changed.
3. **Normalize line endings before judging content equality.** A raw `cmp` or SHA comparison can report differences when deployed files are CRLF and the repo requires LF. Compare normalized bytes (`CRLF`/bare `CR` → `LF`) or use `git diff --no-index`, then separately prove repository EOL policy with `git ls-files --eol`. Report “content-equivalent after EOL normalization,” not byte-identical, unless raw hashes truly match.
4. **Account for every authoritative drift deletion.** Parse each diff block in the drift report, collect deleted live-side lines, and check each line against the reviewed branch. Classify every miss:
   - intentionally superseded contract/conveyor wording, with the governing source named;
   - deliberately omitted content, with the build report's explicit reason; or
   - unaccounted loss, which is a blocking defect.
   Record totals (`reported`, `preserved verbatim`, `explicitly superseded/omitted`, `unaccounted`). Do not accept a summary count without the missing-line classification.
5. **Compare imported support files.** For every changed/imported reference file that has a live source, compare normalized content and report missing/mismatched counts. This catches a build that copied only the files named in the spec's examples while omitting other referenced knowledge.
6. **Parse linked references from the merged entrypoints.** Extract each `references/<name>.md` pointer from the reviewed skill/runbook. Assert the file exists beside the entrypoint in the repo; when the acceptance criterion concerns deployment completeness, also assert it exists in the deployed source directory. Keep this distinct from “all files in the deployed directory were imported”: only the approved scope decides whether unreferenced files must be copied.
7. **Run repository gates.** Reproduce parity/semantic/link checks, `git diff --check`, and EOL checks. Scan imported text for secret-shaped values using focused credential signatures; report only path/line/category, never matching secret text. Classify literal `debugger`, `console.log`, `TODO`, or `token` mentions in documentation before calling them leftovers—examples and scan-pattern prose are not executable residue.
8. **Handle the missing-doc criterion honestly.** If no referenced live doc is absent, state that enumeration found zero and that the negative path was not triggered. Do not fabricate a failure. If the repo supplies a safe semantic/link checker, a temporary negative mutation in the detached worktree may prove fail-closed detection, but restore it and rerun the clean gate before verdict.

## Linear acceptance update discipline

After the final concurrent-review race check:

1. Post the review report first.
2. Update only the acceptance-criteria checkbox block, not every unchecked box in the description.
3. Query the current description immediately before mutation, isolate the text from `## Acceptance criteria` to the next heading, assert the expected unchecked count, replace only those markers, and update state to `Approved`/`Todo` as appropriate.
4. Never reconstruct the full description by hand from model context. If a GraphQL helper is used, keep the credential in the environment and print only counts, IDs, and state names.
5. Read the issue and comments back; verify report presence, checked count, and final state.

## PASS evidence example

A strong report can say:

- authoritative drift lines: 117;
- preserved verbatim: 105;
- superseded contract lines: 12, each covered by the approved merge rule;
- unaccounted lines: 0;
- changed references matching live after EOL normalization: 15/15;
- linked references present: 19/19;
- parity, semantic, diff-check, and LF gates: pass;
- secret-shaped values: 0.

Use the actual observed counts; the numbers above illustrate the proof shape, not expected constants.
