"""Scratch-database fixtures shared by the Phase 4 review SQL tests (a plain helper module, like tl_scratch).

The Phase 4 task briefs import this module under the name trace_lab_m2_fixtures. Rows written here go only to
the WRITABLE engine that tl_scratch.main_scratch yields (database tracelab_scratch on a local host); code under
test reads them through db.main_engine(), the read-only engine. Nothing here can reach a real database.

Dropped from the old fixtures: add_meta (the turn_meta table does not exist in Trace Lab's own database; a
turn's channel is inferred at read time, see provenance.classify).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.services.principal import Principal

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def principal(org: str | None, role: str = "admin", email: str | None = None) -> Principal:
    uid = uuid.uuid4()
    return Principal(user_id=uid, email=email or f"{str(uid)[:6]}@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=True, building_ids=None)


def turn_id(n: int) -> str:
    return f"turn-{n:016x}"


async def add_turn(engine, n: int, org: str | None, *, session: str | None = None,
                   question: str = "How many work orders?", answer: str = "There were 758 work orders in August.",
                   minutes_ago: int = 60, tokens: int = 1000, route: str | None = "single:wo_engine",
                   tools: list[str] | None = None) -> str:
    tid = turn_id(n)
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), 'asker@example.com', :q, :a, :route,
                    :at, :at, 2000, 2, 1, :tin, 50, CAST('["m"]' AS jsonb), CAST(:tools AS jsonb), true)"""),
            {"tid": tid, "sid": session if session is not None else f"sess-{n}", "org": org, "uid": str(uuid.uuid4()),
             "q": question, "a": answer, "route": route, "at": NOW - timedelta(minutes=minutes_ago), "tin": tokens,
             "tools": json.dumps(tools or ["answer_from_records"])})
    return tid


async def add_span(engine, tid: str, *, kind: str = "tool", name: str = "answer_from_records",
                   agent: str = "wo_engine", output=None, seq: int = 1, inp=None, org: str | None = None) -> str:
    sid = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_spans (id, turn_id, organization_id, seq, kind, name, agent, started_at,
                                                       input, output, ok)
            VALUES (CAST(:id AS uuid), :tid, CAST(:org AS uuid), :seq, :kind, :name, :agent, :at, CAST(:inp AS jsonb),
                    CAST(:out AS jsonb), true)"""),
            {"id": sid, "tid": tid, "org": org, "seq": seq, "kind": kind, "name": name, "agent": agent, "at": NOW,
             "inp": json.dumps(inp) if inp is not None else None,
             "out": json.dumps(output if output is not None else {"count": 758, "period": "2026-07"})})
    return sid


async def add_thread(engine, session: str, org: str, *, deleted: bool = False) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title, deleted_at)
            VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), 'A chat', :del)"""),
            {"id": session, "org": org, "uid": str(uuid.uuid4()), "del": NOW if deleted else None})


async def truncate_chat(engine) -> None:
    """Empty the chat tables the review tests seed (the scratch database only). tl_scratch.main_scratch already
    recreates plenum_cafm for every test, so this is for a test that seeds twice."""
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE plenum_cafm.chat_threads, plenum_cafm.chat_turns, "
            "plenum_cafm.agent_trace_turns, plenum_cafm.agent_trace_spans")
