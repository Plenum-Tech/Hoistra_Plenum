-- Add a sub-national grouping column to the compliance register.
-- Idempotent (safe to re-run).
--
-- country_code alone cannot answer "how does compliance look in Scotland" or "which
-- Washington vendors are lapsed". The column is deliberately named `state` per the
-- reporting requirement, but it holds whatever the country's first-level division is:
--
--   UK   England | Scotland | Wales | Northern Ireland   (regulation genuinely differs:
--                                                         Fire Safety Order 2005 in
--                                                         England & Wales vs the Fire
--                                                         (Scotland) Act 2005)
--   US   two-letter state code — WA, CA, NY
--   UAE  emirate — Dubai, Abu Dhabi, Sharjah
--
-- Free text rather than an enum: the values differ per country, a new country must not
-- require a migration, and an unrecognised value is better stored than dropped.

ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS state VARCHAR(64);

COMMENT ON COLUMN plenum_cafm.compliance_certificates.state
    IS 'First-level division within country_code — UK nation, US state, UAE emirate. '
       'Used to group compliance reporting below country level. NULL where the source '
       'document did not state a location.';

-- Reporting reads country_code + state together, so index the pair.
CREATE INDEX IF NOT EXISTS ix_compliance_certificates_country_state
    ON plenum_cafm.compliance_certificates (country_code, state);
