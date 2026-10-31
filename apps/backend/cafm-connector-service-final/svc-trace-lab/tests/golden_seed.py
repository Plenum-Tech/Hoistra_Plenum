"""Seed golden sets, runs and results straight into a scratch tracelab schema, for the views and spend tests.
A plain module (import it as `from golden_seed import ...`), used only with tl_scratch's lab_scratch engine."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text


async def seed_set(conn: Any, org: str, *, cases: tuple = (("1", ()),), version: int = 1, status: str = "active",
                   kind: str = "scenario", evaluation: str = "objective", name: str = "Bishopsgate questions") -> dict:
    """A set and its cases: cases = ((case_key, followups), ...). Returns {"set_id", "cases": {key: case_id}}."""
    sid = str(uuid.uuid4())
    regression = kind == "regression"
    await conn.execute(text(
        "INSERT INTO tracelab.golden_sets (id, organization_id, name, kind, version, status, source_filename, "
        "source_sha256, set_hash, case_count, created_by) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :name, :kind, "
        ":v, :st, :fn, :sha, 'aaaaaaaaaaaa', :n, 'u1')"),
        {"id": sid, "org": org, "name": name, "kind": kind, "v": 0 if regression else version, "st": status,
         "fn": None if regression else "bishopsgate.csv", "sha": None if regression else "a" * 64,
         "n": 0 if regression else len(cases)})
    ids: dict[str, str] = {}
    for i, (key, followups) in enumerate(cases):
        cid = str(uuid.uuid4())
        await conn.execute(text(
            "INSERT INTO tracelab.golden_cases (id, set_id, organization_id, case_key, case_version, idx, question, "
            "followups, expected_answer, evaluation, expected_values, verified_on, notes) VALUES (CAST(:id AS uuid), "
            "CAST(:s AS uuid), CAST(:org AS uuid), :k, :ver, :i, :q, CAST(:fu AS jsonb), :exp, :ev, "
            "CAST(:vals AS jsonb), DATE '2026-10-01', 'Ask Priya before changing this case.')"),
            {"id": cid, "s": sid, "org": org, "k": key, "ver": f"{i:012x}", "i": i, "q": f"Question {key}?",
             "fu": json.dumps(list(followups)), "exp": f"The right answer to {key}.", "ev": evaluation,
             "vals": json.dumps([{"raw": "22", "kind": "number", "value": 22}])})
        ids[key] = cid
    return {"set_id": sid, "cases": ids}


async def seed_run(conn: Any, org: str, set_id: str, *, k: int = 1, status: str = "done", params: dict | None = None,
                   started_at: datetime | None = None, usd_spent: float = 0.0, started_by: str = "u1") -> str:
    rid = str(uuid.uuid4())
    await conn.execute(text(
        "INSERT INTO tracelab.golden_runs (id, organization_id, set_id, label, k, status, started_by, started_by_email, "
        "started_at, params, usd_spent) VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:s AS uuid), :label, :k, "
        ":st, :by, 'admin@example.com', coalesce(CAST(:at AS timestamptz), now()), CAST(:p AS jsonb), :spent)"),
        {"id": rid, "org": org, "s": set_id, "label": "run-" + rid[:8], "k": k, "st": status, "at": started_at,
         "p": json.dumps(params or {}), "spent": usd_spent, "by": started_by})
    return rid


async def seed_result(conn: Any, org: str, run_id: str, case_id: str, case_key: str, *, rep: int = 1,
                      msg_idx: int = 0, outcome: str = "pass", grade: dict | None = None, usd: float | None = 0.05,
                      ms: int | None = 1000, refused: list | None = None, created_at: datetime | None = None,
                      graded_at: datetime | None = None, turn_id: str | None = None) -> tuple[str, str]:
    """One message row. Returns (result_id, turn_id)."""
    xid = str(uuid.uuid4())
    tid = turn_id or f"turn-{uuid.uuid4().hex[:16]}"
    await conn.execute(text(
        "INSERT INTO tracelab.golden_results (id, run_id, organization_id, case_id, case_key, case_version, rep, msg_idx, "
        "session_id, turn_id, question, answer, outcome, grade, refused, usd, ms, created_at, graded_at) VALUES "
        "(CAST(:id AS uuid), CAST(:r AS uuid), CAST(:org AS uuid), CAST(:c AS uuid), :k, '000000000000', :rep, :m, "
        ":sid, :t, :q, 'An answer.', :o, CAST(:g AS jsonb), CAST(:ref AS jsonb), :usd, :ms, "
        "coalesce(CAST(:at AS timestamptz), now()), CAST(:gat AS timestamptz))"),
        {"id": xid, "r": run_id, "org": org, "c": case_id, "k": case_key, "rep": rep, "m": msg_idx,
         "sid": f"golden-{run_id[:8]}-{case_key}-r{rep}", "t": tid, "q": f"Message {msg_idx} of {case_key}?",
         "o": outcome, "g": None if grade is None else json.dumps(grade), "ref": json.dumps(refused or []),
         "usd": usd, "ms": ms, "at": created_at, "gat": graded_at})
    return xid, tid
