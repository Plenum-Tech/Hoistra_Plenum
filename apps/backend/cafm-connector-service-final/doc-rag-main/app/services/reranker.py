"""Hybrid fusion + reranker.

Stage A: merge BM25 and vector hits with weighted score fusion.
Stage B: rerank by combining fused score with section fit, entity
          overlap, and block-type preference from the query classifier.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.models import DocumentChunk
from app.services.bm25_service import BM25Hit
from app.services.vector_service import VectorHit
from app.utils.text_normalization import token_overlap


@dataclass
class RankedChunk:
    chunk: DocumentChunk
    score: float
    vector_score: float
    bm25_score: float
    reasons: dict


class Reranker:
    def fuse_and_rerank(
        self,
        db: Session,
        query: str,
        vector_hits: list[VectorHit],
        bm25_hits: list[BM25Hit],
        query_info: dict,
        top_k: int,
    ) -> list[RankedChunk]:
        # ---- Stage A: weighted fusion ----
        v_map = {h.chunk_id: h.score for h in vector_hits}
        b_map = {h.chunk_id: h.score for h in bm25_hits}
        all_ids = set(v_map) | set(b_map)
        if not all_ids:
            logger.debug("Reranker | no candidates from vector or BM25 — returning empty")
            return []

        w_v = settings.hybrid_vector_weight
        w_b = settings.hybrid_bm25_weight

        vector_only = len(set(v_map) - set(b_map))
        bm25_only   = len(set(b_map) - set(v_map))
        both        = len(set(v_map) & set(b_map))
        logger.info(
            "Reranker Stage A | vector_hits={} | bm25_hits={} | union={} | "
            "vector_only={} | bm25_only={} | both={} | w_vector={} | w_bm25={}",
            len(vector_hits), len(bm25_hits), len(all_ids),
            vector_only, bm25_only, both, w_v, w_b,
        )
        fused = {
            cid: w_v * v_map.get(cid, 0.0) + w_b * b_map.get(cid, 0.0)
            for cid in all_ids
        }

        # Load chunks in one query
        chunks = db.query(DocumentChunk).filter(DocumentChunk.id.in_(all_ids)).all()
        chunk_by_id = {c.id: c for c in chunks}

        # ---- Stage B: rerank with extra features ----
        ranked: list[RankedChunk] = []
        for cid, base_score in fused.items():
            c = chunk_by_id.get(cid)
            if not c:
                continue

            entity_overlap = token_overlap(query, c.text_content)
            block_bonus = 0.0
            if query_info.get("table_bias"):
                # Strongly prefer row-level hits for table/row-grounding
                # queries; summaries are useful but shouldn't outrank rows.
                if c.block_type == "table_row":
                    block_bonus = 0.20
                elif c.block_type == "table_summary":
                    block_bonus = 0.05
                elif c.block_type == "image":
                    block_bonus = 0.10
            elif c.block_type == "paragraph":
                block_bonus = 0.05

            # Exact key bonus
            key_bonus = 0.0
            for key in query_info.get("entity_keys", []):
                if key.lower() in (c.text_content or "").lower():
                    key_bonus += 0.25
            key_bonus = min(key_bonus, 0.5)

            final = base_score + 0.15 * entity_overlap + block_bonus + key_bonus

            logger.debug(
                "Reranker chunk={} | block={} | fused={:.4f} | entity_overlap={:.4f} | "
                "block_bonus={:.4f} | key_bonus={:.4f} | final={:.4f}",
                cid[:8], c.block_type, base_score, entity_overlap,
                block_bonus, key_bonus, final,
            )

            ranked.append(RankedChunk(
                chunk=c,
                score=final,
                vector_score=v_map.get(cid, 0.0),
                bm25_score=b_map.get(cid, 0.0),
                reasons={
                    "fused": round(base_score, 4),
                    "entity_overlap": round(entity_overlap, 4),
                    "block_bonus": block_bonus,
                    "key_bonus": round(key_bonus, 4),
                },
            ))

        ranked.sort(key=lambda r: r.score, reverse=True)
        top = ranked[:top_k]

        if top:
            avg_vec  = sum(r.vector_score for r in top) / len(top)
            avg_bm25 = sum(r.bm25_score  for r in top) / len(top)
            vec_led  = sum(1 for r in top if r.vector_score >= r.bm25_score)
            bm25_led = len(top) - vec_led
        else:
            avg_vec = avg_bm25 = 0.0
            vec_led = bm25_led = 0

        logger.info(
            "Reranker Stage B | candidates={} | top_k={} | "
            "top_score={:.4f} | bottom_score={:.4f} | "
            "avg_vec={:.4f} | avg_bm25={:.4f} | vec_led={} | bm25_led={} | "
            "table_bias={} | entity_keys={}",
            len(ranked), len(top),
            top[0].score if top else 0.0,
            top[-1].score if top else 0.0,
            avg_vec, avg_bm25, vec_led, bm25_led,
            query_info.get("table_bias"), query_info.get("entity_keys"),
        )
        for i, r in enumerate(top[:5]):
            logger.debug(
                "  rank#{} chunk={} | block={} | score={:.4f} | "
                "vec={:.4f} | bm25={:.4f} | reasons={}",
                i + 1, r.chunk.id[:8], r.chunk.block_type, r.score,
                r.vector_score, r.bm25_score, r.reasons,
            )
        return top


reranker = Reranker()
