-- Refresh rotation that a second browser tab survives.
--
-- Two faults, reported from the frontend integration, both caused by rotation ending the
-- session row rather than the token:
--
--   a rotation minted a new session id, and principal_from_token refuses an access token
--   whose sid is revoked — so the moment one tab refreshed, the access token every other
--   tab was holding became invalid and the next bearer call signed the person out.
--
--   two tabs restored together both present the same stored refresh token; the second
--   arrival matched a row already marked revoked, which is the signature of a stolen
--   token, so every session was revoked. A browser restore looked exactly like theft.
--
-- The session row now outlives the token. Rotation replaces refresh_token_hash in place
-- and keeps the one it replaced, so the same token arriving twice within a few seconds is
-- recognised as the concurrent exchange it is rather than as a replay — while the same
-- token arriving later still is one, and still ends every session.

ALTER TABLE plenum_cafm.auth_sessions
    ADD COLUMN IF NOT EXISTS prev_refresh_token_hash TEXT;

ALTER TABLE plenum_cafm.auth_sessions
    ADD COLUMN IF NOT EXISTS rotated_at TIMESTAMPTZ;

-- Rotation looks the presented digest up against both columns in one statement, so the
-- previous hash needs its own index. Not unique: a hash is briefly the current value of
-- one row and the previous value of that same row, and after the next rotation two
-- different sessions could legitimately hold no value at all here.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_auth_sessions_prev_refresh
    ON plenum_cafm.auth_sessions (prev_refresh_token_hash)
    WHERE prev_refresh_token_hash IS NOT NULL;

COMMENT ON COLUMN plenum_cafm.auth_sessions.prev_refresh_token_hash IS
    'The digest this session held before its last rotation. Accepted for a few seconds '
    'after the rotation so that two tabs exchanging the same stored token concurrently '
    'are not read as a replay; rejected as theft after that.';

COMMENT ON COLUMN plenum_cafm.auth_sessions.rotated_at IS
    'When refresh_token_hash was last replaced. The grace window for '
    'prev_refresh_token_hash is measured from here.';
