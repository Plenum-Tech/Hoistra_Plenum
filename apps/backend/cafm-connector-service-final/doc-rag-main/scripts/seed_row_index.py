#!/usr/bin/env python
"""Seed the row_semantic_index table from PostgreSQL or CSV.

USAGE:
    # List tables in your PostgreSQL database
    python scripts/seed_row_index.py --list-tables
    
    # Seed from PostgreSQL
    python scripts/seed_row_index.py --table facilities --limit 1000
    
    # Seed from CSV
    python scripts/seed_row_index.py --table facilities --csv /path/to/data.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.db.models import RowSemanticIndex
from app.db.session import SessionLocal
from app.services.embedding_service import embedding_service

# PostgreSQL connection settings
POSTGRES_HOST = "postgres"
POSTGRES_PORT = 5432
POSTGRES_USER = "rag_user"
POSTGRES_PASSWORD = "rag_password"
POSTGRES_DB = "rag_platform"

# Schema mapping - ADD YOUR TABLES HERE
SCHEMA_MAP = {
    "facilities": {
        "pk_column": "Asset Code",
        "semantic_columns": [
            "Asset Code",  # MUST include PK for exact matching!
            "Asset Name", "Asset Description", "Make", "Model",
            "Serial Number", "Location Name", "Category"
        ],
        "include_all_columns": True,
    },
    "equipment": {
        "pk_column": "equipment_id",
        "semantic_columns": [
            "equipment_id",  # MUST include PK!
            "equipment_name", "location", "manufacturer", "model"
        ],
        "include_all_columns": True,
    },
    "assets": {
        "pk_column": "asset_code",
        "semantic_columns": [
            "asset_code",  # MUST include PK!
            "asset_name", "category", "building", "floor"
        ],
        "include_all_columns": True,
    },
}


def get_postgres_connection():
    """Create PostgreSQL connection."""
    try:
        import psycopg2
        import psycopg2.extras
        
        conn_str = f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
        logger.info("Connecting to PostgreSQL: {}:{}/{}", POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB)
        conn = psycopg2.connect(conn_str)
        logger.info("✓ Connected")
        return conn, psycopg2.extras
        
    except ImportError:
        logger.error("psycopg2 not installed: pip install psycopg2-binary")
        sys.exit(1)
    except Exception as e:
        logger.error("Connection failed: {}", e)
        sys.exit(1)


def list_postgres_tables():
    """List all tables with row counts."""
    conn, _ = get_postgres_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema='public' AND table_type='BASE TABLE'
        ORDER BY table_name
    """)
    
    tables = [row[0] for row in cur.fetchall()]
    logger.info("Tables in {}:", POSTGRES_DB)
    
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
        count = cur.fetchone()[0]
        in_schema = "✓" if table in SCHEMA_MAP else " "
        logger.info("  {} {} ({} rows)", in_schema, table, count)
    
    logger.info("")
    logger.info("✓ = configured in SCHEMA_MAP")
    conn.close()


def seed_from_postgres(table: str, limit: int):
    """Seed from PostgreSQL table."""
    conn, psycopg2_extras = get_postgres_connection()
    cur = conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)
    
    # Validate table in SCHEMA_MAP
    if table not in SCHEMA_MAP:
        logger.error("Table '{}' not in SCHEMA_MAP", table)
        logger.error("Available: {}", ", ".join(SCHEMA_MAP.keys()))
        conn.close()
        return
    
    schema = SCHEMA_MAP[table]
    pk = schema["pk_column"]
    cols = schema["semantic_columns"]
    
    # Check table exists and has data
    cur.execute(f"SELECT COUNT(*) FROM \"{table}\"")
    row_count = cur.fetchone()[0]
    logger.info("Table '{}' has {} rows", table, row_count)
    
    if row_count == 0:
        logger.error("Table is empty!")
        conn.close()
        return
    
    # Fetch rows
    query = f'SELECT * FROM "{table}" LIMIT {limit}'
    logger.info("Query: {}", query)
    cur.execute(query)
    rows = cur.fetchall()
    logger.info("Fetched {} rows", len(rows))
    
    # Show first row structure
    first = dict(rows[0])
    logger.info("Columns: {}", ", ".join(first.keys()))
    logger.info("PK '{}' = {}", pk, first.get(pk, "NOT FOUND"))
    
    # Build batch
    db = SessionLocal()
    batch_texts = []
    batch_objs = []
    
    for row in rows:
        row_dict = dict(row)
        pk_val = str(row_dict.get(pk, "")).strip()
        if not pk_val:
            continue
        
        parts = []
        for col in cols:
            val = row_dict.get(col)
            if val and str(val).strip():
                parts.append(f"{col}: {val}")
        
        if not parts:
            continue
        
        semantic_text = ". ".join(parts)
        meta = row_dict if schema["include_all_columns"] else {}
        
        batch_texts.append(semantic_text)
        batch_objs.append({
            "source_table": table,
            "row_pk": pk_val,
            "semantic_text": semantic_text,
            "meta": meta,
        })
    
    logger.info("Valid rows: {}", len(batch_objs))
    
    # Embed - FIXED: Use embed_batch
    logger.info("Embedding...")
    try:
        embeddings = embedding_service.embed_batch(batch_texts)
        logger.info("✓ Embedded")
    except Exception as e:
        logger.error("Embedding failed: {}", e)
        embeddings = [None] * len(batch_texts)
    
    # Persist
    inserted = updated = 0
    for obj, emb in zip(batch_objs, embeddings, strict=False):
        existing = db.query(RowSemanticIndex).filter(
            RowSemanticIndex.source_table == obj["source_table"],
            RowSemanticIndex.row_pk == obj["row_pk"],
        ).first()
        
        if existing:
            existing.semantic_text = obj["semantic_text"]
            existing.meta = obj["meta"]
            existing.embedding = emb
            updated += 1
        else:
            db.add(RowSemanticIndex(**obj, embedding=emb))
            inserted += 1
    
    db.commit()
    logger.info("✓ Done: {} inserted, {} updated", inserted, updated)
    
    # Sample
    for obj in batch_objs[:2]:
        logger.info("  {}: {}...", obj["row_pk"], obj["semantic_text"][:60])
    
    db.close()
    conn.close()


def seed_from_csv_file(table: str, csv_path: str):
    """Seed from CSV file."""
    import csv
    
    if table not in SCHEMA_MAP:
        logger.error("Table '{}' not in SCHEMA_MAP", table)
        return
    
    schema = SCHEMA_MAP[table]
    pk = schema["pk_column"]
    cols = schema["semantic_columns"]
    
    logger.info("Reading CSV: {}", csv_path)
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    
    logger.info("Loaded {} rows", len(rows))
    if not rows:
        logger.error("CSV is empty!")
        return
    
    logger.info("CSV columns: {}", ", ".join(rows[0].keys()))
    
    # Build batch
    db = SessionLocal()
    batch_texts = []
    batch_objs = []
    
    for row_dict in rows:
        pk_val = str(row_dict.get(pk, "")).strip()
        if not pk_val:
            continue
        
        parts = [
            f"{col}: {row_dict.get(col, '')}"
            for col in cols
            if row_dict.get(col) and str(row_dict.get(col)).strip()
        ]
        
        if not parts:
            continue
        
        semantic_text = ". ".join(parts)
        meta = row_dict if schema["include_all_columns"] else {}
        
        batch_texts.append(semantic_text)
        batch_objs.append({
            "source_table": table,
            "row_pk": pk_val,
            "semantic_text": semantic_text,
            "meta": meta,
        })
    
    logger.info("Valid rows: {}", len(batch_objs))
    
    # Embed
    logger.info("Embedding...")
    try:
        embeddings = embedding_service.embed_batch(batch_texts)
        logger.info("✓ Embedded")
    except Exception as e:
        logger.warning("Embedding failed: {}", e)
        embeddings = [None] * len(batch_texts)
    
    # Persist
    inserted = updated = 0
    for obj, emb in zip(batch_objs, embeddings, strict=False):
        existing = db.query(RowSemanticIndex).filter(
            RowSemanticIndex.source_table == obj["source_table"],
            RowSemanticIndex.row_pk == obj["row_pk"],
        ).first()
        
        if existing:
            existing.semantic_text = obj["semantic_text"]
            existing.meta = obj["meta"]
            existing.embedding = emb
            updated += 1
        else:
            db.add(RowSemanticIndex(**obj, embedding=emb))
            inserted += 1
    
    db.commit()
    logger.info("✓ Done: {} inserted, {} updated", inserted, updated)
    
    # Sample
    for obj in batch_objs[:2]:
        logger.info("  {}: {}...", obj["row_pk"], obj["semantic_text"][:60])
    
    db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", help="Table name")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--csv", help="CSV file path")
    parser.add_argument("--list-tables", action="store_true")
    args = parser.parse_args()
    
    if args.list_tables:
        list_postgres_tables()
    elif not args.table:
        logger.error("--table required")
        sys.exit(1)
    elif args.csv:
        seed_from_csv_file(args.table, args.csv)
    else:
        seed_from_postgres(args.table, args.limit)


if __name__ == "__main__":
    main()
