"""The answer as Hoistra showed it (16 Nov 2026, Hussain: "make sure u show me the exact trace steps and exact same ans
in the trace lab").

A trace keeps only the answer's text. The cards Hoistra drew beside it (compliance_response / compliance_pipeline) and
the OpenUI layout (openui_view) are kept on the chat's own row of the turn: plenum_cafm.chat_turns.tools, written by the
orchestrator (svc-deepagents services/chat_threads.py record_answer / record_view), which is what a reopened chat draws.
This reads that row so the session page can draw the answer the same way, with Hoistra's own components.

Read only: one READ ONLY transaction on the main engine with a statement timeout, bounded to the trace's own company and
session. The row is the one of that session asked nearest the trace's start (a turn's question is recorded as it
starts); one more than WINDOW away is another turn's, and nothing is shown. A nearest row with no answer (the turn
failed) shows nothing: a neighbouring turn's answer is never borrowed. Nothing is written anywhere.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from . import conversations, db, labelling, runs
from .review_errors import NotFound

WINDOW_SECONDS = 600
TIMEOUT_MS = 5000

_ROW = (
    "SELECT answer, tools::text AS tools, turn_no FROM plenum_cafm.chat_turns"
    " WHERE thread_id = :sid AND organization_id = CAST(:org AS uuid)"
    " AND abs(extract(epoch FROM created_at - CAST(:at AS timestamptz))) <= :win"
    " ORDER BY abs(extract(epoch FROM created_at - CAST(:at AS timestamptz))), turn_no LIMIT 1")


async def as_shown(m: labelling.Member, trace_id: str) -> dict[str, Any]:
    """{"found": True, "answer", "tools", "turn_no"} from the chat's row of the turn, or {"found": False} when the chat
    keeps none for it (or it cannot be read). A trace outside the member's companies is not found."""
    labelling.require_active(m)
    scope, p = runs._scope(m, "t")
    async with conversations._lab_read() as conn:
        t = (await conn.execute(text(
            f"SELECT t.organization_id::text AS org, t.header::text AS header FROM tracelab.traces t"
            f" WHERE t.id = :id AND {scope}"), {**p, "id": trace_id})).mappings().first()
    if t is None:
        raise NotFound("trace_not_found", "That trace is not in Trace Lab.")
    head = _loads(t["header"])
    sid, started = head.get("session_id"), head.get("started_at")
    if not sid or not started or not t["org"]:
        return {"found": False}
    try:
        at = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        async with db.main_engine().begin() as conn:
            await conn.execute(text("SET TRANSACTION READ ONLY"))
            await conn.execute(text(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}"))
            row = (await conn.execute(text(_ROW), {"sid": sid, "org": t["org"], "at": at, "win": WINDOW_SECONDS})
                   ).mappings().first()
    except Exception:  # noqa: BLE001 - the page then shows the recorded text, as before
        return {"found": False}
    if row is None or not row["answer"]:
        return {"found": False}             # this turn's own row has no answer: never a neighbouring turn's
    tools = _loads(row["tools"])
    return {"found": True, "answer": row["answer"] or "", "tools": tools if isinstance(tools, list) else [],
            "turn_no": row["turn_no"]}


def _loads(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if v else {}
    except (TypeError, ValueError):
        return {}
