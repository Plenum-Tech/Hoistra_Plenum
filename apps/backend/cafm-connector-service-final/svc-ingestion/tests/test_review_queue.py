"""
tests/test_review_queue.py

Unit tests for review_queue/queue.py and review_queue/websocket.py — Task 3.4.

Covers:
  queue.py:
    - ReviewItem: model defaults, id auto-generated, status default
    - DecisionRequest: field defaults
    - QueueStats: field types
    - enqueue(): stores item in Redis sorted set + hash
    - acquire_next(): pops oldest, sets lock, skips locked items
    - acquire_next(): returns None when queue empty
    - submit_decision(): validates lock, removes from queue, cleans up Redis
    - submit_decision(): raises 409 when lock expired
    - submit_decision(): raises 403 when wrong reviewer holds lock
    - get_queue_stats(): returns correct pending/locked counts
  websocket.py:
    - ReviewQueueConnectionManager: connect, disconnect, connection_count
    - broadcast(): sends to all connections, skips disconnected
    - send_to_reviewer(): targeted send, returns False if not connected
    - notify_item_added(): broadcasts correct event structure
    - notify_item_decided(): broadcasts correct event structure
    - notify_batch_progress(): correct pct_complete calculation
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from review_queue.queue import (
    DecisionRequest,
    QueueStats,
    ReviewItem,
    _ITEM_PREFIX,
    _LOCK_PREFIX,
    _QUEUE_KEY,
    acquire_next,
    enqueue,
    get_queue_stats,
    submit_decision,
)
from review_queue.websocket import (
    ReviewQueueConnectionManager,
    notify_batch_progress,
    notify_item_added,
    notify_item_decided,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_redis(
    queue_members: list[str] | None = None,
    item_data: dict[str, str] | None = None,
    lock_holders: dict[str, str] | None = None,
) -> Any:
    """
    Build a mock Redis client with configurable state.
    queue_members: list of item_id strings in the sorted set
    item_data: {item_id: json_string}
    lock_holders: {item_id: reviewer_id} — items currently locked
    """
    queue_members = queue_members if queue_members is not None else []
    item_data = item_data if item_data is not None else {}
    lock_holders = lock_holders if lock_holders is not None else {}

    redis = AsyncMock()

    # zadd — add to queue
    redis.zadd = AsyncMock(return_value=1)

    # zrange — return queue members as bytes
    redis.zrange = AsyncMock(
        return_value=[m.encode() for m in queue_members]
    )

    # zcard — queue length
    redis.zcard = AsyncMock(return_value=len(queue_members))

    # zrem — remove from queue
    redis.zrem = AsyncMock(return_value=1)

    # hset — store item data
    redis.hset = AsyncMock(return_value=1)

    # hget — retrieve item data
    def _hget(key: str, field: str) -> bytes | None:
        # key is like "review_queue:item:{id}"
        item_id = key.replace(_ITEM_PREFIX, "")
        data = item_data.get(item_id)
        return data.encode() if data else None

    redis.hget = AsyncMock(side_effect=_hget)

    # hgetall — for stats
    redis.hgetall = AsyncMock(return_value={
        b"total_enqueued": b"5",
        b"decided_today": b"2",
    })

    # hincrby — increment stats
    redis.hincrby = AsyncMock(return_value=1)

    # expire — set TTL
    redis.expire = AsyncMock(return_value=1)

    # delete — delete key
    redis.delete = AsyncMock(return_value=1)

    # exists — check if lock key exists
    def _exists(key: str) -> int:
        item_id = key.replace(_LOCK_PREFIX, "")
        return 1 if item_id in lock_holders else 0

    redis.exists = AsyncMock(side_effect=_exists)

    # set (NX lock) — succeed if not already locked
    def _set(key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        item_id = key.replace(_LOCK_PREFIX, "")
        if nx and item_id in lock_holders:
            return None  # Already locked
        lock_holders[item_id] = value
        return True

    redis.set = AsyncMock(side_effect=_set)

    # get — retrieve lock holder
    def _get(key: str) -> bytes | None:
        item_id = key.replace(_LOCK_PREFIX, "")
        holder = lock_holders.get(item_id)
        return holder.encode() if holder else None

    redis.get = AsyncMock(side_effect=_get)

    return redis


def _make_review_item(
    *,
    ingestion_id: str | None = None,
    review_type: str = "eval_score_review",
    eval_score: float = 0.72,
) -> ReviewItem:
    return ReviewItem(
        ingestion_id=ingestion_id or str(uuid4()),
        agent_id="pdf-agent",
        source_filename="test.pdf",
        review_type=review_type,
        eval_score=eval_score,
    )


# ===========================================================================
# ReviewItem model
# ===========================================================================


class TestReviewItemModel:
    def test_auto_generated_id(self):
        item = ReviewItem(
            ingestion_id=str(uuid4()),
            agent_id="pdf-agent",
            source_filename="x.pdf",
            review_type="eval_score_review",
        )
        assert item.id != ""
        assert len(item.id) == 36  # UUID format

    def test_two_items_have_different_ids(self):
        ingestion_id = str(uuid4())
        a = ReviewItem(ingestion_id=ingestion_id, agent_id="a", source_filename="a.pdf", review_type="t")
        b = ReviewItem(ingestion_id=ingestion_id, agent_id="a", source_filename="a.pdf", review_type="t")
        assert a.id != b.id

    def test_default_status_pending(self):
        item = _make_review_item()
        assert item.status == "pending"

    def test_created_at_present(self):
        item = _make_review_item()
        assert "T" in item.created_at  # ISO format

    def test_model_dump_json_roundtrip(self):
        item = _make_review_item(eval_score=0.75)
        json_str = item.model_dump_json()
        restored = ReviewItem.model_validate_json(json_str)
        assert restored.id == item.id
        assert restored.eval_score == pytest.approx(0.75)


class TestDecisionRequest:
    def test_defaults(self):
        req = DecisionRequest(decision="accept", reviewer_id=str(uuid4()))
        assert req.correction_type == "wrong_value"
        assert req.corrections == []
        assert req.corrected_value is None

    def test_with_corrections(self):
        req = DecisionRequest(
            decision="correct",
            reviewer_id=str(uuid4()),
            corrections=[{"field_path": "asset_code", "original_value": "X", "corrected_value": "MOB-AHU-001"}],
        )
        assert len(req.corrections) == 1


class TestQueueStats:
    def test_fields_present(self):
        stats = QueueStats(pending=3, locked=1, decided_today=5, total_enqueued=20)
        assert stats.pending == 3
        assert stats.locked == 1
        assert stats.decided_today == 5
        assert stats.total_enqueued == 20


# ===========================================================================
# enqueue()
# ===========================================================================


class TestEnqueue:
    @pytest.mark.asyncio
    async def test_returns_uuid_string(self):
        redis = _make_redis()
        item_id = await enqueue(
            {
                "ingestion_id": str(uuid4()),
                "agent_id": "pdf-agent",
                "source_filename": "test.pdf",
                "review_type": "eval_score_review",
                "eval_score": 0.72,
            },
            redis=redis,
        )
        assert isinstance(item_id, str)
        assert len(item_id) == 36

    @pytest.mark.asyncio
    async def test_zadd_called(self):
        redis = _make_redis()
        item_id = await enqueue(
            {
                "ingestion_id": str(uuid4()),
                "agent_id": "pdf-agent",
                "source_filename": "test.pdf",
                "review_type": "eval_score_review",
            },
            redis=redis,
        )
        redis.zadd.assert_called_once()
        args = redis.zadd.call_args
        assert _QUEUE_KEY in args[0] or _QUEUE_KEY in str(args)

    @pytest.mark.asyncio
    async def test_hset_called_for_item_data(self):
        redis = _make_redis()
        await enqueue(
            {"ingestion_id": str(uuid4()), "agent_id": "pdf-agent",
             "source_filename": "test.pdf", "review_type": "mapping_review"},
            redis=redis,
        )
        redis.hset.assert_called()

    @pytest.mark.asyncio
    async def test_stats_incremented(self):
        redis = _make_redis()
        await enqueue(
            {"ingestion_id": str(uuid4()), "agent_id": "pdf-agent",
             "source_filename": "test.pdf", "review_type": "eval_score_review"},
            redis=redis,
        )
        redis.hincrby.assert_called_with(_QUEUE_KEY.replace("pending", "stats").replace("pending", "stats") if False else "review_queue:stats", "total_enqueued", 1)

    @pytest.mark.asyncio
    async def test_expire_called_on_item(self):
        redis = _make_redis()
        await enqueue(
            {"ingestion_id": str(uuid4()), "agent_id": "pdf-agent",
             "source_filename": "test.pdf", "review_type": "eval_score_review"},
            redis=redis,
        )
        redis.expire.assert_called()


# ===========================================================================
# acquire_next()
# ===========================================================================


class TestAcquireNext:
    @pytest.mark.asyncio
    async def test_returns_none_on_empty_queue(self):
        redis = _make_redis(queue_members=[])
        result = await acquire_next(reviewer_id="rev-1", redis=redis)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_item_when_available(self):
        item = _make_review_item(review_type="eval_score_review", eval_score=0.72)
        item_json = item.model_dump_json()
        redis = _make_redis(
            queue_members=[item.id],
            item_data={item.id: item_json},
        )
        result = await acquire_next(reviewer_id="rev-1", redis=redis)
        assert result is not None
        assert result.id == item.id
        assert result.status == "locked"

    @pytest.mark.asyncio
    async def test_skips_already_locked_item(self):
        item1 = _make_review_item()
        item2 = _make_review_item()
        redis = _make_redis(
            queue_members=[item1.id, item2.id],
            item_data={
                item1.id: item1.model_dump_json(),
                item2.id: item2.model_dump_json(),
            },
            lock_holders={item1.id: "other-reviewer"},
        )
        result = await acquire_next(reviewer_id="rev-1", redis=redis)
        assert result is not None
        assert result.id == item2.id

    @pytest.mark.asyncio
    async def test_returns_none_when_all_locked(self):
        item = _make_review_item()
        redis = _make_redis(
            queue_members=[item.id],
            item_data={item.id: item.model_dump_json()},
            lock_holders={item.id: "another-reviewer"},
        )
        result = await acquire_next(reviewer_id="rev-1", redis=redis)
        assert result is None

    @pytest.mark.asyncio
    async def test_removes_stale_item_with_no_data(self):
        """Item in sorted set but no hash data → removed from queue."""
        item = _make_review_item()
        redis = _make_redis(
            queue_members=[item.id],
            item_data={},  # No data for this item
        )
        result = await acquire_next(reviewer_id="rev-1", redis=redis)
        assert result is None
        redis.zrem.assert_called()

    @pytest.mark.asyncio
    async def test_lock_set_with_reviewer_id(self):
        item = _make_review_item()
        lock_holders: dict[str, str] = {}
        redis = _make_redis(
            queue_members=[item.id],
            item_data={item.id: item.model_dump_json()},
            lock_holders=lock_holders,
        )
        await acquire_next(reviewer_id="rev-abc", redis=redis)
        assert lock_holders.get(item.id) == "rev-abc"


# ===========================================================================
# submit_decision()
# ===========================================================================


class TestSubmitDecision:
    @pytest.mark.asyncio
    async def test_raises_409_when_lock_expired(self):
        from fastapi import HTTPException
        item_id = str(uuid4())
        redis = _make_redis()
        redis.get = AsyncMock(return_value=None)  # Lock expired
        session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await submit_decision(
                item_id=item_id,
                decision_req=DecisionRequest(decision="accept", reviewer_id="rev-1"),
                redis=redis,
                session=session,
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_403_when_wrong_reviewer(self):
        from fastapi import HTTPException
        item_id = str(uuid4())
        redis = _make_redis(lock_holders={item_id: "other-reviewer"})
        session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await submit_decision(
                item_id=item_id,
                decision_req=DecisionRequest(decision="accept", reviewer_id="rev-1"),
                redis=redis,
                session=session,
            )
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_raises_404_when_item_data_missing(self):
        from fastapi import HTTPException
        item_id = str(uuid4())
        redis = _make_redis(lock_holders={item_id: "rev-1"})
        redis.hget = AsyncMock(return_value=None)
        session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await submit_decision(
                item_id=item_id,
                decision_req=DecisionRequest(decision="accept", reviewer_id="rev-1"),
                redis=redis,
                session=session,
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_successful_decision_removes_from_queue(self):
        item = _make_review_item()
        item_id = item.id
        ingestion_id = item.ingestion_id

        lock_holders = {item_id: "rev-1"}
        item_data = {item_id: item.model_dump_json()}
        redis = _make_redis(lock_holders=lock_holders, item_data=item_data)

        session = AsyncMock()
        # Mock the DB execute to return empty result (no existing row)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        # Patch the model imports inside submit_decision
        with patch("review_queue.queue._upsert_review_decision", AsyncMock()), \
             patch("review_queue.queue._write_corrections", AsyncMock()):
            result = await submit_decision(
                item_id=item_id,
                decision_req=DecisionRequest(decision="accept", reviewer_id="rev-1"),
                redis=redis,
                session=session,
            )

        assert result["decision"] == "accept"
        assert result["item_id"] == item_id
        redis.zrem.assert_called()
        redis.delete.assert_called()

    @pytest.mark.asyncio
    async def test_successful_decision_returns_correct_fields(self):
        item = _make_review_item()
        item_id = item.id

        lock_holders = {item_id: "rev-1"}
        item_data = {item_id: item.model_dump_json()}
        redis = _make_redis(lock_holders=lock_holders, item_data=item_data)
        session = AsyncMock()
        session.commit = AsyncMock()
        session.rollback = AsyncMock()

        with patch("review_queue.queue._upsert_review_decision", AsyncMock()), \
             patch("review_queue.queue._write_corrections", AsyncMock()):
            result = await submit_decision(
                item_id=item_id,
                decision_req=DecisionRequest(
                    decision="correct",
                    reviewer_id="rev-1",
                    corrections=[{"field_path": "asset_code", "original_value": "X", "corrected_value": "Y"}],
                ),
                redis=redis,
                session=session,
            )

        assert result["corrections_logged"] == 1
        assert result["ingestion_id"] == item.ingestion_id


# ===========================================================================
# get_queue_stats()
# ===========================================================================


class TestGetQueueStats:
    @pytest.mark.asyncio
    async def test_empty_queue(self):
        redis = _make_redis(queue_members=[])
        stats = await get_queue_stats(redis)
        assert stats.pending == 0
        assert stats.locked == 0

    @pytest.mark.asyncio
    async def test_counts_locked_vs_pending(self):
        item1 = str(uuid4())
        item2 = str(uuid4())
        item3 = str(uuid4())
        redis = _make_redis(
            queue_members=[item1, item2, item3],
            lock_holders={item1: "rev-1"},  # item1 locked
        )
        stats = await get_queue_stats(redis)
        assert stats.locked == 1
        assert stats.pending == 2

    @pytest.mark.asyncio
    async def test_reads_stats_from_hash(self):
        redis = _make_redis(queue_members=[])
        stats = await get_queue_stats(redis)
        assert stats.total_enqueued == 5
        assert stats.decided_today == 2


# ===========================================================================
# WebSocket connection manager
# ===========================================================================


class TestConnectionManager:
    @pytest.mark.asyncio
    async def test_connect_increments_count(self):
        mgr = ReviewQueueConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, reviewer_id="rev-1")
        assert mgr.connection_count == 1

    @pytest.mark.asyncio
    async def test_disconnect_decrements_count(self):
        mgr = ReviewQueueConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, reviewer_id="rev-1")
        mgr.disconnect(ws, reviewer_id="rev-1")
        assert mgr.connection_count == 0

    @pytest.mark.asyncio
    async def test_broadcast_sends_to_all(self):
        mgr = ReviewQueueConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, "rev-1")
        await mgr.connect(ws2, "rev-2")
        await mgr.broadcast({"event": "test"})
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_skips_disconnected(self):
        mgr = ReviewQueueConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws2.send_text = AsyncMock(side_effect=Exception("disconnected"))
        await mgr.connect(ws1, "rev-1")
        await mgr.connect(ws2, "rev-2")
        # Should not raise, and ws2 should be cleaned up
        await mgr.broadcast({"event": "test"})
        ws1.send_text.assert_called_once()
        assert mgr.connection_count == 1  # ws2 removed

    @pytest.mark.asyncio
    async def test_send_to_reviewer_returns_true_when_connected(self):
        mgr = ReviewQueueConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, reviewer_id="rev-1")
        result = await mgr.send_to_reviewer("rev-1", {"event": "test"})
        assert result is True
        ws.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_to_reviewer_returns_false_when_not_connected(self):
        mgr = ReviewQueueConnectionManager()
        result = await mgr.send_to_reviewer("unknown-reviewer", {"event": "test"})
        assert result is False

    @pytest.mark.asyncio
    async def test_anonymous_connection(self):
        """Connect without reviewer_id — anonymous monitor."""
        mgr = ReviewQueueConnectionManager()
        ws = AsyncMock()
        await mgr.connect(ws, reviewer_id=None)
        assert mgr.connection_count == 1

    @pytest.mark.asyncio
    async def test_multiple_reviewers_independent(self):
        mgr = ReviewQueueConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await mgr.connect(ws1, "rev-1")
        await mgr.connect(ws2, "rev-2")
        await mgr.send_to_reviewer("rev-1", {"event": "a"})
        await mgr.send_to_reviewer("rev-2", {"event": "b"})
        ws1.send_text.assert_called_once_with(json.dumps({"event": "a"}))
        ws2.send_text.assert_called_once_with(json.dumps({"event": "b"}))


# ===========================================================================
# Notify helpers
# ===========================================================================


class TestNotifyHelpers:
    @pytest.mark.asyncio
    async def test_notify_item_added_event_structure(self):
        """notify_item_added broadcasts correct event keys."""
        # Patch the global manager
        from review_queue import websocket as ws_module
        original_manager = ws_module.manager
        mock_manager = AsyncMock()
        ws_module.manager = mock_manager

        try:
            await notify_item_added(
                item_id="item-1",
                review_type="eval_score_review",
                ingestion_id="ing-1",
                eval_score=0.72,
                agent_id="pdf-agent",
            )
            mock_manager.broadcast.assert_called_once()
            event = mock_manager.broadcast.call_args[0][0]
            assert event["event"] == "item_added"
            assert event["item_id"] == "item-1"
            assert event["eval_score"] == pytest.approx(0.72)
        finally:
            ws_module.manager = original_manager

    @pytest.mark.asyncio
    async def test_notify_item_decided_event_structure(self):
        from review_queue import websocket as ws_module
        original_manager = ws_module.manager
        mock_manager = AsyncMock()
        ws_module.manager = mock_manager

        try:
            await notify_item_decided(
                item_id="item-1",
                decision="accept",
                reviewer_id="rev-1",
                ingestion_id="ing-1",
            )
            event = mock_manager.broadcast.call_args[0][0]
            assert event["event"] == "item_decided"
            assert event["decision"] == "accept"
            assert event["reviewer_id"] == "rev-1"
        finally:
            ws_module.manager = original_manager

    @pytest.mark.asyncio
    async def test_notify_batch_progress_pct(self):
        from review_queue import websocket as ws_module
        original_manager = ws_module.manager
        mock_manager = AsyncMock()
        ws_module.manager = mock_manager

        try:
            await notify_batch_progress(
                batch_id="batch-1", total=100, completed=25, failed=0
            )
            event = mock_manager.broadcast.call_args[0][0]
            assert event["event"] == "batch_progress"
            assert event["pct_complete"] == pytest.approx(25.0)
        finally:
            ws_module.manager = original_manager

    @pytest.mark.asyncio
    async def test_notify_batch_progress_zero_total(self):
        from review_queue import websocket as ws_module
        original_manager = ws_module.manager
        mock_manager = AsyncMock()
        ws_module.manager = mock_manager

        try:
            await notify_batch_progress(batch_id="b", total=0, completed=0, failed=0)
            event = mock_manager.broadcast.call_args[0][0]
            assert event["pct_complete"] == pytest.approx(0.0)
        finally:
            ws_module.manager = original_manager
