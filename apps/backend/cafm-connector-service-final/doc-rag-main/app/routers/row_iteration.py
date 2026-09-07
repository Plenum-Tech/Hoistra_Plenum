"""
Row-centric matching endpoint.

This endpoint iterates through each database row and shows which document chunks matched it.
Different from the chunk-centric approach, this gives you a complete view per database row.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import Document, DocumentChunk, RowSemanticIndex
from app.db.session import get_db
from app.services.row_match_service import row_match_service

router = APIRouter(prefix="/rows", tags=["Row Matching"])


@router.post("/{document_id}/iterate-rows")
def iterate_rows_against_document(
    document_id: str,
    confidence_threshold: float = 0.15,
    db: Session = Depends(get_db),
):
    """
    Iterate through each database row and show which document chunks matched.
    
    This is the INVERSE of the normal matching - instead of going chunk-by-chunk,
    this goes row-by-row through your database and shows matches.
    
    Response structure:
    {
      "document_id": "...",
      "total_rows_checked": 30,
      "rows_with_matches": 15,
      "rows_without_matches": 15,
      "iterations": [
        {
          "row_index": 0,
          "source_table": "assets",
          "row_pk": "ESC-001",
          "row_data": {...all CSV columns...},
          "has_match": true,
          "matched_chunks": [
            {
              "chunk_id": "...",
              "chunk_index": 45,
              "page_number": 12,
              "confidence": 0.58,
              "matched_fields": ["manufacturer=Schindler", "model=9300"],
              "chunk_text": "The Schindler 9300 escalator..."
            }
          ],
          "best_confidence": 0.58,
          "total_chunks_matched": 2,
          "match_summary": "Found on pages 12, 20"
        }
      ]
    }
    """
    logger.info("Row-by-row iteration | doc_id={} | threshold={}", document_id, confidence_threshold)
    
    # 1. Verify document exists
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # 2. Get all chunks
    chunks = db.query(DocumentChunk).filter(
        DocumentChunk.document_id == document_id
    ).order_by(DocumentChunk.chunk_index).all()
    
    if not chunks:
        return {
            "document_id": document_id,
            "file_name": doc.file_name,
            "total_rows_checked": 0,
            "rows_with_matches": 0,
            "rows_without_matches": 0,
            "iterations": []
        }
    
    # 3. Get all database rows
    all_rows = db.query(RowSemanticIndex).all()
    
    if not all_rows:
        return {
            "document_id": document_id,
            "file_name": doc.file_name,
            "error": "No rows in database to match against",
            "total_rows_checked": 0,
            "iterations": []
        }
    
    logger.info("Iterating {} database rows against {} document chunks", len(all_rows), len(chunks))
    
    # 4. ITERATE THROUGH EACH DATABASE ROW
    iterations = []
    rows_with_matches = 0
    rows_without_matches = 0
    
    for row_index, db_row in enumerate(all_rows):
        # For this row, check ALL chunks to find matches
        row_iteration = {
            "row_index": row_index,
            "source_table": db_row.source_table,
            "row_pk": db_row.row_pk,
            "row_data": db_row.meta or {},
            "has_match": False,
            "matched_chunks": [],
            "best_confidence": 0.0,
            "total_chunks_matched": 0,
            "match_summary": "",
        }
        
        # Check each chunk against this row
        for chunk in chunks:
            matches = row_match_service.match_chunk(db, chunk)
            
            # Find if this row matched this chunk
            for m in matches:
                if (m.source_table == db_row.source_table and 
                    m.row_pk == db_row.row_pk and 
                    m.confidence >= confidence_threshold):
                    
                    # This row matched this chunk!
                    row_iteration["matched_chunks"].append({
                        "chunk_id": chunk.id,
                        "chunk_index": chunk.chunk_index,
                        "page_number": chunk.page_start,
                        "block_type": chunk.block_type,
                        "confidence": round(m.confidence, 4),
                        "matched_fields": m.matched_metadata_fields,
                        "match_details": m.match_details,
                        "chunk_text": chunk.text_content,
                    })
                    
                    # Update best confidence
                    if m.confidence > row_iteration["best_confidence"]:
                        row_iteration["best_confidence"] = round(m.confidence, 4)
        
        # Summarize this row's matches
        if row_iteration["matched_chunks"]:
            row_iteration["has_match"] = True
            row_iteration["total_chunks_matched"] = len(row_iteration["matched_chunks"])
            rows_with_matches += 1
            
            # Create summary
            pages = sorted(set(c["page_number"] for c in row_iteration["matched_chunks"] if c["page_number"]))
            if pages:
                row_iteration["match_summary"] = f"Found on pages {', '.join(map(str, pages))}"
            else:
                row_iteration["match_summary"] = f"Matched in {len(row_iteration['matched_chunks'])} chunks"
        else:
            rows_without_matches += 1
            row_iteration["match_summary"] = "No matches found"
        
        iterations.append(row_iteration)
    
    logger.info("Iteration complete | matches={} | no_matches={}", rows_with_matches, rows_without_matches)
    
    return {
        "document_id": document_id,
        "file_name": doc.file_name,
        "total_rows_checked": len(all_rows),
        "rows_with_matches": rows_with_matches,
        "rows_without_matches": rows_without_matches,
        "confidence_threshold": confidence_threshold,
        "iterations": iterations,
    }


@router.post("/{document_id}/iterate-rows/summary")
def iterate_rows_summary(
    document_id: str,
    confidence_threshold: float = 0.15,
    show_unmatched: bool = False,
    db: Session = Depends(get_db),
):
    """
    Same as iterate-rows but returns only summary (no full chunk text).
    
    Useful when you have many rows and don't want huge responses.
    Set show_unmatched=false to only see rows that matched.
    """
    # Get full results
    full_result = iterate_rows_against_document(document_id, confidence_threshold, db)
    
    # Simplify iterations
    iterations = []
    for iteration in full_result["iterations"]:
        # Skip unmatched rows if requested
        if not show_unmatched and not iteration["has_match"]:
            continue
        
        # Simplify matched_chunks (no full text)
        simplified_chunks = [
            {
                "chunk_index": c["chunk_index"],
                "page_number": c["page_number"],
                "confidence": c["confidence"],
                "matched_fields": c["matched_fields"],
                "chunk_text_preview": c["chunk_text"][:100] + "..." if len(c["chunk_text"]) > 100 else c["chunk_text"],
            }
            for c in iteration["matched_chunks"]
        ]
        
        iterations.append({
            "row_index": iteration["row_index"],
            "source_table": iteration["source_table"],
            "row_pk": iteration["row_pk"],
            "row_data": iteration["row_data"],
            "has_match": iteration["has_match"],
            "matched_chunks": simplified_chunks,
            "best_confidence": iteration["best_confidence"],
            "total_chunks_matched": iteration["total_chunks_matched"],
            "match_summary": iteration["match_summary"],
        })
    
    return {
        **full_result,
        "iterations": iterations,
        "note": "Unmatched rows hidden" if not show_unmatched else "All rows shown",
    }
