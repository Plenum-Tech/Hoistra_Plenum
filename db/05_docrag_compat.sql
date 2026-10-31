-- doc-rag's half of plenum_cafm, which 01_schema.sql was captured too early to include.
--
-- Uploading a document goes through doc-rag, and doc-rag writes to two tables in this schema:
-- plenum_cafm.ingestion_documents for the file, plenum_cafm.document_chunks for its extracted
-- text. Against a database built from 01_schema.sql both writes fail:
--
--     psycopg.errors.UndefinedColumn: column "mime_type" of relation
--     "ingestion_documents" does not exist
--
-- and the upload still answers 200 with "Ingestion complete", because the failure is caught
-- and summarised rather than raised. So a fresh test environment silently cannot ingest a
-- document — the one thing an ingestion test is for.
--
-- Both differences are between this schema and the production database that doc-rag has been
-- writing into for real, so the shapes below are read from there rather than from doc-rag's
-- model file: these are the columns it has actually been using, with the types it used.
--
-- Safe to re-run.

-- ── ingestion_documents: five columns added after 01_schema.sql was taken ────────────
--
-- Purely additive, all nullable. The CAFM services that already read this table select the
-- columns they know by name and are unaffected.
ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS mime_type     character varying(128);
ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS document_type character varying(64);
ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS source_uri    character varying(1024);
ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS checksum      character varying(128);
ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS updated_at    timestamp with time zone DEFAULT now();

-- ── document_chunks: two different tables share this name ────────────────────────────
--
-- The one in 01_schema.sql is keyed on document_id and stores `content`; the one doc-rag
-- writes is keyed on ingestion_id and stores `chunk_text`. Not a version of each other —
-- different tables that happen to be called the same thing, and no amount of ADD COLUMN
-- reconciles them.
--
-- Replaced rather than migrated, because in a database built from these files it is empty.
-- That is asserted rather than assumed: if this ever runs somewhere the table has rows, it
-- stops instead of destroying them.
DO $$
DECLARE n bigint;
BEGIN
    IF to_regclass('plenum_cafm.document_chunks') IS NULL THEN
        RETURN;
    END IF;
    EXECUTE 'SELECT count(*) FROM plenum_cafm.document_chunks' INTO n;
    IF n > 0 THEN
        RAISE EXCEPTION
            'plenum_cafm.document_chunks holds % rows — refusing to replace it. Inspect '
            'them first: this migration expects the empty table 01_schema.sql creates.', n;
    END IF;
    DROP TABLE plenum_cafm.document_chunks CASCADE;
END $$;

CREATE TABLE IF NOT EXISTS plenum_cafm.document_chunks (
    id               uuid DEFAULT gen_random_uuid() NOT NULL,
    -- The document this chunk came from. doc-rag's model calls it document_id and maps it
    -- onto this column; the name here is the one the table has.
    ingestion_id     uuid,
    source_filename  character varying(500),
    doc_type         character varying(50),
    chunk_index      integer NOT NULL,
    chunk_text       text NOT NULL,
    -- 1536 dimensions: text-embedding-3-small / ada-002. The extension is already required
    -- by 01_schema.sql, so this adds no new dependency.
    embedding        vector(1536),
    heading          character varying(255),
    metadata         jsonb DEFAULT '{}'::jsonb,
    created_at       timestamp with time zone DEFAULT now(),
    page_start       integer,
    page_end         integer,
    block_type       character varying(32),
    normalized_text  text,
    embedding_model  character varying(128),
    -- The structured row a chunk is grounded to, by primary key — how an answer cites the
    -- record it came from rather than only the paragraph.
    source_table     character varying(128),
    row_pk           character varying(128),
    pk_column        character varying(128),
    CONSTRAINT document_chunks_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_ingestion ON plenum_cafm.document_chunks USING btree (ingestion_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_filename  ON plenum_cafm.document_chunks USING btree (source_filename);

-- The vector index is deliberately not created here. ivfflat needs the table populated to
-- pick its lists, and building one on an empty table gives a structure that indexes nothing
-- and has to be rebuilt anyway. Production uses:
--
--   CREATE INDEX idx_doc_chunks_embedding ON plenum_cafm.document_chunks
--     USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);
--
-- Run that once there are chunks to index.
