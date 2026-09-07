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

-- Portfolio seed — the nine buildings the dashboard was designed around. Only inserted when
-- the site_id is not already present, so operator edits are never overwritten. Floor areas
-- are the design figures converted to m² (412,000 ft² = 38,276 m²).
INSERT INTO plenum_cafm.sites (site_id, site_name, building_name, building_code, site_code, country, country_code, state, city, site_type, use_type, use_mix, floors, gfa_sqm, metering_route, metering_granularity, benchmark_standard, benchmark_standing, benchmark_standing_note, eui_kwh_per_m2, benchmark_kwh_per_m2, hoist_score, status)
VALUES
 ('B-001', 'Bishopsgate Tower', 'Bishopsgate Tower', 'B-001', 'B-001', 'United Kingdom', 'UK', 'Greater London', 'London', 'Commercial', 'Commercial', '[{"use":"Commercial","pct":92},{"use":"Retail","pct":8}]'::jsonb, '34', '38276', 'HH data collector · LoA', 'sub-metered', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 214, 215, 88, 'active'),
 ('B-002', 'Kingsway House', 'Kingsway House', 'B-002', 'B-002', 'United Kingdom', 'UK', 'Greater London', 'London', 'Mixed', 'Mixed', '[{"use":"Residential","pct":46},{"use":"Commercial","pct":34},{"use":"Retail","pct":20}]'::jsonb, '11', '13750', 'HH data collector · LoA', 'sub-metered', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 198, 172, 71, 'active'),
 ('B-003', 'Town Hall', 'Town Hall', 'B-003', 'B-003', 'United Kingdom', 'UK', 'Greater Manchester', 'Manchester', 'Commercial', 'Commercial', '[{"use":"Commercial","pct":100}]'::jsonb, '6', '8919', 'HH data collector · LoA', 'building-level', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 231, 215, 62, 'active'),
 ('B-004', 'Meridian Quay', 'Meridian Quay', 'B-004', 'B-004', 'United Kingdom', 'UK', 'Scotland', 'Edinburgh', 'Residential', 'Residential', '[{"use":"Residential","pct":88},{"use":"Retail","pct":12}]'::jsonb, '22', '17280', 'SMETS2 · SEC intermediary', 'building-level', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 164, 150, 84, 'active'),
 ('B-005', 'AN Other House', 'AN Other House', 'B-005', 'B-005', 'United Kingdom', 'UK', 'Greater London', 'London', 'Retail', 'Retail', '[{"use":"Retail","pct":100}]'::jsonb, '4', '6689', 'HH data collector · LoA', 'building-level', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 209, 165, 79, 'active'),
 ('B-006', 'Riverside Court', 'Riverside Court', 'B-006', 'B-006', 'United Kingdom', 'UK', 'Greater Manchester', 'Manchester', 'Residential', 'Residential', '[{"use":"Residential","pct":100}]'::jsonb, '15', '11241', 'SMETS2 · SEC intermediary', 'building-level', 'CIBSE TM46', 'guidance', 'guidance · EPC E law, EPC B proposed 2031', 158, 145, 90, 'active'),
 ('B-007', 'Marina Heights', 'Marina Heights', 'B-007', 'B-007', 'UAE', 'AE', 'Dubai', 'Dubai', 'Hospital', 'Hospital', '[{"use":"Hospital","pct":79},{"use":"Commercial","pct":14},{"use":"Retail","pct":7}]'::jsonb, '8', '18952', 'Own sub-meters + BMS', 'sub-metered', 'Rolling portfolio benchmark', 'none', 'no operational standard · portfolio benchmark', 246, 228, 58, 'active'),
 ('B-008', 'Northgate Mall', 'Northgate Mall', 'B-008', 'B-008', 'United States', 'US', 'New York', 'New York', 'Mixed', 'Mixed', '[{"use":"Mall","pct":62},{"use":"Retail","pct":26},{"use":"Commercial","pct":12}]'::jsonb, '3', '29543', 'Green Button CMD · aggregator', 'sub-metered', 'Energy Star · ASHRAE 100', 'enacted', 'enacted, city-scoped · NYC LL97', 188, 205, 74, 'active'),
 ('B-009', 'Raffles Link', 'Raffles Link', 'B-009', 'B-009', 'Singapore', 'SG', 'Central Region', 'Singapore', 'Commercial', 'Commercial', '[{"use":"Commercial","pct":100}]'::jsonb, '19', '24898', 'Retailer feed · contracted', 'sub-metered', 'BCA Benchmarking Report', 'mandatory_submission', 'submission mandatory · rating voluntary', 176, 192, 92, 'active')
ON CONFLICT (site_id) DO NOTHING;
