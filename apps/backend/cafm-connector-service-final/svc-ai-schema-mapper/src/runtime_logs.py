"""In-process runtime log capture for frontend polling.

Captures Python logging records and stores short rolling buffers globally and
per workflow id (migration_id / schema_mapping_id) so UI clients can fetch
near-live backend logs via API endpoints.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Deque, Dict, Any

_migration_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "runtime_log_migration_id", default=None
)
_schema_mapping_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "runtime_log_schema_mapping_id", default=None
)

_lock = threading.Lock()
_seq = 0
_global_logs: Deque[dict[str, Any]] = deque(maxlen=5000)
_migration_logs: Dict[str, Deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=2000))
_schema_logs: Dict[str, Deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=2000))
_installed = False


@contextmanager
def bind_runtime_log_context(
    *,
    migration_id: str | None = None,
    schema_mapping_id: str | None = None,
):
    """Bind workflow IDs to log records emitted in this context."""
    token_m = _migration_id_var.set(migration_id)
    token_s = _schema_mapping_id_var.set(schema_mapping_id)
    try:
        yield
    finally:
        _migration_id_var.reset(token_m)
        _schema_mapping_id_var.reset(token_s)


class _RuntimeLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _seq
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)

        migration_id = _migration_id_var.get()
        schema_mapping_id = _schema_mapping_id_var.get()

        with _lock:
            _seq += 1
            item = {
                "seq": _seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
                "migration_id": migration_id,
                "schema_mapping_id": schema_mapping_id,
            }
            _global_logs.append(item)
            if migration_id:
                _migration_logs[migration_id].append(item)
            if schema_mapping_id:
                _schema_logs[schema_mapping_id].append(item)


def install_runtime_log_capture() -> None:
    """Attach runtime log handler once to root logger."""
    global _installed
    if _installed:
        return
    root = logging.getLogger()
    handler = _RuntimeLogHandler()
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    _installed = True


def get_runtime_logs(
    *,
    migration_id: str | None = None,
    schema_mapping_id: str | None = None,
    since: int = 0,
    limit: int = 200,
) -> dict[str, Any]:
    """Fetch logs for requested scope.

    Returns:
      {"logs": [...], "next_since": <last_seq>}
    """
    if limit <= 0:
        limit = 1
    if limit > 1000:
        limit = 1000

    with _lock:
        if migration_id:
            pool = list(_migration_logs.get(migration_id, []))
        elif schema_mapping_id:
            pool = list(_schema_logs.get(schema_mapping_id, []))
        else:
            pool = list(_global_logs)

    filtered = [x for x in pool if int(x.get("seq", 0)) > since]
    logs = filtered[:limit]
    next_since = logs[-1]["seq"] if logs else since
    return {"logs": logs, "next_since": next_since}

