"""Vector similarity search service.

- In Postgres + pgvector mode, runs an ORDER BY embedding <=> :q query.
- In SQLite dev mode, loads candidate chunks into memory and computes
  cosine similarity with numpy. Both paths return the same shape.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.models import DocumentChunk
from app.services.embedding_service import embedding_service


@dataclass
class VectorHit:
    chunk_id: str
    score: float  # cosine similarity, 0..1


class VectorService:
    def search(
        self,
        db: Session,
        query: str,
        top_k: int = 20,
        filters: dict | None = None,
    ) -> list[VectorHit]:
        q_vec = embedding_service.embed_text(query)
        if not q_vec:
            return []

        if settings.effective_use_sqlite_dev:
            return self._search_memory(db, q_vec, top_k, filters)
        return self._search_pgvector(db, q_vec, top_k, filters)

    def _apply_filters(self, stmt, filters: dict | None):
        if not filters:
            return stmt
        if "document_id" in filters:
            stmt = stmt.filter(DocumentChunk.document_id == filters["document_id"])
        if "document_type" in filters:
            from app.db.models import Document
            stmt = stmt.join(Document).filter(
                Document.document_type.in_(filters["document_type"])
            )
        return stmt

    # ---------- SQLite / in-memory path ----------
    def _search_memory(
        self, db: Session, q_vec: list[float], top_k: int, filters: dict | None
    ) -> list[VectorHit]:
        stmt = db.query(DocumentChunk).filter(DocumentChunk.embedding.isnot(None))
        stmt = self._apply_filters(stmt, filters)
        chunks: list[DocumentChunk] = stmt.all()
        if not chunks:
            return []

        q = np.asarray(q_vec, dtype=np.float32)
        q_norm = float(np.linalg.norm(q)) or 1.0

        scored: list[tuple[str, float]] = []
        for c in chunks:
            emb = c.embedding
            if not emb:
                continue
            v = np.asarray(emb, dtype=np.float32)
            v_norm = float(np.linalg.norm(v)) or 1.0
            sim = float(np.dot(q, v) / (q_norm * v_norm))
            # Map [-1, 1] → [0, 1]
            scored.append((c.id, (sim + 1.0) / 2.0))

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]
        logger.info(
            "Vector search (memory) | candidates_with_emb={} | top_k={} | hits={} | "
            "top_score={:.4f} | bottom_score={:.4f} | filters={}",
            len(scored), top_k, len(top),
            top[0][1] if top else 0.0, top[-1][1] if top else 0.0,
            list(filters.keys()) if filters else None,
        )
        return [VectorHit(chunk_id=cid, score=s) for cid, s in top]

    # ---------- pgvector path ----------
    def _search_pgvector(
        self, db: Session, q_vec: list[float], top_k: int, filters: dict | None
    ) -> list[VectorHit]:
        from sqlalchemy import func

        stmt = db.query(
            DocumentChunk.id,
            (1 - DocumentChunk.embedding.cosine_distance(q_vec)).label("sim"),
        ).filter(DocumentChunk.embedding.isnot(None))
        stmt = self._apply_filters(stmt, filters)
        stmt = stmt.order_by(DocumentChunk.embedding.cosine_distance(q_vec)).limit(top_k)

        rows = stmt.all()
        hits = [VectorHit(chunk_id=r[0], score=float(r[1])) for r in rows]
        logger.info(
            "Vector search (pgvector) | top_k={} | hits={} | top_score={:.4f} | filters={}",
            top_k, len(hits),
            hits[0].score if hits else 0.0,
            list(filters.keys()) if filters else None,
        )
        return hits


vector_service = VectorService()
