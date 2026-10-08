-- plenum_scoped: one view per plenum_cafm table, filtered to the caller who set the settings.
--
-- svc-udr lets an agent run caller-supplied SELECT for joins and aggregates. A per-table WHERE
-- can be composed for read_records, where the table is known; it cannot be injected into
-- arbitrary SQL. So the restriction is moved into the objects the SQL names: every table gets a
-- view that filters itself, and execute_select runs against plenum_scoped instead of
-- plenum_cafm. Joins, aggregates and CTEs keep working unchanged.
--
-- The caller is carried in three per-transaction settings, set with SET LOCAL by svc-udr and
-- gone when the transaction ends:
--
--   app.udr_org           the caller's company, as text
--   app.udr_buildings     comma-separated building ids, or UNSET for "every building"
--   app.udr_unrestricted  '1' for a superadmin, who is not filtered
--
-- current_setting(name, true) returns NULL when unset rather than raising, which is what makes
-- the three states expressible:
--
--   app.udr_org unset or '' -> organization_id::text = NULL/'' -> no rows. A caller whose
--                              company is unknown sees nothing, which is the safe reading.
--   app.udr_buildings unset
--                    or ''  -> no building filter. Matches building_ids IS NONE in the service.
--
-- There is deliberately NO spelling here for "allocated to no building". NULL and '' both have
-- to mean "no filter", because a pooled connection cannot be relied on to tell them apart
-- across a SET LOCAL, and a setting that is a string has no third empty value. Conflating the
-- two would turn "allocated to nothing" into "allocated to everything" — the exact failure this
-- change exists to stop. svc-udr answers that case with no rows before any SQL runs; see
-- scope.allocated_to_nothing.
--
-- Rows whose building_id IS NULL are excluded for a building-restricted caller: such a row is
-- not attributable to any building they hold. services/scope.py applies the same rule, and the
-- two must agree or the same question answers differently through two routes.
--
-- Tables with neither column are exposed unfiltered, by decision: most are reference data
-- (countries, currencies, regulation_packs) or per-user rows. Some are audit logs, and those
-- stay readable. Narrowing them needs a join route per table and is separate work.
--
-- Idempotent: the schema is created if missing and every view is CREATE OR REPLACE, so a
-- restart re-runs this harmlessly. It is also how a new table gets a view — re-running after a
-- schema change is the intended way to refresh, since this generates from information_schema
-- rather than from a hardcoded list.
--
-- NOTE ON ENFORCEMENT. This restricts what the scoped views return. It does not revoke the
-- application role's access to plenum_cafm itself, because every other service writes there
-- with the same credentials. svc-udr rewrites qualified plenum_cafm references to plenum_scoped
-- before running caller SQL; that is a redirect, not a privilege boundary. A real boundary
-- needs a separate read-only role for UDR with no rights on plenum_cafm.

CREATE SCHEMA IF NOT EXISTS plenum_scoped;

DO $mig$
DECLARE
    r           record;
    has_org     boolean;
    has_bld     boolean;
    conds       text[];
    body        text;
BEGIN
    FOR r IN
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = 'plenum_cafm'
           -- Closed to the Universal Database Reader entirely: live credentials and
           -- bearer-equivalent tokens. Building no view is the enforcement for caller-supplied
           -- SELECT — naming one fails to resolve rather than reaching the base table. Must
           -- stay in step with DENIED_TABLES in svc-udr/src/services/scope.py.
           AND table_name NOT IN ('auth_otp_codes', 'auth_sessions',
                                  'auth_role_changes', 'approval_action_tokens')
         ORDER BY table_name
    LOOP
        SELECT bool_or(column_name = 'organization_id'),
               bool_or(column_name = 'building_id')
          INTO has_org, has_bld
          FROM information_schema.columns
         WHERE table_schema = 'plenum_cafm'
           AND table_name   = r.table_name;

        conds := ARRAY[]::text[];

        IF has_org THEN
            conds := conds || ARRAY[
                't.organization_id::text = current_setting(''app.udr_org'', true)'];
        END IF;

        IF has_bld THEN
            conds := conds || ARRAY[
                '(coalesce(current_setting(''app.udr_buildings'', true), '''') = '''' OR '
                || 't.building_id::text = ANY(string_to_array('
                || 'current_setting(''app.udr_buildings'', true), '','')))'];
        END IF;

        IF array_length(conds, 1) IS NULL THEN
            -- No scope column on this table: exposed as-is.
            body := 'SELECT t.* FROM plenum_cafm.' || quote_ident(r.table_name) || ' t';
        ELSE
            body := 'SELECT t.* FROM plenum_cafm.' || quote_ident(r.table_name) || ' t'
                 || ' WHERE current_setting(''app.udr_unrestricted'', true) = ''1'''
                 || ' OR (' || array_to_string(conds, ' AND ') || ')';
        END IF;

        BEGIN
            EXECUTE 'CREATE OR REPLACE VIEW plenum_scoped.' || quote_ident(r.table_name)
                 || ' AS ' || body;
        EXCEPTION WHEN OTHERS THEN
            -- One unviewable table must not stop the other 223. CREATE OR REPLACE VIEW cannot
            -- change an existing view's column list, so a table that gained a column since the
            -- last run fails here; it is dropped and rebuilt rather than left stale.
            BEGIN
                EXECUTE 'DROP VIEW IF EXISTS plenum_scoped.' || quote_ident(r.table_name)
                     || ' CASCADE';
                EXECUTE 'CREATE VIEW plenum_scoped.' || quote_ident(r.table_name)
                     || ' AS ' || body;
            EXCEPTION WHEN OTHERS THEN
                RAISE WARNING 'plenum_scoped: skipped %: %', r.table_name, SQLERRM;
            END;
        END;
    END LOOP;
END
$mig$;
