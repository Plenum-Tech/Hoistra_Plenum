-- Every record that comes from a document points at it in the register.
--
-- An answer that needs a fact from a document - a contract's service-credit clause, a service
-- report's recommendation, a warranty's cover - finds that document through the record the
-- question is about (engines/document_search.py). Contracts, certificates, invoices and PPM
-- visits could already carry a document_id; work orders, maintenance plans, inspections,
-- warranties, asset documents and work-order attachments could not, so a question about a
-- building's maintenance or an asset's warranty had no path to the paper behind it (2 Oct 2026).
--
-- Nullable, no default, no foreign key: adding the column is a catalogue change only, and the
-- register's key is uuid on one database and text on the other, so joins compare ::text.
-- Re-run safely on every start; udr_scoped_views.sql sorts after this file and picks the new
-- columns up in plenum_scoped on the same start.

ALTER TABLE plenum_cafm.work_orders ADD COLUMN IF NOT EXISTS document_id uuid;

ALTER TABLE plenum_cafm.maintenance_plans ADD COLUMN IF NOT EXISTS document_id uuid;

ALTER TABLE plenum_cafm.inspections ADD COLUMN IF NOT EXISTS document_id uuid;

ALTER TABLE plenum_cafm.asset_warranties ADD COLUMN IF NOT EXISTS document_id uuid;

ALTER TABLE plenum_cafm.asset_documents ADD COLUMN IF NOT EXISTS document_id uuid;

ALTER TABLE plenum_cafm.work_order_attachments ADD COLUMN IF NOT EXISTS document_id uuid;

-- How a register document is handled: 'standard', or 'personal' for an identity paper (visa,
-- Emirates ID, passport, labour card). A personal document stays out of open search; only a
-- company admin within the building scope reads it, its numbers masked, every read logged.
ALTER TABLE plenum_cafm.documents ADD COLUMN IF NOT EXISTS sensitivity text;

-- A document linked to any record, as many as it concerns: a staff access list covering two
-- buildings sits on both. entity_type is building / asset / vendor / work_order / contract;
-- entity_id is that record's key as text (the keys are uuid on one database, text on the
-- other). A link is never deleted: removed_at ends it and the audit trail keeps who did what.
CREATE TABLE IF NOT EXISTS plenum_cafm.document_links (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     uuid NOT NULL,
    entity_type     text NOT NULL,
    entity_id       text NOT NULL,
    relation        text,
    organization_id uuid,
    created_by      uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    removed_by      uuid,
    removed_at      timestamptz
);

CREATE INDEX IF NOT EXISTS document_links_document_idx ON plenum_cafm.document_links (document_id);

CREATE INDEX IF NOT EXISTS document_links_entity_idx ON plenum_cafm.document_links (entity_type, entity_id);
