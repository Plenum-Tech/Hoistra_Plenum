-- Email-as-identity sign-up, OTP verification, and password reset.
--
-- plenum_cafm.users is NOT this service's table. cafm-connector-service declares it
-- (models/plenum_cafm.py :: User) and already carries everything an account needs:
-- a unique email, password_hash, email_verified, status and last_login_at. A second
-- users table here would mean two answers to "who is this person", so this migration
-- creates that table only where it is absent — in the exact shape the owning ORM
-- declares, down to the naive TIMESTAMP columns — and otherwise leaves it alone.
--
-- What is added on top is this service's own: the one-time codes, and the refresh
-- sessions a password reset has to be able to revoke.

-- ── the account table, only if this database does not already have it ────────────────
--
-- Column types match cafm-connector-service's ORM exactly. They are naive TIMESTAMP
-- rather than the TIMESTAMPTZ used everywhere else in this schema, which is not a
-- choice made here: matching the owner matters more than internal consistency, because
-- a mismatch means SQLAlchemy and this SQL disagree about what a stored instant means.
CREATE TABLE IF NOT EXISTS plenum_cafm.users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL,
    full_name       VARCHAR(255) NOT NULL,
    email           VARCHAR(255) NOT NULL,
    password_hash   VARCHAR(500) NOT NULL,
    phone           VARCHAR(50),
    phone2          VARCHAR(50),
    personnel_code  VARCHAR(100),
    hourly_rate     NUMERIC(10, 2),
    is_group        BOOLEAN NOT NULL DEFAULT false,
    status          VARCHAR(50) NOT NULL DEFAULT 'active',
    last_login_at   TIMESTAMP,
    email_verified  BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMP NOT NULL DEFAULT now(),
    updated_at      TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email)
);

-- The organisation link, added separately because plenum_cafm.organizations belongs to
-- another service too and may be created after this file runs. Adding it inline would
-- make the whole table depend on migration order; adding it here means the account
-- table lands either way and the constraint follows when its target exists.
DO $auth_org_fk$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_organization')
    THEN
        ALTER TABLE plenum_cafm.users
            ADD CONSTRAINT fk_users_organization
            FOREIGN KEY (organization_id) REFERENCES plenum_cafm.organizations (id)
            ON DELETE CASCADE;
    END IF;
END
$auth_org_fk$;

-- A server-side default for the primary key.
--
-- This migration's CREATE TABLE above gives id a DEFAULT. cafm-connector-service's ORM
-- declares the same column with a PYTHON-side default, so SQLAlchemy's create_all emits
-- it with none — and on every database where the ORM created the table first, any INSERT
-- that does not name id violates NOT NULL. That is not hypothetical: it is what happens
-- on a database built the documented way, and it made registration impossible while the
-- API still answered 202.
--
-- The auth engine now supplies the id itself, so this is belt and braces — but any other
-- writer of raw SQL against this table deserves the column to behave the way its own
-- declaration says it does.
ALTER TABLE plenum_cafm.users ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- ── what sign-in needs that the owning ORM does not carry ───────────────────────────

-- When the password last changed. Every access token issued before this instant is
-- refused, so "reset my password" actually ends the sessions of whoever had it.
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS password_changed_at TIMESTAMPTZ;

-- Consecutive failed sign-ins, and the lockout they earn. Without these a six-character
-- password is guessable at HTTP speed and nothing in the record would show it happening.
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS failed_login_count INT NOT NULL DEFAULT 0;
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

-- When the address was confirmed, not just that it was. "Verified" with no date cannot
-- answer whether it happened before or after an account was compromised.
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- Addresses are matched case-insensitively: Bala@x.com and bala@x.com are one mailbox,
-- and uq_users_email would happily hold both as separate accounts — two people who each
-- believe they own the same identity. Emails are stored lowercased, and this index is
-- the backstop. It is not relied on for correctness: every lookup normalises anyway, so
-- a deployment where this index cannot build (pre-existing case-variant duplicates)
-- still behaves correctly, it just no longer has the guard.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_users_email_lower
    ON plenum_cafm.users (lower(email));

-- ── one-time codes ──────────────────────────────────────────────────────────────────
--
-- The code itself is never stored. What is stored is HMAC-SHA256 over a per-row salt,
-- keyed by a server-side secret, so a copy of this table is not a set of live codes and
-- an offline attacker without the service secret cannot grind six digits against it.
CREATE TABLE IF NOT EXISTS plenum_cafm.auth_otp_codes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Nullable on purpose: a code is addressed to an EMAIL. If the account is later
    -- deleted the code must die with it, but a code must also be issuable before the
    -- row it belongs to is certain.
    user_id       UUID REFERENCES plenum_cafm.users (id) ON DELETE CASCADE,
    email         TEXT NOT NULL,
    purpose       TEXT NOT NULL,
    code_hash     TEXT NOT NULL,
    salt          TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    -- Guessing budget. Six digits is a million combinations, which is a great many at
    -- one attempt per request and none at all at five attempts per code.
    attempts      INT NOT NULL DEFAULT 0,
    max_attempts  INT NOT NULL DEFAULT 5,
    consumed_at   TIMESTAMPTZ,
    -- Superseded by a newer code, or killed by a completed reset. Distinct from consumed:
    -- one means it was used, the other means it never will be.
    invalidated_at TIMESTAMPTZ,
    request_ip    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_auth_otp_purpose
        CHECK (purpose IN ('email_verification', 'password_reset'))
);

-- The lookup every verification makes: the newest live code for this address and purpose.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_auth_otp_email_purpose
    ON plenum_cafm.auth_otp_codes (email, purpose, created_at DESC);
-- Sweeping expired rows.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_auth_otp_expires
    ON plenum_cafm.auth_otp_codes (expires_at);

-- ── refresh sessions ────────────────────────────────────────────────────────────────
--
-- Access tokens are short-lived and stateless; refresh tokens are rows, because a
-- password reset that cannot end an attacker's session is not a reset. The token is
-- stored as a SHA-256 digest — a leaked backup of this table is not a set of live
-- credentials.
CREATE TABLE IF NOT EXISTS plenum_cafm.auth_sessions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES plenum_cafm.users (id) ON DELETE CASCADE,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    issued_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at         TIMESTAMPTZ NOT NULL,
    revoked_at         TIMESTAMPTZ,
    revoked_reason     TEXT,
    user_agent         TEXT,
    request_ip         TEXT,
    last_used_at       TIMESTAMPTZ
);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_auth_sessions_user
    ON plenum_cafm.auth_sessions (user_id, expires_at DESC);
