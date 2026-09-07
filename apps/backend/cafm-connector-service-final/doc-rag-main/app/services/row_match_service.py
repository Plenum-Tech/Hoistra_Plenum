"""Row-level database mapping service.

Attempts to link retrieved chunks back to enterprise rows stored in
the `row_semantic_index` table. Uses exact key match, normalized key
match, and semantic similarity per section 7 of the spec.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sqlalchemy.orm import Session

from app.core.logger import logger
from app.db.models import DocumentChunk, RowSemanticIndex
from app.services.embedding_service import embedding_service
from app.utils.entity_extraction import extract_keys
from app.utils.text_normalization import normalize_key, token_overlap


@dataclass
class RowMatch:
    source_table: str
    row_pk: str
    confidence: float
    match_method: str
    matched_fields: dict  # All metadata from database
    evidence: str
    matched_metadata_fields: list[str]
    match_details: dict


@dataclass
class RowData:
    """Duck-typed row compatible with RowSemanticIndex for file-based matching."""
    source_table: str
    row_pk: str
    semantic_text: str
    meta: dict | None = None
    embedding: list | None = None


class RowMatchService:
    def match_chunk_against_rows(
        self,
        chunk: DocumentChunk,
        rows: list[RowData],
    ) -> list[RowMatch]:
        """Score a chunk against a list of RowData objects (DB or file-sourced)."""
        if not rows:
            return []

        chunk_keys = extract_keys(chunk.text_content)
        chunk_keys_norm = [normalize_key(k) for k in chunk_keys]

        logger.debug(
            "RowMatch chunk={} | block={} | row_count={} | chunk_keys={}",
            chunk.id[:8], chunk.block_type, len(rows), chunk_keys,
        )

        matches: list[RowMatch] = []
        for r in rows:
            row_keys = extract_keys(r.semantic_text)
            row_keys_norm = [normalize_key(k) for k in row_keys]

            exact = bool(set(chunk_keys) & set(row_keys))
            normalized = bool(set(chunk_keys_norm) & set(row_keys_norm)) and not exact

            overlap = token_overlap(chunk.text_content, r.semantic_text)

            meta_overlap = 0.0
            matched_meta_fields = []
            if r.meta:
                chunk_text_lower = chunk.text_content.lower()
                meta_values_checked = []
                for key, val in r.meta.items():
                    if val and isinstance(val, str) and len(str(val)) > 2:
                        val_lower = str(val).lower()
                        meta_values_checked.append(val_lower)
                        if val_lower in chunk_text_lower:
                            matched_meta_fields.append(f"{key}={val}")
                if meta_values_checked:
                    meta_overlap = min(1.0, len(matched_meta_fields) / max(1, len(meta_values_checked)))

            semantic = 0.0
            if chunk.embedding is not None and r.embedding is not None:
                a = np.asarray(chunk.embedding, dtype=np.float32)
                b = np.asarray(r.embedding, dtype=np.float32)
                denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
                semantic = max(0.0, float(np.dot(a, b) / denom))

            exact_boost = 0.1 if exact else (0.05 if normalized else 0.0)
            confidence = (
                0.40 * semantic
                + 0.30 * overlap
                + 0.30 * meta_overlap
                + exact_boost
            )

            if confidence >= 0.15:
                if exact:
                    method = "exact_key"
                elif normalized:
                    method = "normalized_key"
                elif semantic > 0.5:
                    method = "semantic"
                elif meta_overlap > 0.3:
                    method = "metadata_match"
                elif overlap > 0.2:
                    method = "bm25"
                else:
                    method = "hybrid"

                logger.debug(
                    "  RowMatch hit | row={}/{} | method={} | conf={:.4f} | "
                    "semantic={:.4f} | bm25={:.4f} | meta={:.4f} | "
                    "exact={} | normalized={} | meta_fields={}",
                    r.source_table, r.row_pk, method, round(confidence, 4),
                    round(semantic, 4), round(overlap, 4), round(meta_overlap, 4),
                    exact, normalized, matched_meta_fields,
                )

                matches.append(RowMatch(
                    source_table=r.source_table,
                    row_pk=r.row_pk,
                    confidence=round(confidence, 4),
                    match_method=method,
                    matched_fields=r.meta or {},
                    evidence=r.semantic_text[:200],
                    matched_metadata_fields=matched_meta_fields,
                    match_details={
                        "semantic_score": round(semantic, 4),
                        "bm25_overlap": round(overlap, 4),
                        "metadata_overlap": round(meta_overlap, 4),
                        "exact_key_match": exact,
                        "normalized_key_match": normalized,
                    }
                ))

        matches.sort(key=lambda m: m.confidence, reverse=True)
        top10 = matches[:10]
        below_threshold = len(rows) - len(matches)

        if top10:
            avg_sem  = sum(m.match_details["semantic_score"] for m in top10) / len(top10)
            avg_bm25 = sum(m.match_details["bm25_overlap"]   for m in top10) / len(top10)
            avg_meta = sum(m.match_details["metadata_overlap"] for m in top10) / len(top10)
            sem_led  = sum(1 for m in top10 if m.match_details["semantic_score"] >= m.match_details["bm25_overlap"])
            bm25_led = len(top10) - sem_led
            method_counts: dict[str, int] = {}
            for m in top10:
                method_counts[m.match_method] = method_counts.get(m.match_method, 0) + 1
        else:
            avg_sem = avg_bm25 = avg_meta = 0.0
            sem_led = bm25_led = 0
            method_counts = {}

        logger.info(
            "RowMatch complete | chunk={} | rows_checked={} | matches={} | "
            "below_threshold={} | top_conf={:.4f} | top_method={} | "
            "avg_semantic={:.4f} | avg_bm25={:.4f} | avg_meta={:.4f} | "
            "sem_led={} | bm25_led={} | methods={}",
            chunk.id[:8], len(rows), len(top10), below_threshold,
            top10[0].confidence if top10 else 0.0,
            top10[0].match_method if top10 else "none",
            avg_sem, avg_bm25, avg_meta,
            sem_led, bm25_led, method_counts,
        )
        return top10

    def match_chunk(
        self,
        db: Session,
        chunk: DocumentChunk,
        source_table: str | None = None,
    ) -> list[RowMatch]:
        """Return row matches for a single chunk using the row semantic index."""
        q = db.query(RowSemanticIndex)
        if source_table:
            q = q.filter(RowSemanticIndex.source_table == source_table)
        db_rows = q.all()
        if not db_rows:
            return []

        rows = [
            RowData(
                source_table=r.source_table,
                row_pk=r.row_pk,
                semantic_text=r.semantic_text,
                meta=r.meta,
                embedding=r.embedding,
            )
            for r in db_rows
        ]
        return self.match_chunk_against_rows(chunk, rows)


row_match_service = RowMatchService()
