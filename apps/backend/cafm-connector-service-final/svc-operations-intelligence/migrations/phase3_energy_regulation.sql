-- Phase 3: the engines the energy page promised and the backend did not have.
--
-- B5  An EPC carries a band (A–G) and a score. compliance_certificates had nowhere to keep
--     either, so MEES — "is this building below E?" — could not be answered from the register.
-- B6  Energy Star score (US) and  B7  LL97 emissions position (NYC): both are computed from
--     twelve months of readings and a building's type and area, and both are worth keeping as
--     dated snapshots so a tile can say "actual, 12 months" or "projected from 5".
-- B8  LL84, BCA and Green Mark are obligations discharged by FILING something. A filing is a
--     record: who, when, for which year, with what reference. One table, three schemes.
-- B9  A chiller's kW/RT needs its own readings (kW in, RT out, ambient) and its design figure.
-- B10 Three of the ten new detectors need inputs no table held: degree days for the
--     weather-normalised rule, BMS trends for simultaneous heating and cooling, and the
--     chiller readings above for kW/RT.
--
-- Idempotent throughout: CREATE IF NOT EXISTS, and every ALTER checks the column first.

-- ── B5 · EPC band and score on the certificate ───────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'compliance_certificates'
                      AND column_name = 'energy_rating') THEN
        ALTER TABLE plenum_cafm.compliance_certificates ADD COLUMN energy_rating VARCHAR(4);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'compliance_certificates'
                      AND column_name = 'energy_score') THEN
        ALTER TABLE plenum_cafm.compliance_certificates ADD COLUMN energy_score INTEGER;
    END IF;
END $$;

-- The GOV.UK register check already scraped the band into the verification evidence for
-- every EPC it resolved. Lift it into the column, once, for rows that have none.
UPDATE plenum_cafm.compliance_certificates
   SET energy_rating = upper(trim(raw_metadata #>> '{verification,evidence,current_energy_rating}'))
 WHERE energy_rating IS NULL
   AND upper(trim(coalesce(raw_metadata #>> '{verification,evidence,current_energy_rating}', '')))
       IN ('A','B','C','D','E','F','G');

CREATE INDEX IF NOT EXISTS ix_cc_energy_rating
    ON plenum_cafm.compliance_certificates (energy_rating)
 WHERE energy_rating IS NOT NULL;

-- ── B6 / B7 · rating snapshots ───────────────────────────────────────────────────────
-- scheme: energy_star | ll97 | bca_eui | mees | portfolio_eui. value is the scheme's own
-- number (a score, tCO2e, a %). months_of_data decides whether a tile may say "actual".
CREATE TABLE IF NOT EXISTS plenum_cafm.energy_ratings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    building_id         UUID NOT NULL,
    scheme              VARCHAR(40) NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    months_of_data      INTEGER NOT NULL DEFAULT 0,
    value               NUMERIC(16, 6),
    unit                VARCHAR(40),
    limit_value         NUMERIC(16, 6),
    status              VARCHAR(40),               -- within | over | certified | short | insufficient_data
    basis               VARCHAR(20) NOT NULL DEFAULT 'consumption',  -- consumption | certificate | filing
    detail_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_energy_ratings_building_scheme
    ON plenum_cafm.energy_ratings (building_id, scheme, computed_at DESC);

-- ── B8 · regulatory filings ──────────────────────────────────────────────────────────
-- scheme: LL84 | BCA_BENCHMARKING | GREEN_MARK (others may follow — the column is free text
-- validated by the engine). period_year is the compliance year the filing discharges.
CREATE TABLE IF NOT EXISTS plenum_cafm.regulatory_filings (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID,
    building_id             UUID NOT NULL,
    scheme                  VARCHAR(40) NOT NULL,
    period_year             INTEGER NOT NULL,
    status                  VARCHAR(30) NOT NULL DEFAULT 'filed',  -- filed | submitted | accepted | certified | due | overdue | lapsed
    filed_at                DATE,
    due_date                DATE,
    reference               VARCHAR(160),          -- confirmation / submission / certificate number
    certification_level     VARCHAR(40),           -- Green Mark: Certified | Gold | GoldPlus | Platinum
    valid_until             DATE,                  -- certifications only
    submitted_by            VARCHAR(255),
    evidence_document_id    UUID,
    detail_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by              UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (building_id, scheme, period_year)
);
CREATE INDEX IF NOT EXISTS ix_regulatory_filings_building
    ON plenum_cafm.regulatory_filings (building_id, scheme, period_year DESC);

-- ── B9 · chiller design figures and performance readings ────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.chiller_design_specs (
    asset_id                UUID PRIMARY KEY,
    building_id             UUID,
    organization_id         UUID,
    design_kw_per_rt        NUMERIC(8, 4) NOT NULL,
    design_capacity_rt      NUMERIC(10, 2),
    design_ambient_c        NUMERIC(5, 2),
    design_chw_supply_c     NUMERIC(5, 2),
    source                  VARCHAR(80),           -- datasheet | commissioning | manual
    notes                   TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.chiller_performance_readings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    asset_id            UUID NOT NULL,
    building_id         UUID,
    reading_at          TIMESTAMPTZ NOT NULL,
    kw_input            NUMERIC(12, 4) NOT NULL,
    cooling_load_rt     NUMERIC(12, 4),            -- given directly …
    cooling_load_kw     NUMERIC(12, 4),            -- … or as thermal kW (÷ 3.517 → RT)
    ambient_c           NUMERIC(5, 2),
    chw_supply_c        NUMERIC(5, 2),
    chw_return_c        NUMERIC(5, 2),
    source              VARCHAR(40) NOT NULL DEFAULT 'bms',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_chiller_readings_asset_at
    ON plenum_cafm.chiller_performance_readings (asset_id, reading_at DESC);

-- ── B10 · inputs for the weather and simultaneous-heating-and-cooling rules ─────────
CREATE TABLE IF NOT EXISTS plenum_cafm.weather_degree_days (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    building_id         UUID NOT NULL,
    month               DATE NOT NULL,             -- first of the month
    hdd                 NUMERIC(10, 2) NOT NULL DEFAULT 0,
    cdd                 NUMERIC(10, 2) NOT NULL DEFAULT 0,
    base_temp_c         NUMERIC(5, 2) NOT NULL DEFAULT 15.5,
    station             VARCHAR(120),
    source              VARCHAR(40) NOT NULL DEFAULT 'manual',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (building_id, month)
);

CREATE TABLE IF NOT EXISTS plenum_cafm.bms_trends (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    building_id         UUID NOT NULL,
    asset_id            UUID,
    zone                VARCHAR(120) NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL,
    heating_pct         NUMERIC(6, 2),             -- heating valve / call, 0–100
    cooling_pct         NUMERIC(6, 2),             -- cooling valve / call, 0–100
    zone_temp_c         NUMERIC(5, 2),
    setpoint_c          NUMERIC(5, 2),
    source              VARCHAR(40) NOT NULL DEFAULT 'bms',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_bms_trends_building_at
    ON plenum_cafm.bms_trends (building_id, recorded_at DESC);
