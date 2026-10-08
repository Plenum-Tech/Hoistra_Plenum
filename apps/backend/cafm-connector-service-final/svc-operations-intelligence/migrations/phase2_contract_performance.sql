-- Phase 2 Feature B — Contract Performance Engine (B1–B3)

-- B1: extracted / editable contract SLA parameters
CREATE TABLE IF NOT EXISTS plenum_cafm.contract_sla_parameters (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID,
    contract_id         UUID,
    document_id         UUID,
    contract_ref        VARCHAR(160),
    status              VARCHAR(40) NOT NULL DEFAULT 'draft',  -- draft | confirmed
    -- SLA targets by priority (hours)
    sla_response_p1_hours   NUMERIC(10,2),
    sla_response_p2_hours   NUMERIC(10,2),
    sla_response_p3_hours   NUMERIC(10,2),
    sla_response_p4_hours   NUMERIC(10,2),
    sla_completion_p1_hours NUMERIC(10,2),
    sla_completion_p2_hours NUMERIC(10,2),
    sla_completion_p3_hours NUMERIC(10,2),
    sla_completion_p4_hours NUMERIC(10,2),
    -- Rates
    labour_day_rate     NUMERIC(12,2),
    overtime_rate       NUMERIC(12,2),
    call_out_rate       NUMERIC(12,2),
    parts_pricing_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    payment_terms       TEXT,
    kpi_clauses_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    ppm_obligations_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    task_criticality_json JSONB NOT NULL DEFAULT '{}'::jsonb,  -- L1/L2/L3 defs
    defaults_used       JSONB NOT NULL DEFAULT '[]'::jsonb,    -- fields labelled Default — not contract-sourced
    overrides_log       JSONB NOT NULL DEFAULT '[]'::jsonb,
    field_sources       JSONB NOT NULL DEFAULT '{}'::jsonb,    -- field -> contract|default|pm_override
    raw_extraction      JSONB NOT NULL DEFAULT '{}'::jsonb,
    confirmed_by        UUID,
    confirmed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_csp_vendor ON plenum_cafm.contract_sla_parameters (vendor_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_csp_org ON plenum_cafm.contract_sla_parameters (organization_id);

-- B1: asset criticality (HITL; unapproved default L2)
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_criticality (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    asset_id            UUID NOT NULL,
    asset_code          VARCHAR(120),
    criticality         VARCHAR(8) NOT NULL DEFAULT 'L2',  -- L1 | L2 | L3
    proposed_criticality VARCHAR(8),
    source              VARCHAR(40) NOT NULL DEFAULT 'system', -- system | contract | pm
    rationale           TEXT,
    approved            BOOLEAN NOT NULL DEFAULT false,
    approved_by         UUID,
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id)
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_ac_org ON plenum_cafm.asset_criticality (organization_id);

-- B2: admin-editable scoring weights
CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_score_weight_config (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    sla_response_pct    NUMERIC(5,2) NOT NULL DEFAULT 25,
    sla_completion_pct  NUMERIC(5,2) NOT NULL DEFAULT 25,
    first_fix_pct       NUMERIC(5,2) NOT NULL DEFAULT 20,
    recall_pct          NUMERIC(5,2) NOT NULL DEFAULT 15,
    accreditation_pct   NUMERIC(5,2) NOT NULL DEFAULT 15,
    blocked_score_cap   NUMERIC(5,2) NOT NULL DEFAULT 60,
    cost_variance_alert_pct NUMERIC(5,2) NOT NULL DEFAULT 15,
    cost_variance_job_count INT NOT NULL DEFAULT 3,
    invoice_flag_adversary_gbp NUMERIC(12,2) NOT NULL DEFAULT 500,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id)
);

-- B2: per-WO score rows
CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_wo_scores (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID,
    work_order_id       UUID,
    wo_code             VARCHAR(120),
    asset_id            UUID,
    score_month         DATE,  -- first of month
    sla_response_met    BOOLEAN,
    sla_completion_met  BOOLEAN,
    first_fix           BOOLEAN,
    recall              BOOLEAN,
    accreditation_current BOOLEAN,
    component_scores    JSONB NOT NULL DEFAULT '{}'::jsonb,
    overall_score       NUMERIC(6,2),
    capped_by_block     BOOLEAN NOT NULL DEFAULT false,
    cost_actual         NUMERIC(14,2),
    cost_estimated      NUMERIC(14,2),
    cost_variance_pct   NUMERIC(8,2),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_vws_vendor_month ON plenum_cafm.vendor_wo_scores (vendor_id, score_month);

-- B2: monthly scorecards
CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_monthly_scorecards (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID NOT NULL,
    score_month         DATE NOT NULL,
    overall_score       NUMERIC(6,2),
    trend_delta         NUMERIC(6,2),
    component_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    ppm_compliance_pct  NUMERIC(6,2),
    matched_flagged_ratio NUMERIC(6,4),
    wo_score_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,
    block_capped        BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vendor_id, score_month)
);

-- B2: FM report staleness (PRD Q5 Option A)
CREATE TABLE IF NOT EXISTS plenum_cafm.fm_report_staleness (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID,
    last_report_at      TIMESTAMPTZ,
    data_as_of          DATE,
    note                TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- B3: invoice verification
CREATE TABLE IF NOT EXISTS plenum_cafm.invoice_verifications (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID,
    invoice_ref         VARCHAR(160),
    document_id         UUID,
    status              VARCHAR(40) NOT NULL DEFAULT 'pending', -- pending | completed
    matched_count       INT NOT NULL DEFAULT 0,
    flagged_count       INT NOT NULL DEFAULT 0,
    matched_flagged_ratio NUMERIC(6,4),
    lines_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
    insights_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_iv_vendor ON plenum_cafm.invoice_verifications (vendor_id);
