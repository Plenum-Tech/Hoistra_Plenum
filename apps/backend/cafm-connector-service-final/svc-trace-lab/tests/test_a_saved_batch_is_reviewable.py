"""A synthetic batch is saved where the review page already looks, and reads back as a turn.

Asked for on 3 Nov 2026: generation and answering should not be a table of their own to read --
they should land in Runs the way R31 does, so a question opens with its answer, its step tree, a
place for notes and a place to say what the answer should have been.

Nothing new was needed to show it. A golden run has the same problem -- its turn is captured in
Trace Lab's own process and never existed in the chat's tables -- and solves it by writing the
turn and spans into the evidence tables and teaching the reader to resolve that kind of id from
there. This does the same with its own prefix, and files the batch as a lab run of kind
'synthetic' so the Runs page lists it beside every other.

Against a throwaway Postgres (TRACE_LAB_TEST_DB_URL), because the point is the write and the read
back, which a mock would not prove. Grouped into three tests on purpose: each one drops the
schema and applies twelve migrations, and ten of those in one file exhausts the fixture.

conversations is imported inside the test that needs it, not at the top: importing it at module
scope pulls svc-deepagents in before the fixture has built its schema, and the apply then fails
part-way with "schema tracelab does not exist".
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import db, scenario_store

pytestmark = requires_scratch

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def _batch(space: str = "ops", n: int = 2, *, out_of_module: bool = False, spans: list | None = None) -> dict:
    """What scenario_run hands the store: the rows, and the captured turn behind each one."""
    base = uuid.uuid4().hex[:12]
    captured = []
    for i in range(1, n + 1):
        sid = f"scenario-{space}-{base}{i}"
        tid = scenario_store.turn_id_for(sid, f"turn-{base}{i}")
        in_module = not (out_of_module and i == n)
        captured.append({
            "turn_id": tid,
            "result": {
                "scenario_id": f"{space.upper()}-1-{i:03d}",
                "question": f"question {i} about work orders", "answer": f"answer {i}",
                "engine": "wo_engine", "role": "operative", "style": "neutral", "difficulty": "clear",
                "in_module": in_module, "gate_ok": None if in_module else True,
                "tools": ["list_buildings", "count_work_orders"], "models": ["gpt-x"], "spans": 2,
                "session_id": sid, "cost_usd": 0.01, "ms": 1200, "error": None, "space": space,
            },
            "turn": {"turn_id": tid, "session_id": sid, "question": f"question {i} about work orders",
                     "answer": f"answer {i}", "ok": True},
            "spans": spans if spans is not None else [
                {"id": f"{tid}:0", "name": "orchestrator", "model": None},
                {"id": f"{tid}:1", "name": "wo_engine", "model": "gpt-x"}],
            "cards": [], "refused": [],
        })
    return {"space": space, "seed": 1, "requested": n, "asked": n, "challenge_ratio": 0.0,
            "fingerprint": "abc123def456", "cost_usd": 0.02, "ms": 2400, "captured": captured}


@pytest.mark.asyncio
async def test_a_batch_lands_as_a_run_a_reviewer_can_open(lab_scratch):
    """Everything the Runs page needs: a synthetic run, its questions, the answer linked to each,
    and the turn with its spans behind it."""
    batch = _batch(n=2, out_of_module=True)
    out = await scenario_store.save(batch, organization_id=ORG)
    assert out["questions"] == 2 and out["run_code"].startswith("R")
    tid = batch["captured"][0]["turn_id"]

    async with db.lab_engine().connect() as conn:
        kind = (await conn.execute(text(
            "SELECT kind FROM tracelab.lab_runs WHERE id = CAST(:id AS uuid)"),
            {"id": out["lab_run_id"]})).scalar()
        qs = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.lab_questions WHERE run_id = CAST(:id AS uuid)"),
            {"id": out["lab_run_id"]})).scalar()
        links = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.lab_question_traces t"
            " JOIN tracelab.lab_questions q ON q.id = t.question_id"
            " WHERE q.run_id = CAST(:id AS uuid)"), {"id": out["lab_run_id"]})).scalar()
        reason = (await conn.execute(text(
            "SELECT reason FROM tracelab.evidence_turns WHERE turn_id = :t"), {"t": tid})).scalar()
        spans = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.evidence_spans WHERE turn_id = :t"), {"t": tid})).scalar()
        rows = (await conn.execute(text(
            "SELECT in_module, gate_ok, tools FROM tracelab.scenario_results"
            " WHERE run_id = (SELECT id FROM tracelab.scenario_runs WHERE run_no = :n) ORDER BY seq_no"),
            {"n": out["scenario_run_no"]})).mappings().all()
        intents = [r[0] for r in (await conn.execute(text(
            "SELECT intent FROM tracelab.lab_questions WHERE run_id = CAST(:id AS uuid) ORDER BY seq_no"),
            {"id": out["lab_run_id"]})).all()]

    assert kind == "synthetic", "the batch must not be filed as somebody's live day"
    assert qs == 2
    assert links == 2, "a question with no trace opens on an empty page"
    assert reason == "scenario", "reason says which kind of capture this was; golden is not it"
    assert spans == 2, "without the spans the question opens with no steps behind it"
    assert rows[0]["tools"] == ["list_buildings", "count_work_orders"], "the order is the point"
    assert [(r["in_module"], r["gate_ok"]) for r in rows] == [(True, None), (False, True)], (
        "an in-module question has nothing to grade; an out-of-module one is judged on the refusal")
    assert "must be refused" in intents[-1] and all(intents)


@pytest.mark.asyncio
async def test_the_answer_reads_back_as_a_turn_and_only_for_its_company(lab_scratch):
    """The point of all of it: conversations.get_turn resolves a scenario turn, so the review page
    draws its steps the way it draws a live turn's -- and no other company can read it."""
    from trace_lab import conversations            # imported here: see the note at the top

    batch = _batch(n=1)
    await scenario_store.save(batch, organization_id=ORG)
    tid = batch["captured"][0]["turn_id"]

    assert conversations.is_scenario_turn(tid)
    assert not conversations.is_golden_turn(tid), "a scenario turn must never read as a golden one"

    got = await conversations.scenario_turn(ORG, tid)
    assert got is not None, "the review page would show 'missing' for an answer that is right there"
    assert got["conversation_key"].startswith(conversations.SCENARIO_KEY_PREFIX)
    assert got["trace"], "no normalized turn came back"

    assert await conversations.scenario_turn(OTHER, tid) is None
    assert await conversations.scenario_turn(ORG, "scenario:nothing:here") is None


@pytest.mark.asyncio
async def test_a_real_shaped_span_tree_survives_the_write(lab_scratch):
    """The blocks are drawn from the span fields, so this carries the ones they read: the kinds,
    the model, the payloads, and the parent link that makes them nest."""
    mark = uuid.uuid4().hex[:12]
    batch = _batch(n=1, spans=[
        {"id": f"s-{mark}-0", "parent_id": None, "seq": 0, "kind": "orchestrator",
         "name": "routing by code", "agent": "orchestrator", "model": None,
         "input": {"working_set": None}, "output": "wo_engine", "latency_ms": 12, "cost_usd": 0,
         "ok": True, "error": None},
        {"id": f"s-{mark}-1", "parent_id": f"s-{mark}-0", "seq": 1, "kind": "model",
         "name": "tool decision", "agent": "sub-agent", "model": "gpt-5.6-terra",
         "input": {"messages": 2}, "output": "list_buildings", "latency_ms": 1280,
         "cost_usd": 0.071, "ok": True, "error": None},
    ])
    await scenario_store.save(batch, organization_id=ORG)
    tid = batch["captured"][0]["turn_id"]
    async with db.lab_engine().connect() as conn:
        spans = [r[0] for r in (await conn.execute(text(
            "SELECT span FROM tracelab.evidence_spans WHERE turn_id = :t ORDER BY seq"),
            {"t": tid})).all()]

    assert [sp["kind"] for sp in spans] == ["orchestrator", "model"]
    assert spans[1]["model"] == "gpt-5.6-terra" and spans[1]["cost_usd"] == 0.071
    assert spans[1]["input"] == {"messages": 2}, "the model input is what 'prompt assembled' shows"
    assert spans[1]["parent_id"] == spans[0]["id"], "the tree is flattened; the blocks would not nest"


@pytest.mark.asyncio
async def test_the_next_few_questions_land_under_the_ones_already_there(lab_scratch):
    """A batch is asked a few at a time, and the whole batch is one run.

    Asked for on 3 Nov 2026: "it should add below these one". A second run of five would split one
    batch across two pages of the Runs list, and a reviewer annotating questions 6-10 would be
    annotating a run that looks like a different measurement. So the slice is appended to the run
    the earlier slice made, numbered from where that one stopped, which is also what makes the
    question codes (R32-6) mean the sixth question OF THE BATCH.
    """
    first = _batch(n=2)
    first["offset"] = 0
    out1 = await scenario_store.save(first, organization_id=ORG)

    second = _batch(n=2)
    second["offset"] = 2
    for i, item in enumerate(second["captured"], start=3):
        item["result"]["question"] = f"question {i} about work orders"
    out2 = await scenario_store.save(second, organization_id=ORG)

    assert out2["run_code"] == out1["run_code"], "the second slice started a run of its own"
    assert out2["appended"] is True and out1["appended"] is False
    assert (out2["seq_from"], out2["seq_to"]) == (3, 4)

    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text(
            "SELECT seq_no, question FROM tracelab.lab_questions"
            " WHERE run_id = CAST(:id AS uuid) ORDER BY seq_no"),
            {"id": out1["lab_run_id"]})).all()
        runs = (await conn.execute(text(
            "SELECT count(*) FROM tracelab.lab_runs WHERE kind = 'synthetic'"
            " AND organization_id = CAST(:o AS uuid)"), {"o": ORG})).scalar()

    assert runs == 1, "one batch became two runs"
    assert [r[0] for r in rows] == [1, 2, 3, 4], (
        "the second slice renumbered from one, so the batch reads 1,2,1,2")
    assert [r[1] for r in rows] == [f"question {i} about work orders" for i in (1, 2, 3, 4)], (
        "the order on the page has to be the order the batch was generated in")


@pytest.mark.asyncio
async def test_another_company_asking_the_same_batch_gets_its_own_run(lab_scratch):
    """The run is found by its title, which says the instance and the seed but not the company.
    Without the company in the lookup, one company's questions would land in another's run."""
    mine = _batch(n=1)
    mine["offset"] = 0
    out1 = await scenario_store.save(mine, organization_id=ORG)

    theirs = _batch(n=1)
    theirs["offset"] = 1
    out2 = await scenario_store.save(theirs, organization_id=OTHER)

    assert out2["run_code"] != out1["run_code"]
    assert out2["appended"] is False
