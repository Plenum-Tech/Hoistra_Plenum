-- Three platform roles, and the column that says which one an account has.
--
--   superadmin  the whole platform, across every organisation
--   admin       everything inside their own organisation
--   user        a facilities manager — the default, and what self-registration creates
--
-- WHY A COLUMN AND NOT plenum_cafm.roles. That table exists (roles, user_roles,
-- role_permissions, all CRUD in cafm-connector-service/routes/plenum_cafm/rbac.py) and
-- nothing anywhere enforces it. It is also scoped per organisation by
-- uq_roles_org_name, which a superadmin by definition is not: putting one there means
-- inventing an organisation for them to belong to, and then deciding what it means when
-- somebody deletes it.
--
-- So this column is the PLATFORM role — the one an access token carries and an endpoint
-- gates on. plenum_cafm.roles stays what it is: an unenforced per-organisation
-- permission grid. Two things with the word "role" in them is a real cost, and it is
-- smaller than the cost of a cross-organisation identity living in an
-- organisation-scoped table.

ALTER TABLE plenum_cafm.users
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user';

-- The set is closed in the database, not only in Python. A typo in a script — 'Admin',
-- 'superuser', 'fm' — would otherwise become an account with a role no code checks for,
-- which fails closed for that person and silently, and looks like a bug in the app.
DO $auth_role_check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_role') THEN
        ALTER TABLE plenum_cafm.users
            ADD CONSTRAINT ck_users_role
            CHECK (role IN ('superadmin', 'admin', 'user'));
    END IF;
END
$auth_role_check$;

-- Listing the operators of an organisation, and answering "is there a superadmin yet"
-- — which is what the bootstrap check asks on every registration.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_role
    ON plenum_cafm.users (role);

-- ── who changed whose role ──────────────────────────────────────────────────────────
--
-- Append-only. A privilege change is the single most useful line in an incident
-- timeline, and the one most worth editing afterwards, so nothing here is ever updated
-- or deleted — a correction is another row.
CREATE TABLE IF NOT EXISTS plenum_cafm.auth_role_changes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES plenum_cafm.users (id) ON DELETE CASCADE,
    -- Nullable: the platform itself promotes the bootstrap superadmin, and no person
    -- did that. Recording a human who was not involved would be worse than a null.
    changed_by   UUID REFERENCES plenum_cafm.users (id) ON DELETE SET NULL,
    from_role    VARCHAR(20),
    to_role      VARCHAR(20) NOT NULL,
    reason       TEXT,
    request_ip   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_auth_role_changes_user
    ON plenum_cafm.auth_role_changes (user_id, created_at DESC);

-- ── the invited state ───────────────────────────────────────────────────────────────
--
-- An account an operator created for someone else. It has no usable password: the
-- person sets their own through the reset flow, which proves they hold the mailbox at
-- the same time. See the note on POST /users in cafm-connector-service — that endpoint
-- used to take a password_hash straight from the request body, which let the caller
-- choose the digest for an account they were creating for somebody else.
COMMENT ON COLUMN plenum_cafm.users.status IS
    'active | pending_verification | invited | suspended. "invited" means an operator '
    'created the account and its password_hash is the unusable sentinel — the person '
    'sets a real one through the OTP reset flow.';
