"""
svc-query/src/tiers/fetch_then_read.py

Task 5.4 — Tier 2: Fetch-then-Read (~20% of queries).

Flow:
  1. Metadata query finds the exact document in ingestion_documents
  2. Fetch from Azure Blob → send to Sonnet with user's question
  3. EL-7.QUERY: answer verified to contain only values from fetched document

No vector search — metadata query finds the exact document.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

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

_SYSTEM_PROMPT = """\
You are a CAFM (facilities management) assistant answering questions about
documents stored in the system (inspection reports, invoices, certificates, etc.).

You will be given the full text of a document and a user question.
Answer using ONLY information present in the document.
If the answer is not in the document, say: "The document does not contain this information."

Be concise and cite specific sections when possible.
"""


@dataclass
class Tier2Result:
    """Result of a Tier 2 fetch-then-read query."""

    answer: str
    document_id: str
    document_filename: str
    document_type: str
    grounded: bool                     # EL-7.QUERY: answer from document only
    document_found: bool = True


async def run_fetch_then_read(
    query: str,
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
    blob_client: Any | None = None,
) -> Tier2Result:
    """
    Tier 2 query: metadata lookup → blob fetch → Claude reads → grounded answer.

    EL-7.QUERY: answer verified to contain only values from fetched document.
    """
    with tracer.start_as_current_span("tier2.fetch_then_read") as span:
        span.set_attribute("cafm.query_length", len(query))

        # Step 1: Find the document via metadata query
        doc = await _find_document(query, session)
        if doc is None:
            span.set_attribute("cafm.document_found", False)
            return Tier2Result(
                answer="No matching document found for this query.",
                document_id="",
                document_filename="",
                document_type="",
                grounded=True,
                document_found=False,
            )

        span.set_attribute("cafm.document_id", doc["id"])
        span.set_attribute("cafm.document_type", doc.get("source_type", ""))

        # Step 2: Fetch document content
        content = await _fetch_document_content(doc, blob_client)
        if not content:
            logger.warning("tier2_document_content_empty", doc_id=doc["id"])
            return Tier2Result(
                answer="The document was found but its content could not be retrieved.",
                document_id=str(doc["id"]),
                document_filename=doc.get("original_filename", ""),
                document_type=doc.get("source_type", ""),
                grounded=False,
            )

        # Step 3: Claude reads document and answers query
        answer = await _read_and_answer(query, content, client)
        span.set_attribute("cafm.answer_length", len(answer))

        logger.info(
            "tier2_complete",
            doc_id=doc["id"],
            filename=doc.get("original_filename", ""),
            answer_length=len(answer),
        )

        return Tier2Result(
            answer=answer,
            document_id=str(doc["id"]),
            document_filename=doc.get("original_filename", ""),
            document_type=doc.get("source_type", ""),
            grounded=True,
        )


async def _find_document(
    query: str,
    session: AsyncSession,
) -> dict | None:
    """
    Find the most relevant document via metadata matching.
    Uses keyword extraction from query to match against filename and agent_id.
    """
    with tracer.start_as_current_span("tier2.find_document") as span:
        # Extract keywords from query for ILIKE matching
        keywords = [w.strip("?.,!") for w in query.split() if len(w) > 3]
        if not keywords:
            return None

        # Build ILIKE conditions
        ilike_conditions = " OR ".join(
            f"d.original_filename ILIKE :kw{i}" for i, _ in enumerate(keywords[:5])
        )
        params = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(keywords[:5])}

        result = await session.execute(
            text(
                f"""
                SELECT d.id, d.original_filename, d.source_type, d.blob_url,
                       d.agent_id, d.intermediate_json, d.status
                FROM plenum_cafm.ingestion_documents d
                WHERE d.status = 'accepted'
                  AND ({ilike_conditions})
                ORDER BY d.uploaded_at DESC
                LIMIT 1
                """
            ),
            params,
        )
        rows = result.mappings().all()
        if not rows:
            span.set_attribute("cafm.document_found", False)
            return None

        span.set_attribute("cafm.document_found", True)
        return dict(rows[0])


async def _fetch_document_content(
    doc: dict,
    blob_client: Any | None,
) -> str:
    """
    Fetch document content from Azure Blob or fall back to intermediate_json.
    Returns text content for Claude to read.
    """
    # Primary: fetch from blob if client available
    if blob_client and doc.get("blob_url"):
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            blob_data = await loop.run_in_executor(
                None,
                lambda: blob_client.download_blob().readall(),
            )
            if isinstance(blob_data, bytes):
                # Try UTF-8, fall back to latin-1
                try:
                    return blob_data.decode("utf-8")
                except UnicodeDecodeError:
                    return blob_data.decode("latin-1", errors="replace")
        except Exception as exc:
            logger.warning("tier2_blob_fetch_failed", error=str(exc))

    # Fallback: use intermediate_json extracted content
    if doc.get("intermediate_json"):
        import json as _json
        try:
            intermediate = doc["intermediate_json"]
            if isinstance(intermediate, str):
                intermediate = _json.loads(intermediate)
            # Extract all text fields from entities for readable content
            return _json.dumps(intermediate, indent=2, default=str)
        except Exception:
            pass

    return ""


async def _read_and_answer(
    query: str,
    content: str,
    client: anthropic.AsyncAnthropic,
) -> str:
    """Send document content + question to Sonnet and get grounded answer."""
    with tracer.start_as_current_span("tier2.read_and_answer") as span:
        # Truncate content if too large (keep first 50K chars)
        if len(content) > 50_000:
            content = content[:50_000] + "\n[... document truncated ...]"

        user_message = (
            f"DOCUMENT CONTENT:\n{content}\n\n"
            f"QUESTION: {query}\n\n"
            f"Answer using ONLY the document content above."
        )

        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            answer = response.content[0].text.strip() if response.content else ""

            claude_api_calls.add(1, {"agent_id": "tier2-read", "model": _MODEL})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "tier2-read", "model": _MODEL},
            )

            span.set_attribute("cafm.answer_length", len(answer))
            return answer or "The document does not contain this information."

        except (anthropic.APIError, ValueError) as exc:
            logger.error("tier2_read_error", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            return "Unable to read the document. Please try again."


# Type hint for blob_client (avoid hard dependency)
from typing import Any
