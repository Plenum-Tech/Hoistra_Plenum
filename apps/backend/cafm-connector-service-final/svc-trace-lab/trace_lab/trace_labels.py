"""Failure-mode labels on a session's traces (11 Nov 2026, migration 0015).

Runs are gone from Trace Lab's screens, so a person labels the turn they are reading on the session page: whether a
failure mode is there (positive) or not (negative) in that one trace. The failure modes are the ones the Labels page
uses (lab_failure_modes, by lineage; runs._live_modes). Every value is a new row in tracelab.trace_label_cells: a
person's newest row is their value, a NULL value clears it, and everyone's values are shown side by side, the
reader's own first.

Who: any active Trace Lab member who may see the trace (their own company's; the platform team and a consultant see
every company's), the same rule as a label on a run question.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from . import conversations, db, labelling, runs
from .annotations import _limits
from .review_errors import Invalid, NotFound

VALUES = ("positive", "negative")

# Each person's newest value per failure mode on one trace. A person is their user id, else their email.
_NEWEST = """
    SELECT DISTINCT ON (c.mode_lineage_id, coalesce(c.author_id::text, c.author_email))
           c.mode_lineage_id::text AS mode, c.value, c.author_id::text AS author_id, c.author_email, c.author_role,
           c.source, c.created_at
      FROM tracelab.trace_label_cells c
     WHERE c.trace_id = :tid
     ORDER BY c.mode_lineage_id, coalesce(c.author_id::text, c.author_email), c.created_at DESC, c.id DESC
"""


async def _trace(conn: Any, m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """The trace with its company, when the member may see it."""
    scope, p = runs._scope(m, "t")
    row = (await conn.execute(text(
        f"SELECT t.id, t.session_id, t.organization_id::text AS organization_id FROM tracelab.traces t"
        f" WHERE t.id = :id AND {scope}"), {**p, "id": trace_id})).mappings().first()
    if row is None:
        raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
    return dict(row)


async def labels_of(m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """{"trace_id", "modes": the live failure modes, "cells": {mode lineage: [each person's value, the reader's first,
    then newest first]}, "me"}. A mode nobody has a value for is not in "cells"."""
    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        await _trace(conn, m, trace_id)
        modes = await runs._live_modes(conn)
        rows = (await conn.execute(text(_NEWEST), {"tid": trace_id})).mappings().all()
    live = {x["lineage_id"] for x in modes}
    cells: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["value"] is None or r["mode"] not in live:
            continue
        cells.setdefault(r["mode"], []).append({
            "value": r["value"], "author_id": r["author_id"], "author_email": r["author_email"],
            "author_role": r["author_role"], "source": r["source"], "at": runs._iso(r["created_at"]),
            "mine": r["author_id"] is not None and r["author_id"] == m.user_id,
            "suggestion": r["source"] == "coding_agent"})
    for values in cells.values():
        values.sort(key=lambda c: c["at"] or "", reverse=True)
        values.sort(key=lambda c: not c["mine"])
    return {"trace_id": trace_id, "modes": modes, "cells": cells, "me": m.user_id}


async def set_label(m: labelling.Member, trace_id: str, *, mode: str, value: str | None) -> dict[str, Any]:
    """The caller's value for one failure mode on one trace: positive (it is there), negative (it is not) or None
    (cleared). The mode must be live."""
    labelling.require_active(m)
    if value is not None and value not in VALUES:
        raise Invalid("bad_value", "Choose positive or negative.")
    lineage = runs._uuid_or_none(mode)
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        trace = await _trace(conn, m, trace_id)
        if lineage is None or not any(x["lineage_id"] == lineage for x in await runs._live_modes(conn)):
            raise NotFound("mode_not_found", "That failure mode is not in Trace Lab.")
        row = (await conn.execute(text(
            "INSERT INTO tracelab.trace_label_cells (trace_id, organization_id, mode_lineage_id, value, author_id,"
            " author_email, author_role) VALUES (:tid, CAST(:org AS uuid), CAST(:mode AS uuid), :value,"
            " CAST(:by AS uuid), :email, :role) RETURNING created_at"),
            {"tid": trace_id, "org": trace["organization_id"], "mode": lineage, "value": value, "by": m.user_id,
             "email": m.email, "role": m.role})).mappings().one()
    return {"trace_id": trace_id, "mode": lineage, "value": value, "at": runs._iso(row["created_at"])}
