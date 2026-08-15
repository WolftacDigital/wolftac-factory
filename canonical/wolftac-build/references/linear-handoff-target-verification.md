# Linear handoff target verification

Context: during a Wolftac factory build, a stale/mis-copied Linear UUID from a large queue response was used for the final handoff mutation. The build report and state update landed on a different issue. The error was repaired by deleting the wrong comment, restoring the wrong issue to Todo, posting the report to the intended issue, moving it to In Review, and querying both issues afterward.

Reusable rule:

1. Before the final BUILD REPORT mutation, re-query the claimed issue by short identifier (`WD-###`).
2. Use the fresh response as the only source for UUIDs needed by mutations.
3. Prefer short identifiers for `issueUpdate` where Linear accepts them.
4. After the mutation, re-query both the intended issue and any issue touched during repair/ambiguity.
5. Verify the intended issue has:
   - state `In Review`
   - the new BUILD REPORT comment ID/body
   - expected branch/commit proof in the comment
6. Verify no unrelated issue has the build report or unintended state change.

Repair pattern if the wrong issue was touched:

```text
- Delete the wrong comment by comment ID.
- Restore the wrong issue to its prior state.
- Post the BUILD REPORT to the correct issue.
- Move only the correct issue to In Review.
- Re-query both issues and include the verification in the final operator report.
```

This is especially important when queue output is truncated, when issue IDs are copied manually, or when a mutation mixes UUIDs and short identifiers.