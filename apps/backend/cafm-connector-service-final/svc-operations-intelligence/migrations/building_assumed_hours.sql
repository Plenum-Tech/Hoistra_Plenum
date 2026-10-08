-- The hours a benchmark assumes, so the gap to the hours a building actually keeps can be read.
--
-- A building measured at 198 kWh/m2/yr against a reference of 172 is 15% over. Some of that is
-- waste and some of it is that the reference assumes a shorter day than the building works. The
-- two need different answers — one is a work order, the other is a re-benchmark — and nothing
-- here could tell them apart, because the assumed hours were nowhere in the schema.
--
-- What is stored is the ASSUMPTION only. Actual hours are derived from the load profile at read
-- time and never written: a stored figure goes stale the moment the building changes its
-- pattern, and a stale operating profile is worse than none, because it reads as measurement.
--
-- `assumed_hours_source` is not decoration. A benchmark-fit argument is only as good as the
-- provenance of the assumption behind it, and "CIBSE TM46 general office" is a citation a
-- person can check while a bare 12 is a number they have to trust.
--
-- Nullable throughout and no default. There is no honest default: TM46 assumes different hours
-- per use class, and inventing one would put a fabricated assumption behind every re-benchmark
-- argument the system makes. NULL means "not known", and the engine says so rather than
-- computing a gap against a guess.

ALTER TABLE plenum_cafm.building_energy_profiles
    ADD COLUMN IF NOT EXISTS assumed_open_hour      SMALLINT,
    ADD COLUMN IF NOT EXISTS assumed_close_hour     SMALLINT,
    ADD COLUMN IF NOT EXISTS assumed_days_per_week  SMALLINT,
    ADD COLUMN IF NOT EXISTS assumed_hours_source   TEXT;

-- Hours of the day, and a week that is a week. A close hour of 24 is midnight at the end of the
-- day and is allowed; 0 would mean the building shuts before it opens.
DO $hours$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_profiles_assumed_hours') THEN
        ALTER TABLE plenum_cafm.building_energy_profiles
            ADD CONSTRAINT ck_profiles_assumed_hours CHECK (
                (assumed_open_hour  IS NULL OR assumed_open_hour  BETWEEN 0 AND 23) AND
                (assumed_close_hour IS NULL OR assumed_close_hour BETWEEN 1 AND 24) AND
                (assumed_days_per_week IS NULL OR assumed_days_per_week BETWEEN 1 AND 7) AND
                (assumed_open_hour IS NULL OR assumed_close_hour IS NULL
                 OR assumed_close_hour > assumed_open_hour)
            );
    END IF;
END
$hours$;

COMMENT ON COLUMN plenum_cafm.building_energy_profiles.assumed_open_hour IS
    'Hour the benchmark pack assumes the building opens (0-23). NULL = not known.';
COMMENT ON COLUMN plenum_cafm.building_energy_profiles.assumed_close_hour IS
    'Hour the benchmark pack assumes it closes (1-24; 24 is midnight). NULL = not known.';
COMMENT ON COLUMN plenum_cafm.building_energy_profiles.assumed_days_per_week IS
    'Days per week the benchmark assumes operation (1-7). NULL = not known.';
COMMENT ON COLUMN plenum_cafm.building_energy_profiles.assumed_hours_source IS
    'Where the assumption comes from, e.g. "CIBSE TM46 general office". A benchmark-fit '
    'argument is only as good as the provenance of its assumption.';
