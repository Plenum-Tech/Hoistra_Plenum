"""
WebSocket endpoint — relays import job progress to connected clients.

Flow:
  Worker → Redis pub/sub (PROGRESS_CHANNEL)
       → background listener → WebSocket broadcast

Clients connect to: ws://<host>/ws/imports/progress
They receive JSON messages:
  {"job_id": "...", "status": "running", "progress": 42.5, ...}
"""

from __future__ import annotations

import asyncio
import json

import redis.asyncio as aioredis
from fastapi import WebSocket, WebSocketDisconnect

from cafm_connector.core.config import get_settings
from cafm_connector.core.logging import get_logger
from cafm_connector.jobs.worker import PROGRESS_CHANNEL

logger = get_logger(__name__)

# Registry of active WebSocket connections keyed by job_id (or "*" for all)
_connections: dict[str, list[WebSocket]] = {}


async def websocket_import_progress(websocket: WebSocket, job_id: str = "*"):
    """
    WebSocket endpoint.
    Clients can subscribe to a specific job_id or "*" for all jobs.
    """
    await websocket.accept()
    _connections.setdefault(job_id, []).append(websocket)
    logger.info("ws_connected", job_id=job_id)

    try:
        while True:
            # Keep connection alive — client can send "ping"
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        _connections.get(job_id, []).remove(websocket)
        logger.info("ws_disconnected", job_id=job_id)


async def broadcast_progress(message: dict) -> None:
    """Send a progress message to all relevant WebSocket subscribers."""
    job_id = message.get("job_id", "*")
    text = json.dumps(message)

    # Send to subscribers of this specific job
    for ws in list(_connections.get(job_id, [])):
        try:
            await ws.send_text(text)
        except Exception:
            _connections.get(job_id, []).remove(ws)

    # Send to wildcard subscribers
    for ws in list(_connections.get("*", [])):
        try:
            await ws.send_text(text)
        except Exception:
            _connections.get("*", []).remove(ws)


async def start_redis_listener() -> None:
    """
    Background task — subscribes to Redis pub/sub and relays messages
    to WebSocket clients. Runs for the lifetime of the application.
    """
    settings = get_settings()
    redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(PROGRESS_CHANNEL)
    logger.info("redis_listener_started", channel=PROGRESS_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    await broadcast_progress(data)
                except Exception:
                    logger.exception("ws_relay_error")
    finally:
        await pubsub.unsubscribe(PROGRESS_CHANNEL)
        await redis.aclose()
        logger.info("redis_listener_stopped")
