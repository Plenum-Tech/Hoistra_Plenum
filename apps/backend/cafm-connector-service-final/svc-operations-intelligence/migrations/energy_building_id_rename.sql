-- The energy tables key on the building, and said "site". Rename the column to what it holds.
--
-- Seven energy tables carried a column called ``site_id`` whose value is a
-- ``buildings.building_id``. Verified on both databases before this ran: every non-null value
-- in all seven joins to plenum_cafm.buildings, and NOT ONE joins to plenum_cafm.sites —
-- 32/32 meters, 82/82 anomalies, 9/9 snapshots on one database, 62/62, 154/154 and 25/25 on
-- the other. A site is an address and a building is a structure on it, so the name pointed at
-- the wrong level of the hierarchy and invited exactly the wrong join. The newer energy
-- tables (energy_ratings, chiller_design_specs, chiller_performance_readings,
-- weather_degree_days, bms_trends) already say ``building_id``; this brings the rest into line.
--
-- Deliberately NOT touched, because there the name is correct and the value really is a site:
--   plenum_cafm.buildings.site_id   — the site a building stands on
--   plenum_cafm.sites.site_id       — the site's own key
--   plenum_cafm.assets.site_id, work_orders.site_id, compliance_certificates.site_id
--
-- Idempotent: the rename only fires when the old column is present and the new one is not, so
-- a second run is a no-op and a half-applied state cannot be produced. A table that already
-- has ``building_id`` is left alone.

DO $$
DECLARE
    t text;
    tables text[] := ARRAY[
        'energy_meters',
        'energy_anomalies',
        'eui_snapshots',
        'building_energy_profiles',
        'energy_recommendations',
        'energy_monthly_reports',
        'site_occupancy_logs'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'plenum_cafm' AND table_name = t AND column_name = 'site_id'
        ) AND NOT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'plenum_cafm' AND table_name = t AND column_name = 'building_id'
        ) THEN
            EXECUTE format(
                'ALTER TABLE plenum_cafm.%I RENAME COLUMN site_id TO building_id', t);
            RAISE NOTICE 'renamed %.site_id -> building_id', t;
        END IF;
    END LOOP;
END $$;

COMMENT ON COLUMN plenum_cafm.energy_meters.building_id
    IS 'The building this meter serves (plenum_cafm.buildings.building_id). Was called '
       'site_id until Sep 2026 and never held a site id.';

COMMENT ON COLUMN plenum_cafm.energy_anomalies.building_id
    IS 'The building the finding is against (plenum_cafm.buildings.building_id).';

COMMENT ON COLUMN plenum_cafm.eui_snapshots.building_id
    IS 'The building the intensity figure is for (plenum_cafm.buildings.building_id).';

-- The indexes that named the old column. Postgres carries an index through a column rename,
-- so these only matter for a database where one was missing to begin with.
CREATE INDEX IF NOT EXISTS ix_energy_meters_building
    ON plenum_cafm.energy_meters (building_id);

CREATE INDEX IF NOT EXISTS ix_energy_anomalies_building
    ON plenum_cafm.energy_anomalies (building_id);

CREATE INDEX IF NOT EXISTS ix_eui_snapshots_building
    ON plenum_cafm.eui_snapshots (building_id);
