-- The platform role gets a column of its own, and organisations stop deleting people.
--
-- ── one column, two meanings ────────────────────────────────────────────────────────
--
-- plenum_cafm.users.role is a CAFM field. It holds what a person DOES: 'HVAC Specialist',
-- 'Maintenance Planner', 'Facilities Director'. The auth engine wanted the same column to
-- hold what a person may DO on this platform — superadmin, admin or user, a closed set of
-- three — and the two are not the same question. A person can be a Facilities Director
-- with no admin rights, or a Maintenance Tech who administers the tenant.
--
-- Sharing the column made both meanings wrong at once. On a CAFM database every job title
-- ranked 0, so every member of staff was "no role", superadmin_exists answered false and
-- the bootstrap path stayed open for whoever registered next. On a fresh database it
-- worked, which is why nobody saw it — and adding the closed-set CHECK to a table holding
-- job titles simply failed, so the constraint that was supposed to make the set safe was
-- the first thing to fall off.
--
-- platform_role is now the auth engine's column and role goes back to being the job
-- title. Existing accounts keep the platform role they had: where role already held one
-- of the three, it is carried across.

ALTER TABLE plenum_cafm.users
    ADD COLUMN IF NOT EXISTS platform_role VARCHAR(20) NOT NULL DEFAULT 'user';

-- Carry over what the shared column was holding, where it was holding a platform role.
-- Only touches rows still at the default, so re-running cannot demote anyone.
UPDATE plenum_cafm.users
   SET platform_role = role
 WHERE role IN ('superadmin', 'admin', 'user')
   AND platform_role = 'user'
   AND role <> 'user';

-- Closed in the database, not only in Python. A typo in a script — 'Admin', 'superuser',
-- 'fm' — would otherwise become an account with a role no code checks for, which fails
-- closed for that person, silently, and looks like a bug in the app.
--
-- This CHECK can be added unconditionally where the old one could not: the column is new,
-- so nothing is in it that was not put there by the line above.
DO $platform_role_check$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_platform_role') THEN
        ALTER TABLE plenum_cafm.users
            ADD CONSTRAINT ck_users_platform_role
            CHECK (platform_role IN ('superadmin', 'admin', 'user'));
    END IF;
END
$platform_role_check$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_platform_role
    ON plenum_cafm.users (platform_role);

-- role is a job title again, so the closed set must come off it. Left in place it would
-- reject the next person hired as a 'Quality Inspector' on a database that happens to
-- have had the constraint applied when the column was empty.
ALTER TABLE plenum_cafm.users DROP CONSTRAINT IF EXISTS ck_users_role;

-- And give it room. An earlier version of auth_user_roles.sql declared role VARCHAR(20),
-- sized for 'superadmin'. Job titles are longer than that: 'Maintenance Supervisor' is 22.
DO $widen_role$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                  AND column_name = 'role'
                  AND character_maximum_length IS NOT NULL
                  AND character_maximum_length < 100) THEN
        ALTER TABLE plenum_cafm.users ALTER COLUMN role TYPE VARCHAR(100);
    END IF;
    -- NOT NULL DEFAULT 'user' was also a platform-role idea. A person with no job title
    -- recorded has no job title recorded; 'user' is not one.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                  AND column_name = 'role' AND is_nullable = 'NO') THEN
        ALTER TABLE plenum_cafm.users ALTER COLUMN role DROP NOT NULL;
        ALTER TABLE plenum_cafm.users ALTER COLUMN role DROP DEFAULT;
    END IF;
END
$widen_role$;

COMMENT ON COLUMN plenum_cafm.users.platform_role IS
    'What this account may do on the platform: superadmin | admin | user. Distinct from '
    'users.role, which is the job title and is CAFM''s field, not this service''s.';

-- ── deleting an organisation no longer deletes its staff ────────────────────────────
--
-- fk_users_organization was added ON DELETE CASCADE. On a live table that means removing
-- an organisation row silently removes every person in it — their accounts, and by the
-- cascades below every session and every one-time code with them. There is no
-- confirmation step anywhere in that sentence.
--
-- RESTRICT instead: an organisation with people in it cannot be deleted until they are
-- moved or removed, which is a decision someone has to make on purpose. Not SET NULL —
-- an account belonging to no tenant is not a safe resting state; it is an account that
-- can still sign in and whose scope nobody can name.
DO $auth_org_fk_restrict$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_users_organization' AND confdeltype = 'c') THEN
        ALTER TABLE plenum_cafm.users DROP CONSTRAINT fk_users_organization;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_organization')
    THEN
        ALTER TABLE plenum_cafm.users
            ADD CONSTRAINT fk_users_organization
            FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations (id)
            ON DELETE RESTRICT;
    END IF;
END
$auth_org_fk_restrict$;
