-- The pages a person may open: the second access boundary, beside the buildings.
--
-- user_buildings records WHERE a plain user may look. These columns record WHAT: an
-- administrator may give a plain user an access role (a label) together with the pages that
-- user may open, and data owned by any other page is then withheld from them everywhere.
--
--   access_role         NULL for a normal user, who opens every page. Otherwise one of
--                       portfolio_management | property_manager | general_user |
--                       compliance_officer. A role ticks no page by itself.
--   allowed_pages       NULL for a normal user. Otherwise the ticked pages, a subset of
--                       buildings | compliance | energy | vendors | ops | assets.
--   access_narrowed_at  When this person's pages last got narrower. Chat turns and report
--                       runs made before it may hold data from a page they no longer have,
--                       and are not shown to them. NULL if their access never narrowed.
--
-- The rules live in the service (engines/auth/pages.py), not in constraints: reading fails
-- closed there — an unknown page is dropped, a role with no valid page sees nothing — and a
-- CHECK added here could fail on a row written by an older build and stop this file applying.
-- Nothing is backfilled: every existing account stays a normal user.
--
-- The service runs without these columns (everybody is a normal user and a role cannot be
-- assigned), so this file may be applied before or after the code that reads it.
--
-- RUN BY HAND, never by the service. It lives in migrations/manual/ so the startup apply
-- (src/db.py, which reads migrations/*.sql only) cannot reach it: deploying a build must
-- never alter plenum_cafm.users on its own, whatever AUTO_MIGRATE_ON_STARTUP says. Each ALTER
-- needs a brief ACCESS EXCLUSIVE lock on users, and every sign-in reads users, so a lock it
-- cannot get at once is given up after five seconds rather than queueing sign-in behind it.
-- If it gives up, run the file again; every statement is idempotent.
SET lock_timeout = '5s';
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS access_role varchar(32);
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS allowed_pages text[];
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS access_narrowed_at timestamptz;

COMMENT ON COLUMN plenum_cafm.users.access_role IS
  'NULL = normal user (every page) | portfolio_management | property_manager | general_user | compliance_officer';
COMMENT ON COLUMN plenum_cafm.users.allowed_pages IS
  'NULL = every page; else a subset of buildings | compliance | energy | vendors | ops | assets';
COMMENT ON COLUMN plenum_cafm.users.access_narrowed_at IS
  'when the pages last got narrower; earlier chat turns and report runs are hidden from this user';
