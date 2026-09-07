#!/usr/bin/env python
"""
Debug script to find why document chunks aren't matching database rows.

Usage:
    python scripts/debug_matching.py <document_id>
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.db.models import Document, DocumentChunk, RowSemanticIndex
from app.db.session import SessionLocal
from app.utils.entity_extraction import extract_keys


def debug_matching(document_id: str):
    """Debug why chunks aren't matching rows."""
    
    logger.info("=" * 70)
    logger.info("MATCHING DEBUG TOOL")
    logger.info("=" * 70)
    logger.info("")
    
    db = SessionLocal()
    
    # 1. Check document exists
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        logger.error("Document not found: {}", document_id)
        sys.exit(1)
    
    logger.info("Document: {} ({})", doc.file_name, document_id)
    logger.info("")
    
    # 2. Check chunks
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).all()
    
    logger.info("Total chunks: {}", len(chunks))
    logger.info("")
    
    # 3. Check row_semantic_index
    rows = db.query(RowSemanticIndex).all()
    logger.info("Total rows in index: {}", len(rows))
    
    if len(rows) == 0:
        logger.error("row_semantic_index is EMPTY!")
        logger.error("Run: python scripts/load_csv_to_postgres.py your_assets.csv")
        sys.exit(1)
    
    logger.info("")
    
    # 4. Extract keys from chunks
    logger.info("Extracting asset codes from chunks...")
    chunk_keys = set()
    
    for chunk in chunks[:50]:  # Check first 50 chunks
        keys = extract_keys(chunk.text_content)
        chunk_keys.update(keys)
    
    logger.info("Found {} unique asset codes in document:", len(chunk_keys))
    for key in sorted(chunk_keys)[:20]:
        logger.info("  - {}", key)
    if len(chunk_keys) > 20:
        logger.info("  ... and {} more", len(chunk_keys) - 20)
    
    logger.info("")
    
    # 5. Get row PKs from database
    logger.info("Asset codes in database:")
    row_pks = set()
    for row in rows[:20]:
        row_pks.add(row.row_pk)
        logger.info("  - {} ({})", row.row_pk, row.source_table)
    if len(rows) > 20:
        logger.info("  ... and {} more", len(rows) - 20)
    
    logger.info("")
    
    # 6. Find overlap
    overlap = chunk_keys & row_pks
    logger.info("=" * 70)
    if overlap:
        logger.info("✓ MATCHES FOUND: {}", len(overlap))
        for key in sorted(overlap):
            logger.info("  ✓ {}", key)
        logger.info("")
        logger.info("These should match! If they don't, check:")
        logger.info("  1. Are embeddings created? (OPENAI_API_KEY set?)")
        logger.info("  2. Is confidence_threshold too high? (try 0.25)")
        logger.info("  3. Check the semantic_text includes the PK")
    else:
        logger.error("✗ NO OVERLAP - Asset codes don't match!")
        logger.info("")
        logger.info("Document has codes like: {}", sorted(chunk_keys)[:5])
        logger.info("Database has codes like: {}", sorted(row_pks)[:5])
        logger.info("")
        logger.info("SOLUTIONS:")
        logger.info("  1. Update your CSV to include the asset codes from the document")
        logger.info("  2. OR upload a different document that references your assets")
        logger.info("")
        logger.info("Example: If document mentions 'AHU-017' but database has 'CT-001',")
        logger.info("         they will never match!")
    
    logger.info("")
    
    # 7. Check semantic_text includes PK
    logger.info("Checking if PK is in semantic_text...")
    sample_row = rows[0]
    pk_in_text = sample_row.row_pk in sample_row.semantic_text
    
    if pk_in_text:
        logger.info("  ✓ PK '{}' found in semantic_text", sample_row.row_pk)
    else:
        logger.error("  ✗ PK '{}' NOT in semantic_text!", sample_row.row_pk)
        logger.error("  semantic_text: {}", sample_row.semantic_text[:100])
        logger.error("")
        logger.error("FIX: Re-run seed script with PK in semantic_columns!")
    
    logger.info("")
    
    # 8. Check embeddings
    chunks_with_emb = sum(1 for c in chunks if c.embedding is not None)
    rows_with_emb = sum(1 for r in rows if r.embedding is not None)
    
    logger.info("Embeddings status:")
    logger.info("  Chunks with embeddings: {}/{}", chunks_with_emb, len(chunks))
    logger.info("  Rows with embeddings: {}/{}", rows_with_emb, len(rows))
    
    if chunks_with_emb == 0 or rows_with_emb == 0:
        logger.warning("")
        logger.warning("  ⚠ Missing embeddings! Set OPENAI_API_KEY for better matching")
    
    logger.info("")
    logger.info("=" * 70)
    
    db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id", help="Document ID to debug")
    args = parser.parse_args()
    
    debug_matching(args.document_id)


if __name__ == "__main__":
    main()
