-- Rows that belong to the only company on the platform now say so.
--
-- The access boundary made organization_id mandatory on every read: a route no longer takes
-- the company from the client, it takes it from the token. That is right, and it had one
-- consequence nobody could see until the first page came back empty — a row whose
-- organization_id was NULL used to match "no filter", and now matches nothing. The energy
-- meters, their readings, the gap records, the risk snapshots and the scoring weights were
-- all written before the column meant anything, so the meters list, the anomaly scan and
-- the energy page went blank the moment the boundary went in.
--
-- access_control.sql already established the rule for buildings: a portfolio with exactly
-- one company is that company's portfolio. This applies the same rule to every other table
-- in the schema that carries a UUID organization_id, and only ever fills NULLs.
--
-- Two exclusions, both deliberate:
--   ops_audit_log       append-only by trigger, and history is not ours to rewrite.
--   sites               its organization_id is an INTEGER against a UUID-keyed table; it
--                       never joined and stamping it would make a broken link look sound.
--
-- Where there is more than one company nothing happens: which company an unplaced row
-- belongs to is then a question about the data, and a wrong guess hands one tenant's
-- readings to another.

DO $$
DECLARE
    only_org UUID;
    t RECORD;
    n BIGINT;
BEGIN
    SELECT id INTO only_org FROM plenum_cafm.organizations LIMIT 2;
    IF (SELECT count(*) FROM plenum_cafm.organizations) <> 1 THEN
        RAISE NOTICE 'stamp_single_org: % companies — nothing stamped',
                     (SELECT count(*) FROM plenum_cafm.organizations);
        RETURN;
    END IF;

    FOR t IN
        SELECT c.table_name
          FROM information_schema.columns c
          JOIN information_schema.tables tb
            ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name
           AND tb.table_type = 'BASE TABLE'
         WHERE c.table_schema = 'plenum_cafm'
           AND c.column_name = 'organization_id'
           AND c.data_type = 'uuid'
           AND c.table_name NOT IN ('ops_audit_log', 'sites')
         ORDER BY c.table_name
    LOOP
        EXECUTE format(
            'UPDATE plenum_cafm.%I SET organization_id = $1 WHERE organization_id IS NULL',
            t.table_name) USING only_org;
        GET DIAGNOSTICS n = ROW_COUNT;
        IF n > 0 THEN
            RAISE NOTICE 'stamp_single_org: %.% rows stamped', t.table_name, n;
        END IF;
    END LOOP;
END $$;
