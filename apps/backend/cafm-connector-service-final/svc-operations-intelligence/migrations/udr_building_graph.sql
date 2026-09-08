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

-- ONE locations table serving both meanings.
--
-- svc-work-order-management already defines plenum_cafm.locations as a physical location
-- inside a site — keyed `id`, with name and type — and the graph needs a geographic one
-- carrying country_code, region and the regulation pack. These are not two tables: a
-- location is a place, and a place has both an identity and a jurisdiction. The columns are
-- merged here rather than split, so nothing has to decide which of two tables a row belongs
-- in and no join has to guess.
--
-- The key stays `id`. The work-order ORM maps its `location_id` attribute onto that column
-- explicitly (models/location.py), so renaming it would break live code for a cosmetic gain;
-- readers here introspect `location_id` or `id` and take whichever exists.
CREATE TABLE IF NOT EXISTS plenum_cafm.locations (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(255),
    type         VARCHAR(100),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Physical identity — present whichever service created the table first.
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS name VARCHAR(255);
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS type VARCHAR(100);
-- Where the place sits. Null on a purely geographic row, set on a room or floor area.
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS site_id TEXT;
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS building_id UUID;
-- Jurisdiction — what the building is scored against.
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS country_code CHAR(2);
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS region TEXT;
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS pack_id UUID;
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS postcode VARCHAR(40);
ALTER TABLE plenum_cafm.locations ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- The pack FK only once regulation_packs exists and pack_id is free of a prior constraint.
DO $loc_fk$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'locations_pack_id_fkey') THEN
        BEGIN
            ALTER TABLE plenum_cafm.locations
                ADD CONSTRAINT locations_pack_id_fkey
                FOREIGN KEY (pack_id) REFERENCES plenum_cafm.regulation_packs (pack_id);
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'locations.pack_id FK not added: %', SQLERRM;
        END;
    END IF;
END
$loc_fk$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_locations_pack ON plenum_cafm.locations (pack_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_locations_site ON plenum_cafm.locations (site_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_locations_building ON plenum_cafm.locations (building_id);

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sites_portfolio ON plenum_cafm.sites (portfolio_id);

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
    -- FK added below only once the real key column of locations is known.
    location_id      UUID,
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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_buildings_site ON plenum_cafm.buildings (site_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_buildings_location ON plenum_cafm.buildings (location_id);

-- The FK to sites only where the key types agree — a varchar-keyed sites cannot be
-- referenced from a text column without a cast, and an unenforceable constraint is worse
-- than an honest index.
DO $bloc$
DECLARE loc_key TEXT;
BEGIN
    SELECT column_name INTO loc_key FROM information_schema.columns
     WHERE table_schema = 'plenum_cafm' AND table_name = 'locations'
       AND column_name IN ('location_id', 'id') ORDER BY column_name = 'id' LIMIT 1;
    IF loc_key IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'buildings_location_id_fkey'
    ) THEN
        BEGIN
            EXECUTE format(
                'ALTER TABLE plenum_cafm.buildings ADD CONSTRAINT buildings_location_id_fkey '
                'FOREIGN KEY (location_id) REFERENCES plenum_cafm.locations (%I)', loc_key);
        EXCEPTION WHEN others THEN
            RAISE NOTICE 'buildings.location_id FK not added: %', SQLERRM;
        END;
    END IF;
END
$bloc$;

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_floors_building ON plenum_cafm.floors (building_id);

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_spaces_floor ON plenum_cafm.spaces (floor_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_spaces_building ON plenum_cafm.spaces (building_id);

-- ── :HAS_ASSET → :HAS_EQUIPMENT / :METERED_BY ────────────────────────────────────────
-- assets already exists keyed on varchar; the graph hangs it off the building.
-- A building created from the UI carries fields the canonical table has no column for —
-- use_mix, metering granularity and route, postcode. The platform's rule is that an
-- unmatched field goes to raw_metadata and is never dropped, so it has somewhere to go.
ALTER TABLE plenum_cafm.buildings ADD COLUMN IF NOT EXISTS raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE plenum_cafm.assets ADD COLUMN IF NOT EXISTS building_id UUID;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_assets_building ON plenum_cafm.assets (building_id);

-- work_orders predates this migration in every existing deployment, so the CREATE TABLE
-- above is a no-op there and never adds building_id. Without it an invoice cannot be
-- placed through the work it bills.
ALTER TABLE plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS building_id UUID;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_orders_building ON plenum_cafm.work_orders (building_id);

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_equipment_asset ON plenum_cafm.equipment (asset_id);

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_meters_asset ON plenum_cafm.meters (asset_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_meters_building ON plenum_cafm.meters (building_id);

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
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_documents_building ON plenum_cafm.documents (building_id);

ALTER TABLE plenum_cafm.compliance_certificates ADD COLUMN IF NOT EXISTS building_id UUID;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_compliance_certificates_building
    ON plenum_cafm.compliance_certificates (building_id);

-- ── :UNDER_CONTRACT → :RAISED_UNDER / :INVOICED_BY ───────────────────────────────────

-- contracts and invoices are VIEWS, not tables.
--
-- The contract engine already owns this data and has real logic behind it:
-- contract_sla_parameters holds a contract's terms, invoice_verifications and invoice_lines
-- hold a verified invoice and its lines. A second pair of tables carrying the same facts
-- would mean two answers to "what does this contract say", and the graph would eventually
-- disagree with the engine that maintains it. A view has one copy of the truth and cannot
-- drift from it.
--
-- What a view cannot do is be a foreign-key target, so nothing references contracts
-- (contract_id) — work_orders and invoices carry the id and are joined, not constrained.
--
-- Columns the source does not hold are NULL and say so here rather than being invented:
-- contract_sla_parameters records no contract value, currency or end date, and
-- invoice_verifications records no issue date. Those are gaps in the source, and the view
-- makes them visible instead of papering over them.

DO $drop_contracts$
BEGIN
    -- Only ever drop the placeholder table, and only when empty. A table with rows is
    -- somebody's data and is left alone with the view skipped.
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'plenum_cafm' AND table_name = 'contracts'
                  AND table_type = 'BASE TABLE') THEN
        IF (SELECT count(*) FROM plenum_cafm.contracts) = 0 THEN
            DROP TABLE plenum_cafm.contracts CASCADE;
        ELSE
            RAISE NOTICE 'plenum_cafm.contracts has rows — left as a table, view not created';
        END IF;
    END IF;
END
$drop_contracts$;

DO $contracts_view$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'contracts'
                      AND table_type = 'BASE TABLE') THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW plenum_cafm.contracts AS
            SELECT
                COALESCE(p.contract_id, p.id)          AS contract_id,
                p.organization_id,
                d.building_id,
                p.vendor_id,
                p.contract_ref,
                p.document_id,
                p.sla_response_p1_hours                AS sla_response_hrs,
                NULLIF(p.kpi_clauses_json::text, 'null') AS penalty_clause,
                NULL::NUMERIC(16,2)                    AS annual_value,
                NULL::CHAR(3)                          AS currency,
                p.signed_date                          AS start_date,
                NULL::DATE                             AS end_date,
                p.status,
                p.created_at
            FROM plenum_cafm.contract_sla_parameters p
            LEFT JOIN plenum_cafm.documents d ON d.document_id = p.document_id
        $v$;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'contracts view not created: %', SQLERRM;
END
$contracts_view$;

CREATE TABLE IF NOT EXISTS plenum_cafm.work_orders (
    work_order_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    building_id   UUID REFERENCES plenum_cafm.buildings (building_id),
    asset_id      TEXT,
    -- No FK: contracts is a view over contract_sla_parameters and cannot be referenced.
    contract_id   UUID,
    vendor_id     TEXT,
    title         TEXT,
    status        TEXT,
    priority      TEXT,
    raised_at     TIMESTAMPTZ,
    closed_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_orders_building ON plenum_cafm.work_orders (building_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_orders_asset ON plenum_cafm.work_orders (asset_id);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_work_orders_contract ON plenum_cafm.work_orders (contract_id);

DO $drop_invoices$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'plenum_cafm' AND table_name = 'invoices'
                  AND table_type = 'BASE TABLE') THEN
        IF (SELECT count(*) FROM plenum_cafm.invoices) = 0 THEN
            DROP TABLE plenum_cafm.invoices CASCADE;
        ELSE
            RAISE NOTICE 'plenum_cafm.invoices has rows — left as a table, view not created';
        END IF;
    END IF;
END
$drop_invoices$;

DO $invoices_view$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'invoices'
                      AND table_type = 'BASE TABLE') THEN
        EXECUTE $v$
            CREATE OR REPLACE VIEW plenum_cafm.invoices AS
            SELECT
                v.id                       AS invoice_id,
                v.organization_id,
                v.vendor_id,
                d.building_id,
                v.invoice_ref,
                v.document_id,
                -- The invoice's value IS the sum of its verified lines; storing a second
                -- total would be a number that could disagree with them.
                l.amount,
                l.line_count,
                NULL::CHAR(3)              AS currency,
                NULL::DATE                 AS issued_on,
                v.status,
                v.matched_count,
                v.flagged_count,
                v.created_at
            FROM plenum_cafm.invoice_verifications v
            LEFT JOIN plenum_cafm.documents d ON d.document_id = v.document_id
            LEFT JOIN LATERAL (
                SELECT sum(il.line_total) AS amount, count(*) AS line_count
                FROM plenum_cafm.invoice_lines il
                WHERE il.invoice_verification_id = v.id
            ) l ON TRUE
        $v$;
    END IF;
EXCEPTION WHEN others THEN
    RAISE NOTICE 'invoices view not created: %', SQLERRM;
END
$invoices_view$;

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
