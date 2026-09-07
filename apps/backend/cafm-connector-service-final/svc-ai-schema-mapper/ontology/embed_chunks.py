#!/usr/bin/env python3
"""Embed the RAG ontology chunks into plenum_cafm.udr_ontology_chunk.

Reads rag/manifest.json + rag/chunks/*.md, embeds each chunk with OpenAI
text-embedding-3-small (1536-d), and UPSERTs into udr_ontology_chunk
(ON CONFLICT chunk_key). Idempotent.

SAFETY: dry-run by default. Makes external API calls + writes the DB ONLY with
--apply. Requires OPENAI_API_KEY and a DB URL (env DB_URL/DATABASE_URL or --db-url).
Run AFTER udr_phase1_schema.sql.

    python embed_chunks.py            # dry-run: shows what it would do
    python embed_chunks.py --apply    # embeds + writes (needs OPENAI_API_KEY + DB_URL)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


def _manifest():
    with open(os.path.join(HERE, "rag", "manifest.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)["chunks"]


def _chunk_text(rel_file: str) -> str:
    with open(os.path.join(HERE, "..", rel_file) if not rel_file.startswith("rag")
              else os.path.join(HERE, rel_file), "r", encoding="utf-8") as fh:
        return fh.read()


def _vec_literal(values) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually embed + write the DB")
    ap.add_argument("--db-url", default=os.environ.get("DB_URL") or os.environ.get("DATABASE_URL"))
    args = ap.parse_args()

    chunks = _manifest()
    print(f"Found {len(chunks)} chunks in manifest.")
    if not args.apply:
        for c in chunks[:5]:
            print(f"  [dry-run] would embed {c['chunk_key']} (concept={c['concept']}, "
                  f"tags={c['intent_tags']})")
        print("  ... (dry-run) re-run with --apply to embed + write. No API calls / DB writes made.")
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY not set.", file=sys.stderr)
        return 2
    if not args.db_url:
        print("ERROR: no DB URL (set DB_URL/DATABASE_URL or pass --db-url).", file=sys.stderr)
        return 2

    try:
        from openai import OpenAI  # type: ignore
        import psycopg  # type: ignore  (psycopg3)
    except ImportError as exc:
        print(f"ERROR: missing dependency ({exc}). Needs `openai` and `psycopg`.", file=sys.stderr)
        return 2

    # normalise SQLAlchemy-style URLs to a psycopg DSN
    dsn = args.db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://")

    client = OpenAI()
    texts = [_chunk_text(c["file"]) for c in chunks]
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    vectors = [d.embedding for d in resp.data]
    assert len(vectors) == len(chunks) and len(vectors[0]) == EMBED_DIM

    written = 0
    with psycopg.connect(dsn, autocommit=True) as conn:
        for c, vec in zip(chunks, vectors):
            conn.execute(
                """
                INSERT INTO plenum_cafm.udr_ontology_chunk
                    (chunk_key, concept, entity, intent_tags, content, embedding)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s::vector)
                ON CONFLICT (chunk_key) DO UPDATE SET
                    concept=EXCLUDED.concept, entity=EXCLUDED.entity,
                    intent_tags=EXCLUDED.intent_tags, content=EXCLUDED.content,
                    embedding=EXCLUDED.embedding
                """,
                (c["chunk_key"], c["concept"], c.get("entity"),
                 json.dumps(c["intent_tags"]), _chunk_text(c["file"]), _vec_literal(vec)),
            )
            written += 1
    print(f"Embedded + upserted {written} chunks into plenum_cafm.udr_ontology_chunk.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
