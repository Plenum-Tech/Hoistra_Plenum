"""Citation, highlight, and table parser services.

These are intentionally lean MVP implementations that satisfy the
interface contracts in the spec. Swap in richer logic as needed.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import Document, DocumentChunk
from app.schemas import Citation
from app.services.reranker import RankedChunk


class CitationService:
    MAX_QUOTE_WORDS = 30

    def build(self, db: Session, ranked: list[RankedChunk]) -> list[Citation]:
        citations: list[Citation] = []
        doc_cache: dict[str, Document] = {}
        for r in ranked:
            doc = doc_cache.get(r.chunk.document_id)
            if doc is None:
                doc = db.query(Document).filter(Document.id == r.chunk.document_id).first()
                if doc:
                    doc_cache[doc.id] = doc
            if not doc:
                continue
            quote_words = r.chunk.text_content.split()[: self.MAX_QUOTE_WORDS]
            quote = " ".join(quote_words) + ("…" if len(quote_words) == self.MAX_QUOTE_WORDS else "")
            citations.append(Citation(
                document_id=doc.id,
                file_name=doc.file_name,
                page_start=r.chunk.page_start,
                page_end=r.chunk.page_end,
                section=r.chunk.section_label,
                chunk_id=r.chunk.id,
                quote=quote,
            ))
        logger.info(
            "Citations built | ranked={} | citations={} | unique_docs={}",
            len(ranked), len(citations), len(doc_cache),
        )
        return citations


class HighlightService:
    def build(self, ranked: list[RankedChunk]) -> list[dict]:
        """Return a list of page/span references the UI can use to highlight."""
        out = []
        for r in ranked:
            out.append({
                "chunk_id": r.chunk.id,
                "document_id": r.chunk.document_id,
                "page": r.chunk.page_start,
                "block_type": r.chunk.block_type,
                "text_preview": r.chunk.text_content[:160],
            })
        return out


class TableSemanticParser:
    """Post-processes raw extracted tables into the normalized JSON
    form shown in section 10 of the spec. Lean implementation."""

    def parse(self, table_rows: list[list[str]], page: int, table_index: int) -> dict:
        if not table_rows:
            return {}
        headers = [h.strip() for h in table_rows[0]]
        rows = []
        for row_i, row in enumerate(table_rows[1:]):
            cells = {h: (row[i] if i < len(row) else "").strip()
                     for i, h in enumerate(headers)}
            rows.append({"row_index": row_i, "cells": cells})
        return {
            "table_id": f"tbl_{page}_{table_index}",
            "page": page,
            "headers": headers,
            "rows": rows,
            "semantic_type": None,  # plug in classifier later
        }


citation_service = CitationService()
highlight_service = HighlightService()
table_semantic_parser = TableSemanticParser()
