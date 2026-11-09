"""The Trace tab's endpoint on a throwaway Postgres (9 Nov 2026): a question's answer comes back as Langfuse's tree,
under exactly the access the question page has, and the module never writes. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import ast
import json
import pathlib
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labelling, runs, trace_tree
from trace_lab.review_errors import NotFound
from trace_lab_m2_fixtures import principal

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
AT = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)


async def _turn(main, tid, *, org, email, question):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, cost_usd, models, tools, ok)
            VALUES (:tid, 's-' || :tid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, 'An answer.', 'single:vendor',
                    :at, :at, 2400, 1, 1, 900, 20, 0.0077, CAST('["claude-sonnet-5"]' AS jsonb), CAST('["lookup"]' AS jsonb),
                    true)"""),
            {"tid": tid, "org": org, "uid": str(uuid.uuid4()), "email": email, "q": question, "at": AT})


async def _span(main, tid, sid, parent, seq, kind, name, **kw):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_spans
                (id, turn_id, parent_id, seq, kind, name, agent, model, started_at, ended_at, latency_ms, input, output,
                 input_tokens, output_tokens, cost_usd, ok)
            VALUES (CAST(:id AS uuid), :tid, CAST(:parent AS uuid), :seq, :kind, :name, 'vendor', :model, :at, :at, 10,
                    CAST(:inp AS jsonb), CAST(:out AS jsonb), :tin, :tout, :cost, true)"""),
            {"id": sid, "tid": tid, "parent": parent, "seq": seq, "kind": kind, "name": name, "model": kw.get("model"),
             "at": AT, "inp": json.dumps(kw.get("input")) if kw.get("input") is not None else None,
             "out": json.dumps(kw.get("output")) if kw.get("output") is not None else None,
             "tin": kw.get("input_tokens"), "tout": kw.get("output_tokens"), "cost": kw.get("cost_usd")})


async def _answer(main, tid, org, email, question):
    await _turn(main, tid, org=org, email=email, question=question)
    root, router, tool = (str(uuid.uuid4()) for _ in range(3))
    await _span(main, tid, root, None, 0, "turn", "turn", input={"message": question})
    await _span(main, tid, router, root, 1, "router", "router", model="claude-sonnet-5", input_tokens=900,
                output_tokens=20, cost_usd=0.0077, input={"q": question}, output={"model_output": {"route": "vendor"}})
    await _span(main, tid, tool, root, 2, "tool", "lookup", input={"vendor": "Pennard"}, output={"rows": 2})


async def _code_for(m, tid):
    for r in (await runs.list_runs(m))["runs"]:
        for q in (await runs.list_questions(m, r["code"]))["questions"]:
            if tid in (q.get("turn_ids") or []):
                return q["code"]
    raise AssertionError(f"no question holds {tid}")


async def _boss():
    return await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)


pytestmark = requires_scratch


async def test_the_tree_of_a_questions_answer(main_scratch, lab_scratch):  # noqa: F811
    await _answer(main_scratch, "turn-a1", ORG_A, "ann@a.test", "Which vendors are blocked?")
    boss = await _boss()
    await runs.sync_production(boss)
    out = await trace_tree.question_tree(boss, await _code_for(boss, "turn-a1"))
    assert (out["turn_id"], out["missing"]) == ("turn-a1", False)
    tree = out["tree"]
    assert [n["name"] for n in tree["nodes"]] == ["chat", "router", "lookup"]
    assert [n["type"] for n in tree["nodes"]] == ["agent", "generation", "tool"]
    assert tree["nodes"][1]["cost_usd"] == pytest.approx(0.0077) and tree["purged"] is False
    assert tree["turn"]["email"] == "ann@a.test" and tree["turn"]["route"] == "single:vendor"


async def test_the_tree_follows_the_question_pages_access(main_scratch, lab_scratch):  # noqa: F811
    await _answer(main_scratch, "turn-a1", ORG_A, "ann@a.test", "Which vendors are blocked?")
    await _answer(main_scratch, "turn-b1", ORG_B, "bob@b.test", "What EPC rating?")
    boss = await _boss()
    await runs.sync_production(boss)
    code_a, code_b = await _code_for(boss, "turn-a1"), await _code_for(boss, "turn-b1")

    admin_a = await labelling.access(principal(ORG_A, "admin", email="adm@a.test"), None, all_companies_ok=True)
    assert (await trace_tree.question_tree(admin_a, code_a))["turn_id"] == "turn-a1"
    with pytest.raises(NotFound):
        await trace_tree.question_tree(admin_a, code_b)          # another company's question stays unseen

    fell_back = await trace_tree.question_tree(boss, code_a, turn_id="turn-b1")
    assert fell_back["turn_id"] == "turn-a1", "a turn not linked to the question falls back to its newest answer"
    assert [n["name"] for n in fell_back["tree"]["nodes"]][0] == "chat"

    made = await runs.create_run(boss, title="Not asked yet", company=ORG_A, questions=[{"question": "Any lapsed?"}])
    run_code = (made.get("run") or made)["code"]
    assert await trace_tree.question_tree(boss, run_code + "-1") == {"turn_id": None, "missing": False, "tree": None}


async def test_a_turn_that_can_no_longer_be_read_is_missing(main_scratch, lab_scratch):  # noqa: F811
    boss = await _boss()
    made = await runs.create_run(boss, title="Gone", company=ORG_A, questions=[{"question": "Where did it go?"}])
    run_code = (made.get("run") or made)["code"]
    async with lab_scratch.begin() as conn:
        qid = (await conn.execute(text(
            "SELECT q.id::text FROM tracelab.lab_questions q JOIN tracelab.lab_runs r ON r.id = q.run_id "
            "WHERE r.title = 'Gone'"))).scalar()
        await conn.execute(text(
            "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source) "
            "VALUES (CAST(:q AS uuid), 'turn-gone', CAST(:org AS uuid), 'manual')"), {"q": qid, "org": ORG_A})
    assert await trace_tree.question_tree(boss, run_code + "-1") == {"turn_id": "turn-gone", "missing": True,
                                                                      "tree": None}


def test_the_tree_never_writes():
    src = pathlib.Path(trace_tree.__file__).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):                                   # docstrings may name what it never does
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|CREATE|DROP|GRANT)\b", code, re.I), \
        "the Trace tab only reads"
    assert ".begin()" not in code and "lab_engine().begin" not in code
    assert "conversations._read()" in code and "conversations._lab_read()" in code
