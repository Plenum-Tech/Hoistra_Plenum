"""
svc-ingestion/src/review_queue/queue.py

Task 3.4 — HITL Review Queue.

Items routed here when:
  - EL-2.3 eval_score is 0.60–0.84 (medium confidence)
  - EL-3.0 mapping confidence < 0.80 (schema mapper flag)
  - EL-ER.T4 manual entity resolution required

Implementation:
  - Redis sorted set key: review_queue:pending  (score = created_at epoch)
  - Per-item data: Redis hash key: review_queue:item:{id}
  - Reviewer lock: Redis string key: review_queue:lock:{id}  (TTL = 600s)
  - Decisions written to PostgreSQL review_queue + corrections_log tables

FastAPI router: mounted at /review by app.py
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from shared.db import get_session

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

router = APIRouter(prefix="/review", tags=["review-queue"])

# ── Redis key constants ────────────────────────────────────────────────────────

_QUEUE_KEY: str = "review_queue:pending"           # sorted set (score = epoch)
_ITEM_PREFIX: str = "review_queue:item:"           # hash per item
_LOCK_PREFIX: str = "review_queue:lock:"           # string TTL lock
_LOCK_TTL_SECONDS: int = 600                        # 10-minute reviewer lock
_STATS_KEY: str = "review_queue:stats"             # hash: enqueued/decided totals


# ── Pydantic schemas ───────────────────────────────────────────────────────────


class ReviewItem(BaseModel):
    """A single item in the review queue."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ingestion_id: str
    agent_id: str
    source_filename: str
    review_type: str  # eval_score_review | mapping_review | entity_resolution
    eval_score: float | None = None
    contradictions: list[str] = Field(default_factory=list)
    rules_violations: list[str] = Field(default_factory=list)
    extracted_entities: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)  # full context
    flag: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: str = "pending"  # pending | locked | decided


class DecisionRequest(BaseModel):
    """Reviewer submits a decision for a review item."""

    decision: str  # accept | correct | reject
    corrected_value: str | None = None
    correction_type: str = "wrong_value"
    reviewer_id: str
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    # Each correction: {"field_path": str, "original_value": str, "corrected_value": str}


class QueueStats(BaseModel):
    """Queue statistics."""

    pending: int
    locked: int
    decided_today: int
    total_enqueued: int


# ── Core queue operations ──────────────────────────────────────────────────────


async def enqueue(
    item_data: dict[str, Any],
    redis: Any,
) -> str:
    """
    Add an item to the review queue.

    Args:
        item_data: dict with ingestion_id, agent_id, review_type, etc.
        redis: Redis client

    Returns:
        Item ID (UUID string)
    """
    with tracer.start_as_current_span("review_queue.enqueue") as span:
        item = ReviewItem(**item_data)
        item_id = item.id
        now_epoch = time.time()

        # Store full item data in Redis hash
        item_key = f"{_ITEM_PREFIX}{item_id}"
        await redis.hset(item_key, mapping={"data": item.model_dump_json()})
        # Set 7-day TTL on item data (cleanup old decided items)
        await redis.expire(item_key, 7 * 24 * 3600)

        # Add to sorted set with creation time as score (FIFO order)
        await redis.zadd(_QUEUE_KEY, {item_id: now_epoch})

        # Increment stats
        await redis.hincrby(_STATS_KEY, "total_enqueued", 1)

        span.set_attribute("cafm.review_item_id", item_id)
        span.set_attribute("cafm.review_type", item.review_type)
        span.set_attribute("cafm.ingestion_id", item.ingestion_id)

        logger.info(
            "review_queue_enqueued",
            item_id=item_id,
            ingestion_id=item.ingestion_id,
            review_type=item.review_type,
            eval_score=item.eval_score,
        )
        return item_id


async def acquire_next(
    reviewer_id: str,
    redis: Any,
) -> ReviewItem | None:
    """
    Acquire the next pending item (oldest first).
    Sets a 10-minute reviewer lock so two reviewers don't see the same item.

    Returns None if queue is empty or all items are locked.
    """
    with tracer.start_as_current_span("review_queue.acquire"):
        # Get the oldest pending item IDs (up to 20 to skip locked ones)
        candidates = await redis.zrange(_QUEUE_KEY, 0, 19)

        for raw_id in candidates:
            item_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            lock_key = f"{_LOCK_PREFIX}{item_id}"

            # Try to acquire lock (NX = only set if not exists)
            acquired = await redis.set(lock_key, reviewer_id, ex=_LOCK_TTL_SECONDS, nx=True)
            if not acquired:
                continue  # Already locked by another reviewer

            # Fetch item data
            item_key = f"{_ITEM_PREFIX}{item_id}"
            raw_data = await redis.hget(item_key, "data")
            if raw_data is None:
                # Item data expired — remove from queue
                await redis.zrem(_QUEUE_KEY, item_id)
                await redis.delete(lock_key)
                continue

            data_str = raw_data.decode() if isinstance(raw_data, bytes) else raw_data
            item = ReviewItem.model_validate_json(data_str)
            item.status = "locked"

            # Update item status in Redis
            await redis.hset(item_key, mapping={"data": item.model_dump_json()})

            logger.info(
                "review_queue_acquired",
                item_id=item_id,
                reviewer_id=reviewer_id,
                review_type=item.review_type,
            )
            return item

        return None


async def submit_decision(
    item_id: str,
    decision_req: DecisionRequest,
    redis: Any,
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Submit a reviewer decision for a queue item.

    - Validates reviewer holds the lock
    - Writes decision to PostgreSQL review_queue table
    - Writes each correction to corrections_log
    - Removes item from Redis queue
    - Returns outcome dict
    """
    with tracer.start_as_current_span("review_queue.decide") as span:
        lock_key = f"{_LOCK_PREFIX}{item_id}"
        item_key = f"{_ITEM_PREFIX}{item_id}"

        # Verify reviewer holds the lock
        lock_holder_raw = await redis.get(lock_key)
        if lock_holder_raw is None:
            raise HTTPException(
                status_code=409,
                detail=f"Lock expired for item {item_id} — another reviewer may have decided it",
            )
        lock_holder = (
            lock_holder_raw.decode() if isinstance(lock_holder_raw, bytes) else lock_holder_raw
        )
        if lock_holder != decision_req.reviewer_id:
            raise HTTPException(
                status_code=403,
                detail=f"You do not hold the lock for item {item_id}",
            )

        # Fetch item data
        raw_data = await redis.hget(item_key, "data")
        if raw_data is None:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        data_str = raw_data.decode() if isinstance(raw_data, bytes) else raw_data
        item = ReviewItem.model_validate_json(data_str)

        # Write decision to PostgreSQL review_queue table
        try:
            from models.ingestion import ReviewQueueItem, CorrectionsLog

            # Update existing review_queue row (if it exists) or create it
            await _upsert_review_decision(
                session=session,
                item=item,
                decision_req=decision_req,
            )

            # Write corrections to corrections_log
            if decision_req.corrections:
                await _write_corrections(
                    session=session,
                    item=item,
                    decision_req=decision_req,
                )

            await session.commit()

        except Exception as exc:
            await session.rollback()
            logger.error("review_queue_db_error", item_id=item_id, error=str(exc))
            raise HTTPException(status_code=500, detail=f"DB write failed: {exc}") from exc

        # Remove from Redis queue and clean up
        await redis.zrem(_QUEUE_KEY, item_id)
        await redis.delete(lock_key)
        await redis.delete(item_key)

        # Increment stats
        await redis.hincrby(_STATS_KEY, "decided_today", 1)

        span.set_attribute("cafm.review_item_id", item_id)
        span.set_attribute("cafm.decision", decision_req.decision)

        logger.info(
            "review_queue_decided",
            item_id=item_id,
            reviewer_id=decision_req.reviewer_id,
            decision=decision_req.decision,
            corrections_count=len(decision_req.corrections),
        )

        return {
            "item_id": item_id,
            "decision": decision_req.decision,
            "corrections_logged": len(decision_req.corrections),
            "ingestion_id": item.ingestion_id,
        }


async def get_queue_stats(redis: Any) -> QueueStats:
    """Return current queue statistics."""
    total_in_queue = await redis.zcard(_QUEUE_KEY)

    # Count locked items
    locked_count = 0
    if total_in_queue > 0:
        all_ids = await redis.zrange(_QUEUE_KEY, 0, -1)
        for raw_id in all_ids:
            item_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
            lock_exists = await redis.exists(f"{_LOCK_PREFIX}{item_id}")
            if lock_exists:
                locked_count += 1

    pending_count = total_in_queue - locked_count

    # Get stats from hash
    stats_raw = await redis.hgetall(_STATS_KEY)
    stats: dict[str, int] = {}
    for k, v in (stats_raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        val = v.decode() if isinstance(v, bytes) else v
        stats[key] = int(val)

    return QueueStats(
        pending=max(0, pending_count),
        locked=locked_count,
        decided_today=stats.get("decided_today", 0),
        total_enqueued=stats.get("total_enqueued", 0),
    )


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _upsert_review_decision(
    session: AsyncSession,
    item: ReviewItem,
    decision_req: DecisionRequest,
) -> None:
    """Write or update the review decision in PostgreSQL."""
    from models.ingestion import ReviewQueueItem

    # Look for existing row by ingestion_id
    existing = (
        await session.execute(
            select(ReviewQueueItem).where(
                ReviewQueueItem.ingestion_id == uuid.UUID(item.ingestion_id)
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    reviewer_uuid = uuid.UUID(decision_req.reviewer_id) if decision_req.reviewer_id else None

    if existing:
        await session.execute(
            update(ReviewQueueItem)
            .where(ReviewQueueItem.id == existing.id)
            .values(
                status="decided",
                decision=decision_req.decision,
                corrected_value=decision_req.corrected_value,
                reviewer_id=reviewer_uuid,
                decided_at=now,
            )
        )
    else:
        # Create new row (item was routed from Redis, not yet in DB)
        rq_item = ReviewQueueItem(
            id=uuid.UUID(item.id),
            ingestion_id=uuid.UUID(item.ingestion_id),
            routing_reason=item.review_type,
            status="decided",
            decision=decision_req.decision,
            corrected_value=decision_req.corrected_value,
            reviewer_id=reviewer_uuid,
            decided_at=now,
        )
        session.add(rq_item)


async def _write_corrections(
    session: AsyncSession,
    item: ReviewItem,
    decision_req: DecisionRequest,
) -> None:
    """Write correction records to corrections_log."""
    from models.ingestion import CorrectionsLog

    reviewer_uuid = uuid.UUID(decision_req.reviewer_id)
    ingestion_uuid = uuid.UUID(item.ingestion_id)

    for correction in decision_req.corrections:
        log_entry = CorrectionsLog(
            ingestion_id=ingestion_uuid,
            field_path=correction.get("field_path", ""),
            original_value=str(correction.get("original_value", "")),
            corrected_value=str(correction.get("corrected_value", "")),
            correction_type=correction.get("correction_type", decision_req.correction_type),
            reviewer_id=reviewer_uuid,
        )
        session.add(log_entry)


# ── FastAPI route handlers ─────────────────────────────────────────────────────


def _get_redis() -> Any:
    """Dependency placeholder — actual Redis injected by app.py lifespan."""
    from app import get_redis  # type: ignore[import]
    return get_redis()


@router.get("/stats", response_model=QueueStats)
async def get_stats(redis: Any = Depends(_get_redis)) -> QueueStats:
    """Get current queue statistics."""
    return await get_queue_stats(redis)


@router.get("/next")
async def get_next_item(
    reviewer_id: str,
    redis: Any = Depends(_get_redis),
) -> dict[str, Any]:
    """
    Acquire the next pending review item.
    Sets a 10-minute lock preventing other reviewers from seeing the same item.
    """
    item = await acquire_next(reviewer_id=reviewer_id, redis=redis)
    if item is None:
        return {"item": None, "message": "No pending items in queue"}
    return {"item": item.model_dump()}


@router.post("/{item_id}/decide")
async def decide_item(
    item_id: str,
    decision_req: DecisionRequest,
    redis: Any = Depends(_get_redis),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """
    Submit a decision for a review item.
    Reviewer must hold the lock (obtained via GET /review/next).
    """
    return await submit_decision(
        item_id=item_id,
        decision_req=decision_req,
        redis=redis,
        session=session,
    )


@router.get("/items")
async def list_pending_items(
    limit: int = 20,
    redis: Any = Depends(_get_redis),
) -> dict[str, Any]:
    """List pending review items (without acquiring locks)."""
    raw_ids = await redis.zrange(_QUEUE_KEY, 0, limit - 1)
    items = []

    for raw_id in raw_ids:
        item_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
        item_key = f"{_ITEM_PREFIX}{item_id}"
        raw_data = await redis.hget(item_key, "data")
        if raw_data:
            data_str = raw_data.decode() if isinstance(raw_data, bytes) else raw_data
            item_dict = json.loads(data_str)
            # Mask full entity data in list view
            item_dict.pop("extracted_entities", None)
            item_dict.pop("payload", None)
            items.append(item_dict)

    return {"items": items, "total": len(items)}
