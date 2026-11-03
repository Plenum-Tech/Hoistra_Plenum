"""A synthetic batch, kept where the review page already looks.

The questions are generated and asked by scenario_run; this is where what came back is written
down. Nothing here is a new way to review an answer: a batch becomes a lab run of its own
(tracelab.lab_runs, kind 'synthetic'), its questions become lab questions with codes of the usual
shape (R32-1, R32-2), and each answer is linked to its question as a trace. The Runs page then
lists the batch beside every other run, and opening a question shows the answer, its step tree,
the notes and the expected-answer slot -- the same screens, because it is the same tables.

The one thing that needs explaining is the turn. A scenario turn was never in the chat's own
tables: capture.py builds it in this process with the trace store switched off, so nothing was
recorded there and nothing could be. Golden runs have the same problem and solve it by writing
the captured turn and its spans into Trace Lab's evidence tables and teaching the reader to
resolve that kind of turn id from there (conversations.golden_turn). This does the same, with
reason 'scenario' and its own prefix, so a scenario turn is never mistaken for a golden one and
neither is mistaken for a live turn.

Written to Trace Lab's own database only. The application database is read-only to this process
and nothing here touches it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text

from . import db

log = structlog.get_logger(__name__)

#: A turn id that this module wrote. conversations.is_scenario_turn reads it back, so it has to be
#: recognisable from the id alone and distinct from the golden prefix.
SCENARIO_TURN_PREFIX = "scenario:"

#: What a run of a batch is called in the Runs list. The instance is in the title because it is
#: the thing that makes two batches of the same questions different measurements.
TITLE = "Synthetic · {space} · seed {seed}"


def turn_id_for(session_id: str, turn_id: str) -> str:
    """The id a scenario turn is stored and found under."""
    raw = f"{SCENARIO_TURN_PREFIX}{session_id}:{turn_id}"
    return raw[:200]


def _dumps(value: Any) -> str:
    """JSON for a jsonb column: Postgres refuses the NUL character inside text."""
    return json.dumps(value, default=str).replace("\\u0000", "")


async def save(result: dict[str, Any], *, organization_id: str, principal: Any = None) -> dict[str, Any]:
    """Write one finished batch, and return what it was filed as.

    Everything goes in one transaction: a half-written batch would show in the Runs list with
    questions that have no answers, which reads as the chat having failed rather than the write.
    """
    captured = result.get("captured") or []
    space = str(result.get("space") or "")
    seed = int(result.get("seed") or 0)
    email = getattr(principal, "email", None)
    user_id = getattr(principal, "user_id", None)
    now = datetime.now(timezone.utc)

    async with db.lab_engine().begin() as conn:
        run = (await conn.execute(text(
            "INSERT INTO tracelab.scenario_runs (organization_id, space, seed, challenge_ratio, fingerprint,"
            " requested, asked, status, source, usd_spent, ms, started_by, started_by_email, finished_at)"
            " VALUES (CAST(:org AS uuid), :space, :seed, :ratio, :fp, :req, :asked, 'done', 'page', :usd, :ms,"
            " CAST(:by AS uuid), :email, :fin)"
            " RETURNING id::text AS id, run_no"),
            {"org": organization_id, "space": space, "seed": seed,
             "ratio": float(result.get("challenge_ratio") or 0), "fp": result.get("fingerprint"),
             "req": int(result.get("requested") or len(captured)), "asked": len(captured),
             "usd": float(result.get("cost_usd") or 0), "ms": int(result.get("ms") or 0),
             "by": str(user_id) if user_id else None, "email": email, "fin": now})).mappings().one()

        lab = (await conn.execute(text(
            "INSERT INTO tracelab.lab_runs (kind, title, organization_id, source, created_by, created_by_email)"
            " VALUES ('synthetic', :title, CAST(:org AS uuid), 'coding_agent', CAST(:by AS uuid), :email)"
            " RETURNING id::text AS id, run_no"),
            {"title": TITLE.format(space=space, seed=seed)[:200], "org": organization_id,
             "by": str(user_id) if user_id else None, "email": email})).mappings().one()

        for seq, item in enumerate(captured, start=1):
            row = item["result"]
            tid = item["turn_id"]
            await _evidence(conn, organization_id, tid, item)
            await conn.execute(text(
                "INSERT INTO tracelab.scenario_results (run_id, organization_id, seq_no, scenario_id, question,"
                " answer, engine, role, style, difficulty, in_module, gate_ok, tools, models, spans, session_id,"
                " turn_id, usd, ms, error, asked_at)"
                " VALUES (CAST(:run AS uuid), CAST(:org AS uuid), :seq, :sid, :q, :a, :engine, :role, :style,"
                " :difficulty, :in_module, :gate, CAST(:tools AS jsonb), CAST(:models AS jsonb), :spans, :session,"
                " :tid, :usd, :ms, :err, :at)"),
                {"run": run["id"], "org": organization_id, "seq": seq, "sid": row["scenario_id"],
                 "q": row["question"], "a": row.get("answer") or None, "engine": row.get("engine"),
                 "role": row.get("role"), "style": row.get("style"), "difficulty": row.get("difficulty"),
                 "in_module": bool(row.get("in_module", True)), "gate": row.get("gate_ok"),
                 "tools": _dumps(row.get("tools") or []), "models": _dumps(row.get("models") or []),
                 "spans": int(row.get("spans") or 0), "session": row.get("session_id"), "tid": tid,
                 "usd": float(row.get("cost_usd") or 0), "ms": int(row.get("ms") or 0),
                 "err": (row.get("error") or None), "at": now})

            qid = (await conn.execute(text(
                "INSERT INTO tracelab.lab_questions (run_id, seq_no, question, role, intent, scenario_ref,"
                " organization_id, asked_at, source, created_by, created_by_email)"
                " VALUES (CAST(:run AS uuid), :seq, :q, :role, :intent, :ref, CAST(:org AS uuid), :at,"
                " 'coding_agent', CAST(:by AS uuid), :email) RETURNING id::text"),
                {"run": lab["id"], "seq": seq, "q": row["question"], "role": row.get("role"),
                 # The intent is what the batch expects of this question, which is what a reviewer
                 # needs to know before judging the answer.
                 "intent": _intent(row), "ref": row["scenario_id"], "org": organization_id,
                 "at": now, "by": str(user_id) if user_id else None, "email": email})).scalar()

            await conn.execute(text(
                "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source,"
                " created_by, created_by_email)"
                " VALUES (CAST(:qid AS uuid), :tid, CAST(:org AS uuid), 'coding_agent', CAST(:by AS uuid), :email)"
                " ON CONFLICT (question_id, turn_id) DO NOTHING"),
                {"qid": qid, "tid": tid, "org": organization_id,
                 "by": str(user_id) if user_id else None, "email": email})

    log.info("scenario_store.saved", run_no=run["run_no"], lab_run=lab["run_no"], asked=len(captured), space=space)
    return {"scenario_run_no": run["run_no"], "run_code": "R" + str(lab["run_no"]),
            "lab_run_id": lab["id"], "questions": len(captured)}


def _intent(row: dict[str, Any]) -> str:
    """What this question is for, in a sentence a reviewer can judge against."""
    if not row.get("in_module", True):
        return ("Outside this instance: it must be refused. Answering it is the failure, however "
                "good the answer looks.")
    return (f"Generated for {row.get('engine') or 'this instance'}; asked as a "
            f"{row.get('role') or 'person'} writing {row.get('style') or 'plainly'}. No expected "
            f"answer: one has to be grounded in a real row and written by a person.")


async def _evidence(conn: Any, org: str, turn_id: str, item: dict[str, Any]) -> None:
    """The captured turn and its span tree, so the review page can show the steps.

    A scenario turn is not in the chat's tables and never was, so this IS the record -- not a copy
    of one, the way evidence.snapshot takes a copy of a live turn. reason 'scenario' says which.
    """
    turn = dict(item.get("turn") or {})
    turn["turn_id"] = turn_id
    turn["scenario"] = {k: item["result"].get(k) for k in
                        ("scenario_id", "space", "engine", "role", "style", "difficulty", "in_module", "gate_ok")}
    turn["cards"] = item.get("cards") or []
    turn["refused"] = item.get("refused") or []
    await conn.execute(text(
        "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason)"
        " VALUES (:tid, CAST(:org AS uuid), CAST(:turn AS jsonb), 'scenario') ON CONFLICT (turn_id) DO NOTHING"),
        {"tid": turn_id, "org": org, "turn": _dumps(turn)})
    for seq, span in enumerate(item.get("spans") or []):
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_spans (turn_id, span_id, seq, span)"
            " VALUES (:tid, :sid, :seq, CAST(:span AS jsonb)) ON CONFLICT DO NOTHING"),
            {"tid": turn_id, "sid": str(span.get("id") or f"{turn_id}:{seq}"), "seq": seq, "span": _dumps(span)})
