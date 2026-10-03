-- Hoistra — bootstrap: the one row a brand-new database needs to be usable.
--
-- 01_schema.sql gives you 145 empty tables. That is a correct database and a useless
-- one: POST /api/auth/register refuses with
--
--     "This deployment has no organisation to attach an account to."
--
-- because every account belongs to an organisation and there is not one yet. Registration
-- refuses rather than inventing a tenant, which is right — putting an account in the
-- wrong tenant is a mistake that never announces itself afterwards — but it does mean a
-- fresh database cannot accept its first user until this file runs.
--
-- So: exactly one organisation, and nothing else. No users, no buildings, no assets.
-- The first person to register lands here, and everything they create hangs off it.
--
-- The id is fixed rather than generated so it can be copied into the environment as
-- AUTH_DEFAULT_ORGANIZATION_ID without anyone having to look it up first. Change the
-- name to your own; the id only has to be stable, not meaningful.

INSERT INTO plenum_cafm.organizations (id, name, status)
VALUES ('00000000-0000-0000-0000-000000000001', 'Your Organisation', 'active')
ON CONFLICT (id) DO NOTHING;

-- Then, before starting svc-operations-intelligence:
--
--     AUTH_DEFAULT_ORGANIZATION_ID=00000000-0000-0000-0000-000000000001
--
-- Strictly optional. With exactly one organisation on the platform, registration uses it
-- without being told. Setting it matters the moment a second organisation exists: from
-- then on registration refuses to guess, and this says which one self-registered accounts
-- should join.
