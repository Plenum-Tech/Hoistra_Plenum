"""
svc-ingestion/src/review_queue/websocket.py

Task 3.4 — WebSocket push notifications for the HITL review queue.

Events pushed to connected clients:
  - item_added     : new item enqueued (with item_id + review_type + eval_score)
  - item_decided   : reviewer submitted decision (item_id + decision)
  - queue_stats    : periodic stats push (every 30s) with pending/locked counts
  - batch_progress : batch processor progress (item_id + pct_complete)

WebSocket endpoint: ws://host:8001/ws/review

Clients identify themselves via query param: ?reviewer_id=<uuid>
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from opentelemetry import trace

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

ws_router = APIRouter(tags=["review-websocket"])

# ── Connection manager ─────────────────────────────────────────────────────────


class ReviewQueueConnectionManager:
    """
    Manages active WebSocket connections.
    Broadcasts review queue events to all connected reviewers.
    """

    def __init__(self) -> None:
        # reviewer_id → WebSocket
        self._connections: dict[str, WebSocket] = {}
        # All connections (reviewers + anonymous monitors)
        self._all_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket, reviewer_id: str | None = None) -> None:
        await websocket.accept()
        self._all_connections.append(websocket)
        if reviewer_id:
            self._connections[reviewer_id] = websocket
        logger.info(
            "ws_review_connected",
            reviewer_id=reviewer_id,
            total_connections=len(self._all_connections),
        )

    def disconnect(self, websocket: WebSocket, reviewer_id: str | None = None) -> None:
        if websocket in self._all_connections:
            self._all_connections.remove(websocket)
        if reviewer_id and reviewer_id in self._connections:
            del self._connections[reviewer_id]
        logger.info(
            "ws_review_disconnected",
            reviewer_id=reviewer_id,
            total_connections=len(self._all_connections),
        )

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Broadcast event to all connected clients."""
        message = json.dumps(event)
        disconnected: list[WebSocket] = []
        for ws in list(self._all_connections):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        # Clean up disconnected sockets
        for ws in disconnected:
            if ws in self._all_connections:
                self._all_connections.remove(ws)

    async def send_to_reviewer(
        self,
        reviewer_id: str,
        event: dict[str, Any],
    ) -> bool:
        """Send event to a specific reviewer. Returns False if not connected."""
        ws = self._connections.get(reviewer_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(event))
            return True
        except Exception:
            self._connections.pop(reviewer_id, None)
            if ws in self._all_connections:
                self._all_connections.remove(ws)
            return False

    @property
    def connection_count(self) -> int:
        return len(self._all_connections)


# Singleton manager — shared across all WebSocket connections
manager = ReviewQueueConnectionManager()


# ── Event helpers ──────────────────────────────────────────────────────────────


async def notify_item_added(
    item_id: str,
    review_type: str,
    ingestion_id: str,
    eval_score: float | None = None,
    agent_id: str = "",
) -> None:
    """Broadcast item_added event to all connected reviewers."""
    await manager.broadcast(
        {
            "event": "item_added",
            "item_id": item_id,
            "review_type": review_type,
            "ingestion_id": ingestion_id,
            "eval_score": eval_score,
            "agent_id": agent_id,
            "timestamp": time.time(),
        }
    )


async def notify_item_decided(
    item_id: str,
    decision: str,
    reviewer_id: str,
    ingestion_id: str,
) -> None:
    """Broadcast item_decided event to all connected clients."""
    await manager.broadcast(
        {
            "event": "item_decided",
            "item_id": item_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "ingestion_id": ingestion_id,
            "timestamp": time.time(),
        }
    )


async def notify_batch_progress(
    batch_id: str,
    total: int,
    completed: int,
    failed: int,
) -> None:
    """Broadcast batch processing progress update."""
    pct = round(completed / total * 100, 1) if total > 0 else 0.0
    await manager.broadcast(
        {
            "event": "batch_progress",
            "batch_id": batch_id,
            "total": total,
            "completed": completed,
            "failed": failed,
            "pct_complete": pct,
            "timestamp": time.time(),
        }
    )


# ── Periodic stats push ────────────────────────────────────────────────────────


_STATS_PUSH_INTERVAL: int = 30  # seconds


async def _periodic_stats_push(redis: Any) -> None:
    """Background task: push queue stats every 30 seconds."""
    from review_queue.queue import get_queue_stats

    while True:
        await asyncio.sleep(_STATS_PUSH_INTERVAL)
        if manager.connection_count == 0:
            continue
        try:
            stats = await get_queue_stats(redis)
            await manager.broadcast(
                {
                    "event": "queue_stats",
                    "pending": stats.pending,
                    "locked": stats.locked,
                    "decided_today": stats.decided_today,
                    "total_enqueued": stats.total_enqueued,
                    "timestamp": time.time(),
                }
            )
        except Exception as exc:
            logger.warning("ws_stats_push_error", error=str(exc))


# ── WebSocket endpoint ─────────────────────────────────────────────────────────


@ws_router.websocket("/ws/review")
async def review_websocket(
    websocket: WebSocket,
    reviewer_id: str | None = None,
) -> None:
    """
    WebSocket endpoint for live review queue notifications.

    Query params:
      reviewer_id (optional): identifies the reviewer for targeted messages

    Messages from client (JSON):
      {"action": "ping"}              → {"event": "pong"}
      {"action": "get_stats"}         → {"event": "queue_stats", ...}
      {"action": "ack", "item_id": …} → acknowledged

    Messages to client:
      item_added   — new item in queue
      item_decided — decision submitted
      queue_stats  — periodic stats (every 30s) or on demand
      batch_progress — batch processing updates
    """
    await manager.connect(websocket, reviewer_id)

    try:
        # Send welcome message with current connection count
        await websocket.send_text(
            json.dumps(
                {
                    "event": "connected",
                    "reviewer_id": reviewer_id,
                    "connections": manager.connection_count,
                    "timestamp": time.time(),
                }
            )
        )

        # Listen for client messages
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                msg = json.loads(raw)
                action = msg.get("action", "")

                if action == "ping":
                    await websocket.send_text(
                        json.dumps({"event": "pong", "timestamp": time.time()})
                    )

                elif action == "get_stats":
                    # Stats requested on demand — respond if Redis is available
                    # (Redis injected by caller — skip if not available)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "event": "queue_stats_ack",
                                "message": "stats available via GET /review/stats",
                                "timestamp": time.time(),
                            }
                        )
                    )

                elif action == "ack":
                    # Reviewer acknowledged receipt of an item
                    item_id = msg.get("item_id", "")
                    logger.debug("ws_ack_received", reviewer_id=reviewer_id, item_id=item_id)

            except asyncio.TimeoutError:
                # Send keepalive ping on timeout
                await websocket.send_text(
                    json.dumps({"event": "keepalive", "timestamp": time.time()})
                )
            except json.JSONDecodeError:
                logger.warning("ws_invalid_json", reviewer_id=reviewer_id)

    except WebSocketDisconnect:
        manager.disconnect(websocket, reviewer_id)
    except Exception as exc:
        logger.error("ws_review_error", reviewer_id=reviewer_id, error=str(exc))
        manager.disconnect(websocket, reviewer_id)
