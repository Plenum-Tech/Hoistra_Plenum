-- Phase 2 — DB-enforced append-only audit trail
-- ops_audit_log: INSERT allowed; UPDATE/DELETE blocked by trigger (and optional REVOKE).

CREATE OR REPLACE FUNCTION plenum_cafm.forbid_ops_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION
        'plenum_cafm.ops_audit_log is append-only: % is not permitted',
        TG_OP
        USING ERRCODE = 'integrity_constraint_violation';
    RETURN NULL;
END;
$fn$;

DROP TRIGGER IF EXISTS trg_ops_audit_no_update ON plenum_cafm.ops_audit_log;
CREATE TRIGGER trg_ops_audit_no_update
    BEFORE UPDATE ON plenum_cafm.ops_audit_log
    FOR EACH ROW
    EXECUTE PROCEDURE plenum_cafm.forbid_ops_audit_mutation();

DROP TRIGGER IF EXISTS trg_ops_audit_no_delete ON plenum_cafm.ops_audit_log;
CREATE TRIGGER trg_ops_audit_no_delete
    BEFORE DELETE ON plenum_cafm.ops_audit_log
    FOR EACH ROW
    EXECUTE PROCEDURE plenum_cafm.forbid_ops_audit_mutation();

COMMENT ON TABLE plenum_cafm.ops_audit_log IS
    'Phase 2 immutable ops audit — INSERT only. UPDATE/DELETE blocked by trigger.';

-- Best-effort privilege lockdown for common app roles (ignored if role missing).
DO $do$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['cafm', 'cafm_app', 'plenum_app', 'PUBLIC']
    LOOP
        BEGIN
            EXECUTE format('REVOKE UPDATE, DELETE ON plenum_cafm.ops_audit_log FROM %I', r);
        EXCEPTION
            WHEN undefined_object THEN NULL;
            WHEN insufficient_privilege THEN NULL;
        END;
    END LOOP;
END;
$do$;
