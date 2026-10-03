-- Every work order carries a uuid identity, and something to show for it.
--
-- The two databases identify a work order differently. On one, work_orders.id is itself a
-- uuid and every child table points at it. On the other, id is an integer sequence and the
-- uuid identity lives in a separate column, wo_uuid, which is what invoice_lines, ppm_visits
-- and vendor_wo_scores actually reference. That second arrangement is fine — but wo_uuid was
-- nullable with no default and only populated on 1605 of 2775 rows, so more than a thousand
-- work orders had no uuid for anything to reference, and 879 had neither a wo_code nor a
-- work_order_id either. A decision row for one of those printed a blank where its identifier
-- should be.
--
-- This does three things, in order, and none of them is destructive:
--
--   1. Gives wo_uuid a default, so a work order created from here on has an identity without
--      anybody remembering to supply one.
--   2. Backfills the rows that have none. The uuid is derived from the row's own primary key
--      through uuid_generate_v5-style hashing of a fixed namespace, so it is stable: running
--      this twice produces the same uuid for the same row, and a row that already has one is
--      never given a different one.
--   3. Fills work_order_id from that uuid **only where both code columns are empty**, so a
--      real code is never overwritten by a generated one. A uuid is a poor thing to show a
--      person, which is why it is the last resort rather than the first.
--
-- Nothing already populated is touched, and the primary key is not altered: converting an
-- integer key on a table with twelve child tables would rewrite every one of them, and the
-- uuid identity these rows needed already exists.

DO $work_order_uuid_identity$
DECLARE
    has_uuid   boolean;
    filled     integer;
    coded      integer;
BEGIN
    IF to_regclass('plenum_cafm.work_orders') IS NULL THEN
        RETURN;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'plenum_cafm' AND table_name = 'work_orders'
           AND column_name = 'wo_uuid') INTO has_uuid;

    -- Where the primary key is already a uuid there is nothing to reconcile: id is the
    -- identity and every child points at it.
    IF NOT has_uuid THEN
        RAISE NOTICE 'work_orders has no wo_uuid column; id is the identity here';
        RETURN;
    END IF;

    ALTER TABLE plenum_cafm.work_orders
        ALTER COLUMN wo_uuid SET DEFAULT gen_random_uuid();

    -- Stable by construction: the same row always hashes to the same uuid, so a re-run is a
    -- no-op rather than a churn of new identities that child rows would stop matching.
    UPDATE plenum_cafm.work_orders
       SET wo_uuid = md5('plenum_cafm.work_orders:' || id::text)::uuid
     WHERE wo_uuid IS NULL;
    GET DIAGNOSTICS filled = ROW_COUNT;

    -- The uuid becomes the visible identifier only for rows that have no other one. A code
    -- somebody can read is better, and is left alone wherever it exists.
    UPDATE plenum_cafm.work_orders
       SET work_order_id = wo_uuid::text
     WHERE nullif(btrim(coalesce(wo_code, '')), '') IS NULL
       AND nullif(btrim(coalesce(work_order_id, '')), '') IS NULL
       AND wo_uuid IS NOT NULL;
    GET DIAGNOSTICS coded = ROW_COUNT;

    -- One work order, one identity. A duplicate would let a child row match two parents.
    -- Inside the guard, because the column only exists on the database that needs it.
    CREATE UNIQUE INDEX IF NOT EXISTS ux_work_orders_wo_uuid
        ON plenum_cafm.work_orders (wo_uuid)
     WHERE wo_uuid IS NOT NULL;

    RAISE NOTICE 'work_orders: % rows given a uuid identity, % given a visible identifier',
                 filled, coded;
END
$work_order_uuid_identity$;

DO $wo_uuid_comment$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema='plenum_cafm' AND table_name='work_orders'
                  AND column_name='wo_uuid') THEN
        EXECUTE $c$COMMENT ON COLUMN plenum_cafm.work_orders.wo_uuid
    IS 'The uuid identity of this work order, and what invoice_lines, ppm_visits and '
       'vendor_wo_scores reference. Defaulted and backfilled so every row has one; on the '
       'database where work_orders.id is itself a uuid this column does not exist and id is '
       'the identity instead.'$c$;
    END IF;
END
$wo_uuid_comment$;
