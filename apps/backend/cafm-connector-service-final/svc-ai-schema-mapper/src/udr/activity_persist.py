"""Feature 4 — Activity Log persistence (Section 1 store + notification feed).

Async writer/reader for ``ActivityLogEntry`` (the dedicated activity_log table).
The pure ``entry_to_columns`` maps a built activity entry (from :mod:`udr.activity`)
to row columns; the async functions handle insert / newest-first list / mark-read /
unread-summary. DB + model imports are lazy so the module stays import-clean for unit
tests (no DB config required to import). Mirrors the session pattern in
``services/job_progress.py`` (``get_async_session_factory``).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

# notification severity: red (failed/pending) > orange (escalated) > green (completed)
NOTIF_PRIORITY = {"red": 3, "orange": 2, "green": 1}


# ── pure: build row columns from a built activity entry ─────────────────────────
def entry_to_columns(
    activity_entry: dict,
    *,
    organization_id: Any = None,
    session_id: str | None = None,
) -> dict:
    """Map a built activity entry (see :func:`udr.activity.build_udr_activity_entry`)
    to ``ActivityLogEntry`` column values. Pure — no DB."""
    return {
        "organization_id": organization_id,
        "session_id": session_id,
        "trigger": activity_entry.get("trigger", "query"),
        "trigger_detail": activity_entry.get("trigger_detail"),
        "outcome": activity_entry.get("outcome", ""),
        "status": activity_entry.get("status", "completed"),
        "notif_color": activity_entry.get("notif_color", "green"),
        "refs": activity_entry.get("refs"),
        "processing_log": activity_entry.get("processing_log"),
    }


def _row_to_dict(r) -> dict:
    return {
        "id": str(r.id),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "trigger": r.trigger,
        "trigger_detail": r.trigger_detail,
        "outcome": r.outcome,
        "status": r.status,
        "notif_color": r.notif_color,
        "read": r.read,
        "refs": r.refs,
    }


def _row_to_detail(r) -> dict:
    """Summary fields + the full Processing Log (Section 2 detail view)."""
    return {**_row_to_dict(r), "processing_log": r.processing_log}


def _is_connection_error(exc: BaseException) -> bool:
    """True for a dropped/stale DB connection (vs a real query error). These are transient —
    a cloud DB idle-closing the socket, or a fresh SSL connect the server drops mid-handshake
    ('unexpected connection_lost() call') — so the caller retries once on a fresh session."""
    from sqlalchemy.exc import DBAPIError, DisconnectionError, InterfaceError, OperationalError

    if isinstance(exc, (ConnectionError, OSError, InterfaceError, OperationalError, DisconnectionError)):
        return True
    if isinstance(exc, DBAPIError):
        if getattr(exc, "connection_invalidated", False):
            return True
    msg = str(exc).lower()
    return (
        "connection_lost" in msg
        or "connection was closed" in msg
        or "connection is closed" in msg
        # Connection died MID-query (server closed an active socket): asyncpg/libpq surface this as
        # "consuming input failed: SSL error: unexpected eof while reading".
        or "unexpected eof" in msg
        or "consuming input failed" in msg
        or "server closed the connection" in msg
    )


async def _run_in_session(fn, session):
    """Use the passed session (caller commits) or open one and commit on success.

    On a transient CONNECTION error (not a query error), retry ONCE on a brand-new session so a
    dropped/stale pooled connection doesn't surface as a 500 on the 2s-polled activity reads.
    Callers of the passed-session path here are reads / idempotent upserts, so a single re-run
    is safe."""
    if session is not None:
        try:
            return await fn(session)
        except Exception as exc:  # noqa: BLE001 — re-raised below unless it's a connection blip
            if not _is_connection_error(exc):
                raise
        # Fresh session on a fresh (or pre-pinged) pooled connection.
        from ..db import get_async_session_factory  # lazy — avoids import-time DB config

        factory = get_async_session_factory()
        async with factory() as s:
            return await fn(s)

    from ..db import get_async_session_factory  # lazy — avoids import-time DB config

    factory = get_async_session_factory()
    try:
        async with factory() as s:
            result = await fn(s)
            await s.commit()
            return result
    except Exception as exc:  # noqa: BLE001
        if not _is_connection_error(exc):
            raise
        async with factory() as s:
            result = await fn(s)
            await s.commit()
            return result


# ── write: record one Activity Summary entry ────────────────────────────────────
async def record_activity(
    activity_entry: dict,
    *,
    organization_id: Any = None,
    session_id: str | None = None,
    session=None,
) -> str:
    """Insert one ``ActivityLogEntry`` (one of the four triggers). Returns its id."""
    from ..models.migration import ActivityLogEntry

    new_id = uuid4()
    cols = entry_to_columns(activity_entry, organization_id=organization_id, session_id=session_id)
    row = ActivityLogEntry(id=new_id, **cols)

    async def _do(s):
        s.add(row)
        await s.flush()
        return str(new_id)

    return await _run_in_session(_do, session)


def _merge_decisions(old: list, new: list) -> list:
    """Append-only merge of mapping-decision rows: keep every existing row unchanged
    (immutable audit) and add only source columns not already recorded."""
    def key(d):
        return f"{(d or {}).get('source_table')}.{(d or {}).get('source_column')}"

    merged = list(old or [])
    seen = {key(d) for d in merged}
    for d in new or []:
        k = key(d)
        if k not in seen:
            seen.add(k)
            merged.append(d)
    return merged


def _merge_review_resolutions(old: list, new: list) -> list:
    """Append-only union of human-review-resolution records, keyed by ``gate_type:at``
    (immutable — an already-recorded resolution is never duplicated or overwritten). These
    live on the per-run row's ``refs.review_resolutions`` and are re-woven into the
    Processing Log on every rebuild, so the resolution stays inside the ONE migration
    activity through to completion."""
    def key(r):
        rr = r if isinstance(r, dict) else {}
        return f"{rr.get('gate_type')}:{rr.get('at')}"

    merged = list(old or [])
    seen = {key(r) for r in merged}
    for r in new or []:
        k = key(r)
        if k not in seen:
            seen.add(k)
            merged.append(r)
    return merged


# ── upsert: the single progressive per-run Activity entry (live → completed, same row) ──
async def upsert_run_activity(
    activity_entry: dict,
    *,
    organization_id: Any = None,
    session_id: str | None = None,
    merge_decisions: bool = True,
    session=None,
) -> str:
    """Create-or-update the ONE durable per-run Activity entry (marked
    ``refs.entry_kind == "run"``), so the live and completed views render the same row as it
    accumulates. Updating preserves ``created_at`` (when the run started) and — when
    ``merge_decisions`` — merges Processing-Log ``mapping_decisions`` append-only so resolved
    rows stay immutable. ``merge_decisions=False`` (Node-10 finalise) replaces with the
    authoritative set. Other entries for the same run (e.g. Human-Review-Resolution events)
    are untouched — they aren't marked ``entry_kind=run``."""
    from sqlalchemy import desc, select

    from ..models.migration import ActivityLogEntry

    cols = entry_to_columns(activity_entry, organization_id=organization_id, session_id=session_id)

    async def _do(s):
        existing = None
        if session_id is not None:
            stmt = select(ActivityLogEntry).where(ActivityLogEntry.session_id == session_id)
            if organization_id is not None:
                stmt = stmt.where(ActivityLogEntry.organization_id == organization_id)
            stmt = stmt.order_by(desc(ActivityLogEntry.created_at))
            res = await s.execute(stmt)
            for r in res.scalars().all():
                refs = r.refs if isinstance(r.refs, dict) else {}
                if refs.get("entry_kind") == "run":
                    existing = r
                    break

        if existing is None:
            new_id = uuid4()
            s.add(ActivityLogEntry(id=new_id, **cols))
            await s.flush()
            return str(new_id)

        if merge_decisions:
            old_pl = existing.processing_log if isinstance(existing.processing_log, dict) else {}
            new_pl = dict(cols.get("processing_log") or {})
            new_pl["mapping_decisions"] = _merge_decisions(
                old_pl.get("mapping_decisions") or [], new_pl.get("mapping_decisions") or []
            )
            cols["processing_log"] = new_pl

        # Human-review resolutions are part of THIS run's audit trail — never a separate
        # card. Preserve them across every rebuild (and the Node-10 finalise, which replaces
        # most fields) and re-weave their Processing-Log step at the right anchor. Runs
        # regardless of merge_decisions so the completed entry keeps the resolution inline.
        old_refs = existing.refs if isinstance(existing.refs, dict) else {}
        new_refs = dict(cols.get("refs") or {})
        merged_res = _merge_review_resolutions(
            old_refs.get("review_resolutions") or [], new_refs.get("review_resolutions") or []
        )
        if merged_res:
            from .run_activity import weave_review_resolution_steps

            new_refs["review_resolutions"] = merged_res
            cols["refs"] = new_refs
            pl = dict(cols.get("processing_log") or {})
            steps = weave_review_resolution_steps(list(pl.get("steps") or []), merged_res)
            pl["steps"] = steps
            pl["thought"] = [x for x in steps if isinstance(x, dict) and x.get("kind") == "thought"]
            pl["action"] = [x for x in steps if isinstance(x, dict) and x.get("kind") == "action"]
            cols["processing_log"] = pl

        for k, v in cols.items():
            setattr(existing, k, v)  # created_at is NOT in cols → run start preserved
        await s.flush()
        return str(existing.id)

    return await _run_in_session(_do, session)


# ── append a human-review resolution INTO the run row (no separate card) ────────────
async def append_run_review_resolution(
    resolution_record: dict,
    *,
    organization_id: Any = None,
    session_id: str | None = None,
    session=None,
) -> bool:
    """Append a human-review-resolution RECORD to THE per-run activity row
    (``refs.entry_kind == "run"``): add it to ``refs.review_resolutions`` (append-only /
    immutable) and weave its "Human review resolution" step into the Processing Log at the
    gate's anchor. This keeps the resolution inside the SAME migration activity instead of
    creating a separate top-level card. Returns ``False`` (no-op) when the run row doesn't
    exist yet — the row is created by the pipeline's progressive sync before any gate."""
    from sqlalchemy import desc, select

    from ..models.migration import ActivityLogEntry
    from .run_activity import weave_review_resolution_steps

    async def _do(s):
        existing = None
        if session_id is not None:
            stmt = select(ActivityLogEntry).where(ActivityLogEntry.session_id == session_id)
            if organization_id is not None:
                stmt = stmt.where(ActivityLogEntry.organization_id == organization_id)
            stmt = stmt.order_by(desc(ActivityLogEntry.created_at))
            res = await s.execute(stmt)
            for r in res.scalars().all():
                refs = r.refs if isinstance(r.refs, dict) else {}
                if refs.get("entry_kind") == "run":
                    existing = r
                    break
        if existing is None:
            return False

        refs = dict(existing.refs or {})
        merged = _merge_review_resolutions(refs.get("review_resolutions") or [], [resolution_record])
        refs["review_resolutions"] = merged
        pl = dict(existing.processing_log or {})
        steps = weave_review_resolution_steps(list(pl.get("steps") or []), merged)
        pl["steps"] = steps
        pl["thought"] = [x for x in steps if isinstance(x, dict) and x.get("kind") == "thought"]
        pl["action"] = [x for x in steps if isinstance(x, dict) and x.get("kind") == "action"]
        existing.refs = refs  # reassign (new dict) so SQLAlchemy flags the JSONB column dirty
        existing.processing_log = pl
        await s.flush()
        return True

    return await _run_in_session(_do, session)


# ── read the per-run row's persisted state (for progressive rebuilds) ───────────────
async def get_run_entry_state(
    *,
    organization_id: Any = None,
    session_id: str | None = None,
    session=None,
) -> dict:
    """Read the per-run row's persisted ``refs`` + ``processing_log.mapping_decisions`` (the
    ``refs.entry_kind == "run"`` entry). Lets a progressive rebuild recover data that lived
    only transiently in a gate payload — e.g. the pre-semantic ``column_intelligence`` (gone
    from ``pending_gate_payload`` once the gate resolves) and the accumulated mapping
    decisions — so the live card keeps the column-stage detail through to completion. Returns
    ``{"refs": {...}, "mapping_decisions": [...]}`` (empty when no run row exists yet)."""
    from sqlalchemy import desc, select

    from ..models.migration import ActivityLogEntry

    async def _do(s):
        if session_id is None:
            return {"refs": {}, "mapping_decisions": []}
        stmt = select(ActivityLogEntry).where(ActivityLogEntry.session_id == session_id)
        if organization_id is not None:
            stmt = stmt.where(ActivityLogEntry.organization_id == organization_id)
        stmt = stmt.order_by(desc(ActivityLogEntry.created_at))
        res = await s.execute(stmt)
        for r in res.scalars().all():
            refs = r.refs if isinstance(r.refs, dict) else {}
            if refs.get("entry_kind") == "run":
                pl = r.processing_log if isinstance(r.processing_log, dict) else {}
                md = pl.get("mapping_decisions")
                return {"refs": refs, "mapping_decisions": md if isinstance(md, list) else []}
        return {"refs": {}, "mapping_decisions": []}

    return await _run_in_session(_do, session)


# ── read: newest-first list (AL.1 — history newest chronological) ───────────────
async def list_activity(
    *,
    organization_id: Any = None,
    session_id: str | None = None,
    limit: int = 50,
    session=None,
) -> list[dict]:
    # Cross-tenant guard: never list every org's activity on an unscoped call —
    # require at least one of organization_id / session_id (else return empty).
    if organization_id is None and session_id is None:
        return []

    from sqlalchemy import desc, select

    from ..models.migration import ActivityLogEntry

    async def _do(s):
        stmt = select(ActivityLogEntry).order_by(desc(ActivityLogEntry.created_at)).limit(limit)
        if organization_id is not None:
            stmt = stmt.where(ActivityLogEntry.organization_id == organization_id)
        if session_id is not None:
            stmt = stmt.where(ActivityLogEntry.session_id == session_id)
        res = await s.execute(stmt)
        return [_row_to_dict(r) for r in res.scalars().all()]

    return await _run_in_session(_do, session)


# ── read: single entry WITH processing log (Section 2 detail) ────────────────────
async def get_activity(entry_id, *, session=None) -> dict | None:
    """Fetch one entry including its full ``processing_log`` (or ``None`` if absent)."""
    from uuid import UUID

    from sqlalchemy import select

    from ..models.migration import ActivityLogEntry

    eid = UUID(entry_id) if isinstance(entry_id, str) else entry_id

    async def _do(s):
        res = await s.execute(select(ActivityLogEntry).where(ActivityLogEntry.id == eid))
        row = res.scalar_one_or_none()
        return _row_to_detail(row) if row is not None else None

    return await _run_in_session(_do, session)


# ── CAFM-004/006 — record one Section-3 inline gate decision on the run entry ───────
async def record_gate_decision(
    entry_id,
    card_id: str,
    *,
    option_id: str,
    option_label: str | None = None,
    note: str | None = None,
    at: str | None = None,
    session=None,
) -> dict | None:
    """Merge one resolved gate-decision card into ``refs.gate_decisions`` on the activity entry
    (keyed by ``card_id`` → {option_id, option_label, note, at}). Append-only per card: the FIRST
    resolution wins and re-resolving a card is a no-op (never silently overwrites a human choice).
    Returns the stored record, or ``None`` if the entry doesn't exist."""
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy import select

    from ..models.migration import ActivityLogEntry

    eid = UUID(entry_id) if isinstance(entry_id, str) else entry_id
    stamp = at or datetime.utcnow().isoformat()

    async def _do(s):
        row = (
            await s.execute(select(ActivityLogEntry).where(ActivityLogEntry.id == eid))
        ).scalar_one_or_none()
        if row is None:
            return None
        refs = dict(row.refs or {})
        decisions = dict(refs.get("gate_decisions") or {})
        existing = decisions.get(card_id)
        if isinstance(existing, dict) and existing.get("option_id"):
            return existing  # first-resolution-wins; idempotent re-resolve
        rec = {"option_id": str(option_id), "option_label": option_label, "note": note, "at": stamp}
        decisions[card_id] = rec
        refs["gate_decisions"] = decisions
        row.refs = refs  # reassign so SQLAlchemy flags the JSONB column dirty
        await s.flush()
        return rec

    return await _run_in_session(_do, session)


# ── mark read (clears it from the unread badge) ─────────────────────────────────
async def mark_activity_read(entry_id, *, session=None) -> bool:
    from uuid import UUID

    from sqlalchemy import update

    from ..models.migration import ActivityLogEntry

    eid = UUID(entry_id) if isinstance(entry_id, str) else entry_id

    async def _do(s):
        res = await s.execute(
            update(ActivityLogEntry).where(ActivityLogEntry.id == eid).values(read=True)
        )
        return (res.rowcount or 0) > 0

    return await _run_in_session(_do, session)


# ── unread summary for the notification icon (AL.1 AC4/AC5) ──────────────────────
async def unread_summary(*, organization_id: Any = None, session=None) -> dict:
    """{count, worst_color} over UNREAD entries for ONE organization — drives the icon
    colour + badge. Org scope is MANDATORY: an unscoped call returns an empty summary
    instead of aggregating unread rows across every tenant (cross-tenant isolation)."""
    if organization_id is None:
        return {"count": 0, "worst_color": None}

    from sqlalchemy import select

    from ..models.migration import ActivityLogEntry

    async def _do(s):
        stmt = select(ActivityLogEntry.notif_color).where(
            ActivityLogEntry.read.is_(False),
            ActivityLogEntry.organization_id == organization_id,
        )
        res = await s.execute(stmt)
        colors = [c for (c,) in res.all()]
        worst = max(colors, key=lambda c: NOTIF_PRIORITY.get(c, 0)) if colors else None
        return {"count": len(colors), "worst_color": worst}

    return await _run_in_session(_do, session)
