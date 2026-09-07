-- ============================================================================
-- UDR Phase I-A — Target UDR canonical schema + control/meta layer
-- ============================================================================
-- Source of truth: docs/UDR_CONSOLIDATED_IMPLEMENTATION_PLAN.md (§4)
--                  docs/reference/UDR_PRD_v1.1_*.txt (Features 2,4,7)
-- Decisions (2026-06-18): extend plenum_cafm · in-Postgres relationship graph.
--
-- Idempotent: CREATE TABLE / ADD COLUMN / CREATE INDEX use IF NOT EXISTS;
-- FK constraints are added in guarded DO blocks. Safe to run repeatedly.
-- Canonical hierarchy: Organization -> Location -> Site -> (Building -> Floor
--   -> Space) -> Asset -> WorkOrder -> Resource -> Vendor, plus Compliance /
--   Tenant / Energy / Documents / Spares / Contracts branches.
-- Assumes the existing plenum_cafm tables (organizations, locations, assets,
--   work_orders, vendors, spare_parts) already exist with UUID PKs.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector (Layer 2 embeddings)
CREATE SCHEMA IF NOT EXISTS plenum_cafm;
SET search_path TO plenum_cafm, public;

-- ----------------------------------------------------------------------------
-- SECTION 1 — Canonical domain tables (net-new; existing ones are ALTERed in §3)
-- ----------------------------------------------------------------------------

-- Site: physical site under a Location (portfolio).
CREATE TABLE IF NOT EXISTS plenum_cafm.sites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    location_id     UUID,
    site_code       VARCHAR(100),
    site_name       VARCHAR(255) NOT NULL,
    address         TEXT,
    city            VARCHAR(120),
    postcode        VARCHAR(40),
    country         VARCHAR(120),
    site_type       VARCHAR(120),
    gfa_sqm         NUMERIC(14,2),
    handover_date   DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Optional sub-site levels.
CREATE TABLE IF NOT EXISTS plenum_cafm.buildings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    site_id     UUID NOT NULL,
    building_code VARCHAR(100),
    building_name VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.floors (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    building_id UUID NOT NULL,
    floor_code  VARCHAR(100),
    floor_name  VARCHAR(255),
    level_number INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Space / Unit (tenant-occupiable area).
CREATE TABLE IF NOT EXISTS plenum_cafm.spaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    site_id     UUID,
    building_id UUID,
    floor_id    UUID,
    space_code  VARCHAR(100),
    space_name  VARCHAR(255),
    space_type  VARCHAR(120),       -- unit / common / plant_room / retail ...
    area_sqm    NUMERIC(14,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Reference tables (auto-creatable by Test-1/Test-2 remediation — §6 of plan).
CREATE TABLE IF NOT EXISTS plenum_cafm.asset_types (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID,
    asset_type  VARCHAR(160) NOT NULL,   -- natural key (e.g. "Air Handler")
    category    VARCHAR(160),
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, asset_type)
);

CREATE TABLE IF NOT EXISTS plenum_cafm.manufacturers (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id               UUID,
    manufacturer_name    VARCHAR(200) NOT NULL,   -- e.g. "Siemens 012"
    manufacturer_country VARCHAR(120),
    support_email        VARCHAR(255),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, manufacturer_name)
);

CREATE TABLE IF NOT EXISTS plenum_cafm.contracts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL,
    contract_ref  VARCHAR(160) NOT NULL,
    vendor_id     UUID,
    start_date    DATE,
    end_date      DATE,
    value_amount  NUMERIC(16,2),
    currency      VARCHAR(8),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, contract_ref)
);

-- Resource (labour / manpower / skill).
CREATE TABLE IF NOT EXISTS plenum_cafm.resources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL,
    resource_code VARCHAR(120),
    resource_name VARCHAR(255),
    skill_type    VARCHAR(160),
    vendor_id     UUID,
    contract_id   UUID,
    cost_rate     NUMERIC(12,2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.work_order_resources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id UUID NOT NULL,
    resource_id   UUID NOT NULL,
    hours         NUMERIC(10,2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (work_order_id, resource_id)
);

-- Compliance.
CREATE TABLE IF NOT EXISTS plenum_cafm.duty_holders (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    name            VARCHAR(255) NOT NULL,
    role            VARCHAR(160),    -- Responsible Person / duty holder
    email           VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_certificates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    certificate_ref VARCHAR(160),
    cert_type       VARCHAR(80),     -- gas_safety | EICR | LOLER | legionella_L8 | fire | ...
    asset_id        UUID,
    site_id         UUID,
    duty_holder_id  UUID,
    issuer          VARCHAR(255),
    issue_date      DATE,
    expiry_date     DATE,
    status          VARCHAR(40),     -- valid | expiring | expired
    source_document_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Energy / utilities.
CREATE TABLE IF NOT EXISTS plenum_cafm.meters (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    meter_code  VARCHAR(120),
    meter_type  VARCHAR(80),         -- electricity | water | gas | btu ...
    unit        VARCHAR(40),
    asset_id    UUID,
    site_id     UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.meter_readings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meter_id    UUID NOT NULL,
    reading_value NUMERIC(18,4),
    reading_date  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tenant operations.
CREATE TABLE IF NOT EXISTS plenum_cafm.tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    tenant_code VARCHAR(120),
    tenant_name VARCHAR(255),
    contact_email VARCHAR(255),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.leases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    lease_ref   VARCHAR(160),
    tenant_id   UUID,
    space_id    UUID,
    start_date  DATE,
    end_date    DATE,
    rent_amount NUMERIC(16,2),
    currency    VARCHAR(8),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Layer-2 vector store: document chunks anchored to a structured entity's PK.
CREATE TABLE IF NOT EXISTS plenum_cafm.document_chunks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL,
    document_id        UUID,
    chunk_index        INTEGER,
    content            TEXT,
    embedding          vector(1536),
    -- PK-only anchor (Test 1): the entity TYPE and the PK VALUE of the anchor.
    source_entity_type VARCHAR(120),   -- e.g. 'asset' | 'manufacturer' | 'site'
    source_entity_id   TEXT,           -- the PK VALUE of the anchor entity
    document_type      VARCHAR(120),
    page_reference     VARCHAR(80),
    anchor_is_primary_key BOOLEAN DEFAULT FALSE,  -- set TRUE only after Test 1 pass
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- SECTION 2 — UDR control / meta layer (the engine backbone — all new)
-- ----------------------------------------------------------------------------

-- Catalog of canonical target tables (drives table mapping — PRD 7.4 AC7).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_canonical_table (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_table    VARCHAR(160) NOT NULL UNIQUE,
    hierarchy_level    VARCHAR(80),
    description        TEXT,
    primary_key_column VARCHAR(160),
    standard_columns   JSONB,     -- [{name, datatype, classification, sample_values[]}]
    sample_values      JSONB,
    is_reference_table BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-canonical-column metadata (PRD 7.5 AC3 / 7.11 AC4).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_canonical_column (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_table    VARCHAR(160) NOT NULL,
    canonical_column   VARCHAR(160) NOT NULL,
    datatype           VARCHAR(80),
    classification     VARCHAR(40),   -- primary_key | foreign_key | shared_attribute | standard
    cell_value_format  VARCHAR(120),
    sample_values      JSONB,
    references_table   VARCHAR(160),  -- when FK
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (canonical_table, canonical_column)
);

-- RAG synonym library: CMMS/CAFM-native term -> canonical (PRD 7.4 AC2 / 7.7).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_synonym (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cmms_system      VARCHAR(80),     -- Maximo | Fiix | SAP PM | Archibus | Planon | ...
    source_term      VARCHAR(200) NOT NULL,
    target_kind      VARCHAR(20) NOT NULL,   -- 'table' | 'column'
    canonical_table  VARCHAR(160),
    canonical_column VARCHAR(160),
    confidence       NUMERIC(4,3),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per UDR run (Saved UDR Script — PRD F2.4 / F4).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_script (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL,
    script_code        VARCHAR(200) NOT NULL,  -- <OrgID><#struct><#unstruct><#tables><#cols><ts>
    status             VARCHAR(40),            -- running | awaiting_review | complete | failed | archived
    structured_doc_count INTEGER DEFAULT 0,
    unstructured_doc_count INTEGER DEFAULT 0,
    table_count        INTEGER DEFAULT 0,
    column_count       INTEGER DEFAULT 0,
    recommended_structure_md TEXT,             -- the "finalised DB structure" .md (PRD 4.1 AC6)
    snapshot           JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

-- Version chain — keep last 3 hot, older flagged archived. NEVER overwrite (PRD 4.6).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_script_version (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    udr_script_id UUID NOT NULL,
    org_id        UUID NOT NULL,
    version_no    INTEGER NOT NULL,
    script_code   VARCHAR(200) NOT NULL,
    is_archived   BOOLEAN DEFAULT FALSE,
    change_summary TEXT,
    snapshot      JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (udr_script_id, version_no)
);

-- Traceable mapping decisions (PRD AL.6 AC4).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_mapping_decision (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    udr_script_id      UUID NOT NULL,
    decision_kind      VARCHAR(20),   -- 'table' | 'column'
    source_table       VARCHAR(200),
    source_column      VARCHAR(200),
    dest_table         VARCHAR(200),
    dest_column        VARCHAR(200),
    confidence         NUMERIC(5,2),  -- 2 decimal places per PRD
    resolved_by        VARCHAR(40),   -- deterministic | rag | semantic | user_modified | user_added | auto_created
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Layer-3 relationship graph (in-Postgres — decision 2026-06-18).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_relationship (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL,
    udr_script_id   UUID,
    from_table      VARCHAR(160) NOT NULL,
    from_column     VARCHAR(160),
    to_table        VARCHAR(160) NOT NULL,
    to_column       VARCHAR(160),
    rel_type        VARCHAR(80),    -- belongs_to | raised_against | assigned_to | allocated_to | linked_to ...
    provenance      VARCHAR(40) NOT NULL DEFAULT 'schema_defined',  -- schema_defined | llm_inferred
    confidence      NUMERIC(5,2),
    review_status    VARCHAR(40),   -- promoted | review_queue | held
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Test 1 / Test 2 results (PRD 7.9 / 7.10).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_test_result (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    udr_script_id   UUID NOT NULL,
    test_name       VARCHAR(20) NOT NULL,   -- 'test_1' | 'test_2'
    total_checked   INTEGER,
    passed          INTEGER,
    failed          INTEGER,
    fail_rate       NUMERIC(6,4),
    blocked         BOOLEAN DEFAULT FALSE,
    detail          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RAG ontology chunks (embedding-ready knowledge base — Phase I-B at runtime).
CREATE TABLE IF NOT EXISTS plenum_cafm.udr_ontology_chunk (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_key   VARCHAR(200) NOT NULL UNIQUE,
    concept     VARCHAR(80),    -- table_mapping | column_mapping | hierarchy_detection | entity_resolution | fk_detection | normalization
    entity      VARCHAR(160),
    intent_tags JSONB,
    content     TEXT,
    embedding   vector(1536),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- SECTION 2b — Activity Log, Saved Spaces, Pinned Runs (durable — fixes G5)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS plenum_cafm.activity_log_entry (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    session_id  VARCHAR(200),
    trigger_type VARCHAR(40),   -- query | external | threshold | scheduled
    outcome     TEXT,
    status      VARCHAR(40),     -- completed | pending_human_input | failed | escalated
    udr_script_id UUID,
    read_flag   BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.activity_log_step (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id    UUID NOT NULL,
    step_index  INTEGER,
    kind        VARCHAR(20),    -- cot | coa
    label       VARCHAR(255),
    text        TEXT,
    confidence  NUMERIC(5,2),
    agent_from  VARCHAR(120),
    agent_to    VARCHAR(120),
    faq_key     VARCHAR(120),
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.activity_log_action (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id     UUID NOT NULL,
    title        VARCHAR(255),
    body         TEXT,
    options      JSONB,          -- <= 4 closed options
    resolution   TEXT,
    status       VARCHAR(40),    -- pending | resolved | pending_human_response
    blocks_udr   BOOLEAN DEFAULT FALSE,
    opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS plenum_cafm.activity_log_refinement (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id    UUID NOT NULL,
    kind        VARCHAR(20),    -- refinement | follow_up
    title       VARCHAR(255),
    body        TEXT,
    dismissed   BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.saved_space (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    name        VARCHAR(200) NOT NULL,
    kind        VARCHAR(40),    -- standard | custom
    standard_key VARCHAR(80),   -- work_orders | assets | documents | udr_script (when standard)
    semantic_tag VARCHAR(200),  -- ontology-driven tag (when custom)
    created_by  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.saved_space_item (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id     UUID NOT NULL,
    item_kind    VARCHAR(40),   -- work_order | asset | document | udr_script | chat
    item_ref     VARCHAR(200),
    session_id   VARCHAR(200),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.pinned_run (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID,
    label       VARCHAR(160),
    prompt      TEXT,
    is_default  BOOLEAN DEFAULT FALSE,
    tool_context VARCHAR(80),
    usage_count INTEGER DEFAULT 0,
    created_by  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- SECTION 3 — Extend existing canonical tables with hierarchy FKs (guarded)
-- ----------------------------------------------------------------------------
ALTER TABLE IF EXISTS plenum_cafm.assets       ADD COLUMN IF NOT EXISTS site_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.assets       ADD COLUMN IF NOT EXISTS asset_type_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.assets       ADD COLUMN IF NOT EXISTS manufacturer_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.assets       ADD COLUMN IF NOT EXISTS space_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.work_orders  ADD COLUMN IF NOT EXISTS site_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.work_orders  ADD COLUMN IF NOT EXISTS vendor_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.spare_parts  ADD COLUMN IF NOT EXISTS manufacturer_id UUID;

-- ----------------------------------------------------------------------------
-- SECTION 4 — Foreign keys (guarded: added only if absent; skipped if the
--             referenced/owning table is missing). Names are explicit so reruns
--             are idempotent.
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    fk TEXT[];
    fks CONSTANT TEXT[][] := ARRAY[
        -- [owning_table, constraint_name, column, ref_table, ref_col]
        ['sites','fk_sites_location','location_id','locations','id'],
        ['buildings','fk_buildings_site','site_id','sites','id'],
        ['floors','fk_floors_building','building_id','buildings','id'],
        ['spaces','fk_spaces_site','site_id','sites','id'],
        ['contracts','fk_contracts_vendor','vendor_id','vendors','id'],
        ['resources','fk_resources_vendor','vendor_id','vendors','id'],
        ['resources','fk_resources_contract','contract_id','contracts','id'],
        ['work_order_resources','fk_wor_wo','work_order_id','work_orders','id'],
        ['work_order_resources','fk_wor_resource','resource_id','resources','id'],
        ['compliance_certificates','fk_cc_asset','asset_id','assets','id'],
        ['compliance_certificates','fk_cc_site','site_id','sites','id'],
        ['compliance_certificates','fk_cc_dutyholder','duty_holder_id','duty_holders','id'],
        ['meters','fk_meters_asset','asset_id','assets','id'],
        ['meters','fk_meters_site','site_id','sites','id'],
        ['meter_readings','fk_mr_meter','meter_id','meters','id'],
        ['leases','fk_leases_tenant','tenant_id','tenants','id'],
        ['leases','fk_leases_space','space_id','spaces','id'],
        ['assets','fk_assets_site','site_id','sites','id'],
        ['assets','fk_assets_type','asset_type_id','asset_types','id'],
        ['assets','fk_assets_manufacturer','manufacturer_id','manufacturers','id'],
        ['work_orders','fk_wo_site','site_id','sites','id'],
        ['udr_script_version','fk_usv_script','udr_script_id','udr_script','id'],
        ['udr_mapping_decision','fk_umd_script','udr_script_id','udr_script','id'],
        ['udr_test_result','fk_utr_script','udr_script_id','udr_script','id'],
        ['udr_canonical_column','fk_ucc_table','canonical_table','udr_canonical_table','canonical_table'],
        ['activity_log_step','fk_als_entry','entry_id','activity_log_entry','id'],
        ['activity_log_action','fk_ala_entry','entry_id','activity_log_entry','id'],
        ['activity_log_refinement','fk_alr_entry','entry_id','activity_log_entry','id'],
        ['saved_space_item','fk_ssi_space','space_id','saved_space','id'],
        ['document_chunks','fk_dc_document','document_id','documents','id']
    ];
BEGIN
    FOREACH fk SLICE 1 IN ARRAY fks LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables
                     WHERE table_schema='plenum_cafm' AND table_name=fk[1])
           AND EXISTS (SELECT 1 FROM information_schema.tables
                         WHERE table_schema='plenum_cafm' AND table_name=fk[4])
           AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                             WHERE constraint_schema='plenum_cafm' AND constraint_name=fk[2])
        THEN
            BEGIN
                EXECUTE format(
                    'ALTER TABLE plenum_cafm.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES plenum_cafm.%I(%I) ON DELETE SET NULL',
                    fk[1], fk[2], fk[3], fk[4], fk[5]);
            EXCEPTION WHEN others THEN
                RAISE NOTICE 'Skipped FK % (% .% -> %.%): %', fk[2], fk[1], fk[3], fk[4], fk[5], SQLERRM;
            END;
        END IF;
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- SECTION 5 — Indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_sites_org ON plenum_cafm.sites(org_id);
CREATE INDEX IF NOT EXISTS ix_sites_location ON plenum_cafm.sites(location_id);
CREATE INDEX IF NOT EXISTS ix_assets_site ON plenum_cafm.assets(site_id);
CREATE INDEX IF NOT EXISTS ix_assets_type ON plenum_cafm.assets(asset_type_id);
CREATE INDEX IF NOT EXISTS ix_assets_manufacturer ON plenum_cafm.assets(manufacturer_id);
CREATE INDEX IF NOT EXISTS ix_wo_site ON plenum_cafm.work_orders(site_id);
CREATE INDEX IF NOT EXISTS ix_cc_asset ON plenum_cafm.compliance_certificates(asset_id);
CREATE INDEX IF NOT EXISTS ix_cc_expiry ON plenum_cafm.compliance_certificates(expiry_date);
CREATE INDEX IF NOT EXISTS ix_meter_readings_meter ON plenum_cafm.meter_readings(meter_id);
CREATE INDEX IF NOT EXISTS ix_doc_chunks_anchor ON plenum_cafm.document_chunks(source_entity_type, source_entity_id);
CREATE INDEX IF NOT EXISTS ix_udr_synonym_term ON plenum_cafm.udr_synonym(lower(source_term));
CREATE INDEX IF NOT EXISTS ix_udr_script_org ON plenum_cafm.udr_script(org_id);
CREATE INDEX IF NOT EXISTS ix_udr_script_code ON plenum_cafm.udr_script(script_code);
CREATE INDEX IF NOT EXISTS ix_udr_rel_script ON plenum_cafm.udr_relationship(udr_script_id);
CREATE INDEX IF NOT EXISTS ix_udr_rel_from ON plenum_cafm.udr_relationship(from_table, from_column);
CREATE INDEX IF NOT EXISTS ix_mapping_decision_script ON plenum_cafm.udr_mapping_decision(udr_script_id);
CREATE INDEX IF NOT EXISTS ix_al_entry_org ON plenum_cafm.activity_log_entry(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_al_step_entry ON plenum_cafm.activity_log_step(entry_id);
CREATE INDEX IF NOT EXISTS ix_saved_space_org ON plenum_cafm.saved_space(org_id);
CREATE INDEX IF NOT EXISTS ix_saved_space_item_space ON plenum_cafm.saved_space_item(space_id);

-- ANN indexes for embeddings (IVFFlat — build after data load for best recall).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN
        BEGIN
            CREATE INDEX IF NOT EXISTS ix_doc_chunks_embedding
                ON plenum_cafm.document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
            CREATE INDEX IF NOT EXISTS ix_ontology_chunk_embedding
                ON plenum_cafm.udr_ontology_chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'Skipped ivfflat index creation: %', SQLERRM;
        END;
    END IF;
END $$;

-- ============================================================================
-- End UDR Phase I-A
-- ============================================================================
