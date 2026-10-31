"""golden_views on a throwaway Postgres (tracelab_scratch): run summaries newest first inside one company, run detail
and result detail, the expected outcome closed until this reviewer reveals it (and recorded once), judgment results
read unvalidated until grader validation passes, and two runs compared. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from golden_seed import seed_result, seed_run, seed_set
from src.services.principal import Principal
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import golden_store, golden_views, grader_validation

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
NOW = datetime.now(timezone.utc).replace(microsecond=0)
STARTER = str(uuid.uuid4())
GRADE = {"verdict": "fail", "critique": "It counted cancelled jobs.", "mismatches": ["759 not 812"],
         "value_check": {"status": "mismatch", "items": []}, "grader_version": "v1", "reading": "fail",
         "validated_outcome": "fail", "evaluation": "objective", "usd": 0.02, "source": "grader"}


def reviewer(org=ORG, user=None):
    return Principal(user_id=user or uuid.uuid4(), email="reviewer@example.com", organization_id=uuid.UUID(org),
                     role="admin", can_ingest=True, building_ids=None)


@pytest.fixture(autouse=True)
def nothing_validated(monkeypatch):
    async def never(version, **kw):
        return False

    monkeypatch.setattr(grader_validation, "passes", never)


async def world(engine):
    async with engine.begin() as conn:
        s = await seed_set(conn, ORG, cases=(("1", ()), ("2", ("And how many were reactive?",))))
        run = await seed_run(conn, ORG, s["set_id"], k=1, params={"chat_model": "gpt-5.6-terra"}, started_by=STARTER)
        a_id, a_turn = await seed_result(conn, ORG, run, s["cases"]["1"], "1", outcome="fail", grade=GRADE,
                                         refused=[{"label": "Saving a record", "path": "/api/records"}])
        _, b0 = await seed_result(conn, ORG, run, s["cases"]["2"], "2", outcome="pending", grade=None)
        b_id, b1 = await seed_result(conn, ORG, run, s["cases"]["2"], "2", msg_idx=1, outcome="pass",
                                     grade=dict(GRADE, verdict="pass", reading="pass", validated_outcome="pass"))
    return {"set": s, "run": run, "a": (a_id, a_turn), "b": (b_id, b0, b1)}


async def test_run_summaries_are_newest_first_inside_one_company(lab_scratch):
    w = await world(lab_scratch)
    async with lab_scratch.begin() as conn:
        older = await seed_run(conn, ORG, w["set"]["set_id"], started_at=NOW - timedelta(days=3))
        o = await seed_set(conn, OTHER)
        await seed_run(conn, OTHER, o["set_id"])
    runs = await golden_views.run_summaries(ORG)
    assert [r["run_id"] for r in runs] == [w["run"], older]
    top = runs[0]
    assert top["pass_rate"]["passes"] == 1 and top["pass_rate"]["graded"] == 2 and top["outcomes"]["fail"] == 1
    assert top["refused"] == 1 and top["models"] == ["gpt-5.6-terra"] and top["kind"] == "golden"
    assert [r["run_id"] for r in await golden_views.run_summaries(ORG, since=NOW - timedelta(days=1))] == [w["run"]]
    assert [r["run_id"] for r in await golden_views.run_summaries(ORG, limit=1)] == [w["run"]]
    assert await golden_views.run_summaries(str(uuid.uuid4())) == []
    me = reviewer(user=uuid.UUID(STARTER))
    assert [r["mine"] for r in await golden_views.run_summaries(ORG, reviewer=me)] == [True, False]


async def test_run_detail_and_result_detail_stay_blind_until_revealed(lab_scratch):
    w = await world(lab_scratch)
    me = reviewer()
    detail = await golden_views.run_detail(ORG, w["run"], reviewer=me)
    assert [c["case_key"] for c in detail["cases"]] == ["1", "2"] and detail["caveats"] == list(golden_views.FIDELITY_CAVEATS)
    one, two = detail["cases"]
    assert one["reps"][0]["outcome"] == "fail" and one["reps"][0]["refused"] == ["Saving a record"]
    assert two["reps"][0]["final_turn_id"] == w["b"][2] and two["reps"][0]["turn_ids"] == [w["b"][1], w["b"][2]]
    assert detail["params"] == {"chat_model": "gpt-5.6-terra"} and "It counted" not in str(detail)
    res = await golden_views.result_detail(ORG, w["run"], w["a"][0], reviewer=me)
    assert (res["outcome"], res["revealed"], res["messages"][0]["refused"]) == ("fail", False, ["Saving a record"])
    assert "result" not in res and "case" not in res and "/api/records" not in str(res)
    await golden_views.reveal(ORG, w["a"][1], reviewer=me)
    res = await golden_views.result_detail(ORG, w["run"], w["a"][0], reviewer=me)
    assert res["revealed"] is True and res["result"]["grade"]["critique"] == "It counted cancelled jobs."
    assert res["case"]["expected_answer"] == "The right answer to 1." and res["case"]["notes"]
    assert (await golden_views.result_detail(ORG, w["run"], w["a"][0], reviewer=reviewer()))["revealed"] is False
    for call in (golden_views.run_detail(OTHER, w["run"]), golden_views.result_detail(OTHER, w["run"], w["a"][0], reviewer=me),
                 golden_views.result_detail(ORG, w["run"], str(uuid.uuid4()), reviewer=me),
                 golden_views.run_detail(ORG, "not-a-uuid")):
        with pytest.raises(golden_store.NotFound):
            await call


async def test_the_expected_outcome_is_closed_until_this_reviewer_reveals_it(lab_scratch):
    w = await world(lab_scratch)
    me = reviewer()
    first = await golden_views.expected_outcome(ORG, w["b"][1], reviewer=me)          # the first message of case 2
    assert first["available"] is True and first["revealed"] is False
    g = first["golden"]
    assert (g["case_key"], g["msg_idx"], g["is_final"], g["messages_total"], g["complete"]) == ("2", 0, False, 2, True)
    assert "case" not in g and "result" not in g
    opened = await golden_views.reveal(ORG, w["b"][1], reviewer=me)
    g = opened["golden"]
    assert opened["revealed"] is True and g["case"]["followups"] == ["And how many were reactive?"]
    assert g["result"]["outcome"] == "pass" and g["result"]["grade"]["verdict"] == "pass" and g["revealed_at"]
    again = await golden_views.expected_outcome(ORG, w["b"][2], reviewer=me)          # the same rep's last message
    assert again["revealed"] is True
    await golden_views.reveal(ORG, w["b"][1], reviewer=me)                             # recorded once
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.golden_reveals"))).scalar_one() == 1
    assert (await golden_views.expected_outcome(ORG, "turn-live-0001", reviewer=me)) == \
        {"ok": True, "available": False, "reason": "not_a_golden_turn"}
    assert (await golden_views.expected_outcome(OTHER, w["b"][1], reviewer=reviewer(OTHER)))["available"] is False
    assert (await golden_views.reveal(OTHER, w["b"][1], reviewer=reviewer(OTHER)))["available"] is False


async def test_a_judgment_result_reads_unvalidated_until_validation_passes(lab_scratch, monkeypatch):
    async with lab_scratch.begin() as conn:
        s = await seed_set(conn, ORG, evaluation="judgment")
        run = await seed_run(conn, ORG, s["set_id"])
        await seed_result(conn, ORG, run, s["cases"]["1"], "1", outcome="unsure",
                          grade=dict(GRADE, reading="needs_person", validated_outcome="fail", evaluation="judgment"))
    detail = await golden_views.run_detail(ORG, run)
    assert detail["cases"][0]["reps"][0]["outcome"] == "unvalidated" and detail["run"]["pass_rate"]["graded"] == 0
    assert detail["run"]["grader_validated"] is False

    async def v1_passes(version, **kw):
        return version == "v1"

    monkeypatch.setattr(grader_validation, "passes", v1_passes)
    detail = await golden_views.run_detail(ORG, run)
    assert detail["cases"][0]["reps"][0]["outcome"] == "fail" and detail["run"]["pass_rate"]["graded"] == 1
    assert detail["run"]["grader_validated"] is True


async def test_two_runs_compared(lab_scratch):
    async with lab_scratch.begin() as conn:
        s = await seed_set(conn, ORG, cases=(("1", ()), ("2", ())))
        base = await seed_run(conn, ORG, s["set_id"], started_at=NOW - timedelta(days=1), params={"git_sha": "abc"})
        head = await seed_run(conn, ORG, s["set_id"], params={"git_sha": "def"})
        for run, one, two in ((base, "fail", "pass"), (head, "pass", "pass")):
            await seed_result(conn, ORG, run, s["cases"]["1"], "1", outcome=one, grade=dict(GRADE, verdict=one))
            await seed_result(conn, ORG, run, s["cases"]["2"], "2", outcome=two, grade=dict(GRADE, verdict=two))
        o = await seed_set(conn, OTHER)
        theirs = await seed_run(conn, OTHER, o["set_id"])
    out = await golden_views.compare(ORG, base, head)
    assert [f["case_key"] for f in out["flips"]["fixed"]] == ["1"] and out["still_passing"] == 1
    assert out["comparable"] is True and out["warnings"] == ["The two runs ran on different builds of the assistant."]
    assert out["params_diff"] == {"git_sha": ["abc", "def"]} and out["pass_rate"]["delta"] == 0.5
    with pytest.raises(golden_store.NotFound):
        await golden_views.compare(ORG, base, theirs)
