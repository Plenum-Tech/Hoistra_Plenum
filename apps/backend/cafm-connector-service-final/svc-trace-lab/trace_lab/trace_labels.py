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


GRID_MAX = 2000      # rows (turns) in one run tag's labels grid

# A run tag's sessions the member may see, each scenario's latest attempt only (as the Sessions list shows them), and
# every turn of them in order: the rows of the Labels page for sessions.
_GRID_ROWS = """
    SELECT t.id, t.turn_index, t.input, t.session_id, x.scenario_id
      FROM (SELECT s.id, s.scenario_id, row_number() OVER (PARTITION BY s.run_tag, s.scenario_id
                   ORDER BY s.attempt DESC, s.created_at DESC, s.id DESC) AS rn
              FROM tracelab.sessions s WHERE s.run_tag = :tag AND {scope}) x
      JOIN tracelab.traces t ON t.session_id = x.id
     WHERE x.rn = 1 OR x.scenario_id IS NULL
     ORDER BY coalesce(x.scenario_id, x.id), x.id, t.turn_index
     LIMIT :lim
"""
# Each person's newest value per failure mode on each of those traces, clears included (they say when it was touched).
_GRID_CELLS = """
    SELECT DISTINCT ON (c.trace_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email))
           c.trace_id, c.mode_lineage_id::text AS mode, c.value, c.author_id::text AS author_id, c.author_email,
           c.author_role, c.source, c.created_at
      FROM tracelab.trace_label_cells c
     WHERE c.trace_id = ANY(CAST(:ids AS text[]))
     ORDER BY c.trace_id, c.mode_lineage_id, coalesce(c.author_id::text, c.author_email), c.created_at DESC, c.id DESC
"""


def _cell(r: Any, m: labelling.Member) -> dict[str, Any]:
    return {"value": r["value"], "author_id": r["author_id"], "author_email": r["author_email"],
            "author_role": r["author_role"], "source": r["source"], "at": runs._iso(r["created_at"]),
            "mine": r["author_id"] is not None and r["author_id"] == m.user_id,
            "suggestion": r["source"] == "coding_agent"}


async def tag_matrix(m: labelling.Member, run_tag: str) -> dict[str, Any]:
    """The Labels page for a run tag's sessions (13 Nov 2026), in the shape of a run's labels view (runs.matrix) so the
    same grid draws it: {"run": {"code": the tag, "title": "Sessions", "kind": "sessions"}, "questions": one row per
    turn (id = the trace id, code = "<scenario> · turn <n>", question, turn_ids, session_id), "modes", "cells":
    {trace id: {mode lineage: [each person's value]}}, "last_updated", "me"}. Values are the session labels
    (trace_label_cells), set with set_label."""
    labelling.require_active(m)
    scope, p = runs._scope(m, "s")
    async with conversations._lab_read() as conn:
        rows = (await conn.execute(text(_GRID_ROWS.format(scope=scope)),
                                   {**p, "tag": run_tag, "lim": GRID_MAX})).mappings().all()
        modes = await runs._live_modes(conn)
        cells = (await conn.execute(text(_GRID_CELLS), {"ids": [r["id"] for r in rows]})).mappings().all() if rows else []
    live = {x["lineage_id"] for x in modes}
    by: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for c in cells:
        if c["value"] is not None and c["mode"] in live:
            by.setdefault(c["trace_id"], {}).setdefault(c["mode"], []).append(_cell(c, m))
    last = max((c["created_at"] for c in cells), default=None)
    questions = [{"id": r["id"], "code": f"{r['scenario_id'] or r['session_id']} · turn {r['turn_index']}",
                  "question": r["input"], "turn_ids": [r["id"]], "session_id": r["session_id"]} for r in rows]
    return {"run": {"code": run_tag, "title": "Sessions", "kind": "sessions"}, "questions": questions, "modes": modes,
            "cells": by, "last_updated": runs._iso(last), "me": m.user_id}


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
