-- The document is checked against the building before it is bound to it.
--
-- A contract filed under the wrong property, a vendor certificate for a firm that has never
-- worked there, a Bishopsgate document uploaded under Riverside: each of those is one click
-- to make and months to notice, and each of them moves a compliance position that somebody
-- will later act on. An admin approving it is not a check — an admin can select the wrong
-- building as easily as anyone, so the same protocol runs for everyone.
--
-- A validation case is that protocol made durable. It holds what was claimed by the
-- document, what the building's own ontology says, every check that was run and how it
-- came out, the building that looks more likely when one does, the question put to the
-- uploader, their explanation, the agent's assessment of it, and the explicit yes or no
-- that released or refused the ingestion. Nothing is bound while a case is open.
--
-- The decision trail already has a home — ingestion_audit_events, from access_control.sql —
-- so this adds the case those events belong to rather than a second trail beside it.

CREATE TABLE IF NOT EXISTS plenum_cafm.ingestion_validation_cases (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id         UUID,
    actor_user_id           UUID,
    actor_role              VARCHAR(16),
    session_id              VARCHAR(120),
    -- what is being filed
    document_name           TEXT,
    document_id             UUID,
    document_sha256         VARCHAR(64),
    doc_type                VARCHAR(60),
    -- where it was filed, and where it ended up: selected_building_id is what the uploader
    -- chose, final_building_id is what was agreed. They differ on a reassignment, and the
    -- difference is the whole point of keeping both.
    selected_building_id    UUID,
    suggested_building_id   UUID,
    final_building_id       UUID,
    -- verdict: matched | uncertain | mismatch. status: the case's position in the protocol.
    verdict                 VARCHAR(20) NOT NULL DEFAULT 'uncertain',
    confidence              NUMERIC(4,3),
    status                  VARCHAR(28) NOT NULL DEFAULT 'needs_confirmation',
    outcome                 VARCHAR(32),
    -- the evidence: every check run, each with what the document said, what the building
    -- says, and which way it points. Kept in full so a decision can be re-read later.
    findings                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    claims                  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ontology                JSONB       NOT NULL DEFAULT '{}'::jsonb,
    candidates              JSONB       NOT NULL DEFAULT '[]'::jsonb,
    -- the conversation: what was asked, what was answered, what the agent made of it
    question                TEXT,
    explanation             TEXT,
    assessment              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    -- the decision
    decided_by              UUID,
    decided_at              TIMESTAMPTZ,
    decision_note           TEXT,
    -- the conversation as a list, in order, for the interface to render
    events                  JSONB       NOT NULL DEFAULT '[]'::jsonb,
    detail                  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ivc_org_status
    ON plenum_cafm.ingestion_validation_cases (organization_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ivc_building
    ON plenum_cafm.ingestion_validation_cases (selected_building_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ivc_actor
    ON plenum_cafm.ingestion_validation_cases (actor_user_id, created_at DESC);

-- The audit event says which case it belongs to, so "what warning did they see" and "what
-- did they answer" are one join away from "who approved it".
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'ingestion_audit_events'
                      AND column_name = 'case_id') THEN
        ALTER TABLE plenum_cafm.ingestion_audit_events ADD COLUMN case_id UUID;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_iae_case ON plenum_cafm.ingestion_audit_events (case_id);
