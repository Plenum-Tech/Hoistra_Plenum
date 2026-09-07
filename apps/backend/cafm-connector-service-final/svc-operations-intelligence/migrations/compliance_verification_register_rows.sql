-- CCC §8.2 — local store for ingested weekly register dumps (Channel A / data_dump).
-- register_dump.py reads/writes this table; without it, every data_dump verify 500s.

CREATE TABLE IF NOT EXISTS plenum_cafm.compliance_verification_register_rows
(
    id                     uuid        NOT NULL,
    certificate_type_code  varchar(80) NOT NULL,
    lookup_key             varchar(255) NOT NULL,
    payload                jsonb,
    source_file            varchar(255),
    ingested_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT compliance_verification_register_rows_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS ix_cvrr_code_key
    ON plenum_cafm.compliance_verification_register_rows (certificate_type_code, upper(lookup_key));

CREATE INDEX IF NOT EXISTS ix_cvrr_ingested_at
    ON plenum_cafm.compliance_verification_register_rows (ingested_at DESC);
