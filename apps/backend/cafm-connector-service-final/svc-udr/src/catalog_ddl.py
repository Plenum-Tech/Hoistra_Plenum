"""DDL for the table catalogue — one row per plenum_cafm table, with an embedding.

Kept import-free so ``db.ensure_udr_tables`` can apply it at startup without pulling the
service layer (which imports ``db``) into a cycle.

The embedding is text-embedding-3-small (1536 dims), the same model doc-rag uses for
``document_chunks``, so a question embedded once can be matched against tables and against
documents without a second model. ``CREATE EXTENSION`` is idempotent and already present in
this database (doc-rag created it); it is here so a fresh database gets it too.
"""
from __future__ import annotations

CATALOG_TABLE = "udr_table_catalog"
EMBEDDING_DIM = 1536


def catalog_ddl(schema: str) -> list[str]:
    return [
        "CREATE EXTENSION IF NOT EXISTS vector",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.{CATALOG_TABLE} (
            table_name        VARCHAR(128) PRIMARY KEY,
            domain            VARCHAR(40)  NOT NULL,
            owning_service    VARCHAR(64),
            grain             TEXT,
            purpose           TEXT         NOT NULL,
            answers           JSONB        NOT NULL DEFAULT '[]'::jsonb,
            not_for           TEXT,
            row_estimate      BIGINT,
            columns           JSONB        NOT NULL DEFAULT '[]'::jsonb,
            keys              JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
            links_out         JSONB        NOT NULL DEFAULT '[]'::jsonb,
            links_in          JSONB        NOT NULL DEFAULT '[]'::jsonb,
            sample_rows       JSONB        NOT NULL DEFAULT '[]'::jsonb,
            semantic_text     TEXT         NOT NULL,
            embedding         vector({EMBEDDING_DIM}),
            embedding_model   VARCHAR(64),
            purpose_model     VARCHAR(64),
            purpose_source    VARCHAR(16)  NOT NULL DEFAULT 'heuristic',
            catalog_version   INTEGER      NOT NULL DEFAULT 1,
            built_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """,
        f"CREATE INDEX IF NOT EXISTS idx_{CATALOG_TABLE}_domain ON {schema}.{CATALOG_TABLE} (domain)",
    ]
