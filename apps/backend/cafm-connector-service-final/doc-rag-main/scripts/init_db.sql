-- Runs automatically when the pgvector/pgvector:pg16 container first starts
-- (mounted into /docker-entrypoint-initdb.d/). Tables are then created by
-- SQLAlchemy via `python -m scripts.init_db` when the api service boots.

CREATE EXTENSION IF NOT EXISTS vector;

-- ANN indexes are created after the SQLAlchemy tables exist. To enable them,
-- after the first successful run of `scripts.init_db`, exec into the postgres
-- container and run:
--
--   CREATE INDEX IF NOT EXISTS idx_doc_chunk_embedding_ivfflat
--   ON document_chunks
--   USING ivfflat (embedding vector_cosine_ops)
--   WITH (lists = 100);
--
--   CREATE INDEX IF NOT EXISTS idx_row_semantic_embedding_ivfflat
--   ON row_semantic_index
--   USING ivfflat (embedding vector_cosine_ops)
--   WITH (lists = 100);
