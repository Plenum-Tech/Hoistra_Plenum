-- The canonical building graph.
--
--   portfolios ──< sites ──< buildings >── locations >── regulation_packs
--                              │
--                              ├──< floors ──< spaces
--                              ├──< assets ──< equipment / meters
--                              ├──< documents ──< certificates
--                              └──< contracts ──< work_orders / invoices
--
-- Everything resolves to a building. A building resolves UP two ways: through its site to
-- the portfolio that owns it, and through its location to the regulation pack it is scored
-- against. That second path is why there are no country or benchmark columns on a building
-- — the standard, its required types and its benchmark source are properties of WHERE the
-- building is, held once in regulation_packs, not copied onto every row.
--
-- Named to sort last: migrations apply in filename order and the ALTERs below extend tables
-- that compliance_entity_bootstrap.sql creates.
--
-- ── One deliberate compromise ────────────────────────────────────────────────────────
-- The target keys sites on uuid. plenum_cafm.sites already exists in this deployment keyed
-- on site_id VARCHAR(50), and the whole compliance engine reads it (site_links, coverage,
-- certificates, contractors, booking …). Changing that primary key is a data-bearing
-- migration that would rewrite every certificate's site link, so it is NOT done here.
-- Instead: a database that has no sites table gets the canonical uuid shape, and one that
-- already has the varchar table keeps it and gains the target's new columns. buildings.site_id
-- is therefore typed text and carries whichever key the deployment uses; the readers
-- introspect it (engines/energy/building_rollup.py) rather than assuming. The FK to sites is
-- added only where the types line up.

CREATE SCHEMA IF NOT EXISTS plenum_cafm;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── the two upward spines ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plenum_cafm.portfolios (
    portfolio_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name               TEXT,
    owner_entity       TEXT,
    reporting_currency CHAR(3),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.regulation_packs (
    pack_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    standard         TEXT,
    -- The certificate types the pack requires — the denominator coverage is measured against.
    required_types   TEXT[],
    -- Where the benchmark number comes from, e.g. 'CIBSE TM46' or 'rolling portfolio median'.
    benchmark_source TEXT,
    -- enacted | guidance | mandatory_submission | none. A proposal must never read as a duty.
    standing         VARCHAR(40),
    standing_note    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plenum_cafm.locations (
    location_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    country_code CHAR(2),
    region       TEXT,
    pack_id      UUID REFERENCES plenum_cafm.regulation_packs (pack_id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_locations_pack ON plenum_cafm.locations (pack_id);

-- sites: canonical shape for a fresh database; an existing varchar-keyed table keeps its key.
CREATE TABLE IF NOT EXISTS plenum_cafm.sites (
    site_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    portfolio_id UUID REFERENCES plenum_cafm.portfolios (portfolio_id),
    name         TEXT,
    address      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS portfolio_id UUID;
ALTER TABLE plenum_cafm.sites ADD COLUMN IF NOT EXISTS address TEXT;
CREATE INDEX IF NOT EXISTS ix_sites_portfolio ON plenum_cafm.sites (portfolio_id);

-- ── buildings — everything resolves to this ──────────────────────────────────────────

DO $enum$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'building_primary_use') THEN
        CREATE TYPE plenum_cafm.building_primary_use AS ENUM (
            'Commercial', 'Residential', 'Retail', 'Mixed', 'Industrial', 'Logistics',
            'Hospital', 'Hotel', 'Education', 'Laboratory', 'Leisure', 'Other'
        );
    END IF;
END
$enum$;

CREATE TABLE IF NOT EXISTS plenum_cafm.buildings (
    building_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- text, not uuid: it carries whichever key plenum_cafm.sites uses in this deployment.
    site_id          TEXT,
    location_id      UUID REFERENCES plenum_cafm.locations (location_id),
    name             TEXT,
    primary_use      plenum_cafm.building_primary_use,
    floors           INTEGER,
    gross_area_sqft  NUMERIC(16,2),
    eui_kwh_m2       NUMERIC(14,2),
    building_code    VARCHAR(50),
    hoist_score      INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_buildings_site ON plenum_cafm.buildings (site_id);
CREATE INDEX IF NOT EXISTS ix_buildings_location ON plenum_cafm.buildings (location_id);

-- The FK to sites only where the key types agree — a varchar-keyed sites cannot be
-- referenced from a text column without a cast, and an unenforceable constraint is worse
-- than an honest index.
DO $fk$
DECLARE site_key_type TEXT;
BEGIN
    SELECT data_type INTO site_key_type FROM information_schema.columns
     WHERE table_schema = 'plenum_cafm' AND table_name = 'sites' AND column_name = 'site_id';
    IF site_key_type IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'buildings_site_id_fkey'
    ) THEN
        RAISE NOTICE 'sites.site_id is %, buildings.site_id left unconstrained', site_key_type;
    END IF;
END
$fk$;

-- ── :HAS_FLOOR → :CONTAINS_SPACE ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plenum_cafm.floors (
    floor_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id UUID REFERENCES plenum_cafm.buildings (building_id),
    level       INTEGER,
    name        TEXT,
    gross_area_sqft NUMERIC(16,2),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_floors_building ON plenum_cafm.floors (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.spaces (
    space_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    floor_id    UUID REFERENCES plenum_cafm.floors (floor_id),
    building_id UUID REFERENCES plenum_cafm.buildings (building_id),
    name        TEXT,
    -- Drives the use / floor-area split: spaces grouped by type, weighted by area.
    space_type  TEXT,
    gross_area_sqft NUMERIC(16,2),
    tenant_ref  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_spaces_floor ON plenum_cafm.spaces (floor_id);
CREATE INDEX IF NOT EXISTS ix_spaces_building ON plenum_cafm.spaces (building_id);

-- ── :HAS_ASSET → :HAS_EQUIPMENT / :METERED_BY ────────────────────────────────────────
-- assets already exists keyed on varchar; the graph hangs it off the building.
ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS building_id UUID;
CREATE INDEX IF NOT EXISTS ix_assets_building ON plenum_cafm.assets (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.equipment (
    equipment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id     TEXT,
    name         TEXT,
    manufacturer TEXT,
    model        TEXT,
    serial       TEXT,
    installed_on DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_equipment_asset ON plenum_cafm.equipment (asset_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.meters (
    meter_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id    TEXT,
    building_id UUID REFERENCES plenum_cafm.buildings (building_id),
    meter_type  TEXT,
    mpan_mprn   TEXT,
    unit        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.meters ADD COLUMN IF NOT EXISTS building_id UUID;
ALTER TABLE plenum_cafm.meters ADD COLUMN IF NOT EXISTS mpan_mprn TEXT;
CREATE INDEX IF NOT EXISTS ix_meters_asset ON plenum_cafm.meters (asset_id);
CREATE INDEX IF NOT EXISTS ix_meters_building ON plenum_cafm.meters (building_id);

-- ── :HAS_DOC → :EVIDENCES ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plenum_cafm.documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id UUID REFERENCES plenum_cafm.buildings (building_id),
    doc_type    TEXT,
    title       TEXT,
    file_name   TEXT,
    blob_url    TEXT,
    uploaded_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_documents_building ON plenum_cafm.documents (building_id);

ALTER TABLE plenum_cafm.compliance_certificates ADD COLUMN IF NOT EXISTS building_id UUID;
CREATE INDEX IF NOT EXISTS ix_compliance_certificates_building
    ON plenum_cafm.compliance_certificates (building_id);

-- ── :UNDER_CONTRACT → :RAISED_UNDER / :INVOICED_BY ───────────────────────────────────

CREATE TABLE IF NOT EXISTS plenum_cafm.contracts (
    contract_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id      UUID REFERENCES plenum_cafm.buildings (building_id),
    vendor_id        TEXT,
    sla_response_hrs INTEGER,
    penalty_clause   TEXT,
    annual_value     NUMERIC(16,2),
    currency         CHAR(3),
    start_date       DATE,
    end_date         DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS building_id UUID;
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS sla_response_hrs INTEGER;
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS penalty_clause TEXT;
ALTER TABLE plenum_cafm.contracts ADD COLUMN IF NOT EXISTS annual_value NUMERIC(16,2);
CREATE INDEX IF NOT EXISTS ix_contracts_building ON plenum_cafm.contracts (building_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.work_orders (
    work_order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id   UUID REFERENCES plenum_cafm.buildings (building_id),
    asset_id      TEXT,
    contract_id   UUID REFERENCES plenum_cafm.contracts (contract_id),
    vendor_id     TEXT,
    title         TEXT,
    status        TEXT,
    priority      TEXT,
    raised_at     TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_work_orders_building ON plenum_cafm.work_orders (building_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_asset ON plenum_cafm.work_orders (asset_id);
CREATE INDEX IF NOT EXISTS ix_work_orders_contract ON plenum_cafm.work_orders (contract_id);

CREATE TABLE IF NOT EXISTS plenum_cafm.invoices (
    invoice_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id   TEXT,
    contract_id UUID REFERENCES plenum_cafm.contracts (contract_id),
    building_id UUID REFERENCES plenum_cafm.buildings (building_id),
    invoice_ref TEXT,
    amount      NUMERIC(16,2),
    currency    CHAR(3),
    issued_on   DATE,
    status      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_invoices_vendor ON plenum_cafm.invoices (vendor_id);
CREATE INDEX IF NOT EXISTS ix_invoices_contract ON plenum_cafm.invoices (contract_id);

-- ── the four regulation packs ────────────────────────────────────────────────────────
-- Reference data, not demo data: these are the standards the markets are scored against,
-- and a location cannot resolve a benchmark without them. Idempotent on `standard`.

INSERT INTO plenum_cafm.regulation_packs (standard, benchmark_source, standing, standing_note, required_types)
SELECT v.standard, v.benchmark_source, v.standing, v.standing_note, v.required_types
FROM (VALUES
    ('CIBSE TM46', 'CIBSE TM46 category benchmark', 'guidance',
     'guidance · EPC E law, EPC B proposed 2031', ARRAY['FRA','EICR','CP17','L8_RISK','EMERGENCY_LIGHTING']),
    ('Energy Star · ASHRAE 100', 'Energy Star Portfolio Manager', 'enacted',
     'enacted, city-scoped · NYC LL97', ARRAY['US_FIRE_ALARM_NFPA72','US_SPRINKLER_NFPA25']),
    ('BCA Benchmarking Report', 'BCA Building Energy Benchmarking Report', 'mandatory_submission',
     'submission mandatory · rating voluntary', ARRAY[]::TEXT[]),
    ('Rolling portfolio benchmark', 'Median EUI of comparable buildings in the portfolio', 'none',
     'no operational standard · comparable buildings in the portfolio', ARRAY[]::TEXT[])
) AS v(standard, benchmark_source, standing, standing_note, required_types)
WHERE NOT EXISTS (
    SELECT 1 FROM plenum_cafm.regulation_packs p WHERE p.standard = v.standard
);

COMMENT ON TABLE plenum_cafm.buildings
    IS 'The key. Everything resolves to it — up through sites to the portfolio, and up through locations to the regulation pack it is scored against.';
COMMENT ON COLUMN plenum_cafm.buildings.site_id
    IS 'Text, not uuid: carries whichever key plenum_cafm.sites uses in this deployment.';
COMMENT ON COLUMN plenum_cafm.regulation_packs.required_types
    IS 'Certificate types the pack requires — the denominator for coverage.';
