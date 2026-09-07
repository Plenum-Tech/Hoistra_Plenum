-- Phase 2 Feature C — Energy Intelligence Engine

CREATE TABLE IF NOT EXISTS plenum_cafm.energy_meters (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID,
    asset_id            UUID,  -- sub-meter → asset FK
    meter_type          VARCHAR(20) NOT NULL,  -- electricity | gas
    mpan                VARCHAR(40),           -- UK electricity
    mprn                VARCHAR(40),           -- UK gas
    dcc_device_id       VARCHAR(80),
    tariff_gbp_per_kwh  NUMERIC(12,6) NOT NULL DEFAULT 0.28,
    carbon_kg_per_kwh   NUMERIC(12,6) NOT NULL DEFAULT 0.207,
    is_sub_meter        BOOLEAN NOT NULL DEFAULT false,
    asset_type_benchmark_kwh NUMERIC(14,4),    -- optional per-asset-type benchmark
    active              BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_em_site ON plenum_cafm.energy_meters (site_id);
CREATE INDEX IF NOT EXISTS ix_em_asset ON plenum_cafm.energy_meters (asset_id);
CREATE INDEX IF NOT EXISTS ix_em_mpan ON plenum_cafm.energy_meters (mpan);
CREATE INDEX IF NOT EXISTS ix_em_mprn ON plenum_cafm.energy_meters (mprn);

-- Half-hourly (and other) readings — UDR MeterReading entity
CREATE TABLE IF NOT EXISTS plenum_cafm.meter_readings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    meter_id            UUID NOT NULL REFERENCES plenum_cafm.energy_meters(id),
    asset_id            UUID,
    reading_at          TIMESTAMPTZ NOT NULL,  -- period start (HH boundary)
    period_minutes      INT NOT NULL DEFAULT 30,
    consumption_kwh     NUMERIC(14,6) NOT NULL,
    source              VARCHAR(40) NOT NULL DEFAULT 'dcc',  -- dcc | manual | import
    quality_flag        VARCHAR(40),  -- ok | estimated | gap_fill
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (meter_id, reading_at)
);

CREATE INDEX IF NOT EXISTS ix_mr_meter_at ON plenum_cafm.meter_readings (meter_id, reading_at);

-- Reading gaps + retry escalation
CREATE TABLE IF NOT EXISTS plenum_cafm.meter_reading_gaps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    meter_id            UUID NOT NULL,
    gap_start           TIMESTAMPTZ NOT NULL,
    gap_end             TIMESTAMPTZ NOT NULL,
    missing_periods     INT NOT NULL,
    retry_count         INT NOT NULL DEFAULT 0,
    status              VARCHAR(40) NOT NULL DEFAULT 'open',  -- open | retrying | resolved | escalated
    last_retry_at       TIMESTAMPTZ,
    escalated_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_mrg_status ON plenum_cafm.meter_reading_gaps (status);

-- Building GIA + type for EUI / TM46
CREATE TABLE IF NOT EXISTS plenum_cafm.building_energy_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID NOT NULL,
    building_type       VARCHAR(80) NOT NULL DEFAULT 'office',  -- TM46 category key
    gia_m2              NUMERIC(14,2) NOT NULL,
    tm46_electricity_benchmark NUMERIC(14,4),
    tm46_gas_benchmark  NUMERIC(14,4),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id)
);

-- EUI snapshots
CREATE TABLE IF NOT EXISTS plenum_cafm.eui_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    meter_type          VARCHAR(20) NOT NULL,  -- electricity | gas | combined
    total_kwh           NUMERIC(16,4) NOT NULL,
    gia_m2              NUMERIC(14,2) NOT NULL,
    eui_kwh_per_m2      NUMERIC(14,6) NOT NULL,
    benchmark_kwh_per_m2 NUMERIC(14,6),
    deviation_pct       NUMERIC(10,4),
    excess_kwh          NUMERIC(16,4),
    financial_gbp       NUMERIC(14,2),
    tariff_used         NUMERIC(12,6),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eui_site_period ON plenum_cafm.eui_snapshots (site_id, period_start);

-- Asset condition scores (1–5) with provenance
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_condition_scores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    asset_id            UUID NOT NULL,
    asset_code          VARCHAR(120),
    condition_score     INT NOT NULL CHECK (condition_score BETWEEN 1 AND 5),
    llm_label           VARCHAR(80),
    provenance_json     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- source report refs
    source_report_ref   TEXT,
    deduced_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    written_to_asset    BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id)
);

-- Remediation / replace recommendations (no WO create)
CREATE TABLE IF NOT EXISTS plenum_cafm.energy_recommendations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    asset_id            UUID,
    site_id             UUID,
    recommendation_type VARCHAR(40) NOT NULL,  -- preventive | remediation | replacement
    condition_score     INT,
    consumption_vs_benchmark_pct NUMERIC(10,4),
    repair_cost_gbp     NUMERIC(14,2),
    replace_cost_gbp    NUMERIC(14,2),
    summary             TEXT NOT NULL,
    detail_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              VARCHAR(40) NOT NULL DEFAULT 'open',  -- open | acknowledged | dismissed
    queue_item_id       UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Anomalies (notification/dashboard only; status for future WO Engine)
CREATE TABLE IF NOT EXISTS plenum_cafm.energy_anomalies (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID,
    meter_id            UUID,
    asset_id            UUID,
    anomaly_type        VARCHAR(60) NOT NULL,
    -- weekend_spike | baseline_drift | asset_spike
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_start        TIMESTAMPTZ,
    window_end          TIMESTAMPTZ,
    metric_pct          NUMERIC(10,4),
    excess_kwh          NUMERIC(16,4),
    annualised_excess_kwh NUMERIC(16,4),
    financial_gbp       NUMERIC(14,2),
    tariff_used         NUMERIC(12,6),
    detail_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              VARCHAR(40) NOT NULL DEFAULT 'open',
    -- open | acknowledged | monitoring | expected | closed
    pm_action           VARCHAR(40),
    pm_reason           TEXT,
    acted_at            TIMESTAMPTZ,
    queue_item_id       UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ea_status ON plenum_cafm.energy_anomalies (status);
CREATE INDEX IF NOT EXISTS ix_ea_type ON plenum_cafm.energy_anomalies (anomaly_type);

-- Monthly energy reports
CREATE TABLE IF NOT EXISTS plenum_cafm.energy_monthly_reports (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    site_id             UUID,
    report_month        DATE NOT NULL,
    eui_trend_json      JSONB NOT NULL DEFAULT '[]'::jsonb,
    anomalies_ranked_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    total_excess_cost_gbp NUMERIC(14,2),
    carbon_exposure_kg  NUMERIC(16,4),
    report_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    pdf_blob_url        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id, report_month)
);

-- Optional: condition_score column on assets if missing
ALTER TABLE plenum_cafm.assets
    ADD COLUMN IF NOT EXISTS condition_score INT,
    ADD COLUMN IF NOT EXISTS condition_provenance JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS condition_updated_at TIMESTAMPTZ;
