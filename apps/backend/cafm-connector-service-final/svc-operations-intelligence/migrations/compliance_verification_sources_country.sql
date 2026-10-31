-- CCC 8.8 - country-aware verification sources: schema upgrade.
--
-- The original table was UK-only: the PK was certificate_type_code alone, so a US/UAE
-- certificate either found NO row (status "not_configured" - never actually checked) or,
-- worse, collided with a UK code and was verified against a UK regulator (e.g. UAE
-- PUBLIC_LIABILITY canonicalised to CONTRACTOR_PL -> the UK FCA register).
--
-- This migration upgrades an EXISTING deployment: add country_code (existing rows are UK
-- by definition) and swap the PK to (certificate_type_code, country_code). On a fresh
-- database the base compliance_verification_sources.sql already creates the table in this
-- shape, so every statement here is a guarded no-op.
--
-- MUST run BEFORE the base seed, because that seed upserts with
-- ON CONFLICT (certificate_type_code, country_code).

DO $$
BEGIN
    IF to_regclass('plenum_cafm.compliance_verification_sources') IS NULL THEN
        RETURN;  -- fresh DB: base migration creates the table already country-aware
    END IF;

    ALTER TABLE plenum_cafm.compliance_verification_sources
        ADD COLUMN IF NOT EXISTS country_code varchar(8) NOT NULL DEFAULT 'UK';

    -- Swap a single-column PK for the composite one (idempotent).
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'compliance_verification_sources_pkey'
          AND conrelid = 'plenum_cafm.compliance_verification_sources'::regclass
          AND array_length(conkey, 1) = 1
    ) THEN
        ALTER TABLE plenum_cafm.compliance_verification_sources
            DROP CONSTRAINT compliance_verification_sources_pkey;
        ALTER TABLE plenum_cafm.compliance_verification_sources
            ADD CONSTRAINT compliance_verification_sources_pkey
            PRIMARY KEY (certificate_type_code, country_code);
    END IF;
END $$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cvs_country
    ON plenum_cafm.compliance_verification_sources (country_code);
