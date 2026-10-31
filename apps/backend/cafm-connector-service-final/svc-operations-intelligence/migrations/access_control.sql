-- Access control: companies, invitations, building allocation, usage and the ingestion audit.
--
-- Three things the platform has to be able to say and today cannot.
--
-- Which company a building belongs to. plenum_cafm.buildings has no organization_id at all,
-- and sites.organization_id is an INTEGER against a UUID-keyed organizations table, so
-- nothing joins. Every query in the service takes organization_id from the client — thirty
-- of them — and none derives it from the signed-in caller. Tenancy is therefore a
-- convention, not a boundary. The building is the unit everything else hangs off (country,
-- regulations, vendors, assets, documents, compliance, UDR), so the building is where the
-- boundary goes, and it has to know its company first.
--
-- Which buildings a user may see. user_buildings is the allocation a company admin makes;
-- a user with rows here sees only those buildings, a user with none sees nothing, and an
-- admin or superadmin is not consulted against it at all.
--
-- Whether a user may ingest. can_ingest is per user and defaults to no: a person who can
-- read a building's records is not thereby someone who can add to them.
--
-- Plus the two ledgers the consoles read from — platform_usage_events for credits and
-- activity, ingestion_audit_events for who submitted what, what warning they saw, what they
-- said, and who approved the outcome.
--
-- Idempotent throughout. Every ALTER checks the column first and every CREATE is IF NOT
-- EXISTS, because the runner applies this on each start and retries on ordering.

-- ── buildings know their company ─────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'buildings'
                      AND column_name = 'organization_id') THEN
        ALTER TABLE plenum_cafm.buildings ADD COLUMN organization_id UUID;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_buildings_organization ON plenum_cafm.buildings (organization_id);

-- A portfolio with exactly one company is that company's portfolio. Where there are
-- several, nothing here guesses: the column stays NULL and the API reports the count of
-- unplaced buildings so a person assigns them. Assigning one company's building to another
-- hands over its documents, certificates and readings, and nothing about it looks wrong
-- afterwards.
DO $$
DECLARE only_org UUID;
BEGIN
    IF (SELECT count(*) FROM plenum_cafm.organizations) = 1 THEN
        SELECT id INTO only_org FROM plenum_cafm.organizations;
        UPDATE plenum_cafm.buildings SET organization_id = only_org
         WHERE organization_id IS NULL;
    END IF;
END $$;

-- ── companies carry what the super admin console shows ───────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations'
                      AND column_name = 'country_code') THEN
        ALTER TABLE plenum_cafm.organizations ADD COLUMN country_code VARCHAR(3);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations'
                      AND column_name = 'admin_email') THEN
        ALTER TABLE plenum_cafm.organizations ADD COLUMN admin_email VARCHAR(255);
    END IF;
    -- created: account exists, nobody invited. onboarding: administrator invited, not yet
    -- activated. active: the administrator has signed in. Distinct from `status`, which is
    -- operational (active/suspended) and already in use.
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations'
                      AND column_name = 'lifecycle') THEN
        ALTER TABLE plenum_cafm.organizations
            ADD COLUMN lifecycle VARCHAR(16) NOT NULL DEFAULT 'created';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'organizations'
                      AND column_name = 'created_by') THEN
        ALTER TABLE plenum_cafm.organizations ADD COLUMN created_by UUID;
    END IF;
END $$;

-- A company that already has an active admin is active, whatever the default says.
UPDATE plenum_cafm.organizations o
   SET lifecycle = 'active'
 WHERE lifecycle = 'created'
   AND EXISTS (SELECT 1 FROM plenum_cafm.users u
                WHERE u.organization_id = o.id
                  AND u.platform_role IN ('admin', 'superadmin')
                  AND lower(u.status) = 'active');

-- ── users: ingestion right, title, invitation trail ──────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                      AND column_name = 'can_ingest') THEN
        ALTER TABLE plenum_cafm.users ADD COLUMN can_ingest BOOLEAN NOT NULL DEFAULT FALSE;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                      AND column_name = 'job_title') THEN
        ALTER TABLE plenum_cafm.users ADD COLUMN job_title VARCHAR(120);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                      AND column_name = 'invited_at') THEN
        ALTER TABLE plenum_cafm.users ADD COLUMN invited_at TIMESTAMPTZ;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                      AND column_name = 'invited_by') THEN
        ALTER TABLE plenum_cafm.users ADD COLUMN invited_by UUID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                      AND column_name = 'activated_at') THEN
        ALTER TABLE plenum_cafm.users ADD COLUMN activated_at TIMESTAMPTZ;
    END IF;
END $$;

-- Admins ingest by definition. Existing plain users keep the default of no, which a company
-- admin then grants per person — the console's whole premise.
UPDATE plenum_cafm.users SET can_ingest = TRUE
 WHERE platform_role IN ('admin', 'superadmin') AND can_ingest = FALSE;

-- ── the allocation ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS plenum_cafm.user_buildings (
    user_id     UUID        NOT NULL,
    building_id UUID        NOT NULL,
    granted_by  UUID,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, building_id)
);
CREATE INDEX IF NOT EXISTS ix_user_buildings_building ON plenum_cafm.user_buildings (building_id);

-- ── invitations ──────────────────────────────────────────────────────────────────────
-- The token is stored hashed, like a password: the email carries the only copy of the
-- plaintext, and a table anyone with read access can query must not be a second one.
CREATE TABLE IF NOT EXISTS plenum_cafm.invitations (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID        NOT NULL,
    email           VARCHAR(255) NOT NULL,
    full_name       VARCHAR(255),
    role            VARCHAR(16) NOT NULL DEFAULT 'user',
    can_ingest      BOOLEAN     NOT NULL DEFAULT FALSE,
    building_ids    UUID[]      NOT NULL DEFAULT '{}',
    token_hash      TEXT        NOT NULL UNIQUE,
    invited_by      UUID,
    expires_at      TIMESTAMPTZ NOT NULL,
    accepted_at     TIMESTAMPTZ,
    accepted_user_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_invitations_org_email
    ON plenum_cafm.invitations (organization_id, lower(email));

-- ── usage and credits ────────────────────────────────────────────────────────────────
-- One row per billable thing that happened. Credits are computed at write time from the
-- tariff in force, so a later tariff change does not rewrite history. kind is one of
-- query | ingest | api_request.
CREATE TABLE IF NOT EXISTS plenum_cafm.platform_usage_events (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    user_id         UUID,
    building_id     UUID,
    kind            VARCHAR(24) NOT NULL,
    credits         NUMERIC(12,2) NOT NULL DEFAULT 0,
    detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_usage_org_time ON plenum_cafm.platform_usage_events (organization_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_usage_user_time ON plenum_cafm.platform_usage_events (user_id, occurred_at DESC);

-- ── the ingestion audit trail ────────────────────────────────────────────────────────
-- If a document is ever questioned: who submitted it, what warning they were shown, what
-- they said about it, and who approved the final action. outcome is one of
-- accepted | reassigned | overridden | rejected | approved_on_confirmation.
CREATE TABLE IF NOT EXISTS plenum_cafm.ingestion_audit_events (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id             UUID,
    actor_user_id               UUID,
    actor_role                  VARCHAR(16),
    document_name               TEXT,
    document_id                 UUID,
    building_id                 UUID,
    reassigned_to_building_id   UUID,
    warning                     TEXT,
    explanation                 TEXT,
    outcome                     VARCHAR(32) NOT NULL,
    approved_by                 UUID,
    detail                      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    occurred_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ingestion_audit_org_time
    ON plenum_cafm.ingestion_audit_events (organization_id, occurred_at DESC);
