-- Add the city/county grouping level below `state`, and index the full hierarchy.
-- Idempotent (safe to re-run). Companion to compliance_certificates_state.sql.
--
-- `state` holds the country's first-level division; `region` holds the level below it.
-- The distinction matters because they are not interchangeable:
--
--   state   England | Scotland | Wales | Northern Ireland   ← regulation keys off this
--                                                             (Fire Safety Order 2005 in
--                                                             England & Wales vs the Fire
--                                                             (Scotland) Act 2005)
--   region  London | Norfolk | Greater Manchester           ← how FM reporting is read
--
-- Putting London in `state` alongside England would make them siblings in a grouped
-- report, hiding that one contains the other and breaking the nation-level regulation
-- mapping. Two columns keep country -> state -> region a real hierarchy.
--
-- Values are stored spelled out in full, never abbreviated, so a report reads as written.

ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS region VARCHAR(64);

COMMENT ON COLUMN plenum_cafm.compliance_certificates.region
    IS 'City or county within state — London, Norfolk, Greater Manchester. The level '
       'below state in the reporting hierarchy. NULL where the location is unknown.';

-- Reporting reads the hierarchy top-down, so index it in that order.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_compliance_certificates_country_state_region
    ON plenum_cafm.compliance_certificates (country_code, state, region);
