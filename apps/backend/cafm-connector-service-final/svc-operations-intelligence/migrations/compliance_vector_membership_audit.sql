-- CCC §3.2 — audit log for vector-DB membership Keep/Remove decisions (who / when /
-- which document). membership.py:decide_membership INSERTs here; without the table the
-- Keep/Remove chat action 500s.

CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_vector_membership_audit
(
    id               uuid        NOT NULL,
    document_id      uuid        NOT NULL,
    action           varchar(20) NOT NULL,   -- keep | remove
    actor            varchar(120),
    organization_id  uuid,
    detail           jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT compliance_vector_membership_audit_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_cvma_document_id
    ON plenum_cafm.compliance_vector_membership_audit (document_id);

CREATE INDEX IF NOT EXISTS ix_cvma_created_at
    ON plenum_cafm.compliance_vector_membership_audit (created_at DESC);
