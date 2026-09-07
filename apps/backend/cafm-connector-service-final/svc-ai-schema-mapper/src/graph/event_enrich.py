"""F4-2 — enrich the per-node audit trail with the Activity-Log dimensions.

Both the DB ``node_logs`` (one entry per completed node) and the in-state ``event_log`` get
the four dimensions the Activity Log reasons over: ``trigger`` (query / scheduled / threshold /
external_input), a one-line ``outcome``, ``status`` (completed / pending_human_input / failed /
escalated), and ``actor`` (system / human). These pure helpers make the shape testable;
``append_node_log`` / ``append_migration_node_log`` apply them centrally for every node, and
``append_event`` populates ``state['event_log']``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ACTOR_SYSTEM = "system"
ACTOR_HUMAN = "human"


def _int(output: dict, *keys):
    for k in keys:
        v = output.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return int(v)
    return None


def derive_node_outcome(node_name: str, output: dict | None) -> str:
    """A concise one-line outcome for a completed node, from its output counters."""
    o = output or {}
    name = (node_name or "").lower()

    if "ingest" in name:
        t, r = _int(o, "table_count", "tables"), _int(o, "row_count", "rows", "total_rows")
        bits = []
        if t is not None:
            bits.append(f"{t} table{'s' if t != 1 else ''}")
        if r is not None:
            bits.append(f"{r} rows")
        return "Ingested " + (", ".join(bits) if bits else "source data")
    if "determinist" in name:
        m, u = _int(o, "mapped", "tier1_count", "total_mapped"), _int(o, "unresolved", "total_unresolved")
        bits = []
        if m is not None:
            bits.append(f"{m} mapped")
        if u is not None:
            bits.append(f"{u} to semantic")
        return "Deterministic mapping" + (": " + ", ".join(bits) if bits else " complete")
    if "semantic" in name:
        a = _int(o, "auto", "tier2_auto", "auto_count")
        return "Semantic mapping" + (f": {a} auto-accepted" if a is not None else " complete")
    if "hierarchy" in name:
        f = _int(o, "fk_count", "relationships", "edges")
        return "Hierarchy detection" + (f": {f} relationships" if f is not None else " complete")
    if "write" in name:
        w = _int(o, "written", "rows_written", "row_count")
        return "Write complete" + (f" — {w} rows" if w is not None else "")
    if "output" in name:
        return "Output generated"
    if "review" in name or "gate" in name:
        return f"{node_name} — awaiting/processed review"
    return f"{node_name} complete"


def enrich_node_entry(
    entry: dict,
    *,
    node_name: str | None = None,
    trigger: str = "query",
    outcome: str | None = None,
    actor: str = ACTOR_SYSTEM,
) -> dict:
    """Add ``trigger`` / ``outcome`` / ``actor`` to an existing node-log entry (in place)."""
    entry.setdefault("trigger", trigger)
    entry.setdefault("actor", actor)
    if not entry.get("outcome"):
        entry["outcome"] = outcome or derive_node_outcome(
            node_name or entry.get("node_name", ""), entry.get("output")
        )
    return entry


def build_node_event(
    *,
    node_id,
    node_name: str,
    status: str = "completed",
    trigger: str = "query",
    outcome: str | None = None,
    actor: str = ACTOR_SYSTEM,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    output: dict | None = None,
    logs: list | None = None,
) -> dict:
    """A fully-formed enriched node-log entry (used when there is no existing entry)."""
    ca = completed_at or datetime.utcnow()
    sa = started_at or ca
    return {
        "node_id": node_id,
        "node_name": node_name,
        "status": status,
        "trigger": trigger,
        "outcome": outcome or derive_node_outcome(node_name, output),
        "actor": actor,
        "started_at": sa.isoformat(),
        "completed_at": ca.isoformat(),
        "duration_ms": int((ca - sa).total_seconds() * 1000),
        "output": output or {},
        "logs": logs or [],
    }


def append_event(
    state: dict,
    *,
    node_id=None,
    node_name: str = "",
    event: str = "node_complete",
    status: str = "completed",
    trigger: str = "query",
    outcome: str | None = None,
    actor: str = ACTOR_SYSTEM,
    detail: str | None = None,
    timestamp: Any = None,
) -> dict:
    """Append an enriched audit event to ``state['event_log']`` (initialising it if absent)."""
    if isinstance(timestamp, str):
        ts = timestamp
    else:
        ts = (timestamp or datetime.utcnow()).isoformat()
    item = {
        "timestamp": ts,
        "event": event,
        "node": node_id,
        "node_name": node_name,
        "trigger": trigger,
        "outcome": outcome or (derive_node_outcome(node_name, None) if node_name else None),
        "status": status,
        "actor": actor,
    }
    if detail:
        item["detail"] = detail
    log = state.get("event_log")
    if not isinstance(log, list):
        log = []
        state["event_log"] = log
    log.append(item)
    return item
