-- Per-meter metadata, so a meter can carry facts about itself rather than needing a
-- lookup somewhere else. Idempotent.
--
-- First use: {"simulate": true, "sim_base_kwh": 42} marks a meter as fed by the demo
-- simulator. Keeping that on the meter matters — anyone reading a reading can tell
-- whether it came from a real MPAN or from a generator, and the engine's findings are
-- only worth anything if that distinction is never ambiguous.

ALTER TABLE plenum_cafm.energy_meters
    ADD COLUMN IF NOT EXISTS raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN plenum_cafm.energy_meters.raw_metadata
    IS 'Per-meter metadata. simulate=true marks a meter fed by the demo simulator '
       'rather than a real DCC feed.';

-- The simulator selects on this flag every 30 minutes.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_energy_meters_simulate
    ON plenum_cafm.energy_meters ((raw_metadata ->> 'simulate'));
