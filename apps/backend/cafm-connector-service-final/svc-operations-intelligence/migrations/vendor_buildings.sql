-- Which buildings a vendor serves, as an admin assigned it (Hoist vendors and the Vendors page,
-- 12 Oct 2026). Until now "Buildings served" was only inferred from certificates, work orders
-- and invoices naming the vendor; an assignment is the statement that the vendor works there,
-- made before any of those exist.
--
-- vendor_id is text: vendors.id is uuid on one deployment and varchar with legacy ids on
-- another. An assignment is ended (removed_at), never deleted, so the history stays readable;
-- only one live row per vendor and building.
CREATE TABLE IF NOT EXISTS plenum_cafm.vendor_buildings (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL,
    vendor_id       text NOT NULL,
    building_id     uuid NOT NULL,
    building_name   text,
    source          text NOT NULL DEFAULT 'manual',   -- hoist | manual
    assigned_by     text,
    assigned_at     timestamptz NOT NULL DEFAULT now(),
    removed_at      timestamptz,
    removed_by      text
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_vendor_buildings_live
    ON plenum_cafm.vendor_buildings (organization_id, vendor_id, building_id) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_vendor_buildings_building
    ON plenum_cafm.vendor_buildings (organization_id, building_id) WHERE removed_at IS NULL;
