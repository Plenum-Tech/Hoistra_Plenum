-- Building/site identification columns on compliance certificates.
-- Building-scope certs capture the building name + reference/number from the document so the
-- portfolio lists and chat cards can show which building each certificate belongs to.
ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS building_name VARCHAR(255);

ALTER TABLE plenum_cafm.compliance_certificates
    ADD COLUMN IF NOT EXISTS building_reference VARCHAR(120);
