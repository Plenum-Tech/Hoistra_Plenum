-- One criticality scale, derived from whatever was recorded.
--
-- plenum_cafm.assets.criticality holds eight distinct values across three vocabularies:
--
--     L1 10 · L2 11 · L3 6            the FM level scale
--     critical 9 · high 7 · medium 11 · Low 7 · Med 7   two word scales, mixed case
--
-- So "which L1 assets are there" answers 10 when the real figure includes everything recorded
-- as `critical`, and a maintenance priority query silently misses a third of the estate. The
-- vocabularies came from different imports and neither is wrong; what is wrong is asking a
-- question of a column that answers in three languages.
--
-- criticality_level is GENERATED, not backfilled. A backfill would overwrite what somebody
-- actually recorded and could not be undone once the source import was forgotten; a generated
-- column leaves `criticality` as the record of what was entered and cannot drift from it,
-- because Postgres recomputes it on every write. Query criticality_level; keep criticality.
--
-- THE MAPPING IS A JUDGEMENT, and it is written here rather than buried in a query:
--
--     critical, L1            -> L1
--     high                    -> L1     <- the debatable one
--     medium, Med, L2         -> L2
--     low, Low, L3            -> L3
--     anything else, or NULL  -> NULL   (unknown, never guessed)
--
-- Four word levels do not fit three L levels, so one boundary has to be chosen. `high` maps UP
-- to L1 rather than down to L2 because the two errors are not symmetric: calling a critical
-- asset L2 delays attention on plant that matters, while calling an important asset L1 spends
-- some attention that was not strictly required. Where a scale must lose precision, it should
-- lose it in the direction that fails safe. This puts 26 of 68 assets at L1 — a large share,
-- and the honest consequence of a four-into-three fit. Change the CASE below if the business
-- reads `high` as L2; that is a policy decision, not a technical one.

ALTER TABLE plenum_cafm.assets
    ADD COLUMN IF NOT EXISTS criticality_level TEXT
        GENERATED ALWAYS AS (
            CASE lower(btrim(COALESCE(criticality, '')))
                WHEN 'l1'       THEN 'L1'
                WHEN 'critical' THEN 'L1'
                WHEN 'high'     THEN 'L1'
                WHEN 'l2'       THEN 'L2'
                WHEN 'medium'   THEN 'L2'
                WHEN 'med'      THEN 'L2'
                WHEN 'l3'       THEN 'L3'
                WHEN 'low'      THEN 'L3'
                ELSE NULL
            END
        ) STORED;

COMMENT ON COLUMN plenum_cafm.assets.criticality_level IS
    'Canonical L1/L2/L3 derived from criticality, which holds three vocabularies. Generated, '
    'so it cannot drift from the recorded value. NULL means the recorded value is not one this '
    'mapping knows - unknown, not low. See migrations/asset_criticality_level.sql for the '
    'mapping and why `high` maps up to L1.';

CREATE INDEX IF NOT EXISTS ix_assets_criticality_level
    ON plenum_cafm.assets (criticality_level);
