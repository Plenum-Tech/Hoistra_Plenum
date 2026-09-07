#!/usr/bin/env python
"""
Direct CSV to row_semantic_index loader.

This script reads a CSV file and inserts the data directly into the 
row_semantic_index table. No PostgreSQL connection needed - just CSV → SQLite/Postgres.

USAGE:
    python scripts/load_csv_to_index.py assets.csv

CSV Format Requirements:
    - Must have a column that will be used as the primary key
    - Recommended columns: asset_code, asset_name, category, building, etc.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.db.models import RowSemanticIndex
from app.db.session import SessionLocal
from app.services.embedding_service import embedding_service


def load_csv_to_index(
    csv_path: str,
    table_name: str = "assets",
    pk_column: str = "asset_code",
    semantic_columns: list[str] = None,
):
    """
    Load CSV data directly into row_semantic_index.
    
    Args:
        csv_path: Path to CSV file
        table_name: Name to use for source_table field (default: "assets")
        pk_column: Column name to use as primary key
        semantic_columns: List of columns to include in semantic_text
                         If None, uses all columns
    """
    logger.info("=" * 60)
    logger.info("CSV to row_semantic_index Loader")
    logger.info("=" * 60)
    logger.info("CSV file: {}", csv_path)
    logger.info("Table name: {}", table_name)
    logger.info("PK column: {}", pk_column)
    logger.info("")

    # Read CSV
    logger.info("Step 1: Reading CSV...")
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except FileNotFoundError:
        logger.error("CSV file not found: {}", csv_path)
        sys.exit(1)
    except Exception as e:
        logger.error("Error reading CSV: {}", e)
        sys.exit(1)

    logger.info("  Loaded {} rows", len(rows))
    
    if not rows:
        logger.error("CSV is empty!")
        sys.exit(1)

    # Show CSV structure
    columns = list(rows[0].keys())
    logger.info("  CSV columns: {}", ", ".join(columns))
    
    # Validate PK column exists
    if pk_column not in columns:
        logger.error("PK column '{}' not found in CSV!", pk_column)
        logger.error("Available columns: {}", ", ".join(columns))
        sys.exit(1)
    
    # If semantic_columns not specified, use all columns
    if semantic_columns is None:
        semantic_columns = columns
        logger.info("  Using all columns for semantic text")
    else:
        # Validate semantic columns exist
        missing = [col for col in semantic_columns if col not in columns]
        if missing:
            logger.warning("Semantic columns not in CSV: {}", ", ".join(missing))
        logger.info("  Semantic columns: {}", ", ".join(semantic_columns))
    
    logger.info("")

    # Build batch for embedding
    logger.info("Step 2: Building semantic text for each row...")
    batch_texts = []
    batch_objs = []
    skipped = 0

    for i, row_dict in enumerate(rows, 1):
        # Get primary key value
        pk_val = str(row_dict.get(pk_column, "")).strip()
        
        if not pk_val:
            logger.warning("  Row {} missing PK, skipping", i)
            skipped += 1
            continue

        # Build semantic text from specified columns
        semantic_parts = []
        for col in semantic_columns:
            val = row_dict.get(col, "")
            if val and str(val).strip():
                # Include column name in semantic text for better matching
                semantic_parts.append(f"{col}: {val}")
        
        if not semantic_parts:
            logger.warning("  Row {} ({}) has no valid data, skipping", i, pk_val)
            skipped += 1
            continue
        
        semantic_text = ". ".join(semantic_parts)
        
        # Store ALL columns from CSV in meta JSON
        meta = {k: v for k, v in row_dict.items() if v}  # Remove empty values

        batch_texts.append(semantic_text)
        batch_objs.append({
            "source_table": table_name,
            "row_pk": pk_val,
            "semantic_text": semantic_text,
            "meta": meta,
        })

    logger.info("  Valid rows: {} (skipped: {})", len(batch_objs), skipped)
    
    if not batch_objs:
        logger.error("No valid rows to insert!")
        sys.exit(1)
    
    # Show sample
    logger.info("")
    logger.info("Sample semantic text:")
    for obj in batch_objs[:2]:
        preview = obj["semantic_text"][:100] + "..." if len(obj["semantic_text"]) > 100 else obj["semantic_text"]
        logger.info("  {}: {}", obj["row_pk"], preview)
    logger.info("")

    # Embed
    logger.info("Step 3: Creating embeddings...")
    try:
        embeddings = embedding_service.embed_batch(batch_texts)
        logger.info("  ✓ Created {} embeddings", len(embeddings))
        
        if embedding_service.mock:
            logger.warning("  ⚠ Using MOCK embeddings (no OPENAI_API_KEY)")
            logger.warning("  → Semantic matching will be limited")
            logger.warning("  → Set OPENAI_API_KEY in .env for full functionality")
    except Exception as e:
        logger.error("  ✗ Embedding failed: {}", e)
        logger.warning("  → Proceeding with NULL embeddings")
        embeddings = [None] * len(batch_objs)
    
    logger.info("")

    # Insert into database
    logger.info("Step 4: Inserting into row_semantic_index...")
    db = SessionLocal()
    
    inserted = 0
    updated = 0

    for obj, emb in zip(batch_objs, embeddings, strict=False):
        # Check if row already exists
        existing = (
            db.query(RowSemanticIndex)
            .filter(
                RowSemanticIndex.source_table == obj["source_table"],
                RowSemanticIndex.row_pk == obj["row_pk"],
            )
            .first()
        )

        if existing:
            # Update existing row
            existing.semantic_text = obj["semantic_text"]
            existing.meta = obj["meta"]
            existing.embedding = emb
            updated += 1
        else:
            # Insert new row
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
    db.close()

    # Verify
    logger.info("")
    logger.info("Step 5: Verifying insertion...")
    db = SessionLocal()
    count = db.query(RowSemanticIndex).filter(
        RowSemanticIndex.source_table == table_name
    ).count()
    logger.info("  Total rows in index for table '{}': {}", table_name, count)
    
    # Show sample
    samples = (
        db.query(RowSemanticIndex)
        .filter(RowSemanticIndex.source_table == table_name)
        .limit(3)
        .all()
    )
    
    logger.info("")
    logger.info("Sample entries:")
    for row in samples:
        preview = row.semantic_text[:80] + "..." if len(row.semantic_text) > 80 else row.semantic_text
        has_emb = "✓" if row.embedding else "✗"
        logger.info("  {} {} : {}", has_emb, row.row_pk, preview)
    
    db.close()

    logger.info("")
    logger.info("=" * 60)
    logger.info("✓ SUCCESS - Data loaded into row_semantic_index")
    logger.info("=" * 60)
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Upload a document that mentions these assets")
    logger.info("  2. Run: POST /documents/{{doc_id}}/match-rows")
    logger.info("  3. Validate the matched_rows in the response")
    logger.info("")


def main():
    parser = argparse.ArgumentParser(
        description="Load CSV data directly into row_semantic_index table",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Load assets.csv with default settings
  python scripts/load_csv_to_index.py assets.csv

  # Specify table name and PK column
  python scripts/load_csv_to_index.py assets.csv --table equipment --pk equipment_id

  # Specify which columns to include in semantic text
  python scripts/load_csv_to_index.py assets.csv --columns "asset_code,asset_name,building,floor"

CSV Requirements:
  - Must have a primary key column (default: asset_code)
  - Column names should match what's in your documents
  - All columns are stored in the meta JSON for retrieval
        """
    )
    
    parser.add_argument(
        "csv_file",
        help="Path to CSV file to load"
    )
    
    parser.add_argument(
        "--table",
        default="assets",
        help="Table name to use (default: assets)"
    )
    
    parser.add_argument(
        "--pk",
        default="asset_code",
        help="Primary key column name (default: asset_code)"
    )
    
    parser.add_argument(
        "--columns",
        help="Comma-separated list of columns to include in semantic text (default: all)"
    )

    args = parser.parse_args()

    # Parse columns if provided
    semantic_cols = None
    if args.columns:
        semantic_cols = [c.strip() for c in args.columns.split(",")]

    # Run loader
    load_csv_to_index(
        csv_path=args.csv_file,
        table_name=args.table,
        pk_column=args.pk,
        semantic_columns=semantic_cols,
    )


if __name__ == "__main__":
    main()
