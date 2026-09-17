-- The building a person is currently working in, alongside the ones they are allowed.
--
-- user_buildings already records which buildings a plain user is ALLOWED — the access
-- boundary. This is a different fact: which one of those they have SELECTED to work in
-- right now, so a question asked from the shell is answered for that building rather than
-- for every building they hold. It is a per-person default, not a grant: it may only ever
-- name a building the person is allocated to (or, for an admin, one in their company), and
-- Scope narrows to it only when it does. It is never a way to see more.
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS selected_building_id uuid;

-- A status the product now distinguishes. active / invited / pending_verification sign in;
-- inactive is a deactivated account kept whole so it can be reactivated; deleted is a
-- soft-deleted account with its personal details scrubbed and its id kept, because five
-- tables and the append-only audit log name it and a hard delete would be refused.
COMMENT ON COLUMN plenum_cafm.users.status IS
  'active | invited | pending_verification | inactive | deleted (soft; details scrubbed, id kept)';
