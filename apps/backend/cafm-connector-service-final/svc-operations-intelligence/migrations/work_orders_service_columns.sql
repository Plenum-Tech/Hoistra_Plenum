-- The work-order service's own columns, on every database it runs against.
--
-- svc-work-order-management maps 34 columns on plenum_cafm.work_orders. On one of the two
-- databases 24 of them do not exist, so every ORM read raised UndefinedColumnError and the
-- whole service — the work-order list, the filters, the detail route, the dashboard counts —
-- answered 500 there. The service's own init runs Base.metadata.create_all, which creates a
-- missing TABLE but never a missing COLUMN, so the drift could not heal itself.
--
-- These are the columns that service writes when it raises a work order of its own: where the
-- request came from, who asked, when it was scheduled, what was sent to the CMMS. A database
-- whose work orders arrived by a different route never had them. All nullable and additive —
-- no existing row changes, and no column is dropped or retyped.
--
-- Deliberately NOT added here: id, wo_code, building_id, asset_id, status, priority, title,
-- created_at. Those exist on both already, and `id` is the primary key the service now keys
-- on — it is set and distinct on every row on both databases, which wo_code is not (1,728 of
-- 2,775 set and only 948 distinct on one of them).

ALTER TABLE plenum_cafm.work_orders
    ADD COLUMN IF NOT EXISTS source               VARCHAR(50),
    ADD COLUMN IF NOT EXISTS source_reference     VARCHAR(255),
    ADD COLUMN IF NOT EXISTS asset                VARCHAR(255),
    ADD COLUMN IF NOT EXISTS asset_category       VARCHAR(100),
    ADD COLUMN IF NOT EXISTS location             VARCHAR(255),
    ADD COLUMN IF NOT EXISTS issue_description    TEXT,
    ADD COLUMN IF NOT EXISTS task_description     TEXT,
    ADD COLUMN IF NOT EXISTS request_type         VARCHAR(50),
    ADD COLUMN IF NOT EXISTS approval_type        VARCHAR(50),
    ADD COLUMN IF NOT EXISTS requester_name       VARCHAR(255),
    ADD COLUMN IF NOT EXISTS requester_email      VARCHAR(255),
    ADD COLUMN IF NOT EXISTS requester_phone      VARCHAR(50),
    ADD COLUMN IF NOT EXISTS vendor               VARCHAR(255),
    ADD COLUMN IF NOT EXISTS manpower             JSONB,
    ADD COLUMN IF NOT EXISTS scheduled_date       VARCHAR(20),
    ADD COLUMN IF NOT EXISTS scheduled_time       VARCHAR(20),
    ADD COLUMN IF NOT EXISTS estimated_duration   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS inspection_required  BOOLEAN DEFAULT false,
    ADD COLUMN IF NOT EXISTS special_requirements TEXT,
    ADD COLUMN IF NOT EXISTS cmms_work_order_id   VARCHAR(100),
    ADD COLUMN IF NOT EXISTS journey_log_id       VARCHAR(100),
    ADD COLUMN IF NOT EXISTS approved_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS prepared_at          TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sent_to_cmms_at      TIMESTAMPTZ;

-- The register already holds a name for the asset on the rows that have one; carry it into
-- the service's own column so a row reads the same whichever database it came from. Only
-- where the service's column is still empty, so this never overwrites anything.
UPDATE plenum_cafm.work_orders w
   SET asset = a.asset_name
  FROM plenum_cafm.assets a
 WHERE w.asset IS NULL
   AND w.asset_id IS NOT NULL
   AND a.id::text = w.asset_id::text;

COMMENT ON COLUMN plenum_cafm.work_orders.source
    IS 'Where the work order came from: this service, email intake, the PPM scheduler, an import.';

COMMENT ON COLUMN plenum_cafm.work_orders.asset
    IS 'The asset name as text. Join on asset_id — this is for display and for rows that '
       'predate the key.';
