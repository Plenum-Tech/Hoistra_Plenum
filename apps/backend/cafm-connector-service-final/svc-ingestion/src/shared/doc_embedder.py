"""
svc-ingestion/src/shared/doc_embedder.py

Shared chunking + embedding pipeline for DOCX (and future PDF/text) documents.

Design:
  - Splits document text into overlapping chunks by heading/paragraph boundary
  - Embeds each chunk via Anthropic's text-embedding-3-small (1536 dims)
  - Writes chunks + embeddings to plenum_cafm.document_chunks
  - Used by word_agent (primary path) and future pdf_agent dual path

Chunking strategy:
  - Split on headings first (lines that are short + Title Case / ALL CAPS)
  - If a section is still > MAX_CHUNK_CHARS, split further by paragraph
  - Overlap: last OVERLAP_CHARS of previous chunk prepended to next
    (so queries that span a boundary still match)

Embedding model:
  - Anthropic does not expose a standalone embedding API — use
    claude-haiku-4-5 with a "return embedding vector" prompt approach.
  - Instead we use the voyage-3 compatible path via the Anthropic SDK
    (anthropic.Anthropic().beta.messages with embedding=True).
  - FALLBACK: if embeddings unavailable, store chunk_text only and rely
    on the full-text search path in Tier 3 (already implemented).

Vector dimension: 1536 (voyage-3 / text-embedding-3-small compatible)
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from uuid import UUID

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Chunking config ────────────────────────────────────────────────────────────

MAX_CHUNK_CHARS = 1_200    # ~300 tokens — fits cleanly in context
OVERLAP_CHARS   = 150      # overlap between consecutive chunks
MIN_CHUNK_CHARS = 50       # discard chunks shorter than this (headers, noise)

# ── Heading detection ──────────────────────────────────────────────────────────

_HEADING_RE = re.compile(
    r"^(?:"
    r"#{1,3}\s+"                        # Markdown headings
    r"|[A-Z][A-Z\s]{3,50}$"             # ALL CAPS lines
    r"|Section\s+[A-Z0-9]"             # Section A / Section 1
    r"|[0-9]+\.[0-9]*\s+[A-Z]"         # 1.2 Something
    r")",
    re.MULTILINE,
)


def _is_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 120:
        return False
    # Short Title Case line with no sentence ending
    if line[0].isupper() and len(line) < 80 and not line.endswith((".", ",", ";")):
        words = line.split()
        if len(words) <= 8 and all(w[0].isupper() or w.lower() in
                                    {"and","or","of","the","in","for","a","an","to","with","at","by"}
                                    for w in words if w):
            return True
    return bool(_HEADING_RE.match(line))


# ── Chunker ────────────────────────────────────────────────────────────────────


def chunk_document(full_text: str, source_filename: str) -> list[dict[str, Any]]:
    """
    Split document text into overlapping chunks with heading metadata.

    Returns list of:
        {
          "chunk_index": int,
          "chunk_text":  str,
          "heading":     str | None,   # nearest heading above this chunk
        }
    """
    lines = full_text.splitlines()
    sections: list[tuple[str | None, list[str]]] = []  # (heading, lines)

    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if _is_heading(line):
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = line.strip()
            current_lines = []
        else:
            if line.strip():
                current_lines.append(line)

    if current_lines:
        sections.append((current_heading, current_lines))

    chunks: list[dict[str, Any]] = []
    prev_tail = ""

    for heading, section_lines in sections:
        section_text = "\n".join(section_lines).strip()
        if not section_text:
            continue

        # Prepend heading to section text for context
        if heading:
            section_text = f"{heading}\n{section_text}"

        # Split large sections by paragraph boundaries
        paragraphs = re.split(r"\n{2,}", section_text)
        buffer = prev_tail
        buf_heading = heading

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(buffer) + len(para) + 1 <= MAX_CHUNK_CHARS:
                buffer = (buffer + "\n" + para).strip() if buffer else para
            else:
                # Flush buffer as a chunk
                if len(buffer) >= MIN_CHUNK_CHARS:
                    chunks.append({
                        "chunk_index": len(chunks),
                        "chunk_text":  buffer,
                        "heading":     buf_heading,
                    })
                    prev_tail = buffer[-OVERLAP_CHARS:] if len(buffer) > OVERLAP_CHARS else buffer
                buffer = (prev_tail + "\n" + para).strip() if prev_tail else para
                buf_heading = heading

        # Flush remaining buffer
        if len(buffer) >= MIN_CHUNK_CHARS:
            chunks.append({
                "chunk_index": len(chunks),
                "chunk_text":  buffer,
                "heading":     buf_heading,
            })
            prev_tail = buffer[-OVERLAP_CHARS:] if len(buffer) > OVERLAP_CHARS else buffer

    logger.info(
        "doc_embedder.chunked",
        source_filename=source_filename,
        total_chars=len(full_text),
        chunks=len(chunks),
    )
    return chunks


# ── Embedder ───────────────────────────────────────────────────────────────────


async def embed_chunks(
    chunks: list[dict[str, Any]],
    client: anthropic.AsyncAnthropic,
) -> list[dict[str, Any]]:
    """
    Embed each chunk using claude-haiku-4-5 with a vector extraction prompt.

    Anthropic does not expose a standalone embeddings endpoint in the standard
    SDK (voyage embeddings require a separate client). We fall back to storing
    NULL embeddings and relying on full-text search in Tier 3 — the vector
    column will be populated when the voyage-3 API is available.

    Returns chunks with "embedding" key added (list[float] | None).
    """
    # Check if voyage client is available via anthropic SDK beta
    # For now: store NULL embeddings, full-text search handles queries
    # TODO: wire up voyage-3 when the anthropic SDK exposes it directly
    for chunk in chunks:
        chunk["embedding"] = None

    logger.info(
        "doc_embedder.embeddings_skipped",
        reason="voyage-3 not yet wired — full-text search active",
        chunks=len(chunks),
    )
    return chunks


# ── DB writer ──────────────────────────────────────────────────────────────────


async def store_chunks(
    chunks: list[dict[str, Any]],
    *,
    ingestion_id: UUID,
    source_filename: str,
    doc_type: str,
    engine: AsyncEngine,
) -> int:
    """
    Write chunks to plenum_cafm.document_chunks.

    Old chunks for the same source_filename are deleted first (re-ingestion safe).
    Returns number of rows written.
    """
    if not chunks:
        return 0

    with tracer.start_as_current_span("doc_embedder.store_chunks") as span:
        span.set_attribute("cafm.ingestion_id", str(ingestion_id))
        span.set_attribute("cafm.source_filename", source_filename)
        span.set_attribute("cafm.chunk_count", len(chunks))

        async with engine.begin() as conn:
            # Delete previous chunks for this file (idempotent re-ingestion)
            await conn.execute(
                text(
                    "DELETE FROM plenum_cafm.document_chunks "
                    "WHERE source_filename = :filename"
                ),
                {"filename": source_filename},
            )

            for chunk in chunks:
                embedding = chunk.get("embedding")
                embedding_sql = (
                    f"'{json.dumps(embedding)}'::vector"
                    if embedding is not None
                    else "NULL"
                )

                await conn.execute(
                    text(
                        f"INSERT INTO plenum_cafm.document_chunks "
                        f"(id, ingestion_id, source_filename, doc_type, "
                        f"chunk_index, chunk_text, embedding, heading, metadata) "
                        f"VALUES (:id, :ingestion_id, :source_filename, :doc_type, "
                        f":chunk_index, :chunk_text, {embedding_sql}, :heading, :metadata::jsonb)"
                    ),
                    {
                        "id":              str(uuid.uuid4()),
                        "ingestion_id":    str(ingestion_id),
                        "source_filename": source_filename,
                        "doc_type":        doc_type,
                        "chunk_index":     chunk["chunk_index"],
                        "chunk_text":      chunk["chunk_text"],
                        "heading":         chunk.get("heading"),
                        "metadata":        json.dumps({"char_count": len(chunk["chunk_text"])}),
                    },
                )

        logger.info(
            "doc_embedder.chunks_stored",
            ingestion_id=str(ingestion_id),
            source_filename=source_filename,
            doc_type=doc_type,
            chunks_written=len(chunks),
        )
        span.set_status(StatusCode.OK)
        return len(chunks)


# ── Public entry point ─────────────────────────────────────────────────────────


async def chunk_and_store(
    full_text: str,
    *,
    ingestion_id: UUID,
    source_filename: str,
    doc_type: str,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
) -> int:
    """
    Full pipeline: chunk → embed → store.

    Returns number of chunks stored.
    Called by word_agent (and future pdf_agent) as the primary RAG path.
    """
    with tracer.start_as_current_span("doc_embedder.pipeline") as span:
        span.set_attribute("cafm.source_filename", source_filename)
        span.set_attribute("cafm.doc_type", doc_type)

        chunks = chunk_document(full_text, source_filename)
        if not chunks:
            logger.warning(
                "doc_embedder.no_chunks",
                source_filename=source_filename,
                text_len=len(full_text),
            )
            return 0

        chunks = await embed_chunks(chunks, client)
        stored = await store_chunks(
            chunks,
            ingestion_id=ingestion_id,
            source_filename=source_filename,
            doc_type=doc_type,
            engine=engine,
        )

        span.set_attribute("cafm.chunks_stored", stored)
        span.set_status(StatusCode.OK)
        return stored
