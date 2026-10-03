-- Site link for building certificates when plenum_cafm.sites is not UUID-keyed.
--
-- compliance_certificates.site_id is UUID, but plenum_cafm.sites keys on
-- site_id VARCHAR(50) in this deployment, so no building certificate could ever record
-- which building it belongs to: per-site coverage saw one "Portfolio (no site linked)"
-- bucket for the whole register. site_ref carries the site key when it is not UUID-shaped;
-- site_id still carries it when it is. Exactly one of the two is set.
ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS site_ref VARCHAR(120);

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_compliance_certificates_site_ref
    ON plenum_cafm.compliance_certificates (cert_scope, site_ref);
