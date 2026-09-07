"""RAG query + debug endpoints."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.db.session import get_db
from app.schemas import RagDebugResponse, RagQueryRequest, RagQueryResponse, RetrievedChunk
from app.services.answer_service import answer_service
from app.services.audit_service import audit_service
from app.services.bm25_service import bm25_service
from app.services.citation_service import citation_service, highlight_service
from app.services.query_classifier import query_classifier
from app.services.reranker import reranker
from app.services.row_match_service import row_match_service
from app.services.vector_service import vector_service

router = APIRouter(prefix="/rag", tags=["rag"])


def _run_pipeline(req: RagQueryRequest, db: Session, debug: bool = False):
    start = time.time()
    logger.info("RAG query | q='{}' | debug={}", req.query[:80], debug)

    # 1. Classify
    t_classify = time.time()
    qinfo = query_classifier.classify(req.query)
    logger.debug("Pipeline stage=classify | ms={:.1f}", (time.time() - t_classify) * 1000)

    # 2. Audit query
    audit_query = audit_service.log_query(
        db=db,
        query_text=req.query,
        query_type=qinfo["query_type"],
        filters=req.filters,
        user_id=req.user_id,
        session_id=req.session_id,
    )

    # 3. Hybrid candidate generation
    t_search = time.time()
    vhits = vector_service.search(db, req.query, top_k=settings.top_k_vector, filters=req.filters)
    bhits = bm25_service.search(db, req.query, top_k=settings.top_k_bm25, filters=req.filters)
    logger.info(
        "Pipeline stage=search | vector_hits={} | bm25_hits={} | ms={:.1f}",
        len(vhits), len(bhits), (time.time() - t_search) * 1000,
    )

    # 4. Fusion + rerank
    t_rerank = time.time()
    ranked = reranker.fuse_and_rerank(
        db=db,
        query=req.query,
        vector_hits=vhits,
        bm25_hits=bhits,
        query_info=qinfo,
        top_k=req.top_k or settings.top_k_final,
    )
    logger.debug("Pipeline stage=rerank | ranked={} | ms={:.1f}", len(ranked), (time.time() - t_rerank) * 1000)

    # 5. Row-level mapping (best-effort, only for top 3 chunks)
    # For each citation chunk, look for matching enterprise database rows
    # in the row_semantic_index table and return the FULL row data.
    from app.schemas import MatchedRow

    t_rowmatch = time.time()
    matched_rows: list[MatchedRow] = []
    for r in ranked[:3]:
        for m in row_match_service.match_chunk(db, r.chunk):
            matched_rows.append(MatchedRow(
                source_table=m.source_table,
                row_pk=m.row_pk,
                confidence=m.confidence,
                match_method=m.match_method,
                row_data=m.matched_fields,
                evidence=m.evidence,
                matched_metadata_fields=m.matched_metadata_fields,
                match_details=m.match_details,
            ))

    logger.info(
        "Pipeline stage=row_match | chunks_checked=3 | matched_rows={} | ms={:.1f}",
        len(matched_rows), (time.time() - t_rowmatch) * 1000,
    )

    # 6. Generate answer
    t_answer = time.time()
    gen = answer_service.generate(req.query, ranked)

    logger.debug("Pipeline stage=answer | ms={:.1f}", (time.time() - t_answer) * 1000)

    # 7. Citations + highlights
    citations = citation_service.build(db, ranked)
    highlights = highlight_service.build(ranked)
    logger.debug("Pipeline stage=citations | count={} | highlights={}", len(citations), len(highlights))

    latency_ms = int((time.time() - start) * 1000)

    logger.info(
        "RAG pipeline complete | total_ms={} | query_type={} | ranked={} | "
        "matched_rows={} | citations={} | confidence={:.4f} | model={}",
        latency_ms, qinfo["query_type"], len(ranked),
        len(matched_rows), len(citations), gen.confidence, gen.model_name,
    )

    # 8. Audit answer
    answer_row = audit_service.log_answer(
        db=db,
        query_id=audit_query.id,
        answer_text=gen.text,
        citations=[c.model_dump() for c in citations],
        highlights=highlights,
        model_name=gen.model_name,
        prompt_tokens=gen.prompt_tokens,
        completion_tokens=gen.completion_tokens,
        latency_ms=latency_ms,
        confidence=gen.confidence,
    )

    base = RagQueryResponse(
        query_id=audit_query.id,
        query_type=qinfo["query_type"],
        answer=gen.text,
        confidence=gen.confidence,
        citations=citations,
        matched_rows=matched_rows,
        latency_ms=latency_ms,
        model_name=gen.model_name,
    )

    if not debug:
        return base

    retrieved = [
        RetrievedChunk(
            chunk_id=r.chunk.id,
            document_id=r.chunk.document_id,
            file_name="",  # filled below
            score=round(r.score, 4),
            vector_score=round(r.vector_score, 4),
            bm25_score=round(r.bm25_score, 4),
            block_type=r.chunk.block_type,
            page_start=r.chunk.page_start,
            page_end=r.chunk.page_end,
            text_content=r.chunk.text_content,
            meta=r.chunk.meta,
        )
        for r in ranked
    ]
    # Fill filenames
    from app.db.models import Document
    docs = {d.id: d for d in db.query(Document).filter(
        Document.id.in_([c.document_id for c in retrieved])
    ).all()}
    for rc in retrieved:
        if rc.document_id in docs:
            rc.file_name = docs[rc.document_id].file_name

    return RagDebugResponse(
        **base.model_dump(),
        retrieved_chunks=retrieved,
        stages={
            "query_info": qinfo,
            "vector_hits": len(vhits),
            "bm25_hits": len(bhits),
            "fused_reranked": len(ranked),
            "answer_id": answer_row.id,
        },
    )


@router.post("/query", response_model=RagQueryResponse)
def query(req: RagQueryRequest, db: Session = Depends(get_db)):
    return _run_pipeline(req, db, debug=False)


@router.post("/debug", response_model=RagDebugResponse)
def debug_query(req: RagQueryRequest, db: Session = Depends(get_db)):
    return _run_pipeline(req, db, debug=True)


@router.post("/rows")
def query_matched_rows(req: RagQueryRequest, db: Session = Depends(get_db)):
    """Return ONLY the matched enterprise database rows for a query.

    Use this when you want structured row-level data without generating
    an LLM answer. Example use cases:
      - "List all equipment rows mentioned in document X"
      - "Find asset rows related to 'elevator maintenance'"
      - "Which contracts reference asset code AHU-001?"

    Returns MatchedRow[] (same structure as /rag/query's matched_rows
    field) but skips answer generation, so it's faster and cheaper.
    """
    from app.schemas import MatchedRow
    start = time.time()

    qinfo = query_classifier.classify(req.query)
    vhits = vector_service.search(db, req.query, top_k=settings.top_k_vector, filters=req.filters)
    bhits = bm25_service.search(db, req.query, top_k=settings.top_k_bm25, filters=req.filters)
    ranked = reranker.fuse_and_rerank(
        db=db,
        query=req.query,
        vector_hits=vhits,
        bm25_hits=bhits,
        query_info=qinfo,
        top_k=req.top_k or settings.top_k_final,
    )

    # Match ALL top chunks (not just top 3 like the main query path)
    matched_rows: list[MatchedRow] = []
    seen_pks: set[tuple[str, str]] = set()  # dedup by (table, pk)

    for r in ranked:
        for m in row_match_service.match_chunk(db, r.chunk):
            key = (m.source_table, m.row_pk)
            if key in seen_pks:
                continue
            seen_pks.add(key)
            matched_rows.append(MatchedRow(
                source_table=m.source_table,
                row_pk=m.row_pk,
                confidence=m.confidence,
                match_method=m.match_method,
                row_data=m.matched_fields,
                evidence=m.evidence,
                matched_metadata_fields=m.matched_metadata_fields,
                match_details=m.match_details,
            ))

    # Sort by confidence desc
    matched_rows.sort(key=lambda x: x.confidence, reverse=True)

    latency_ms = int((time.time() - start) * 1000)

    return {
        "query": req.query,
        "query_type": qinfo["query_type"],
        "matched_rows": matched_rows,
        "latency_ms": latency_ms,
        "total_chunks_searched": len(ranked),
        "unique_rows_matched": len(matched_rows),
    }
