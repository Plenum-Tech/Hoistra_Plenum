"""A reviewer flags a turn's expected outcome as wrong (12 Nov 2026, migration 0016).

Expected outcomes are checked while reviewing, not before asking: next to the answer, a reviewer can say the expected
outcome itself is wrong (the scenario needs correcting), rather than the answer. A turn is flagged while any person's
newest flag on it says so. A flagged turn is never counted against Hoistra: evaluation leaves it unscored
(eval_judges), compare leaves its scenario out (scores.compare), and the scenario shows that it needs correcting until a
corrected version is stored (scenario_file.list_scenarios counts flags on the latest version's sessions only).

Who: any active Trace Lab member who may see the trace, the same rule as notes and failure-mode labels.
"""
from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text

from . import conversations, db, labelling, runs
from .annotations import _limits
from .review_errors import Invalid, NotFound

COMMENT_MAX = 2000

# Each person's newest flag on one trace (a person is their user id, else their email).
_NEWEST = """
    SELECT DISTINCT ON (coalesce(f.author_id::text, f.author_email))
           f.flagged, f.comment, f.author_id::text AS author_id, f.author_email, f.author_role, f.created_at
      FROM tracelab.expected_flags f
     WHERE f.trace_id = :tid
     ORDER BY coalesce(f.author_id::text, f.author_email), f.created_at DESC, f.id DESC
"""


def flagged_id_sql(trace_id: str) -> str:
    """SQL that is true when the trace whose id is the SQL expression `trace_id` is flagged: some person's newest flag
    on it says so."""
    return (f"EXISTS (SELECT 1 FROM (SELECT DISTINCT ON (coalesce(f.author_id::text, f.author_email)) f.flagged"
            f" FROM tracelab.expected_flags f WHERE f.trace_id = {trace_id}"
            f" ORDER BY coalesce(f.author_id::text, f.author_email), f.created_at DESC, f.id DESC) nf WHERE nf.flagged)")


def flagged_sql(trace_alias: str) -> str:
    """SQL that is true when the trace `<alias>.id` is flagged."""
    return flagged_id_sql(f"{trace_alias}.id")


async def flagged_traces(conn: Any, trace_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """{trace id: {"count": people flagging it, "people": their ids or emails}} for the flagged ones among trace_ids."""
    if not trace_ids:
        return {}
    rows = (await conn.execute(text(
        "SELECT x.trace_id, count(*) AS n, array_agg(x.who) AS people FROM ("
        " SELECT DISTINCT ON (f.trace_id, coalesce(f.author_id::text, f.author_email)) f.trace_id, f.flagged,"
        " coalesce(f.author_id::text, f.author_email) AS who FROM tracelab.expected_flags f"
        " WHERE f.trace_id = ANY(CAST(:ids AS text[]))"
        " ORDER BY f.trace_id, coalesce(f.author_id::text, f.author_email), f.created_at DESC, f.id DESC) x"
        " WHERE x.flagged GROUP BY x.trace_id"), {"ids": list(trace_ids)})).mappings().all()
    return {r["trace_id"]: {"count": int(r["n"]), "people": list(r["people"] or [])} for r in rows}


async def _trace(conn: Any, m: labelling.Member, trace_id: str) -> dict[str, Any]:
    scope, p = runs._scope(m, "t")
    row = (await conn.execute(text(
        f"SELECT t.id, t.organization_id::text AS organization_id FROM tracelab.traces t WHERE t.id = :id AND {scope}"),
        {**p, "id": trace_id})).mappings().first()
    if row is None:
        raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
    return dict(row)


async def _has_expected(conn: Any, trace_id: str) -> bool:
    """Whether the scenario version the trace's session was asked with has an expected outcome for its turn."""
    row = (await conn.execute(text(
        "SELECT sc.turns::text AS turns, t.turn_index FROM tracelab.traces t JOIN tracelab.sessions s ON s.id = t.session_id"
        " JOIN tracelab.scenarios sc ON sc.scenario_id = s.scenario_id AND sc.version = s.scenario_version"
        " WHERE t.id = :tid"), {"tid": trace_id})).mappings().first()
    if row is None:
        return False
    import json
    turns = json.loads(row["turns"]) if isinstance(row["turns"], str) else (row["turns"] or [])
    i = int(row["turn_index"] or 0)
    turn = turns[i - 1] if isinstance(turns, list) and 0 < i <= len(turns) else None
    return isinstance(turn, dict) and turn.get("expected") is not None


async def flags_of(m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """{"trace_id", "flagged": whether anyone flags it now, "mine": whether the reader does, "flags": the people
    flagging it, the reader first, then newest first}."""
    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        await _trace(conn, m, trace_id)
        rows = (await conn.execute(text(_NEWEST), {"tid": trace_id})).mappings().all()
    flags = [{"comment": r["comment"], "author_id": r["author_id"], "author_email": r["author_email"],
              "author_role": r["author_role"], "at": runs._iso(r["created_at"]),
              "mine": r["author_id"] is not None and r["author_id"] == m.user_id} for r in rows if r["flagged"]]
    flags.sort(key=lambda f: f["at"] or "", reverse=True)
    flags.sort(key=lambda f: not f["mine"])
    return {"trace_id": trace_id, "flagged": bool(flags), "mine": any(f["mine"] for f in flags), "flags": flags}


async def set_flag(m: labelling.Member, trace_id: str, *, flagged: bool, comment: str | None = None) -> dict[str, Any]:
    """The caller's flag on one trace's expected outcome: True (it is wrong) or False (cleared)."""
    labelling.require_active(m)
    note = (comment or "").strip() or None
    if note is not None and len(note) > COMMENT_MAX:
        raise Invalid("comment_too_long", f"Keep the comment under {COMMENT_MAX} characters.")
    async with db.lab_engine().begin() as conn:
        await _limits(conn)
        trace = await _trace(conn, m, trace_id)
        if flagged and not await _has_expected(conn, trace_id):
            raise Invalid("no_expected_outcome", "This turn has no expected outcome to flag.")
        row = (await conn.execute(text(
            "INSERT INTO tracelab.expected_flags (trace_id, organization_id, flagged, comment, author_id, author_email,"
            " author_role) VALUES (:tid, CAST(:org AS uuid), :flagged, :comment, CAST(:by AS uuid), :email, :role)"
            " RETURNING created_at"),
            {"tid": trace_id, "org": trace["organization_id"], "flagged": bool(flagged), "comment": note if flagged else None,
             "by": m.user_id, "email": m.email, "role": m.role})).mappings().one()
    return {"trace_id": trace_id, "flagged": bool(flagged), "at": runs._iso(row["created_at"])}
