#!/usr/bin/env python
"""Test metadata-based matching."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logger import logger
from app.db.models import RowSemanticIndex, DocumentChunk, Document
from app.db.session import SessionLocal
from app.services.row_match_service import row_match_service

# Create test data
db = SessionLocal()

# Clear existing test data
db.query(RowSemanticIndex).filter(RowSemanticIndex.source_table == "test_assets").delete()
db.query(DocumentChunk).filter(DocumentChunk.document_id == "test-doc").delete()
db.query(Document).filter(Document.id == "test-doc").delete()
db.commit()

logger.info("Creating test data...")

# Create test document
doc = Document(
    id="test-doc",
    file_name="test.txt",
    file_path="/test.txt",
    mime_type="text/plain",
    file_size_bytes=100,
    num_pages=1,
    document_type="other",
    status="indexed",
)
db.add(doc)

# Create test row with metadata
test_row = RowSemanticIndex(
    source_table="test_assets",
    row_pk="ESC-001",
    semantic_text="asset_code: ESC-001. asset_name: Main Escalator Lobby. category: Escalator",
    meta={
        "asset_code": "ESC-001",
        "asset_name": "Main Escalator Lobby",
        "category": "Escalator",
        "building": "Building A",
        "floor": "Ground",
        "location": "Main Entrance",
        "manufacturer": "Schindler",
        "model": "9300",
        "serial_number": "SCH-2019-ESC001",
        "status": "Operational",
    },
    embedding=None,
)
db.add(test_row)

# Test case 1: Chunk mentions asset code
chunk1 = DocumentChunk(
    id="chunk-1",
    document_id="test-doc",
    chunk_index=0,
    text_content="The escalator ESC-001 is located in the main entrance.",
    page_number=1,
    block_type="paragraph",
)
db.add(chunk1)

# Test case 2: Chunk mentions manufacturer and model
chunk2 = DocumentChunk(
    id="chunk-2",
    document_id="test-doc",
    chunk_index=1,
    text_content="The Schindler 9300 escalator in Building A lobby needs maintenance.",
    page_number=1,
    block_type="paragraph",
)
db.add(chunk2)

# Test case 3: Chunk mentions location details
chunk3 = DocumentChunk(
    id="chunk-3",
    document_id="test-doc",
    chunk_index=2,
    text_content="Ground floor Main Entrance escalator is operational.",
    page_number=1,
    block_type="paragraph",
)
db.add(chunk3)

# Test case 4: Chunk has no matches
chunk4 = DocumentChunk(
    id="chunk-4",
    document_id="test-doc",
    chunk_index=3,
    text_content="The fire alarm system was tested yesterday.",
    page_number=1,
    block_type="paragraph",
)
db.add(chunk4)

db.commit()

logger.info("Running matching tests...")
logger.info("")

# Test each chunk
for chunk in [chunk1, chunk2, chunk3, chunk4]:
    logger.info("=" * 60)
    logger.info("Chunk: {}", chunk.text_content)
    logger.info("-" * 60)
    
    matches = row_match_service.match_chunk(db, chunk)
    
    if matches:
        logger.info("✓ MATCHES FOUND: {}", len(matches))
        for m in matches:
            logger.info("  Row: {} | Confidence: {} | Method: {}", 
                       m.row_pk, m.confidence, m.match_method)
    else:
        logger.info("✗ No matches")
    
    logger.info("")

# Cleanup
logger.info("Cleaning up test data...")
db.query(RowSemanticIndex).filter(RowSemanticIndex.source_table == "test_assets").delete()
db.query(DocumentChunk).filter(DocumentChunk.document_id == "test-doc").delete()
db.query(Document).filter(Document.id == "test-doc").delete()
db.commit()
db.close()

logger.info("✓ Test complete")
