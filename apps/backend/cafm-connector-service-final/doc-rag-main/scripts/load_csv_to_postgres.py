#!/usr/bin/env python
"""
Load CSV directly into PostgreSQL row_semantic_index table.

This version explicitly connects to PostgreSQL, not SQLite.

USAGE:
    python scripts/load_csv_to_postgres.py your_assets.csv
"""
import argparse
import csv
import os
import sys
from pathlib import Path

# Force PostgreSQL mode (disable SQLite)
os.environ["USE_SQLITE_DEV"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.core.config import settings
from app.db.models import RowSemanticIndex
from app.db.session import SessionLocal
from app.services.embedding_service import embedding_service


def verify_postgres_connection():
    """Verify we're connected to PostgreSQL, not SQLite."""
    db_url = settings.database_url
    
    logger.info("Database URL: {}", db_url)
    
    if "sqlite" in db_url.lower():
        logger.error("ERROR: Still using SQLite!")
        logger.error("Database URL: {}", db_url)
        logger.error("")
        logger.error("Fix: Set environment variable before running:")
        logger.error("  export USE_SQLITE_DEV=false")
        logger.error("  python scripts/load_csv_to_postgres.py your_file.csv")
        sys.exit(1)
    
    if "postgresql" not in db_url.lower():
        logger.error("ERROR: Not using PostgreSQL!")
        logger.error("Database URL: {}", db_url)
        sys.exit(1)
    
    logger.info("✓ Confirmed: Using PostgreSQL")
    logger.info("  Host: {}", settings.postgres_host)
    logger.info("  Port: {}", settings.postgres_port)
    logger.info("  Database: {}", settings.postgres_db)
    logger.info("  User: {}", settings.postgres_user)
    logger.info("")


def load_csv_to_postgres(
    csv_path: str,
    table_name: str = "assets",
    pk_column: str = "asset_code",
):
    """Load CSV into PostgreSQL row_semantic_index."""
    
    logger.info("=" * 70)
    logger.info("CSV to PostgreSQL row_semantic_index Loader")
    logger.info("=" * 70)
    logger.info("")
    
    # Verify PostgreSQL connection
    verify_postgres_connection()
    
    # Read CSV
    logger.info("Step 1: Reading CSV file...")
    logger.info("  File: {}", csv_path)
    
    try:
        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        logger.error("  ✗ File not found: {}", csv_path)
        sys.exit(1)
    except Exception as e:
        logger.error("  ✗ Error reading CSV: {}", e)
        sys.exit(1)
    
    logger.info("  ✓ Loaded {} rows", len(rows))
    
    if not rows:
        logger.error("  ✗ CSV is empty!")
        sys.exit(1)
    
    # Show CSV structure
    columns = list(rows[0].keys())
    logger.info("  Columns: {}", ", ".join(columns[:5]) + ("..." if len(columns) > 5 else ""))
    
    # Validate PK column
    if pk_column not in columns:
        logger.error("  ✗ PK column '{}' not found!", pk_column)
        logger.error("  Available columns: {}", ", ".join(columns))
        sys.exit(1)
    
    logger.info("  Primary key column: {}", pk_column)
    logger.info("")
    
    # Build batch
    logger.info("Step 2: Building semantic text...")
    batch_texts = []
    batch_objs = []
    
    for i, row_dict in enumerate(rows, 1):
        pk_val = str(row_dict.get(pk_column, "")).strip()
        if not pk_val:
            logger.warning("  Row {} missing PK, skipping", i)
            continue
        
        # Build semantic text from ALL columns
        parts = []
        for col, val in row_dict.items():
            if val and str(val).strip():
                parts.append(f"{col}: {val}")
        
        if not parts:
            continue
        
        semantic_text = ". ".join(parts)
        meta = {k: v for k, v in row_dict.items() if v}
        
        batch_texts.append(semantic_text)
        batch_objs.append({
            "source_table": table_name,
            "row_pk": pk_val,
            "semantic_text": semantic_text,
            "meta": meta,
        })
    
    logger.info("  ✓ Valid rows: {}", len(batch_objs))
    
    if not batch_objs:
        logger.error("  ✗ No valid rows to insert!")
        sys.exit(1)
    
    # Show sample
    logger.info("")
    logger.info("  Sample semantic text:")
    for obj in batch_objs[:2]:
        preview = obj["semantic_text"][:80] + "..."
        logger.info("    {}: {}", obj["row_pk"], preview)
    
    logger.info("")
    
    # Create embeddings
    logger.info("Step 3: Creating embeddings...")
    try:
        embeddings = embedding_service.embed_batch(batch_texts)
        logger.info("  ✓ Created {} embeddings", len(embeddings))
        
        if embedding_service.mock:
            logger.warning("  ⚠ Using MOCK embeddings (no OPENAI_API_KEY)")
    except Exception as e:
        logger.error("  ✗ Embedding failed: {}", e)
        embeddings = [None] * len(batch_objs)
    
    logger.info("")
    
    # Insert into PostgreSQL
    logger.info("Step 4: Inserting into PostgreSQL...")
    db = SessionLocal()
    
    inserted = 0
    updated = 0
    
    try:
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
                db.add(RowSemanticIndex(
                    source_table=obj["source_table"],
                    row_pk=obj["row_pk"],
                    semantic_text=obj["semantic_text"],
                    meta=obj["meta"],
                    embedding=emb,
                ))
                inserted += 1
        
        db.commit()
        logger.info("  ✓ Inserted: {}", inserted)
        logger.info("  ✓ Updated: {}", updated)
        
    except Exception as e:
        logger.error("  ✗ Database error: {}", e)
        db.rollback()
        sys.exit(1)
    finally:
        db.close()
    
    logger.info("")
    
    # Verify in PostgreSQL
    logger.info("Step 5: Verifying in PostgreSQL...")
    db = SessionLocal()
    
    count = db.query(RowSemanticIndex).filter(
        RowSemanticIndex.source_table == table_name
    ).count()
    
    logger.info("  Total rows in PostgreSQL for table '{}': {}", table_name, count)
    
    if count > 0:
        samples = db.query(RowSemanticIndex).filter(
            RowSemanticIndex.source_table == table_name
        ).limit(3).all()
        
        logger.info("")
        logger.info("  Sample entries from PostgreSQL:")
        for row in samples:
            preview = row.semantic_text[:60] + "..."
            has_emb = "✓" if row.embedding else "✗"
            logger.info("    {} {} : {}", has_emb, row.row_pk, preview)
    
    db.close()
    
    logger.info("")
    logger.info("=" * 70)
    logger.info("✓ SUCCESS - Data loaded into PostgreSQL")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Verify in PostgreSQL:")
    logger.info("  psql -h postgres -U rag_user -d rag_platform")
    logger.info("  SELECT COUNT(*) FROM row_semantic_index;")
    logger.info("  SELECT * FROM row_semantic_index LIMIT 5;")
    logger.info("")


def main():
    parser = argparse.ArgumentParser(
        description="Load CSV into PostgreSQL row_semantic_index"
    )
    parser.add_argument("csv_file", help="CSV file to load")
    parser.add_argument("--table", default="assets", help="Table name")
    parser.add_argument("--pk", default="asset_code", help="PK column name")
    
    args = parser.parse_args()
    
    load_csv_to_postgres(
        csv_path=args.csv_file,
        table_name=args.table,
        pk_column=args.pk,
    )


if __name__ == "__main__":
    main()
