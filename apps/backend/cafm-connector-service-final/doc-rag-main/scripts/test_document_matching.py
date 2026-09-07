#!/usr/bin/env python
"""End-to-end test for document-to-rows matching.

This script:
1. Seeds a test asset table into row_semantic_index
2. Creates a test document with known asset mentions
3. Calls /documents/{id}/match-rows
4. Validates that the expected rows were matched

Usage:
    python -m scripts.test_document_matching

This verifies the full pipeline works before you integrate with real data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.db.models import Document, DocumentChunk, RowSemanticIndex
from app.db.session import SessionLocal, init_db
from app.services.chunker import Chunk, chunker
from app.services.embedding_service import embedding_service
from app.services.extraction_service import ExtractedDocument, ExtractedPage


def test_end_to_end():
    """Full integration test of document-to-rows matching."""
    logger.info("=== Starting end-to-end document matching test ===")

    # 0. Initialize
    init_db()
    db = SessionLocal()

    # Clean slate
    db.query(DocumentChunk).delete()
    db.query(Document).delete()
    db.query(RowSemanticIndex).delete()
    db.commit()

    # 1. Seed test asset rows
    logger.info("Step 1: Seeding test asset rows...")
    test_assets = [
        {
            "table": "equipment",
            "pk": "AHU-017",
            # CRITICAL: semantic_text must include the PK for exact key matching to work
            "text": "equipment_id: AHU-017. equipment_name: Main AHU North Wing. location: Building A Floor 5. manufacturer: Trane",
            "meta": {
                "equipment_id": "AHU-017",
                "equipment_name": "Main AHU - North Wing",
                "building": "Building A",
                "floor": 5,
                "manufacturer": "Trane",
                "model": "CGAM-100",
            },
        },
        {
            "table": "equipment",
            "pk": "AHU-018",
            "text": "equipment_id: AHU-018. equipment_name: Service AHU South Wing. location: Building B Floor 3. manufacturer: Carrier",
            "meta": {
                "equipment_id": "AHU-018",
                "equipment_name": "Service AHU - South Wing",
                "building": "Building B",
                "floor": 3,
                "manufacturer": "Carrier",
                "model": "39M",
            },
        },
        {
            "table": "elevators",
            "pk": "EL-001",
            "text": "elevator_id: EL-001. elevator_name: Main Elevator North. location: Building A. total_load_kw: 45",
            "meta": {
                "elevator_id": "EL-001",
                "elevator_name": "Main Elevator - North Wing",
                "building": "Building A",
                "total_load_kw": 45,
                "manufacturer": "Otis",
            },
        },
    ]

    texts = [a["text"] for a in test_assets]
    embeddings = embedding_service.embed_batch(texts)

    for asset, emb in zip(test_assets, embeddings, strict=False):
        db.add(RowSemanticIndex(
            source_table=asset["table"],
            row_pk=asset["pk"],
            semantic_text=asset["text"],
            meta=asset["meta"],
            embedding=emb,
        ))
    db.commit()
    logger.info(f"  ✓ Seeded {len(test_assets)} test rows")

    # 2. Create a test document that mentions these assets
    logger.info("Step 2: Creating test document with asset mentions...")
    test_doc_text = """
MAINTENANCE REPORT - JANUARY 2025

Inspected equipment AHU-017 in Building A, Floor 5. All filters replaced.
Trane unit operating normally. Next service due in 90 days.

Also checked AHU-018 in Building B. Carrier unit requires belt replacement.

Elevator EL-001 was inspected. All safety systems operational.
Total load test passed at 45 kW.

Some text with no asset mentions for testing false negatives.
"""

    doc = Document(
        file_name="test_maintenance_report.txt",
        mime_type="text/plain",
        status="indexed",
        num_pages=1,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Chunk it
    extracted = ExtractedDocument(
        file_name="test_maintenance_report.txt",
        mime_type="text/plain",
        num_pages=1,
        pages=[ExtractedPage(page_number=1, text=test_doc_text)],
    )
    chunks = chunker.chunk(extracted)

    # Persist chunks
    chunk_texts = [c.text_content for c in chunks]
    chunk_embs = embedding_service.embed_batch(chunk_texts)

    for c, emb in zip(chunks, chunk_embs, strict=False):
        db.add(DocumentChunk(
            document_id=doc.id,
            page_start=c.page_start,
            page_end=c.page_end,
            chunk_index=c.chunk_index,
            block_type=c.block_type,
            section_label=c.section_label,
            text_content=c.text_content,
            normalized_text=c.normalized_text,
            meta=c.meta,
            embedding=emb,
            embedding_model="mock" if embedding_service.mock else embedding_service.model,
        ))
    db.commit()
    logger.info(f"  ✓ Created document {doc.id} with {len(chunks)} chunks")

    # 3. Test the /match-rows endpoint logic
    logger.info("Step 3: Testing document-to-rows matching...")
    from app.routers.document_match import match_document_to_rows

    result = match_document_to_rows(
        document_id=doc.id,
        confidence_threshold=0.25,
        group_by_table=True,
        db=db,
    )

    logger.info(f"  Total chunks analyzed: {result['total_chunks_analyzed']}")
    logger.info(f"  Unique rows matched: {result['unique_rows_matched']}")
    logger.info(f"  By table: {result['by_table']}")

    # 4. Validate results
    logger.info("Step 4: Validating matches...")
    matched_pks = {r["row_pk"] for r in result["matched_rows"]}

    expected = {"AHU-017", "AHU-018", "EL-001"}
    if matched_pks == expected:
        logger.info("  ✓ All expected assets matched!")
    else:
        missing = expected - matched_pks
        extra = matched_pks - expected
        if missing:
            logger.error(f"  ✗ Missing expected matches: {missing}")
        if extra:
            logger.warning(f"  ⚠ Unexpected matches: {extra}")

    # Show match details
    for r in result["matched_rows"]:
        logger.info(
            f"  - {r['source_table']}.{r['row_pk']}: "
            f"confidence={r['confidence']:.2f}, "
            f"method={r['match_method']}, "
            f"chunks={r['chunk_count']}"
        )

    # 5. Test the debug endpoint
    logger.info("Step 5: Testing chunk-level debug endpoint...")
    from app.routers.document_match import debug_chunk_to_row_matching

    debug_result = debug_chunk_to_row_matching(
        document_id=doc.id,
        show_all_chunks=True,
        confidence_threshold=0.25,
        db=db,
    )

    logger.info(f"  Chunks with matches: {debug_result['chunks_with_matches']}")
    logger.info(f"  Chunks without matches: {debug_result['chunks_without_matches']}")

    # Show a few chunk details
    for chunk_detail in debug_result["chunk_details"][:3]:
        logger.info(
            f"  Chunk {chunk_detail['chunk_index']}: "
            f"{chunk_detail['match_count']} matches, "
            f"text='{chunk_detail['text'][:60]}...'"
        )

    db.close()

    logger.info("\n=== Test complete ===")
    logger.info(
        "Summary: "
        f"Seeded {len(test_assets)} assets, "
        f"created {len(chunks)} chunks, "
        f"matched {result['unique_rows_matched']} rows"
    )

    if matched_pks == expected:
        logger.info("✓ ALL TESTS PASSED")
        return 0
    else:
        logger.error("✗ SOME TESTS FAILED - check the logs above")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(test_end_to_end())
    except Exception as e:
        logger.exception("Test failed with exception: {}", e)
        sys.exit(1)
