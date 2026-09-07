"""
svc-ingestion/src/batch/batch_processor.py

Task 2.8 — Batch Processor.

Submits up to 10,000 PDF extraction requests to the Anthropic Batch API
(50% cost reduction vs real-time), polls for completion, then processes
each result through the full EL-2.1 → EL-2.2 → EL-2.3 eval chain before
writing accepted results to the unified store via Stage 4 (unifier).

Batch safety gate (CLAUDE.md §21 Task 2.8):
  If > 20% of batch results fail any EL-2.x check → entire batch is flagged,
  processing halts, and ops are notified via structured log + audit event.

Flow:
  1. submit_batch()   — build JSONL requests → Anthropic Batch API → batch_id
  2. poll_batch()     — poll every 30s (exp backoff to 5 min) until "ended"
  3. process_results()— per result: EL-2.1 → EL-2.2 → EL-2.3 → route
  4. run_batch()      — convenience wrapper: submit → poll → process

  Per result routing:
    eval_score ≥ 0.85 → unify (Stage 4 write)
    eval_score 0.60–0.84 → review_queue
    eval_score < 0.60 → manual_only status on ingestion_documents

Progress updates are delivered via an optional async `on_progress` callback,
which the WebSocket handler (review_queue/websocket.py) wires up.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine
from uuid import UUID

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from cafm_shared.logging import get_logger
from shared.audit import EVENT_STAGE2_EXTRACT, EVENT_STAGE3_EVAL, write_audit_event
from shared.intermediate_schema import (
    AgentId,
    AuditInfo,
    ConfidenceLevel,
    ConfidenceResult,
    EntitiesBlock,
    ExtractionMethod,
    IntermediateSchema,
    ModelUsed,
    SourceType,
)
from shared.unifier import unify

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_MAX_BATCH_SIZE = 10_000
_POLL_INITIAL_SECONDS = 30
_POLL_MAX_SECONDS = 300       # 5 minutes
_POLL_BACKOFF_FACTOR = 1.5

_BATCH_FAILURE_THRESHOLD = 0.20   # 20% failure → flag entire batch

_EVAL_SCORE_ACCEPT = 0.85
_EVAL_SCORE_REVIEW = 0.60

_DEFAULT_MODEL = "claude-sonnet-4-6"
_JUDGE_MODEL = "claude-haiku-4-5-20251001"

_EXTRACTION_SYSTEM = (
    "You are a CAFM data extraction specialist. "
    "Extract structured data from the provided document. "
    "Return ONLY valid JSON with keys: entities, confidence, audit. "
    "Never invent data — every value must come from the document."
)

_EXTRACTION_USER_TMPL = (
    "Extract all CAFM entities (assets, work orders, parts, inspections, technicians) "
    "from this document: {source_filename}. "
    "Return JSON matching the intermediate schema with entities, confidence, and audit blocks."
)

_JUDGE_PROMPT_TMPL = """\
Rate the quality of this CAFM data extraction (0.0-1.0).

SOURCE EXCERPT (first 2000 chars):
{source_excerpt}

EXTRACTED JSON:
{extracted_json}

Return JSON: {{"eval_score": 0.0, "contradictions": [], "verdict": "accept|review|reject"}}
Return ONLY valid JSON."""


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class BatchItem:
    """One document to be processed in the batch."""
    ingestion_id: UUID
    source_filename: str
    blob_url: str
    source_type: SourceType = SourceType.PDF
    pdf_bytes: bytes | None = None      # supply bytes OR let processor fetch from blob
    file_id: str | None = None          # Anthropic Files API file_id (if pre-uploaded)
    organization_id: UUID = field(default_factory=uuid.uuid4)


@dataclass
class BatchProgress:
    """Snapshot of in-progress batch processing — sent via WebSocket."""
    batch_id: str
    total: int
    completed: int
    succeeded_el: int       # passed all EL-2.x checks
    failed_el: int          # failed at least one EL-2.x check
    written: int            # Stage 4 writes completed
    queued_for_review: int
    flagged: bool           # True if > 20% failed EL-2.x
    status: str             # polling | processing | complete | flagged | error


@dataclass
class BatchProcessResult:
    """Final result of a completed batch run."""
    batch_id: str
    total: int
    succeeded_el: int
    failed_el: int
    written: int
    queued_for_review: int
    manual_only: int
    flagged: bool
    flag_reason: str | None = None
    errors: list[str] = field(default_factory=list)
    processing_ms: int = 0


# ── Internal helpers ───────────────────────────────────────────────────────────


def _build_document_block(item: BatchItem) -> dict[str, Any]:
    """Build the Anthropic document content block for one BatchItem."""
    if item.file_id:
        return {
            "type": "document",
            "source": {"type": "file", "file_id": item.file_id},
            "cache_control": {"type": "ephemeral"},
        }
    if item.pdf_bytes:
        b64 = base64.standard_b64encode(item.pdf_bytes).decode()
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": b64,
            },
            "cache_control": {"type": "ephemeral"},
        }
    raise ValueError(
        f"BatchItem {item.ingestion_id}: must supply pdf_bytes or file_id"
    )


def _build_batch_request(item: BatchItem) -> dict[str, Any]:
    """Build one Anthropic Batch API request dict for a BatchItem."""
    doc_block = _build_document_block(item)
    user_text = _EXTRACTION_USER_TMPL.format(source_filename=item.source_filename)

    return {
        "custom_id": str(item.ingestion_id),
        "params": {
            "model": _DEFAULT_MODEL,
            "max_tokens": 8192,
            "system": _EXTRACTION_SYSTEM,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        doc_block,
                        {"type": "text", "text": user_text},
                    ],
                }
            ],
        },
    }


# ── EL-2.x inline implementations ─────────────────────────────────────────────


def _el_2_1_parse(raw_text: str) -> dict[str, Any] | None:
    """
    EL-2.1 — Raw extraction output eval.
    Returns parsed dict if JSON is valid and contains 'entities' key, else None.
    """
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        if "entities" not in parsed:
            return None
        return parsed
    except json.JSONDecodeError:
        return None


def _el_2_2_build_schema(
    parsed: dict[str, Any],
    item: BatchItem,
) -> IntermediateSchema | None:
    """
    EL-2.2 — Intermediate JSON schema conformance.
    Builds an IntermediateSchema from the parsed dict; returns None on Pydantic failure.
    """
    try:
        entities_raw = parsed.get("entities", {})
        entities = EntitiesBlock(
            assets=entities_raw.get("assets", []),
            work_orders=entities_raw.get("work_orders", []),
            findings=entities_raw.get("findings", []),
            readings=entities_raw.get("readings", []),
            technicians=entities_raw.get("technicians", []),
            vendors=entities_raw.get("vendors", []),
            certificates=entities_raw.get("certificates", []),
            spare_parts=entities_raw.get("spare_parts", []),
        )

        conf_raw = parsed.get("confidence", {})
        confidence = ConfidenceResult(
            overall=ConfidenceLevel(conf_raw.get("overall", "low")),
            eval_score=float(conf_raw.get("eval_score", 0.0)),
            rules_passed=bool(conf_raw.get("rules_passed", False)),
            rules_violations=conf_raw.get("rules_violations", []),
        )

        audit_raw = parsed.get("audit", {})
        audit = AuditInfo(
            tokens_in=int(audit_raw.get("tokens_in", 0)),
            tokens_out=int(audit_raw.get("tokens_out", 0)),
            processing_ms=int(audit_raw.get("processing_ms", 0)),
        )

        return IntermediateSchema(
            ingestion_id=item.ingestion_id,
            source_type=item.source_type,
            agent_id=AgentId.PDF,
            source_filename=item.source_filename,
            source_blob_url=item.blob_url,
            extraction_method=ExtractionMethod.CLAUDE_VISION,
            model_used=ModelUsed.SONNET,
            entities=entities,
            confidence=confidence,
            audit=audit,
        )
    except Exception:
        return None


async def _el_2_3_judge(
    client: anthropic.AsyncAnthropic,
    source_filename: str,
    raw_text: str,
    extracted_json: str,
) -> tuple[float, list[str]]:
    """
    EL-2.3 — LLM-as-judge (Haiku).
    Returns (eval_score, contradictions).
    Falls back to 0.5 on all errors.
    """
    source_excerpt = f"[PDF document: {source_filename}]\n\n" + raw_text[:1800]
    prompt = _JUDGE_PROMPT_TMPL.format(
        source_excerpt=source_excerpt,
        extracted_json=extracted_json[:2000],
    )

    for attempt in range(3):
        try:
            resp = await client.messages.create(
                model=_JUDGE_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text if resp.content else ""
            parsed = json.loads(raw.strip())
            score = max(0.0, min(1.0, float(parsed.get("eval_score", 0.5))))
            contradictions: list[str] = parsed.get("contradictions", [])
            return score, contradictions
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                logger.warning("batch.el_2_3_failed", error=str(exc))
                return 0.5, []
            await asyncio.sleep(2 ** attempt)

    return 0.5, []


# ── Review queue writer ────────────────────────────────────────────────────────


async def _route_to_review_queue(
    session: AsyncSession,
    item: BatchItem,
    schema: IntermediateSchema,
    eval_score: float,
    contradictions: list[str],
    flag: str,
) -> None:
    """Insert a review_queue row for a medium-confidence batch result."""
    from models.ingestion import ReviewQueueItem  # noqa: PLC0415

    rq = ReviewQueueItem(
        id=uuid.uuid4(),
        ingestion_id=item.ingestion_id,
        flag=flag,
        extracted_json=schema.model_dump(),
        eval_score=round(eval_score, 3),
        rules_violations={"contradictions": contradictions},
        status="pending",
    )
    session.add(rq)
    await session.flush()


async def _mark_manual_only(session: AsyncSession, ingestion_id: UUID) -> None:
    """Set ingestion_documents.status = 'manual_only' for a low-confidence result."""
    from sqlalchemy import update  # noqa: PLC0415

    from models.ingestion import IngestionDocument  # noqa: PLC0415

    await session.execute(
        update(IngestionDocument)
        .where(IngestionDocument.id == ingestion_id)
        .values(status="manual_only")
    )
    await session.flush()


# ── Public API ─────────────────────────────────────────────────────────────────


async def submit_batch(
    items: list[BatchItem],
    *,
    client: anthropic.AsyncAnthropic,
) -> str:
    """
    Submit a list of BatchItems to the Anthropic Batch API.

    Returns the batch_id string.
    Raises ValueError if items list exceeds _MAX_BATCH_SIZE.
    """
    if not items:
        raise ValueError("Cannot submit an empty batch")
    if len(items) > _MAX_BATCH_SIZE:
        raise ValueError(
            f"Batch size {len(items)} exceeds maximum {_MAX_BATCH_SIZE}"
        )

    with tracer.start_as_current_span("batch.submit") as span:
        span.set_attribute("cafm.batch_item_count", len(items))

        requests = [_build_batch_request(item) for item in items]

        batch = await client.messages.batches.create(requests=requests)
        batch_id = batch.id

        span.set_attribute("cafm.batch_id", batch_id)
        span.set_status(StatusCode.OK)

        logger.info(
            "batch.submitted",
            batch_id=batch_id,
            item_count=len(items),
        )

    return batch_id


async def poll_batch(
    batch_id: str,
    *,
    client: anthropic.AsyncAnthropic,
    on_progress: Callable[[BatchProgress], Coroutine[Any, Any, None]] | None = None,
) -> None:
    """
    Poll the Anthropic Batch API until the batch processing_status == "ended".

    Uses exponential backoff: starts at _POLL_INITIAL_SECONDS, doubles up to
    _POLL_MAX_SECONDS.

    Calls on_progress after each poll with a BatchProgress snapshot.
    """
    interval = _POLL_INITIAL_SECONDS

    with tracer.start_as_current_span("batch.poll") as span:
        span.set_attribute("cafm.batch_id", batch_id)

        while True:
            batch = await client.messages.batches.retrieve(batch_id)
            status = batch.processing_status

            logger.info(
                "batch.poll",
                batch_id=batch_id,
                status=status,
                request_counts=batch.request_counts.model_dump()
                if hasattr(batch.request_counts, "model_dump")
                else str(batch.request_counts),
            )

            if on_progress:
                counts = batch.request_counts
                progress = BatchProgress(
                    batch_id=batch_id,
                    total=getattr(counts, "processing", 0)
                    + getattr(counts, "succeeded", 0)
                    + getattr(counts, "errored", 0),
                    completed=getattr(counts, "succeeded", 0)
                    + getattr(counts, "errored", 0),
                    succeeded_el=0,
                    failed_el=0,
                    written=0,
                    queued_for_review=0,
                    flagged=False,
                    status="polling",
                )
                await on_progress(progress)

            if status == "ended":
                span.set_status(StatusCode.OK)
                return

            if status in ("canceling", "canceled", "expired"):
                span.set_status(StatusCode.ERROR, f"Batch ended with status: {status}")
                raise RuntimeError(f"Batch {batch_id} ended with status: {status}")

            await asyncio.sleep(interval)
            interval = min(interval * _POLL_BACKOFF_FACTOR, _POLL_MAX_SECONDS)


async def process_batch_results(
    batch_id: str,
    items_by_id: dict[str, BatchItem],
    *,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    on_progress: Callable[[BatchProgress], Coroutine[Any, Any, None]] | None = None,
) -> BatchProcessResult:
    """
    Stream batch results from Anthropic, run EL-2.1/2.2/2.3 on each,
    then write accepted results via Stage 4 (unifier).

    Safety gate: if > 20% of results fail EL-2.x, flagged=True is set,
    processing halts, and the result is returned immediately.
    """
    t0 = time.monotonic()

    total = len(items_by_id)
    succeeded_el = 0
    failed_el = 0
    written = 0
    queued_for_review = 0
    manual_only_count = 0
    errors: list[str] = []
    flagged = False
    flag_reason: str | None = None

    with tracer.start_as_current_span("batch.process_results") as span:
        span.set_attribute("cafm.batch_id", batch_id)
        span.set_attribute("cafm.total_items", total)

        # Stream results from Anthropic — one at a time (memory efficient)
        async for result in await client.messages.batches.results(batch_id):
            custom_id = result.custom_id
            item = items_by_id.get(custom_id)

            if item is None:
                logger.warning("batch.unknown_custom_id", custom_id=custom_id)
                errors.append(f"Unknown custom_id: {custom_id}")
                continue

            # ── API-level failure (not an EL-2.x failure) ─────────────────
            if result.result.type != "succeeded":
                failed_el += 1
                errors.append(
                    f"{item.source_filename}: API result type={result.result.type}"
                )
                logger.warning(
                    "batch.result_not_succeeded",
                    ingestion_id=str(item.ingestion_id),
                    result_type=result.result.type,
                )
                # Safety gate check after each failure
                if (failed_el / total) > _BATCH_FAILURE_THRESHOLD:
                    flagged = True
                    flag_reason = (
                        f"{failed_el}/{total} results failed "
                        f"(>{_BATCH_FAILURE_THRESHOLD:.0%} threshold)"
                    )
                    break
                continue

            # ── Extract raw text from the successful result ────────────────
            message = result.result.message
            raw_text = ""
            if message.content:
                raw_text = "".join(
                    block.text
                    for block in message.content
                    if hasattr(block, "text")
                )

            tokens_in = message.usage.input_tokens if message.usage else 0
            tokens_out = message.usage.output_tokens if message.usage else 0

            # ── EL-2.1 — Raw extraction output eval ───────────────────────
            with tracer.start_as_current_span(
                "ingestion.eval.extraction_output"
            ) as el21:
                el21.set_attribute("cafm.ingestion_id", str(item.ingestion_id))
                parsed = _el_2_1_parse(raw_text)
                el21_passed = parsed is not None
                el21.set_attribute("cafm.json_valid", el21_passed)
                el21.set_attribute("cafm.retry_count", 0)  # batch: no per-item retry

            if not el21_passed:
                failed_el += 1
                logger.warning(
                    "batch.el_2_1_failed",
                    ingestion_id=str(item.ingestion_id),
                    source_filename=item.source_filename,
                )
                if (failed_el / total) > _BATCH_FAILURE_THRESHOLD:
                    flagged = True
                    flag_reason = (
                        f"{failed_el}/{total} results failed EL-2.1 "
                        f"(>{_BATCH_FAILURE_THRESHOLD:.0%} threshold)"
                    )
                    break
                continue

            # ── EL-2.2 — Intermediate JSON schema conformance ─────────────
            with tracer.start_as_current_span(
                "ingestion.eval.schema_conformance"
            ) as el22:
                el22.set_attribute("cafm.ingestion_id", str(item.ingestion_id))
                schema = _el_2_2_build_schema(parsed, item)  # type: ignore[arg-type]
                el22_passed = schema is not None
                el22.set_attribute("cafm.schema_valid", el22_passed)

            if not el22_passed:
                failed_el += 1
                logger.warning(
                    "batch.el_2_2_failed",
                    ingestion_id=str(item.ingestion_id),
                    source_filename=item.source_filename,
                )
                if (failed_el / total) > _BATCH_FAILURE_THRESHOLD:
                    flagged = True
                    flag_reason = (
                        f"{failed_el}/{total} results failed EL-2.2 "
                        f"(>{_BATCH_FAILURE_THRESHOLD:.0%} threshold)"
                    )
                    break
                continue

            # ── EL-2.3 — LLM-as-judge ─────────────────────────────────────
            with tracer.start_as_current_span("ingestion.eval.llm_judge") as el23:
                el23.set_attribute("cafm.ingestion_id", str(item.ingestion_id))
                eval_score, contradictions = await _el_2_3_judge(
                    client,
                    item.source_filename,
                    raw_text,
                    json.dumps(parsed, default=str)[:3000],
                )
                el23.set_attribute("cafm.eval_score", eval_score)
                el23.set_attribute(
                    "cafm.rules_violations_count", len(contradictions)
                )

                if eval_score >= _EVAL_SCORE_ACCEPT:
                    route = "accept"
                elif eval_score >= _EVAL_SCORE_REVIEW:
                    route = "review"
                else:
                    route = "re_extract"

                el23.set_attribute("cafm.route", route)

            # Stamp eval_score onto the schema before routing
            assert schema is not None
            schema.confidence.eval_score = round(eval_score, 3)
            schema.confidence.rules_violations = contradictions

            # ── Route result ──────────────────────────────────────────────
            async with session_factory() as db_session:
                try:
                    if route == "accept":
                        succeeded_el += 1
                        unify_result = await unify(
                            schema,
                            db=db_session,
                            organization_id=item.organization_id,
                        )
                        written += unify_result.entities_written
                        await write_audit_event(
                            ingestion_id=item.ingestion_id,
                            event_type=EVENT_STAGE3_EVAL,
                            db=db_session,
                            model_used=_JUDGE_MODEL,
                            eval_score=eval_score,
                            rules_violations=contradictions,
                        )
                        await db_session.commit()
                        logger.info(
                            "batch.result_accepted",
                            ingestion_id=str(item.ingestion_id),
                            eval_score=eval_score,
                            entities_written=unify_result.entities_written,
                        )

                    elif route == "review":
                        succeeded_el += 1  # passed EL-2.x, just needs review
                        await _route_to_review_queue(
                            db_session,
                            item,
                            schema,
                            eval_score,
                            contradictions,
                            flag="medium_confidence",
                        )
                        await write_audit_event(
                            ingestion_id=item.ingestion_id,
                            event_type=EVENT_STAGE3_EVAL,
                            db=db_session,
                            model_used=_JUDGE_MODEL,
                            eval_score=eval_score,
                            rules_violations=contradictions,
                        )
                        await db_session.commit()
                        queued_for_review += 1
                        logger.info(
                            "batch.result_queued_for_review",
                            ingestion_id=str(item.ingestion_id),
                            eval_score=eval_score,
                        )

                    else:  # re_extract → manual_only
                        failed_el += 1
                        await _mark_manual_only(db_session, item.ingestion_id)
                        await write_audit_event(
                            ingestion_id=item.ingestion_id,
                            event_type=EVENT_STAGE3_EVAL,
                            db=db_session,
                            model_used=_JUDGE_MODEL,
                            eval_score=eval_score,
                            rules_violations=contradictions
                            + ["batch_low_confidence_manual_only"],
                        )
                        await db_session.commit()
                        manual_only_count += 1
                        logger.warning(
                            "batch.result_manual_only",
                            ingestion_id=str(item.ingestion_id),
                            eval_score=eval_score,
                        )

                except Exception as write_exc:
                    await db_session.rollback()
                    failed_el += 1
                    errors.append(
                        f"{item.source_filename}: write error: {write_exc}"
                    )
                    logger.error(
                        "batch.write_error",
                        ingestion_id=str(item.ingestion_id),
                        error=str(write_exc),
                    )

            # ── Batch-level safety gate ───────────────────────────────────
            if not flagged and (failed_el / total) > _BATCH_FAILURE_THRESHOLD:
                flagged = True
                flag_reason = (
                    f"{failed_el}/{total} results failed EL-2.x checks "
                    f"(>{_BATCH_FAILURE_THRESHOLD:.0%} threshold)"
                )
                break

            # ── Progress update ───────────────────────────────────────────
            if on_progress:
                progress = BatchProgress(
                    batch_id=batch_id,
                    total=total,
                    completed=succeeded_el + failed_el + manual_only_count,
                    succeeded_el=succeeded_el,
                    failed_el=failed_el,
                    written=written,
                    queued_for_review=queued_for_review,
                    flagged=flagged,
                    status="flagged" if flagged else "processing",
                )
                await on_progress(progress)

        processing_ms = round((time.monotonic() - t0) * 1000)

        if flagged:
            span.set_status(StatusCode.ERROR, flag_reason or "batch flagged")
            logger.error(
                "batch.flagged",
                batch_id=batch_id,
                failed_el=failed_el,
                total=total,
                flag_reason=flag_reason,
            )
        else:
            span.set_status(StatusCode.OK)
            logger.info(
                "batch.complete",
                batch_id=batch_id,
                total=total,
                succeeded_el=succeeded_el,
                failed_el=failed_el,
                written=written,
                queued_for_review=queued_for_review,
                processing_ms=processing_ms,
            )

        span.set_attribute("cafm.batch_flagged", flagged)
        span.set_attribute("cafm.succeeded_el", succeeded_el)
        span.set_attribute("cafm.failed_el", failed_el)
        span.set_attribute("cafm.written", written)

        # Final progress push
        if on_progress:
            final_progress = BatchProgress(
                batch_id=batch_id,
                total=total,
                completed=succeeded_el + failed_el + manual_only_count,
                succeeded_el=succeeded_el,
                failed_el=failed_el,
                written=written,
                queued_for_review=queued_for_review,
                flagged=flagged,
                status="flagged" if flagged else "complete",
            )
            await on_progress(final_progress)

        return BatchProcessResult(
            batch_id=batch_id,
            total=total,
            succeeded_el=succeeded_el,
            failed_el=failed_el,
            written=written,
            queued_for_review=queued_for_review,
            manual_only=manual_only_count,
            flagged=flagged,
            flag_reason=flag_reason,
            errors=errors,
            processing_ms=processing_ms,
        )


async def run_batch(
    items: list[BatchItem],
    *,
    client: anthropic.AsyncAnthropic,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    on_progress: Callable[[BatchProgress], Coroutine[Any, Any, None]] | None = None,
) -> BatchProcessResult:
    """
    Convenience wrapper: submit → poll → process in one call.

    Suitable for ARQ worker jobs (fire-and-forget from the API endpoint;
    the worker handles the full lifecycle).
    """
    # Index items by ingestion_id string for fast lookup during result streaming
    items_by_id: dict[str, BatchItem] = {
        str(item.ingestion_id): item for item in items
    }

    batch_id = await submit_batch(items, client=client)

    await poll_batch(batch_id, client=client, on_progress=on_progress)

    return await process_batch_results(
        batch_id,
        items_by_id,
        client=client,
        engine=engine,
        session_factory=session_factory,
        on_progress=on_progress,
    )
