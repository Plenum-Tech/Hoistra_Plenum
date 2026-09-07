-- Building table columns on plenum_cafm.sites.
--
-- The dashboard's Buildings table shows, per site: building name and id, country and
-- state, use with its floor-area split, floors, floor area, EUI, benchmark, the benchmark
-- standard with its legal standing, the metering route and granularity, and a Hoist
-- Score. Until now sites carried only name / city / country / site_type / floors / gfa_sqm,
-- so most of those columns had nowhere to live on the site itself. Idempotent.
--
-- Measured figures (EUI, benchmark) are normally computed from meters and the energy
-- profile; the columns here are the RECORDED value for a site that has no meter feed yet
-- (a survey figure, a landlord statement, an EPC). GET /api/energy/buildings prefers the
-- computed figure and says which one it used.

ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS building_name VARCHAR(255);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS building_code VARCHAR(50);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS country_code VARCHAR(8);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS state VARCHAR(120);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS use_type VARCHAR(80);
-- [{"use": "Commercial", "pct": 92}, {"use": "Retail", "pct": 8}] — share of floor area by use.
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS use_mix JSONB;
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS floors TEXT;
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS gfa_sqm TEXT;
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS metering_route VARCHAR(160);
-- sub-metered | building-level | none
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS metering_granularity VARCHAR(40);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS benchmark_standard VARCHAR(120);
-- enacted | guidance | mandatory_submission | none
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS benchmark_standing VARCHAR(40);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS benchmark_standing_note TEXT;
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS eui_kwh_per_m2 NUMERIC(14,2);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS benchmark_kwh_per_m2 NUMERIC(14,2);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS hoist_score INTEGER;

COMMENT ON COLUMN plenum_cafm.sites.eui_kwh_per_m2
    IS 'Recorded annual EUI for a site without a meter feed. Computed EUI from eui_snapshots takes precedence.';
COMMENT ON COLUMN plenum_cafm.sites.benchmark_standing
    IS 'Legal standing of benchmark_standard: enacted | guidance | mandatory_submission | none.';
COMMENT ON COLUMN plenum_cafm.sites.hoist_score
    IS 'Hoist Score 0-100: how completely the building is hoisted onto the graph.';
