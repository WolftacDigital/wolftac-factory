# Resolving a blocked spec (build stopped on spec conflicts)

Use when /wolftac-build returned an issue to Todo with a BLOCKED comment (an AC-vs-NG conflict, a schema mismatch, a missing mapping), and you are asked to fix the spec. Two phases: recommend first, then amend once the owner approves. Never merge the two phases.

## Phase 1: recommendation (no spec edits)

1. Read the issue description and every comment, newest first. The builder's BLOCKED comment lists the conflicts. Executive triage comments set the deadline and the boundaries.
2. Re-verify each blocker against current `origin/main`, read-only:
   - `git fetch origin` then `git show origin/main:<path>` / `git grep -n <pattern> origin/main -- <dirs>`. Never check out a branch or pull in a shared clone. Write long outputs to scratch files and grep them; big `git show` output gets truncated.
   - Record the commit SHA you verified against. main may have moved since the build attempt.
3. Whenever a spec says "no schema change" or "all tables exist", check every target table for:
   - CHECK constraints on enum-like columns (entity_type, channel, status). An insert with a value outside the check fails at runtime.
   - Unique constraints, which can make "keep source order" or "seed rows" collide with existing rows.
   - NOT NULL columns with no default (required text fields).
   - App type unions mirroring the column (e.g. `type X = "email" | "sms"`). Widening the DB alone pulls in UI and send-path scope.
   - Which tables have a JSON column (custom_fields / metadata). That decides where flags and provenance can live without a migration.
   Also look for latent defects the builder did not report, and include them.
4. For each blocker, give options with one recommended. Prefer options that need no schema change or UI change. When a schema change is the honest option, keep it additive, mirrored in every migration tree, and give it a guarded down path. Explain why the rejected option costs more, with the mechanism (e.g. a permanently mislabeled ledger, or inbox/send-path scope).
5. Post the recommendation as one Linear comment ending in a numbered **Decisions** list (D1..Dn), each answerable yes/no or A/B, plus the estimate impact. Then post one thread message that links the comment and repeats the decisions.
6. Stop. Do not edit the description until the owner's approval artifact exists (an owner message from the verified account, recorded in Linear). For a spec amendment, reactions and bot relays are not approval. This does not change the Slack :rocket: merge gate, which is defined by the integrate skill.

## Phase 2: amend in place after approval

1. Confirm the approval: the comment citing the owner's message ID and account. Note any extra requirements the CEO attached to it.
2. Rewrite the description in place (full replace, Todo, Factory Queue label kept):
   - Add an amendment banner under the header: version, which comments hold the blocker, recommendation and approval, and a one-line list of what changed.
   - Keep every AC-N/NG-N ID. Edit changed ones in place and tag them `*(Amended <date>, Dn.)*`. New criteria take the next free number, tagged `*(Added …)*`.
   - Add non-goals that fence the rejected option (e.g. "no channel widening") so the builder cannot drift into it.
   - If a migration was approved: update the Data model line and the Data/migration gate, add the new migration and test files to Entry points, put the down script outside the migration directories so no runner applies it, and add an operator step (after the recovery manifest, before apply). Also add a refusal case such as `migration_missing` to the safety ACs.
   - Extend idempotency, rollback and reconciliation ACs to every new entity path explicitly. A generic "all rows" is easy to under-test.
   - Add Evidence-map rows for every new AC, and update the Test plan and Build notes (the verified commit SHA).
   - Rerun the AC-vs-NG comparison.
   - Set the estimate the owner approved, in both the header and the field.
3. Post a short change comment (what changed, mapped to D1..Dn) and one thread message. If someone asked for an ETA and the work is already done, report it done instead of giving a time.

## Writing decisions for Jonathan

Jonathan reads these on his phone, without the spec open. Each decision must stand alone:
- Name the product or repo it concerns (e.g. "our own CRM, not the vendor's").
- Say in one plain sentence what the choice changes, then give the recommendation and the cost or estimate impact.
- Put field names in backticks after the plain explanation, never instead of it.
- Restate the boundary: approving the spec is not approval for the production run, migration apply or merge.
