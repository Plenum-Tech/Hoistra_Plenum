-- Phase 2 Feature A — Compliance Engine (A1–A5)
-- Idempotent migration for plenum_cafm schema.
-- Forward-compatible: remedial_status + vendor.block_state ready for future WO Engine.

CREATE SCHEMA IF NOT EXISTS plenum_cafm;

-- ── A4 Country Certificate Pack ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.country_certificate_packs (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pack_id                         VARCHAR(80) NOT NULL,
    country_code                    VARCHAR(8)  NOT NULL,  -- UK / US / UAE / SG / AU
    pack_version                    VARCHAR(40) NOT NULL DEFAULT '1.0',
    certificate_type_code           VARCHAR(80) NOT NULL,
    certificate_type_name           VARCHAR(255) NOT NULL,
    certificate_scope               VARCHAR(20) NOT NULL,  -- Building | Vendor
    trade_category                  VARCHAR(120),
    regulation_reference            VARCHAR(255),
    regulation_url                  TEXT,
    frequency_months                INTEGER,
    issuing_body                    VARCHAR(255),
    required_contractor_accreditation VARCHAR(255),
    verification_url                TEXT,
    key_fields_schema               JSONB NOT NULL DEFAULT '{}'::jsonb,
    alert_thresholds                JSONB NOT NULL DEFAULT '{
        "current_gt": 90,
        "expiring_soon_min": 61,
        "expiring_soon_max": 90,
        "due_for_renewal_min": 31,
        "due_for_renewal_max": 60,
        "overdue_min": 8,
        "overdue_max": 30,
        "lapsed_lte": 7
    }'::jsonb,
    is_active                       BOOLEAN NOT NULL DEFAULT true,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (country_code, certificate_type_code, pack_version)
);

CREATE INDEX IF NOT EXISTS ix_ccp_country_scope
    ON plenum_cafm.country_certificate_packs (country_code, certificate_scope);
CREATE INDEX IF NOT EXISTS ix_ccp_code
    ON plenum_cafm.country_certificate_packs (certificate_type_code);

-- ── Ensure base compliance_certificates exists (UDR Phase 1 stub) ─────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_certificates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID,
    certificate_ref     VARCHAR(160),
    cert_type           VARCHAR(80),
    asset_id            UUID,
    site_id             UUID,
    duty_holder_id      UUID,
    issuer              VARCHAR(255),
    issue_date          DATE,
    expiry_date         DATE,
    status              VARCHAR(40),
    source_document_id  UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A1/A2/A3 column extensions
ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS organization_id UUID,
    ADD COLUMN IF NOT EXISTS vendor_id UUID,
    ADD COLUMN IF NOT EXISTS certificate_type_code VARCHAR(80),
    ADD COLUMN IF NOT EXISTS certificate_number VARCHAR(160),
    ADD COLUMN IF NOT EXISTS next_due_date DATE,
    ADD COLUMN IF NOT EXISTS inspection_frequency_months INTEGER,
    ADD COLUMN IF NOT EXISTS inspector_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS inspector_accreditation_number VARCHAR(160),
    ADD COLUMN IF NOT EXISTS result VARCHAR(40),
    ADD COLUMN IF NOT EXISTS defects_found TEXT,
    ADD COLUMN IF NOT EXISTS remedial_actions TEXT,
    ADD COLUMN IF NOT EXISTS remedial_status VARCHAR(40) DEFAULT 'Closed',
    ADD COLUMN IF NOT EXISTS document_id UUID,
    ADD COLUMN IF NOT EXISTS cert_scope VARCHAR(20),
    ADD COLUMN IF NOT EXISTS country_code VARCHAR(8) DEFAULT 'UK',
    ADD COLUMN IF NOT EXISTS authenticity_warning TEXT,
    ADD COLUMN IF NOT EXISTS days_to_expiry INTEGER,
    ADD COLUMN IF NOT EXISTS insurance_risk_flag BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS raw_metadata JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_cc_org ON plenum_cafm.compliance_certificates (organization_id);
CREATE INDEX IF NOT EXISTS ix_cc_vendor ON plenum_cafm.compliance_certificates (vendor_id);
CREATE INDEX IF NOT EXISTS ix_cc_scope_status
    ON plenum_cafm.compliance_certificates (cert_scope, status);
CREATE INDEX IF NOT EXISTS ix_cc_type_code
    ON plenum_cafm.compliance_certificates (certificate_type_code);
CREATE INDEX IF NOT EXISTS ix_cc_expiry2 ON plenum_cafm.compliance_certificates (expiry_date);

-- ── Vendor block state (A3) — forward-compatible for WO Engine ────────────────
ALTER TABLE plenum_cafm.vendors
    ADD COLUMN IF NOT EXISTS block_state VARCHAR(40) DEFAULT 'Clear',
    ADD COLUMN IF NOT EXISTS block_reason TEXT,
    ADD COLUMN IF NOT EXISTS block_date TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS blocked_accreditation_type VARCHAR(160);

CREATE INDEX IF NOT EXISTS ix_vendors_block_state
    ON plenum_cafm.vendors (block_state);

-- ── Approvals & Notifications queue (replaces Activity Log UI dependency) ─────
CREATE TABLE IF NOT EXISTS plenum_cafm.approvals_queue_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    source_feature  CHAR(1) NOT NULL,          -- A | B | C
    item_type       VARCHAR(80) NOT NULL,      -- booking_request | vendor_email | block_ack | remedial | alert
    summary         TEXT NOT NULL,
    severity        VARCHAR(40) NOT NULL,      -- Info | Action required | Critical
    status          VARCHAR(40) NOT NULL DEFAULT 'pending',  -- pending | approved | edited | dismissed | sent
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    related_entity_type VARCHAR(80),
    related_entity_id   UUID,
    email_draft     JSONB,
    pm_notes        TEXT,
    decided_by      UUID,
    decided_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_aqi_org_status
    ON plenum_cafm.approvals_queue_items (organization_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_aqi_source
    ON plenum_cafm.approvals_queue_items (source_feature, severity);

-- ── Immutable ops audit log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.ops_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    actor           VARCHAR(160) NOT NULL,     -- system | adversary | user:<id>
    action_type     VARCHAR(120) NOT NULL,
    source_feature  CHAR(1),
    input_hash      VARCHAR(64),
    output_hash     VARCHAR(64),
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ops_audit_org_ts
    ON plenum_cafm.ops_audit_log (organization_id, created_at DESC);

-- ── Compliance scan run receipts ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_scan_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    scope           VARCHAR(80) NOT NULL DEFAULT 'all',  -- all | building | vendor | site
    scope_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,
    building_scanned INTEGER NOT NULL DEFAULT 0,
    vendor_scanned   INTEGER NOT NULL DEFAULT 0,
    alerts_created   INTEGER NOT NULL DEFAULT 0,
    blocks_set       INTEGER NOT NULL DEFAULT 0,
    adversary_passed INTEGER NOT NULL DEFAULT 0,
    adversary_failed INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(40) NOT NULL DEFAULT 'completed',
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    detail          JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ── Email send log (platform-sent after PM Approve) ───────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.ops_email_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    queue_item_id   UUID REFERENCES plenum_cafm.approvals_queue_items(id),
    to_address      VARCHAR(255) NOT NULL,
    subject         VARCHAR(500) NOT NULL,
    body            TEXT NOT NULL,
    status          VARCHAR(40) NOT NULL DEFAULT 'queued',  -- queued | sent | failed | dry_run
    error_message   TEXT,
    sent_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
