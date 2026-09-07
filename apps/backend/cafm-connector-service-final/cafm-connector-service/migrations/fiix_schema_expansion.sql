-- =============================================================================
-- FIIX-INSPIRED SCHEMA EXPANSION
-- Run this script directly on your PostgreSQL database (plenum_agent).
-- All new columns are nullable (or have safe defaults) — no data loss.
-- Requires PostgreSQL 9.6+ for ADD COLUMN IF NOT EXISTS.
-- =============================================================================

SET search_path = plenum_cafm;

-- Enable pgcrypto for gen_random_uuid() if not already enabled
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- SECTION 1: NEW REFERENCE / LOOKUP TABLES
-- (must be created before any ALTER TABLE that FKs into them)
-- =============================================================================

-- 1.1 priorities
CREATE TABLE IF NOT EXISTS priorities (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    color_hex       VARCHAR(10),
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_priorities_org_name UNIQUE (organization_id, name)
);

-- 1.2 maintenance_types
CREATE TABLE IF NOT EXISTS maintenance_types (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_maintenance_types_org_name UNIQUE (organization_id, name)
);

-- 1.3 work_order_statuses
CREATE TABLE IF NOT EXISTS work_order_statuses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    is_closed_state BOOLEAN     NOT NULL DEFAULT FALSE,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wo_statuses_org_name UNIQUE (organization_id, name)
);

-- 1.4 charge_departments
CREATE TABLE IF NOT EXISTS charge_departments (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    code            VARCHAR(100) NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_charge_departments_org_code UNIQUE (organization_id, code)
);

-- 1.5 projects
CREATE TABLE IF NOT EXISTS projects (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    site_id         UUID        REFERENCES locations(id) ON DELETE SET NULL,
    status          VARCHAR(50) NOT NULL DEFAULT 'active',
    start_date      DATE,
    end_date        DATE,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_projects_org_name UNIQUE (organization_id, name)
);

-- 1.6 bom_groups
CREATE TABLE IF NOT EXISTS bom_groups (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bom_groups_org_name UNIQUE (organization_id, name)
);

-- 1.7 meter_reading_units
CREATE TABLE IF NOT EXISTS meter_reading_units (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    precision       INTEGER     NOT NULL DEFAULT 2,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_meter_reading_units_org_symbol UNIQUE (organization_id, symbol)
);

-- 1.8 task_groups
CREATE TABLE IF NOT EXISTS task_groups (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_task_groups_org_name UNIQUE (organization_id, name)
);

-- 1.9 purchase_order_statuses
CREATE TABLE IF NOT EXISTS purchase_order_statuses (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,
    is_closed_state BOOLEAN     NOT NULL DEFAULT FALSE,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_po_statuses_org_name UNIQUE (organization_id, name)
);


-- =============================================================================
-- SECTION 2: ALTER EXISTING TABLES — ADD NEW COLUMNS
-- =============================================================================

-- 2.1 users — Fiix: strTelephone2, strPersonnelCode, dblHourlyRate, bolGroup
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS phone2          VARCHAR(50),
    ADD COLUMN IF NOT EXISTS personnel_code  VARCHAR(100),
    ADD COLUMN IF NOT EXISTS hourly_rate     NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS is_group        BOOLEAN NOT NULL DEFAULT FALSE;


-- 2.2 locations — Fiix: address, city, province, postal code, country, timezone
ALTER TABLE locations
    ADD COLUMN IF NOT EXISTS address     TEXT,
    ADD COLUMN IF NOT EXISTS city        VARCHAR(150),
    ADD COLUMN IF NOT EXISTS province    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS postal_code VARCHAR(20),
    ADD COLUMN IF NOT EXISTS country     VARCHAR(100),
    ADD COLUMN IF NOT EXISTS timezone    VARCHAR(100);


-- 2.3 asset_categories — Fiix: intParentID (self-referential hierarchy)
ALTER TABLE asset_categories
    ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES asset_categories(id) ON DELETE SET NULL;


-- 2.4 assets — many new Fiix fields
ALTER TABLE assets
    -- Identity
    ADD COLUMN IF NOT EXISTS barcode            VARCHAR(255),
    ADD COLUMN IF NOT EXISTS inventory_code     VARCHAR(150),
    -- Make / model aliases (Fiix stores strMake / strModel)
    ADD COLUMN IF NOT EXISTS make               VARCHAR(150),
    ADD COLUMN IF NOT EXISTS model              VARCHAR(150),
    -- Status flags
    ADD COLUMN IF NOT EXISTS is_online          BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS is_site            BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS criticality        VARCHAR(50),
    -- Notes
    ADD COLUMN IF NOT EXISTS notes              TEXT,
    -- Asset hierarchy (Fiix: intAssetParentID)
    ADD COLUMN IF NOT EXISTS parent_asset_id    UUID REFERENCES assets(id) ON DELETE SET NULL,
    -- Physical storage location within facility
    ADD COLUMN IF NOT EXISTS aisle              VARCHAR(100),
    ADD COLUMN IF NOT EXISTS row                VARCHAR(100),
    ADD COLUMN IF NOT EXISTS bin_number         VARCHAR(100),
    ADD COLUMN IF NOT EXISTS stock_location     VARCHAR(255),
    -- Cost / org classification
    ADD COLUMN IF NOT EXISTS charge_department_id UUID REFERENCES charge_departments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS project_id           UUID REFERENCES projects(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS account_code         VARCHAR(100),
    -- Misc
    ADD COLUMN IF NOT EXISTS timezone           VARCHAR(100),
    ADD COLUMN IF NOT EXISTS raw_metadata       JSONB;


-- 2.5 asset_readings — Fiix: MeterReadingUnit FK, intSubmittedByUserID
ALTER TABLE asset_readings
    ADD COLUMN IF NOT EXISTS unit_id      UUID REFERENCES meter_reading_units(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS submitted_by UUID REFERENCES users(id) ON DELETE SET NULL;


-- 2.6 vendors — Fiix: strCode, city, province, postal_code, country, phone, fax, website, notes, status
ALTER TABLE vendors
    ADD COLUMN IF NOT EXISTS vendor_code VARCHAR(100),
    ADD COLUMN IF NOT EXISTS city        VARCHAR(150),
    ADD COLUMN IF NOT EXISTS province    VARCHAR(150),
    ADD COLUMN IF NOT EXISTS postal_code VARCHAR(20),
    ADD COLUMN IF NOT EXISTS country     VARCHAR(100),
    ADD COLUMN IF NOT EXISTS phone       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS fax         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS website     VARCHAR(500),
    ADD COLUMN IF NOT EXISTS notes       TEXT,
    ADD COLUMN IF NOT EXISTS status      VARCHAR(50) NOT NULL DEFAULT 'active';

-- Unique constraint on org + vendor_code (vendor_code is nullable, so only enforce when set)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_vendors_org_code' AND conrelid = 'plenum_cafm.vendors'::regclass
    ) THEN
        ALTER TABLE vendors
            ADD CONSTRAINT uq_vendors_org_code UNIQUE (organization_id, vendor_code);
    END IF;
END;
$$;


-- 2.7 maintenance_plans — Fiix: strCode, strDescription, FKs for type/priority/task_group/project/dept
ALTER TABLE maintenance_plans
    ADD COLUMN IF NOT EXISTS sm_code               VARCHAR(150),
    ADD COLUMN IF NOT EXISTS description           TEXT,
    ADD COLUMN IF NOT EXISTS maintenance_type_id   UUID REFERENCES maintenance_types(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS priority_id           UUID REFERENCES priorities(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS task_group_id         UUID REFERENCES task_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS status                VARCHAR(50) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS project_id            UUID REFERENCES projects(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS charge_department_id  UUID REFERENCES charge_departments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMP NOT NULL DEFAULT NOW();


-- 2.8 work_orders — Fiix: strCode, problem, solution, completion_notes, FK overrides
ALTER TABLE work_orders
    ADD COLUMN IF NOT EXISTS wo_code               VARCHAR(150),
    ADD COLUMN IF NOT EXISTS problem               TEXT,
    ADD COLUMN IF NOT EXISTS solution              TEXT,
    ADD COLUMN IF NOT EXISTS completion_notes      TEXT,
    ADD COLUMN IF NOT EXISTS priority_id           UUID REFERENCES priorities(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS status_id             UUID REFERENCES work_order_statuses(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS maintenance_type      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS maintenance_type_id   UUID REFERENCES maintenance_types(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS requested_by_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS completed_by_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS estimated_hours       NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS actual_hours          NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS charge_department_id  UUID REFERENCES charge_departments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS project_id            UUID REFERENCES projects(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS task_group_id         UUID REFERENCES task_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS maintenance_plan_id   UUID REFERENCES maintenance_plans(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at            TIMESTAMP NOT NULL DEFAULT NOW();


-- 2.9 work_order_tasks — Fiix: intTaskGroupControlID, intTaskType, intOrder, time tracking
ALTER TABLE work_order_tasks
    ADD COLUMN IF NOT EXISTS task_group_id   UUID REFERENCES task_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS task_type       INTEGER,
    ADD COLUMN IF NOT EXISTS sort_order      INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS completed_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS estimated_hours NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS actual_hours    NUMERIC(10, 2);


-- 2.10 work_order_history — add notes field
ALTER TABLE work_order_history
    ADD COLUMN IF NOT EXISTS notes TEXT;


-- 2.11 spare_parts — Fiix: qtyMaxQty, unit_of_measure, storage location, supplier, bom_group
ALTER TABLE spare_parts
    ADD COLUMN IF NOT EXISTS max_quantity    INTEGER,
    ADD COLUMN IF NOT EXISTS unit_of_measure VARCHAR(50),
    ADD COLUMN IF NOT EXISTS aisle           VARCHAR(100),
    ADD COLUMN IF NOT EXISTS row             VARCHAR(100),
    ADD COLUMN IF NOT EXISTS bin_number      VARCHAR(100),
    ADD COLUMN IF NOT EXISTS supplier_id     UUID REFERENCES vendors(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS bom_group_id    UUID REFERENCES bom_groups(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS updated_at      TIMESTAMP NOT NULL DEFAULT NOW();


-- 2.12 inventory_transactions — Fiix: dblUnitCost, dblTotalCost, notes
ALTER TABLE inventory_transactions
    ADD COLUMN IF NOT EXISTS unit_cost  NUMERIC(18, 2),
    ADD COLUMN IF NOT EXISTS total_cost NUMERIC(18, 2),
    ADD COLUMN IF NOT EXISTS notes      TEXT;


-- 2.13 work_order_parts — Fiix: intAssetID, unit_cost
ALTER TABLE work_order_parts
    ADD COLUMN IF NOT EXISTS asset_id  UUID REFERENCES assets(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS unit_cost NUMERIC(18, 2);


-- =============================================================================
-- SECTION 3: NEW ENTITY TABLES
-- =============================================================================

-- 3.1 asset_warranties — Fiix: Warranty object
CREATE TABLE IF NOT EXISTS asset_warranties (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID        NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    description     TEXT,
    provider        VARCHAR(255),
    start_date      DATE,
    expiry_date     DATE,
    coverage_notes  TEXT,
    document_url    TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_asset_warranties_asset_id ON asset_warranties(asset_id);


-- 3.2 user_certifications — Fiix: UserCertification (licences / qualifications)
CREATE TABLE IF NOT EXISTS user_certifications (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    issued_by       VARCHAR(255),
    issued_date     DATE,
    expiry_date     DATE,
    document_url    TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_user_certifications_user_id ON user_certifications(user_id);


-- 3.3 scheduled_tasks — Fiix: ScheduledTask (individual task within a TaskGroup or SM plan)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    task_group_id        UUID        REFERENCES task_groups(id) ON DELETE CASCADE,
    maintenance_plan_id  UUID        REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    description          TEXT        NOT NULL,
    estimated_hours      NUMERIC(10, 2),
    task_type            INTEGER,                            -- 0=text, 1=numeric, 2=checkbox, etc.
    sort_order           INTEGER     NOT NULL DEFAULT 0,
    asset_id             UUID        REFERENCES assets(id) ON DELETE SET NULL,
    created_at           TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_task_group_id ON scheduled_tasks(task_group_id);
CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_maintenance_plan_id ON scheduled_tasks(maintenance_plan_id);


-- 3.4 purchase_orders — Fiix: PurchaseOrder
CREATE TABLE IF NOT EXISTS purchase_orders (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    po_code              VARCHAR(150),
    supplier_id          UUID        REFERENCES vendors(id) ON DELETE SET NULL,
    status_id            UUID        REFERENCES purchase_order_statuses(id) ON DELETE SET NULL,
    status               VARCHAR(50) NOT NULL DEFAULT 'draft',
    site_id              UUID        REFERENCES locations(id) ON DELETE SET NULL,
    charge_department_id UUID        REFERENCES charge_departments(id) ON DELETE SET NULL,
    subtotal             NUMERIC(18, 2),
    tax_amount           NUMERIC(18, 2),
    total_amount         NUMERIC(18, 2),
    notes                TEXT,
    created_by           UUID        REFERENCES users(id) ON DELETE SET NULL,
    created_at           TIMESTAMP   NOT NULL DEFAULT NOW(),
    received_at          TIMESTAMP,
    updated_at           TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_purchase_orders_organization_id ON purchase_orders(organization_id);
CREATE INDEX IF NOT EXISTS ix_purchase_orders_supplier_id ON purchase_orders(supplier_id);


-- 3.5 purchase_order_line_items — Fiix: PurchaseOrderLineItem
CREATE TABLE IF NOT EXISTS purchase_order_line_items (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_order_id UUID        NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    part_id           UUID        REFERENCES spare_parts(id) ON DELETE SET NULL,
    asset_id          UUID        REFERENCES assets(id) ON DELETE SET NULL,
    description       TEXT,
    quantity          INTEGER     NOT NULL DEFAULT 1,
    unit_price        NUMERIC(18, 2),
    total_price       NUMERIC(18, 2),
    tax_rate          NUMERIC(6, 4)
);

CREATE INDEX IF NOT EXISTS ix_po_line_items_purchase_order_id ON purchase_order_line_items(purchase_order_id);


-- =============================================================================
-- SECTION 4: NEW JUNCTION TABLES
-- =============================================================================

-- 4.1 scheduled_maintenance_assets — one SM plan can cover multiple assets
CREATE TABLE IF NOT EXISTS scheduled_maintenance_assets (
    id                   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    maintenance_plan_id  UUID    NOT NULL REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    asset_id             UUID    NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT uq_sm_asset UNIQUE (maintenance_plan_id, asset_id)
);

CREATE INDEX IF NOT EXISTS ix_sm_assets_plan_id ON scheduled_maintenance_assets(maintenance_plan_id);
CREATE INDEX IF NOT EXISTS ix_sm_assets_asset_id ON scheduled_maintenance_assets(asset_id);


-- 4.2 scheduled_maintenance_users — assigned technicians for a PM plan
CREATE TABLE IF NOT EXISTS scheduled_maintenance_users (
    id                   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    maintenance_plan_id  UUID    NOT NULL REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    user_id              UUID    NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT uq_sm_user UNIQUE (maintenance_plan_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_sm_users_plan_id ON scheduled_maintenance_users(maintenance_plan_id);


-- 4.3 scheduled_maintenance_parts — parts required for a PM plan
CREATE TABLE IF NOT EXISTS scheduled_maintenance_parts (
    id                   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    maintenance_plan_id  UUID    NOT NULL REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    part_id              UUID    NOT NULL REFERENCES spare_parts(id) ON DELETE CASCADE,
    quantity_required    INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_sm_part UNIQUE (maintenance_plan_id, part_id)
);

CREATE INDEX IF NOT EXISTS ix_sm_parts_plan_id ON scheduled_maintenance_parts(maintenance_plan_id);


-- 4.4 work_order_assets — a WO can span multiple assets
CREATE TABLE IF NOT EXISTS work_order_assets (
    id             UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id  UUID    NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    asset_id       UUID    NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    CONSTRAINT uq_wo_asset UNIQUE (work_order_id, asset_id)
);

CREATE INDEX IF NOT EXISTS ix_wo_assets_work_order_id ON work_order_assets(work_order_id);
CREATE INDEX IF NOT EXISTS ix_wo_assets_asset_id ON work_order_assets(asset_id);


-- 4.5 work_order_users — multiple technicians assigned to a single WO
CREATE TABLE IF NOT EXISTS work_order_users (
    id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id  UUID            NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    user_id        UUID            NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hours_spent    NUMERIC(10, 2),
    CONSTRAINT uq_wo_user UNIQUE (work_order_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_wo_users_work_order_id ON work_order_users(work_order_id);


-- =============================================================================
-- SECTION 5: INDEXES FOR COMMON QUERY PATTERNS
-- =============================================================================

-- assets
CREATE INDEX IF NOT EXISTS ix_assets_barcode         ON assets(barcode)         WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_assets_inventory_code  ON assets(inventory_code)  WHERE inventory_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_assets_parent_asset_id ON assets(parent_asset_id) WHERE parent_asset_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_assets_project_id      ON assets(project_id)      WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_assets_charge_dept_id  ON assets(charge_department_id) WHERE charge_department_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_assets_raw_metadata    ON assets USING GIN (raw_metadata) WHERE raw_metadata IS NOT NULL;

-- work_orders
CREATE INDEX IF NOT EXISTS ix_work_orders_wo_code         ON work_orders(wo_code)         WHERE wo_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_work_orders_priority_id     ON work_orders(priority_id)     WHERE priority_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_work_orders_status_id       ON work_orders(status_id)       WHERE status_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_work_orders_maintenance_plan ON work_orders(maintenance_plan_id) WHERE maintenance_plan_id IS NOT NULL;

-- maintenance_plans
CREATE INDEX IF NOT EXISTS ix_maintenance_plans_sm_code    ON maintenance_plans(sm_code)  WHERE sm_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_maintenance_plans_priority   ON maintenance_plans(priority_id) WHERE priority_id IS NOT NULL;

-- spare_parts
CREATE INDEX IF NOT EXISTS ix_spare_parts_bom_group  ON spare_parts(bom_group_id) WHERE bom_group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_spare_parts_supplier   ON spare_parts(supplier_id)  WHERE supplier_id IS NOT NULL;


-- =============================================================================
-- SECTION 6: HIGH-IMPACT FIIX TABLES
-- 14 objects from the Fiix API that are heavily cross-referenced.
-- =============================================================================

-- ── Reference / lookup ───────────────────────────────────────────────────────

-- 6.1 misc_cost_types — Fiix: MiscCostType (labour external, travel, subcontract, etc.)
CREATE TABLE IF NOT EXISTS misc_cost_types (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID         NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(150) NOT NULL,
    CONSTRAINT uq_misc_cost_types_org_name UNIQUE (organization_id, name)
);


-- ── Entity tables ─────────────────────────────────────────────────────────────

-- 6.2 files — Fiix: File (universal polymorphic attachment — linked from any entity)
CREATE TABLE IF NOT EXISTS files (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID         REFERENCES organizations(id) ON DELETE CASCADE,
    name            VARCHAR(500) NOT NULL,
    blob_url        TEXT,
    file_size_bytes INTEGER,
    mime_type       VARCHAR(150),
    entity_type     VARCHAR(100),   -- 'asset' | 'work_order' | 'work_order_task' | 'inspection' | etc.
    entity_id       UUID,           -- polymorphic — no FK constraint intentional
    uploaded_by     UUID         REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_files_entity     ON files(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_files_uploaded_at ON files(uploaded_at);


-- 6.3 bom_group_parts — Fiix: BOMGroupPart (which spare parts belong to a BOM group)
CREATE TABLE IF NOT EXISTS bom_group_parts (
    id           UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    bom_group_id UUID     NOT NULL REFERENCES bom_groups(id) ON DELETE CASCADE,
    part_id      UUID     NOT NULL REFERENCES spare_parts(id) ON DELETE CASCADE,
    quantity     INTEGER  NOT NULL DEFAULT 1,
    CONSTRAINT uq_bom_group_parts UNIQUE (bom_group_id, part_id)
);

CREATE INDEX IF NOT EXISTS ix_bom_group_parts_bom_group_id ON bom_group_parts(bom_group_id);
CREATE INDEX IF NOT EXISTS ix_bom_group_parts_part_id      ON bom_group_parts(part_id);


-- 6.4 schedule_triggers — Fiix: ScheduleTrigger
--   A single SM plan can have MULTIPLE triggers (e.g. every 30 days OR every 500 hours).
--   Replaces the flat frequency_type/frequency_value columns on maintenance_plans.
CREATE TABLE IF NOT EXISTS schedule_triggers (
    id                   UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    maintenance_plan_id  UUID         NOT NULL REFERENCES maintenance_plans(id) ON DELETE CASCADE,
    trigger_type         VARCHAR(20)  NOT NULL,    -- 'time' | 'meter'
    -- Time-based trigger
    interval_value       INTEGER,                  -- e.g. 30
    interval_unit        VARCHAR(20),              -- 'days' | 'weeks' | 'months' | 'years'
    -- Meter-based trigger
    meter_interval       NUMERIC(18, 4),           -- e.g. 500.0 (hours, km, cycles)
    meter_unit_id        UUID         REFERENCES meter_reading_units(id) ON DELETE SET NULL,
    -- Shared
    last_triggered_at    TIMESTAMPTZ,
    next_due_date        DATE,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_schedule_triggers_plan_id ON schedule_triggers(maintenance_plan_id);


-- 6.5 misc_costs — Fiix: MiscCost (travel, subcontract, etc. costs on a WO)
CREATE TABLE IF NOT EXISTS misc_costs (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id     UUID         NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    misc_cost_type_id UUID         REFERENCES misc_cost_types(id) ON DELETE SET NULL,
    description       TEXT,
    amount            NUMERIC(18, 2),
    created_by        UUID         REFERENCES users(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_misc_costs_work_order_id ON misc_costs(work_order_id);


-- 6.6 asset_offline_log — Fiix: AssetOfflineTracker (downtime log per asset)
--   downtime_minutes is a generated column — auto-computed from timestamps.
CREATE TABLE IF NOT EXISTS asset_offline_log (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id         UUID         NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    offline_reason   TEXT,
    online_reason    TEXT,
    went_offline_at  TIMESTAMPTZ  NOT NULL,
    came_online_at   TIMESTAMPTZ,
    downtime_minutes INTEGER      GENERATED ALWAYS AS (
                         CASE WHEN came_online_at IS NOT NULL
                              THEN EXTRACT(EPOCH FROM (came_online_at - went_offline_at))::INTEGER / 60
                              ELSE NULL
                         END
                     ) STORED,
    work_order_id    UUID         REFERENCES work_orders(id) ON DELETE SET NULL,
    recorded_by      UUID         REFERENCES users(id) ON DELETE SET NULL,
    notes            TEXT
);

CREATE INDEX IF NOT EXISTS ix_asset_offline_log_asset_id        ON asset_offline_log(asset_id);
CREATE INDEX IF NOT EXISTS ix_asset_offline_log_went_offline_at ON asset_offline_log(went_offline_at);


-- 6.7 receipts — Fiix: Receipt (goods received against a PO)
CREATE TABLE IF NOT EXISTS receipts (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    purchase_order_id UUID         NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    status            VARCHAR(20)  NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'partial', 'complete', 'cancelled')),
    site_id           UUID         REFERENCES locations(id) ON DELETE SET NULL,
    received_by       UUID         REFERENCES users(id) ON DELETE SET NULL,
    received_at       TIMESTAMPTZ,
    notes             TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_receipts_purchase_order_id ON receipts(purchase_order_id);


-- 6.8 receipt_line_items — Fiix: ReceiptLineItem (actual quantities received per PO line)
CREATE TABLE IF NOT EXISTS receipt_line_items (
    id                UUID     PRIMARY KEY DEFAULT gen_random_uuid(),
    receipt_id        UUID     NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    po_line_item_id   UUID     REFERENCES purchase_order_line_items(id) ON DELETE SET NULL,
    part_id           UUID     REFERENCES spare_parts(id) ON DELETE SET NULL,
    quantity_ordered  INTEGER,
    quantity_received INTEGER  NOT NULL DEFAULT 0,
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS ix_receipt_line_items_receipt_id ON receipt_line_items(receipt_id);


-- ── Root Cause Analysis (RCA) ─────────────────────────────────────────────────

-- 6.9 rca_problems — Fiix: RCAProblem
CREATE TABLE IF NOT EXISTS rca_problems (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    description     TEXT NOT NULL
);

-- 6.10 rca_causes — Fiix: RCACause
CREATE TABLE IF NOT EXISTS rca_causes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    description     TEXT NOT NULL
);

-- 6.11 rca_actions — Fiix: RCAAction (corrective/preventive actions)
CREATE TABLE IF NOT EXISTS rca_actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    description     TEXT NOT NULL
);

-- 6.12 rca_groupings — Fiix: RCAGrouping (ties a closed WO to its RCA)
CREATE TABLE IF NOT EXISTS rca_groupings (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id UUID         NOT NULL REFERENCES work_orders(id) ON DELETE CASCADE,
    problem_id    UUID         REFERENCES rca_problems(id) ON DELETE SET NULL,
    notes         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_rca_groupings_work_order_id ON rca_groupings(work_order_id);

-- 6.13 rca_grouping_causes — Fiix: RCAGroupingCause (many causes per grouping)
CREATE TABLE IF NOT EXISTS rca_grouping_causes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rca_grouping_id UUID NOT NULL REFERENCES rca_groupings(id) ON DELETE CASCADE,
    rca_cause_id    UUID NOT NULL REFERENCES rca_causes(id) ON DELETE CASCADE,
    CONSTRAINT uq_rca_grouping_cause UNIQUE (rca_grouping_id, rca_cause_id)
);

-- 6.14 rca_grouping_actions — Fiix: RCAGroupingAction (many actions per grouping)
CREATE TABLE IF NOT EXISTS rca_grouping_actions (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    rca_grouping_id UUID         NOT NULL REFERENCES rca_groupings(id) ON DELETE CASCADE,
    rca_action_id   UUID         NOT NULL REFERENCES rca_actions(id) ON DELETE CASCADE,
    assigned_to     UUID         REFERENCES users(id) ON DELETE SET NULL,
    due_date        DATE,
    completed_at    TIMESTAMPTZ,
    CONSTRAINT uq_rca_grouping_action UNIQUE (rca_grouping_id, rca_action_id)
);


-- ── FK addition to existing table ─────────────────────────────────────────────

-- Allow file attachments to reference a specific WO task (not just the WO)
ALTER TABLE work_order_attachments
    ADD COLUMN IF NOT EXISTS work_order_task_id UUID REFERENCES work_order_tasks(id) ON DELETE CASCADE;


-- =============================================================================
-- SECTION 7: SPRINT 2 INGESTION TABLES (migration 001 — already live in Azure)
-- Included here for completeness. CREATE TABLE IF NOT EXISTS = safe to re-run.
-- =============================================================================

-- 6.1 prompt_templates — versioned Jinja2 prompts per agent + doc type
CREATE TABLE IF NOT EXISTS prompt_templates (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id            VARCHAR(50)  NOT NULL,
    doc_type            VARCHAR(50)  NOT NULL,
    system_prompt       TEXT         NOT NULL,
    user_template       TEXT         NOT NULL,
    extraction_schema   JSONB,
    version             VARCHAR(20)  NOT NULL DEFAULT '1.0',
    accuracy_score      NUMERIC(5, 4),
    usage_count         INTEGER      NOT NULL DEFAULT 0,
    avg_tokens          INTEGER,
    is_active           BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_prompt_template_version UNIQUE (agent_id, doc_type, version)
);

CREATE INDEX IF NOT EXISTS ix_prompt_templates_agent_doc ON prompt_templates(agent_id, doc_type);


-- 6.2 ingestion_documents — every source file ingested through svc-ingestion
CREATE TABLE IF NOT EXISTS ingestion_documents (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID,
    source_type         VARCHAR(20)  NOT NULL,
    agent_id            VARCHAR(50)  NOT NULL,
    original_filename   VARCHAR(500) NOT NULL,
    blob_url            TEXT,
    file_hash_sha256    VARCHAR(64),
    page_count          INTEGER,
    intermediate_json   JSONB,
    final_json          JSONB,
    status              VARCHAR(20)  NOT NULL DEFAULT 'queued',
    confidence_overall  VARCHAR(10),
    eval_score          NUMERIC(4, 3),
    model_used          VARCHAR(50),
    prompt_template_id  UUID REFERENCES prompt_templates(id) ON DELETE SET NULL,
    tokens_in           INTEGER      NOT NULL DEFAULT 0,
    tokens_out          INTEGER      NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER      NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(10, 6) NOT NULL DEFAULT 0,
    processing_ms       INTEGER      NOT NULL DEFAULT 0,
    uploaded_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    processed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_ingestion_documents_tenant_status  ON ingestion_documents(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_ingestion_documents_file_hash      ON ingestion_documents(file_hash_sha256);
CREATE INDEX IF NOT EXISTS ix_ingestion_documents_uploaded_at    ON ingestion_documents(uploaded_at);


-- 6.3 ingestion_audit_log — full traceability per ingestion event
CREATE TABLE IF NOT EXISTS ingestion_audit_log (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_id    UUID         NOT NULL REFERENCES ingestion_documents(id) ON DELETE CASCADE,
    event_type      VARCHAR(50)  NOT NULL,
    model_used      VARCHAR(50),
    prompt_version  VARCHAR(20),
    eval_score      NUMERIC(4, 3),
    rules_violations JSONB,
    reviewer_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    decision        VARCHAR(20),
    corrected_json  JSONB,
    timestamp       TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_ingestion_audit_log_ingestion_id ON ingestion_audit_log(ingestion_id);
CREATE INDEX IF NOT EXISTS ix_ingestion_audit_log_timestamp    ON ingestion_audit_log(timestamp);


-- 6.4 prompt_ab_tests — A/B test tracking between two prompt template versions
CREATE TABLE IF NOT EXISTS prompt_ab_tests (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    template_a_id   UUID         NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
    template_b_id   UUID         NOT NULL REFERENCES prompt_templates(id) ON DELETE CASCADE,
    status          VARCHAR(20)  NOT NULL DEFAULT 'running',
    accuracy_a      NUMERIC(5, 4),
    accuracy_b      NUMERIC(5, 4),
    winner_id       UUID REFERENCES prompt_templates(id) ON DELETE SET NULL,
    docs_processed  INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);


-- 6.5 review_queue — HITL items awaiting human decision
CREATE TABLE IF NOT EXISTS review_queue (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_id    UUID         NOT NULL REFERENCES ingestion_documents(id) ON DELETE CASCADE,
    field_path      VARCHAR(255),
    extracted_value TEXT,
    confidence      VARCHAR(10),
    routing_reason  VARCHAR(255),
    reviewer_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',
    decision        VARCHAR(20),
    corrected_value TEXT,
    locked_until    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decided_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_review_queue_ingestion_id    ON review_queue(ingestion_id);
CREATE INDEX IF NOT EXISTS ix_review_queue_status_created  ON review_queue(status, created_at);


-- 6.6 corrections_log — every human correction (feeds weekly prompt refinement)
CREATE TABLE IF NOT EXISTS corrections_log (
    id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_id     UUID         NOT NULL REFERENCES ingestion_documents(id) ON DELETE CASCADE,
    field_path       VARCHAR(255) NOT NULL,
    original_value   TEXT,
    corrected_value  TEXT,
    correction_type  VARCHAR(50)  NOT NULL DEFAULT 'wrong_value',
    reviewer_id      UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    timestamp        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_corrections_log_ingestion_id ON corrections_log(ingestion_id);
CREATE INDEX IF NOT EXISTS ix_corrections_log_timestamp    ON corrections_log(timestamp);


-- 6.7 query_audit_log — every user query through svc-query
CREATE TABLE IF NOT EXISTS query_audit_log (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text          TEXT         NOT NULL,
    intent_classified   VARCHAR(30),
    retrieval_tier      VARCHAR(10),
    docs_consulted      JSONB,
    model_used          VARCHAR(50),
    response_text       TEXT,
    eval_score          NUMERIC(4, 3),
    output_format       VARCHAR(10),
    tokens_in           INTEGER      NOT NULL DEFAULT 0,
    tokens_out          INTEGER      NOT NULL DEFAULT 0,
    cost_usd            NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms          INTEGER      NOT NULL DEFAULT 0,
    user_id             UUID REFERENCES users(id) ON DELETE SET NULL,
    timestamp           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_query_audit_log_user_id       ON query_audit_log(user_id);
CREATE INDEX IF NOT EXISTS ix_query_audit_log_timestamp     ON query_audit_log(timestamp);
CREATE INDEX IF NOT EXISTS ix_query_audit_log_retrieval_tier ON query_audit_log(retrieval_tier);


-- 6.8 claude_api_usage — per-request Claude API cost tracking
CREATE TABLE IF NOT EXISTS claude_api_usage (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_id      UUID REFERENCES ingestion_documents(id) ON DELETE SET NULL,
    query_id          UUID REFERENCES query_audit_log(id) ON DELETE SET NULL,
    service           VARCHAR(60)  NOT NULL,
    agent_id          VARCHAR(50),
    model             VARCHAR(50)  NOT NULL,
    tokens_in         INTEGER      NOT NULL DEFAULT 0,
    tokens_out        INTEGER      NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER      NOT NULL DEFAULT 0,
    cost_usd          NUMERIC(10, 6) NOT NULL DEFAULT 0,
    latency_ms        INTEGER      NOT NULL DEFAULT 0,
    timestamp         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_claude_api_usage_ingestion_id    ON claude_api_usage(ingestion_id);
CREATE INDEX IF NOT EXISTS ix_claude_api_usage_timestamp       ON claude_api_usage(timestamp);
CREATE INDEX IF NOT EXISTS ix_claude_api_usage_service_model   ON claude_api_usage(service, model);


-- 6.9 claude_budget_config — budget guardrails (alert at threshold, pause at 100%)
CREATE TABLE IF NOT EXISTS claude_budget_config (
    id                    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    period                VARCHAR(20)  NOT NULL DEFAULT 'monthly',
    limit_usd             NUMERIC(10, 2) NOT NULL,
    alert_threshold_pct   NUMERIC(5, 2)  NOT NULL DEFAULT 80.0,
    auto_pause            BOOLEAN      NOT NULL DEFAULT TRUE,
    is_active             BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);


-- =============================================================================
-- SECTION 8: SPRINT 2 PHASE 2 TABLES (migration 002 — NOT yet in Azure)
-- =============================================================================

-- 7.1 inspections — from DOCX/PDF agents (Sections A–G site inspection reports)
CREATE TABLE IF NOT EXISTS inspections (
    id                UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id          UUID         REFERENCES assets(id) ON DELETE SET NULL,
    asset_code        VARCHAR(50),
    ingestion_id      UUID         REFERENCES ingestion_documents(id) ON DELETE SET NULL,
    inspector         VARCHAR(255),
    inspection_date   DATE,
    section           VARCHAR(10),           -- A | B | C | D | E | F | G
    finding_type      VARCHAR(100),
    observations      TEXT,
    risk_level        VARCHAR(20),           -- High | Medium | Low
    corrective_action BOOLEAN      NOT NULL DEFAULT FALSE,
    source_file       VARCHAR(500),          -- original blob URL
    findings_jsonb    JSONB,                 -- full raw extraction preserved
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_inspections_asset_id        ON inspections(asset_id)        WHERE asset_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_inspections_asset_code      ON inspections(asset_code)      WHERE asset_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_inspections_inspection_date ON inspections(inspection_date);
CREATE INDEX IF NOT EXISTS ix_inspections_risk_level      ON inspections(risk_level);
CREATE INDEX IF NOT EXISTS ix_inspections_findings_jsonb  ON inspections USING GIN (findings_jsonb) WHERE findings_jsonb IS NOT NULL;


-- 7.2 agent_audit_log — Layer 5 per-agent determinism audit (EL-5.x)
--     One row per agent invocation. Used by EL-6.BOUND to verify audit_ids.
CREATE TABLE IF NOT EXISTS agent_audit_log (
    id                      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id                VARCHAR(50)  NOT NULL,      -- asset|wo|pm|parts|inspection
    domain                  VARCHAR(50)  NOT NULL,
    asset_code              VARCHAR(50),
    -- EL-5.BOUND
    bound_validation_passed BOOLEAN      NOT NULL DEFAULT FALSE,
    -- EL-5.AGG — individual run outputs and validity flags
    run_1_output            JSONB,
    run_2_output            JSONB,
    run_3_output            JSONB,
    run_1_valid             BOOLEAN,
    run_2_valid             BOOLEAN,
    run_3_valid             BOOLEAN,
    -- EL-5.VOTE
    runs_agreed             INTEGER      NOT NULL DEFAULT 0,
    winner_status           VARCHAR(50),
    winner_confidence       NUMERIC(4, 3),
    -- EL-5.CONSTRAIN
    hard_rules_fired        JSONB,
    final_status            VARCHAR(50),
    confidence_gate_passed  BOOLEAN      NOT NULL DEFAULT FALSE,
    requires_human_review   BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Cost
    model_used              VARCHAR(50),
    tokens_total            INTEGER      NOT NULL DEFAULT 0,
    cost_usd                NUMERIC(10, 6) NOT NULL DEFAULT 0,
    timestamp               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agent_audit_log_agent_id   ON agent_audit_log(agent_id);
CREATE INDEX IF NOT EXISTS ix_agent_audit_log_asset_code ON agent_audit_log(asset_code) WHERE asset_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_agent_audit_log_timestamp  ON agent_audit_log(timestamp);


-- 7.3 orchestration_audit_log — Layer 6 full decision audit (INSERT ONLY — never UPDATE or DELETE)
CREATE TABLE IF NOT EXISTS orchestration_audit_log (
    id                      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_code              VARCHAR(50),
    -- EL-6.BOUND
    bound_passed            BOOLEAN      NOT NULL DEFAULT FALSE,
    -- EL-6.AGG
    run_1_valid             BOOLEAN,
    run_2_valid             BOOLEAN,
    run_3_valid             BOOLEAN,
    runs_agreed             INTEGER      NOT NULL DEFAULT 0,
    -- EL-6.VOTE + EL-6.CONSTRAIN
    action                  VARCHAR(50),           -- create_wo|order_part|alert_critical|no_action|human_review
    priority                VARCHAR(20),
    confidence              NUMERIC(4, 3),
    reasoning               TEXT,
    confidence_gate_passed  BOOLEAN      NOT NULL DEFAULT FALSE,
    safety_passed           BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Payload — all 5 AgentResults serialised
    agent_results_jsonb     JSONB,
    hard_rules_fired        JSONB,
    -- Cost
    model_used              VARCHAR(50),
    tokens_total            INTEGER      NOT NULL DEFAULT 0,
    cost_usd                NUMERIC(10, 6) NOT NULL DEFAULT 0,
    timestamp               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    -- NOTE: no UPDATE or DELETE ever — this table is INSERT ONLY
);

CREATE INDEX IF NOT EXISTS ix_orchestration_audit_log_asset_code ON orchestration_audit_log(asset_code) WHERE asset_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_orchestration_audit_log_action     ON orchestration_audit_log(action);
CREATE INDEX IF NOT EXISTS ix_orchestration_audit_log_timestamp  ON orchestration_audit_log(timestamp);

-- Enforce INSERT ONLY via a rule (belt-and-suspenders on top of application policy)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_rules
        WHERE schemaname = 'plenum_cafm'
          AND tablename  = 'orchestration_audit_log'
          AND rulename   = 'no_update_orchestration_audit_log'
    ) THEN
        EXECUTE $rule$
            CREATE RULE no_update_orchestration_audit_log AS
                ON UPDATE TO plenum_cafm.orchestration_audit_log DO INSTEAD NOTHING;
        $rule$;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_rules
        WHERE schemaname = 'plenum_cafm'
          AND tablename  = 'orchestration_audit_log'
          AND rulename   = 'no_delete_orchestration_audit_log'
    ) THEN
        EXECUTE $rule$
            CREATE RULE no_delete_orchestration_audit_log AS
                ON DELETE TO plenum_cafm.orchestration_audit_log DO INSTEAD NOTHING;
        $rule$;
    END IF;
END;
$$;


-- 7.4 document_generation_log — every generated or filled document (EL-7.DOC.EVAL)
CREATE TABLE IF NOT EXISTS document_generation_log (
    id                      UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    request_text            TEXT,
    intent_type             VARCHAR(30),           -- document_generate | template_fill
    document_type           VARCHAR(50),
    document_plan_json      JSONB,                 -- full DocumentPlan executed
    plan_validation_passed  BOOLEAN,               -- EL-7.DOC.PLAN result
    output_format           VARCHAR(10),           -- docx | xlsx | pdf
    output_blob_url         TEXT,
    data_sources            JSONB,                 -- tables + row counts consulted
    -- EL-7.DOC.RENDER + EL-7.DOC.EVAL
    spot_checks_run         INTEGER,
    spot_checks_passed      INTEGER,
    eval_score              NUMERIC(4, 3),
    plan_runs_agreed        INTEGER,
    held_for_review         BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Cost
    model_used              VARCHAR(50),
    tokens_in               INTEGER      NOT NULL DEFAULT 0,
    tokens_out              INTEGER      NOT NULL DEFAULT 0,
    cost_usd                NUMERIC(10, 6) NOT NULL DEFAULT 0,
    render_ms               INTEGER,
    user_id                 UUID REFERENCES users(id) ON DELETE SET NULL,
    timestamp               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_doc_generation_log_user_id        ON document_generation_log(user_id)        WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_doc_generation_log_document_type  ON document_generation_log(document_type);
CREATE INDEX IF NOT EXISTS ix_doc_generation_log_held           ON document_generation_log(held_for_review) WHERE held_for_review = TRUE;
CREATE INDEX IF NOT EXISTS ix_doc_generation_log_timestamp      ON document_generation_log(timestamp);


-- =============================================================================
-- DONE
-- =============================================================================
-- Summary of changes applied:
--
-- SECTION 1–5  Fiix-inspired expansion of plenum_cafm core schema:
--   NEW TABLES (9 reference + 5 entity + 5 junction = 19):
--     priorities, maintenance_types, work_order_statuses, charge_departments,
--     projects, bom_groups, meter_reading_units, task_groups, purchase_order_statuses,
--     asset_warranties, user_certifications, scheduled_tasks,
--     purchase_orders, purchase_order_line_items,
--     scheduled_maintenance_assets, scheduled_maintenance_users,
--     scheduled_maintenance_parts, work_order_assets, work_order_users
--   ALTERED TABLES (13):
--     users (+4), locations (+6), asset_categories (+1), assets (+19),
--     asset_readings (+2), vendors (+10), maintenance_plans (+9),
--     work_orders (+16), work_order_tasks (+6), work_order_history (+1),
--     spare_parts (+8), inventory_transactions (+3), work_order_parts (+2)
--
-- SECTION 6  Missing core Fiix tables (34 new tables + 6 ALTER additions):
--   Reference: countries, currencies, billing_terms, sm_statuses, stock_tx_types,
--              misc_cost_types, reasons_asset_offline, reasons_asset_online,
--              business_groups, business_classifications, asset_event_types,
--              receipt_statuses, rfq_statuses, po_additional_cost_types
--   Entity:    files, bom_group_parts, schedule_triggers, misc_costs,
--              asset_offline_log, asset_events, site_users,
--              asset_businesses, work_order_businesses,
--              receipts, receipt_line_items, rfqs, rfq_line_items,
--              purchase_order_additional_costs
--   RCA:       rca_problems, rca_causes, rca_actions, rca_groupings,
--              rca_grouping_causes, rca_grouping_actions
--   FK additions to: assets, vendors, purchase_orders, maintenance_plans,
--                    inventory_transactions, work_order_attachments
--
-- SECTION 7  Sprint 2 ingestion tables (migration 001 — already live):
--   prompt_templates, ingestion_documents, ingestion_audit_log,
--   prompt_ab_tests, review_queue, corrections_log,
--   query_audit_log, claude_api_usage, claude_budget_config
--
-- SECTION 8  Sprint 2 Phase 2 tables (migration 002 — new):
--   inspections, agent_audit_log, orchestration_audit_log,
--   document_generation_log
--   (orchestration_audit_log has INSERT-ONLY rules applied)
-- =============================================================================
