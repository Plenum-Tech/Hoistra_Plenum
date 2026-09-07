-- Feature B scoring columns on UDR work_orders (Phase 1 migration target).
-- Safe to re-run. Enables CSV/XLS migration CSVs to land attendance, costs, first-fix, etc.

ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS wo_code VARCHAR(80);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS workorder_ref VARCHAR(80);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS reported_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS attended_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS responded_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS first_fix BOOLEAN;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS recall BOOLEAN;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS return_visit BOOLEAN;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(14, 2);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(14, 2);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS labour_hours NUMERIC(10, 2);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS part_code VARCHAR(80);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS parts_cost NUMERIC(14, 2);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS priority VARCHAR(40);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS wo_type VARCHAR(80);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS maintenance_type VARCHAR(80);
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS organization_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS vendor_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS asset_id UUID;
ALTER TABLE IF EXISTS plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS notes TEXT;

CREATE INDEX IF NOT EXISTS ix_wo_vendor_completed
  ON plenum_cafm.work_orders (vendor_id, completed_at)
  WHERE completed_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_wo_code ON plenum_cafm.work_orders (wo_code);
