-- The building graph: buildings → floors → spaces, assets → equipment / meters,
-- documents → certificates, contracts → work orders / invoices.
--
-- The Buildings screen used to read denormalised columns on plenum_cafm.sites — floors and
-- gfa_sqm as text, use_mix as JSONB — which meant the figure on screen was whatever someone
-- typed there, and it could disagree with the floors and spaces actually on record. Those
-- columns stay (a building with no floor rows still has a surveyed area), but the graph is
-- now the primary source: floors are counted, floor area is summed from spaces, and the use
-- split is spaces grouped by type.
--
-- Named to sort last: migrations are applied in filename order, and the ALTER statements
-- below extend tables (assets, compliance_certificates) that compliance_entity_bootstrap.sql
-- creates. As building_graph.sql this file ran first, so those two ALTERs hit a table that
-- did not exist yet and were skipped, leaving assets with no building_id.
--
-- Idempotent. Keys follow this deployment's convention — VARCHAR(50) business keys, the same
-- shape as plenum_cafm.sites.site_id and plenum_cafm.assets.id ("B-001", not a UUID). Where a
-- UUID-keyed table from udr_phase1_schema.sql already exists, CREATE TABLE IF NOT EXISTS
-- leaves it alone and the ADD COLUMN statements below still apply; the reader introspects
-- the real key column rather than assuming one (see engines/energy/building_rollup.py).

CREATE SCHEMA IF NOT EXISTS plenum_cafm;

-- ── the root ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.buildings (
    building_id     VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    site_id         VARCHAR(50),
    building_code   VARCHAR(50),
    name            VARCHAR(255),
    country         VARCHAR(100),
    country_code    VARCHAR(8),
    state           VARCHAR(120),
    city            VARCHAR(100),
    postcode        VARCHAR(40),
    use_type        VARCHAR(80),
    status          VARCHAR(50) DEFAULT 'active',
    -- Recorded fallbacks, used only where the graph has nothing to count or sum.
    floors_recorded     INTEGER,
    gfa_sqm_recorded    NUMERIC(14,2),
    eui_kwh_per_m2      NUMERIC(14,2),
    benchmark_kwh_per_m2 NUMERIC(14,2),
    benchmark_standard  VARCHAR(120),
    benchmark_standing  VARCHAR(40),
    benchmark_standing_note TEXT,
    metering_route      VARCHAR(160),
    metering_granularity VARCHAR(40),
    hoist_score         INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A pre-existing UUID-keyed buildings table gets the descriptive columns too.
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS name VARCHAR(255);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS country VARCHAR(100);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS country_code VARCHAR(8);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS state VARCHAR(120);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS city VARCHAR(100);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS postcode VARCHAR(40);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS use_type VARCHAR(80);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS floors_recorded INTEGER;
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS gfa_sqm_recorded NUMERIC(14,2);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS eui_kwh_per_m2 NUMERIC(14,2);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS benchmark_kwh_per_m2 NUMERIC(14,2);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS benchmark_standard VARCHAR(120);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS benchmark_standing VARCHAR(40);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS benchmark_standing_note TEXT;
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS metering_route VARCHAR(160);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS metering_granularity VARCHAR(40);
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS hoist_score INTEGER;

-- ── :HAS_FLOOR → :CONTAINS_SPACE ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.floors (
    floor_id     VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    building_id  VARCHAR(50),
    floor_code   VARCHAR(50),
    name         VARCHAR(255),
    level        INTEGER,
    gfa_sqm      NUMERIC(14,2),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.floors ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
ALTER TABLE plenum_cafm.floors ADD COLUMN IF NOT EXISTS level INTEGER;
ALTER TABLE plenum_cafm.floors ADD COLUMN IF NOT EXISTS gfa_sqm NUMERIC(14,2);
CREATE INDEX IF NOT EXISTS ix_floors_building ON plenum_cafm.floors (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.spaces (
    space_id     VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    floor_id     VARCHAR(50),
    building_id  VARCHAR(50),
    space_code   VARCHAR(50),
    name         VARCHAR(255),
    -- Commercial | Retail | Residential | … — grouped by area for the use split.
    space_type   VARCHAR(80),
    area_sqm     NUMERIC(14,2),
    tenant_ref   VARCHAR(120),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.spaces ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
ALTER TABLE plenum_cafm.spaces ADD COLUMN IF NOT EXISTS space_type VARCHAR(80);
ALTER TABLE plenum_cafm.spaces ADD COLUMN IF NOT EXISTS area_sqm NUMERIC(14,2);
ALTER TABLE plenum_cafm.spaces ADD COLUMN IF NOT EXISTS tenant_ref VARCHAR(120);
CREATE INDEX IF NOT EXISTS ix_spaces_floor ON plenum_cafm.spaces (floor_id);
CREATE INDEX IF NOT EXISTS ix_spaces_building ON plenum_cafm.spaces (building_id);

-- ── :HAS_ASSET → :HAS_EQUIPMENT / :METERED_BY ────────────────────────────────────────
-- assets already exists and keys to site_id; the graph hangs it off the building.
ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
CREATE INDEX IF NOT EXISTS ix_assets_building ON plenum_cafm.assets (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.equipment (
    equipment_id VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    asset_id     VARCHAR(50),
    equipment_code VARCHAR(50),
    name         VARCHAR(255),
    manufacturer VARCHAR(150),
    model        VARCHAR(150),
    serial       VARCHAR(120),
    installed_on DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_equipment_asset ON plenum_cafm.equipment (asset_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.meters (
    meter_id     VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    asset_id     VARCHAR(50),
    building_id  VARCHAR(50),
    meter_code   VARCHAR(50),
    meter_type   VARCHAR(40),
    mpan_mprn    VARCHAR(60),
    unit         VARCHAR(20),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.meters ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
ALTER TABLE plenum_cafm.meters ADD COLUMN IF NOT EXISTS mpan_mprn VARCHAR(60);
CREATE INDEX IF NOT EXISTS ix_meters_asset ON plenum_cafm.meters (asset_id);
CREATE INDEX IF NOT EXISTS ix_meters_building ON plenum_cafm.meters (building_id);

-- ── :HAS_DOC → :EVIDENCES ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.documents (
    document_id  VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    building_id  VARCHAR(50),
    site_id      VARCHAR(50),
    doc_type     VARCHAR(80),
    title        VARCHAR(255),
    file_name    VARCHAR(255),
    blob_url     TEXT,
    uploaded_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_documents_building ON plenum_cafm.documents (building_id);

-- compliance_certificates is the certificate register; the graph reaches it from documents.
ALTER TABLE plenum_cafm.compliance_certificates ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
CREATE INDEX IF NOT EXISTS ix_compliance_certificates_building
    ON plenum_cafm.compliance_certificates (building_id);

-- ── :UNDER_CONTRACT → :RAISED_UNDER / :INVOICED_BY ───────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.contracts (
    contract_id  VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    building_id  VARCHAR(50),
    vendor_id    VARCHAR(50),
    contract_ref VARCHAR(120),
    sla_response_hrs INTEGER,
    penalty_clause TEXT,
    annual_value NUMERIC(16,2),
    currency     VARCHAR(8),
    start_date   DATE,
    end_date     DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS building_id VARCHAR(50);
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS sla_response_hrs INTEGER;
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS penalty_clause TEXT;
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS annual_value NUMERIC(16,2);
CREATE INDEX IF NOT EXISTS ix_contracts_building ON plenum_cafm.contracts (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.work_orders (
    work_order_id VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    building_id  VARCHAR(50),
    asset_id     VARCHAR(50),
    contract_id  VARCHAR(50),
    vendor_id    VARCHAR(50),
    wo_code      VARCHAR(50),
    title        VARCHAR(255),
    status       VARCHAR(40),
    priority     VARCHAR(40),
    raised_at    TIMESTAMPTZ,
    closed_at    TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_work_orders_building ON plenum_cafm.work_orders (building_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_asset ON plenum_cafm.work_orders (asset_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_contract ON plenum_cafm.work_orders (contract_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.invoices (
    invoice_id   VARCHAR(50) PRIMARY KEY,
    organization_id INTEGER,
    vendor_id    VARCHAR(50),
    contract_id  VARCHAR(50),
    building_id  VARCHAR(50),
    invoice_ref  VARCHAR(120),
    amount       NUMERIC(16,2),
    currency     VARCHAR(8),
    issued_on    DATE,
    status       VARCHAR(40),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_invoices_vendor ON plenum_cafm.invoices (vendor_id);
CREATE INDEX IF NOT EXISTS ix_invoices_contract ON plenum_cafm.invoices (contract_id);

COMMENT ON COLUMN plenum_cafm.buildings.floors_recorded
    IS 'Surveyed floor count, used only when no rows exist in plenum_cafm.floors.';
COMMENT ON COLUMN plenum_cafm.buildings.gfa_sqm_recorded
    IS 'Surveyed floor area, used only when plenum_cafm.spaces sums to nothing.';
COMMENT ON COLUMN plenum_cafm.spaces.space_type
    IS 'Drives the use / floor-area split: spaces grouped by type, weighted by area_sqm.';
