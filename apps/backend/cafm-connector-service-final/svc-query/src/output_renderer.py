"""
svc-query/src/output_renderer.py

Task 5.12 — Output Renderer.

Dispatches query answers and document generation results to the
correct output format: text / json / docx / xlsx / pdf.

Called at the end of every query path (Tier 1/2/3 + document_generate
+ template_fill) to produce the final response payload.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from opentelemetry import trace

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

OutputFormat = Literal["text", "json", "docx", "xlsx", "pdf"]


@dataclass
class RenderedOutput:
    """Final output ready for delivery to the client."""

    format: OutputFormat
    content_type: str
    content: bytes | str          # bytes for binary formats, str for text/json
    filename: str | None = None   # set for downloadable formats
    audit_id: str | None = None


def render_text_answer(
    answer: str,
    audit_id: str | None = None,
) -> RenderedOutput:
    """
    Render a plain-text answer (Tier 1/2/3 chat answers).
    EL-7.QUERY answers always go through this path.
    """
    with tracer.start_as_current_span("output.render.text"):
        return RenderedOutput(
            format="text",
            content_type="text/plain",
            content=answer,
            audit_id=audit_id,
        )


def render_json_answer(
    data: dict[str, Any] | list[Any],
    audit_id: str | None = None,
) -> RenderedOutput:
    """
    Render a structured JSON answer (API consumers, downstream systems).
    """
    with tracer.start_as_current_span("output.render.json"):
        payload = json.dumps(data, indent=2, default=str)
        return RenderedOutput(
            format="json",
            content_type="application/json",
            content=payload,
            audit_id=audit_id,
        )


def render_document_output(
    content: bytes,
    output_format: OutputFormat,
    document_type: str,
    audit_id: str | None = None,
) -> RenderedOutput:
    """
    Render a generated or filled document (docx / xlsx / pdf).
    Called after EL-7.DOC.EVAL passes (eval_score >= 0.85).
    """
    with tracer.start_as_current_span("output.render.document") as span:
        span.set_attribute("cafm.output_format", output_format)
        span.set_attribute("cafm.document_type", document_type)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_type = document_type.replace(" ", "_").lower()

        if output_format == "docx":
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            filename = f"{safe_type}_{ts}.docx"
        elif output_format == "xlsx":
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = f"{safe_type}_{ts}.xlsx"
        elif output_format == "pdf":
            content_type = "application/pdf"
            filename = f"{safe_type}_{ts}.pdf"
        else:
            content_type = "application/octet-stream"
            filename = f"{safe_type}_{ts}.bin"

        logger.info(
            "document_output_rendered",
            format=output_format,
            document_type=document_type,
            size_bytes=len(content),
        )

        return RenderedOutput(
            format=output_format,
            content_type=content_type,
            content=content,
            filename=filename,
            audit_id=audit_id,
        )


def render_held_for_review(
    document_type: str,
    eval_score: float,
    errors: list[str] | None = None,
) -> RenderedOutput:
    """
    Render a "held for review" response when EL-7.DOC.EVAL fails.
    Document is NOT delivered — human must review.
    """
    with tracer.start_as_current_span("output.render.held"):
        message = {
            "status": "held_for_review",
            "message": (
                f"The generated {document_type} document has been held for human review "
                f"(eval_score={eval_score:.3f} < 0.85). "
                "A reviewer will verify the document values before delivery."
            ),
            "eval_score": eval_score,
            "errors": errors or [],
        }
        return RenderedOutput(
            format="json",
            content_type="application/json",
            content=json.dumps(message, indent=2),
        )


def render_error(message: str, detail: str | None = None) -> RenderedOutput:
    """Render an error response."""
    payload = {"error": message}
    if detail:
        payload["detail"] = detail
    return RenderedOutput(
        format="json",
        content_type="application/json",
        content=json.dumps(payload, indent=2),
    )


def render_clarifying_question(question: str) -> RenderedOutput:
    """Render a clarifying question when intent classifier confidence < 0.80."""
    with tracer.start_as_current_span("output.render.clarification"):
        payload = {
            "status": "needs_clarification",
            "question": question,
        }
        return RenderedOutput(
            format="json",
            content_type="application/json",
            content=json.dumps(payload, indent=2),
        )


def get_content_type(output_format: str) -> str:
    """Return the MIME type for a given output format."""
    return {
        "text": "text/plain",
        "json": "application/json",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }.get(output_format, "application/octet-stream")
