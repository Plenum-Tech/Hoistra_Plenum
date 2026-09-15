-- One identity for a work order on both databases, and one sequence for the number people read.
--
-- The two databases disagreed about what a work order *is*. On hoistra_test, work_orders.id is
-- a uuid and twelve child tables point at it. On plenum_agent it was an integer sequence, 1 to
-- 16872, and nothing pointed at it at all — the children there reference wo_uuid and
-- work_order_id instead. So the cheap direction is to converge on uuid by changing the side
-- with no children hanging off its key, which is what this does.
--
-- Two jobs, two columns, and they are not the same job:
--
--   * **id** is what the system joins on. A uuid can be generated before the insert, does not
--     collide when tenants merge, and cannot be walked by an attacker the way /work-orders/1,
--     /2, /3 can.
--   * **wo_code** is what a person reads, quotes on the phone and types into a search box.
--     That is what should count upward, so it gets a sequence and a default: WO-000001.
--
-- The mapping from old integer to new uuid is deterministic — the row's existing wo_uuid where
-- it has one, otherwise a hash of its old key. Deterministic matters twice over: the migration
-- can be verified row for row afterwards, and re-deriving the same uuid later is possible if
-- anything external recorded the old number.
--
-- Existing codes are never touched. A work order number that changes after the fact breaks
-- every email and PDF that quoted it, so the sequence only fills what is empty.

-- ── the number people read ──────────────────────────────────────────────────────────────
CREATE SEQUENCE IF NOT EXISTS plenum_cafm.work_order_code_seq AS BIGINT START WITH 1;

DO $wo_code_sequence$
DECLARE
    filled integer;
BEGIN
    IF to_regclass('plenum_cafm.work_orders') IS NULL THEN
        RETURN;
    END IF;

    -- Start above anything already in this format, so a generated code can never collide with
    -- one that is already printed on something.
    PERFORM setval('plenum_cafm.work_order_code_seq',
                   greatest(1, coalesce((SELECT max(substring(wo_code from 'WO-([0-9]{6})$')::bigint)
                                           FROM plenum_cafm.work_orders
                                          WHERE wo_code ~ '^WO-[0-9]{6}$'), 0)),
                   true);

    ALTER TABLE plenum_cafm.work_orders
        ALTER COLUMN wo_code
        SET DEFAULT 'WO-' || lpad(nextval('plenum_cafm.work_order_code_seq')::text, 6, '0');

    -- Only the empty ones. Everything already coded keeps the code it has.
    UPDATE plenum_cafm.work_orders
       SET wo_code = 'WO-' || lpad(nextval('plenum_cafm.work_order_code_seq')::text, 6, '0')
     WHERE nullif(btrim(coalesce(wo_code, '')), '') IS NULL;
    GET DIAGNOSTICS filled = ROW_COUNT;
    RAISE NOTICE 'wo_code: % rows given a number; sequence now at %',
                 filled, currval('plenum_cafm.work_order_code_seq');
END
$wo_code_sequence$;

-- Only the generated codes are policed for uniqueness. The legacy ones are not unique and
-- never were — 390 of them repeat across 1170 rows on one database — and renumbering to make
-- an index happy would change numbers that are already printed on emails and PDFs. So the
-- constraint covers exactly what this sequence issues, which is the part that must not repeat.
CREATE UNIQUE INDEX IF NOT EXISTS ux_work_orders_wo_code_generated
    ON plenum_cafm.work_orders (wo_code)
 WHERE wo_code ~ '^WO-[0-9]{6}$';

-- ── the key the system joins on ─────────────────────────────────────────────────────────
DO $wo_id_to_uuid$
DECLARE
    current_type text;
    mapped       integer;
    clashes      integer;
BEGIN
    IF to_regclass('plenum_cafm.work_orders') IS NULL THEN
        RETURN;
    END IF;

    SELECT data_type INTO current_type
      FROM information_schema.columns
     WHERE table_schema = 'plenum_cafm' AND table_name = 'work_orders' AND column_name = 'id';

    IF current_type = 'uuid' THEN
        RAISE NOTICE 'work_orders.id is already uuid; nothing to convert';
        RETURN;
    END IF;

    -- The map, built once and kept for the duration so every dependent uses the same answer.
    CREATE TEMP TABLE _wo_id_map ON COMMIT DROP AS
    SELECT id AS old_id,
           coalesce(
               CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                                  WHERE table_schema='plenum_cafm' AND table_name='work_orders'
                                    AND column_name='wo_uuid')
                    THEN wo_uuid END,
               md5('plenum_cafm.work_orders:' || id::text)::uuid) AS new_id
      FROM plenum_cafm.work_orders;

    SELECT count(*) - count(DISTINCT new_id) INTO clashes FROM _wo_id_map;
    IF clashes > 0 THEN
        RAISE EXCEPTION 'work_orders.id conversion aborted: % rows would share a uuid', clashes;
    END IF;

    -- Dependents that hold the old key with no constraint on it. These were found by scanning
    -- every column whose name looks like a work-order reference and checking which actually
    -- contain values from work_orders.id; only inspections did.
    IF to_regclass('plenum_cafm.inspections') IS NOT NULL THEN
        UPDATE plenum_cafm.inspections i
           SET work_order_id = m.new_id::text
          FROM _wo_id_map m
         WHERE i.work_order_id = m.old_id::text;
        GET DIAGNOSTICS mapped = ROW_COUNT;
        RAISE NOTICE 'inspections.work_order_id: % rows remapped', mapped;

        UPDATE plenum_cafm.inspections i
           SET converted_work_order_id = m.new_id::text
          FROM _wo_id_map m
         WHERE i.converted_work_order_id = m.old_id::text;
        GET DIAGNOSTICS mapped = ROW_COUNT;
        RAISE NOTICE 'inspections.converted_work_order_id: % rows remapped', mapped;
    END IF;

    -- The key itself. The sequence default goes first: a uuid column has no use for nextval.
    --
    -- USING takes an expression over the row, not a subquery — Postgres rejects the latter in
    -- a transform. The expression here is the same formula the map was built from, so the
    -- value each row lands on is exactly the one the dependents were just pointed at.
    ALTER TABLE plenum_cafm.work_orders ALTER COLUMN id DROP DEFAULT;
    EXECUTE format(
        'ALTER TABLE plenum_cafm.work_orders ALTER COLUMN id TYPE uuid USING %s',
        CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_schema='plenum_cafm' AND table_name='work_orders'
                             AND column_name='wo_uuid')
             THEN 'coalesce(wo_uuid, md5(''plenum_cafm.work_orders:'' || id::text)::uuid)'
             ELSE 'md5(''plenum_cafm.work_orders:'' || id::text)::uuid' END);
    ALTER TABLE plenum_cafm.work_orders ALTER COLUMN id SET DEFAULT gen_random_uuid();

    RAISE NOTICE 'work_orders.id converted from % to uuid', current_type;
END
$wo_id_to_uuid$;

COMMENT ON COLUMN plenum_cafm.work_orders.wo_code
    IS 'The work order number people read, WO-000001, from plenum_cafm.work_order_code_seq. '
       'Defaulted on insert; existing codes in other formats are left as they are.';
