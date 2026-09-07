"""BM25 lexical retrieval service.

In dev mode we build an in-memory BM25 index from all chunks each query
(fast enough for thousands of chunks). In production on Postgres you'd
instead use tsvector + ts_rank — this module exposes the same interface
either way.
"""
from __future__ import annotations

from dataclasses import dataclass

from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import DocumentChunk
from app.utils.text_normalization import tokenize


@dataclass
class BM25Hit:
    chunk_id: str
    score: float


class BM25Service:
    def search(
        self,
        db: Session,
        query: str,
        top_k: int = 20,
        filters: dict | None = None,
    ) -> list[BM25Hit]:
        """Build a BM25 index over all chunks and return top_k hits."""
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        stmt = db.query(DocumentChunk)
        if filters:
            if "document_type" in filters:
                # join through Document — simplified; real impl would filter upstream
                from app.db.models import Document
                stmt = stmt.join(Document).filter(
                    Document.document_type.in_(filters["document_type"])
                )
            if "document_id" in filters:
                stmt = stmt.filter(DocumentChunk.document_id == filters["document_id"])

        chunks: list[DocumentChunk] = stmt.all()
        if not chunks:
            logger.debug("BM25: no chunks to search")
            return []

        tokenized_corpus = [tokenize(c.normalized_text or c.text_content) for c in chunks]
        bm25 = BM25Okapi(tokenized_corpus)
        scores = bm25.get_scores(q_tokens)

        # Normalize to 0..1 so we can blend with vector scores later
        max_score = float(scores.max()) if len(scores) else 0.0
        if max_score <= 0:
            return []

        ranked = sorted(
            [(chunks[i].id, float(scores[i]) / max_score) for i in range(len(chunks))],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]

        hits = [BM25Hit(chunk_id=cid, score=s) for cid, s in ranked if s > 0]
        logger.info(
            "BM25 search | q='{}' | q_tokens={} | corpus={} | top={} | hits={} | "
            "top_score={:.4f} | filters={}",
            query[:60], len(q_tokens), len(chunks), top_k, len(hits),
            hits[0].score if hits else 0.0, list(filters.keys()) if filters else None,
        )
        return hits


bm25_service = BM25Service()
