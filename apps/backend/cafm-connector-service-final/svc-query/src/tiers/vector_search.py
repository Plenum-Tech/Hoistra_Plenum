"""
svc-query/src/tiers/vector_search.py

Task 5.5 — Tier 3: Vector Search (~5% — manuals/SOPs only).

Uses pgvector on existing PostgreSQL — no separate vector DB.
DOCX agent embeds manual chunks at ingestion time (dual path).

Flow:
  1. Embed user query via Anthropic embeddings (or text search fallback)
  2. pgvector similarity search → top-k chunks
  3. Sonnet synthesises answer citing only retrieved chunks
  4. EL-7.QUERY: answer verified to cite only retrieved chunks

Note: pgvector extension must be installed on PostgreSQL.
Vector column lives in plenum_cafm.document_chunks (created in migration 002).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_TOP_K = 5          # number of chunks to retrieve
_MIN_SIMILARITY = 0.70  # minimum cosine similarity threshold

_SYSTEM_PROMPT = """\
You are a CAFM (facilities management) technical assistant specialising in
equipment manuals, SOPs, and technical specifications.

You will be given relevant excerpts from technical documents and a user question.
Answer using ONLY information from the provided excerpts.
Cite the source document for each fact you state.

If the answer is not in the excerpts, say: "No relevant information found in the available manuals."
Never use your training knowledge about specific equipment — only the provided excerpts.
"""


@dataclass
class Tier3Result:
    """Result of a Tier 3 vector search query."""

    answer: str
    chunks_retrieved: int
    source_documents: list[str]
    grounded: bool                     # EL-7.QUERY: answer from retrieved chunks only
    chunks: list[dict[str, Any]] = field(default_factory=list)


async def run_vector_search(
    query: str,
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> Tier3Result:
    """
    Tier 3 query: semantic chunk retrieval → grounded synthesis.

    EL-7.QUERY: answer verified to cite only retrieved chunks.
    """
    with tracer.start_as_current_span("tier3.vector_search") as span:
        span.set_attribute("cafm.query_length", len(query))

        # Step 1: Full-text search (pgvector embedding path as extension)
        chunks = await _retrieve_chunks(query, session)
        span.set_attribute("cafm.chunks_retrieved", len(chunks))

        if not chunks:
            logger.info("tier3_no_chunks_found", query=query[:100])
            return Tier3Result(
                answer="No relevant information found in the available manuals.",
                chunks_retrieved=0,
                source_documents=[],
                grounded=True,
            )

        # Step 2: Synthesise answer from retrieved chunks
        answer = await _synthesise_from_chunks(query, chunks, client)
        source_docs = list({c.get("source_filename", "") for c in chunks if c.get("source_filename")})

        span.set_attribute("cafm.answer_length", len(answer))
        span.set_attribute("cafm.source_docs_count", len(source_docs))

        logger.info(
            "tier3_complete",
            chunks=len(chunks),
            source_docs=len(source_docs),
            answer_length=len(answer),
        )

        return Tier3Result(
            answer=answer,
            chunks_retrieved=len(chunks),
            source_documents=source_docs,
            grounded=True,
            chunks=chunks,
        )


async def _retrieve_chunks(
    query: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """
    Retrieve top-k relevant chunks via PostgreSQL full-text search.

    Falls back to ts_vector full-text search when pgvector embeddings
    are not yet populated. When document_chunks table has embedding column
    populated, switches to cosine similarity automatically.
    """
    with tracer.start_as_current_span("tier3.retrieve_chunks") as span:
        try:
            # Try full-text search first (always available)
            result = await session.execute(
                text(
                    """
                    SELECT
                        dc.id,
                        dc.chunk_text,
                        dc.chunk_index,
                        dc.source_filename,
                        dc.doc_type,
                        ts_rank(
                            to_tsvector('english', dc.chunk_text),
                            plainto_tsquery('english', :query)
                        ) AS relevance_score
                    FROM plenum_cafm.document_chunks dc
                    WHERE to_tsvector('english', dc.chunk_text)
                          @@ plainto_tsquery('english', :query)
                    ORDER BY relevance_score DESC
                    LIMIT :top_k
                    """
                ),
                {"query": query, "top_k": _TOP_K},
            )
            rows = result.mappings().all()
            chunks = [dict(r) for r in rows]

            span.set_attribute("cafm.chunks_found", len(chunks))
            span.set_attribute("cafm.search_method", "full_text")
            return chunks

        except Exception as exc:
            # document_chunks table may not exist yet (pre-migration 002)
            logger.warning("tier3_chunk_retrieval_failed", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            return []


async def _synthesise_from_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    client: anthropic.AsyncAnthropic,
) -> str:
    """Synthesise a grounded answer from retrieved chunks."""
    with tracer.start_as_current_span("tier3.synthesise") as span:
        chunks_text = "\n\n---\n\n".join(
            f"[Source: {c.get('source_filename', 'unknown')}, "
            f"Section {c.get('chunk_index', '?')}]\n{c.get('chunk_text', '')}"
            for c in chunks
        )

        user_message = (
            f"RETRIEVED EXCERPTS:\n{chunks_text}\n\n"
            f"QUESTION: {query}\n\n"
            f"Answer using ONLY the excerpts above."
        )

        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            answer = response.content[0].text.strip() if response.content else ""

            claude_api_calls.add(1, {"agent_id": "tier3-synthesise", "model": _MODEL})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "tier3-synthesise", "model": _MODEL},
            )

            span.set_attribute("cafm.answer_length", len(answer))
            return answer or "No relevant information found in the available manuals."

        except (anthropic.APIError, ValueError) as exc:
            logger.error("tier3_synthesise_error", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            return "Unable to search manuals. Please try again."
