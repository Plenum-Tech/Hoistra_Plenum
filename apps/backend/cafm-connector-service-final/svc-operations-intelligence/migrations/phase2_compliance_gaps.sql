-- Phase 2 Compliance Engine — gap-fill tables (A2–A4, ResourceSkill, trends, tokens)

CREATE TABLE IF NOT EXISTS plenum_cafm.building_country_packs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    site_id         UUID NOT NULL,
    country_code    VARCHAR(8) NOT NULL DEFAULT 'UK',
    pack_version    VARCHAR(40) NOT NULL DEFAULT '1.1',
    activated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id)
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bcp_org ON plenum_cafm.building_country_packs (organization_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.resource_skills (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID,
    vendor_id           UUID NOT NULL,
    resource_id         UUID,
    operative_name      VARCHAR(255),
    skill_type_code     VARCHAR(80) NOT NULL,
    certificate_number  VARCHAR(160),
    issue_date          DATE,
    expiry_date         DATE,
    status              VARCHAR(40),
    days_to_expiry      INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rs_vendor ON plenum_cafm.resource_skills (vendor_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rs_status ON plenum_cafm.resource_skills (status);

CREATE TABLE IF NOT EXISTS plenum_cafm.approval_action_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    queue_item_id   UUID NOT NULL,
    token           VARCHAR(64) NOT NULL UNIQUE,
    action          VARCHAR(40) NOT NULL DEFAULT 'approve',
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_aat_token ON plenum_cafm.approval_action_tokens (token);

CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_risk_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    snapshot_date   DATE NOT NULL,
    vendors_blocked INTEGER NOT NULL DEFAULT 0,
    high_risk_lt_30 INTEGER NOT NULL DEFAULT 0,
    medium_risk_lt_90 INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, snapshot_date)
);

ALTER TABLE plenum_cafm.country_certificate_packs
    ADD COLUMN IF NOT EXISTS change_notes TEXT;
