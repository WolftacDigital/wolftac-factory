# Ordered SQL seed regressions

- Exercise ordered-row seeds against production-shaped contiguous positions, not only sparse fixtures. A unique `(parent_id, sort_order)` can make `previous_order + 1` collide even when a happy-path fixture passes.
- When existing rows must remain unchanged, choose a free position within the approved placement semantics. Include archived rows when they remain covered by the unique constraint; do not renumber rows without spec authorization.
- Assert that the intended seed actually exists after migration. Blanket `ON CONFLICT DO NOTHING` plus a successful migration exit is not proof of insertion.
- Compare complete pre-existing rows before/after, rerun the migration, and test duplicate anchor names, existing seed rows and ineligible parents.
- Reproduce the downstream failure before fixing the seed, then rerun the actual application path with the same colliding fixture. Supplement embedded PostgreSQL with a disposable real PostgreSQL migration check when available; do not call it application or contention proof unless those paths actually ran.
