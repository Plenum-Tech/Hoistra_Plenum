"""Human verdicts on golden answers are the labels grader validation measures the grader against: right is a pass,
wrong is a fail, one per answer per reviewer with the latest winning, a cleared verdict is no label, reviewers who
disagree leave the answer out, and only golden answers count (a verdict on live traffic is not a label). Before any
human has judged, validation counts none. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from golden_seed import seed_result, seed_run, seed_set
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, grader_validation as gvl, review_labels
from trace_lab.annotations import Anchor
from trace_lab_m2_fixtures import add_turn, principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
V = "abcdef012345"
WRONG_ON_ANSWER = Anchor(None, "answer", "759")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(gvl, "_SOURCES", [])
    gvl.clear_cache()
    review_labels.register()


async def _golden_answers(lab, org, n, *, grader):
    """n graded golden answers of one run of one judgment set; grader[i] is the grader's verdict on answer i."""
    ids = []
    async with lab.begin() as conn:
        s = await seed_set(conn, org, cases=tuple((f"c{i}", ()) for i in range(n)), evaluation="judgment")
        run = await seed_run(conn, org, s["set_id"])
        for i in range(n):
            tid = f"turn-g{uuid.uuid4().hex[:14]}"
            await seed_result(conn, org, run, s["cases"][f"c{i}"], f"c{i}", turn_id=tid,
                              grade={"verdict": grader[i], "grader_version": V, "source": "grader"})
            turn = {"turn_id": tid, "session_id": f"golden-{run[:8]}-c{i}-r1", "organization_id": org,
                    "user_id": str(uuid.uuid4()), "question": "q", "answer": "759 work orders.", "ok": True}
            await conn.execute(text("INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
                                    "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
                               {"t": tid, "o": org, "j": json.dumps(turn)})
            ids.append(tid)
    return ids


async def _judge(who, org, tid, verdict, base=None):
    if verdict == "right":
        return await annotations.save_verdict(who, org, tid, verdict="right", note=None, anchor=None, base=base)
    return await annotations.save_verdict(who, org, tid, verdict="wrong", note="Off.", anchor=WRONG_ON_ANSWER, base=base)


async def test_before_any_human_has_judged_there_are_no_labels_and_validation_counts_none(main_scratch, lab_scratch):  # noqa: F811
    ids = await _golden_answers(lab_scratch, ORG, 2, grader=["pass", "fail"])
    assert await gvl.human_labels(ids) == {}
    v = await gvl.validation(grader_version=V)
    assert (v["graded"], v["labelled"], v["pooled"]["n"], v["passes"]) == (2, 0, 0, False)


async def test_two_humans_verdicts_become_pass_and_fail_labels_that_validation_counts(main_scratch, lab_scratch):  # noqa: F811
    t = await _golden_answers(lab_scratch, ORG, 4, grader=["pass", "fail", "pass", "unsure"])
    ada, bo = principal(ORG), principal(ORG)
    await _judge(ada, ORG, t[0], "right")          # grader pass, human pass  -> true positive
    await _judge(bo, ORG, t[0], "right")           # two humans agree
    await _judge(ada, ORG, t[1], "wrong")          # grader fail, human fail  -> true negative
    await _judge(bo, ORG, t[2], "wrong")           # grader pass, human fail  -> false positive
    await _judge(bo, ORG, t[3], "right")           # grader unsure: reported, never agreement
    labels = await gvl.human_labels(t)
    assert labels == {t[0]: "pass", t[1]: "fail", t[2]: "fail", t[3]: "pass"}
    v = await gvl.validation(grader_version=V)
    p = v["pooled"]
    assert (v["graded"], v["labelled"]) == (4, 4)
    assert (p["n"], p["n_pass"], p["n_fail"], p["tp"], p["fn"], p["tn"], p["fp"], p["unsure"]) == (4, 2, 2, 1, 0, 1, 1, 1)
    assert [c["organization_id"] for c in v["by_company"]] == [ORG] and v["passes"] is False


async def test_the_latest_verdict_of_a_reviewer_wins_and_a_cleared_one_is_no_label(main_scratch, lab_scratch):  # noqa: F811
    t = await _golden_answers(lab_scratch, ORG, 2, grader=["pass", "pass"])
    ada = principal(ORG)
    first = await _judge(ada, ORG, t[0], "right")
    await _judge(ada, ORG, t[0], "wrong", base=first["id"])               # she changed her mind
    cleared = await _judge(ada, ORG, t[1], "right")
    await annotations.delete_verdict(ada, ORG, t[1], base=cleared["id"])  # and cleared this one
    assert await gvl.human_labels(t) == {t[0]: "fail"}


async def test_reviewers_who_disagree_leave_the_answer_out(main_scratch, lab_scratch):  # noqa: F811
    t = await _golden_answers(lab_scratch, ORG, 1, grader=["pass"])
    await _judge(principal(ORG), ORG, t[0], "right")
    bo = principal(ORG)
    await _judge(bo, ORG, t[0], "wrong")
    assert await gvl.human_labels(t) == {}


async def test_a_verdict_on_live_traffic_is_not_a_label(main_scratch, lab_scratch):  # noqa: F811
    live = await add_turn(main_scratch, 1, ORG)
    await _judge(principal(ORG), ORG, live, "right")
    assert await review_labels.golden_human_labels([live]) == {}


async def test_labels_pool_across_companies_and_validation_reports_each(main_scratch, lab_scratch):  # noqa: F811
    a = await _golden_answers(lab_scratch, ORG, 1, grader=["pass"])
    b = await _golden_answers(lab_scratch, OTHER, 1, grader=["fail"])
    await _judge(principal(ORG), ORG, a[0], "right")
    await _judge(principal(OTHER), OTHER, b[0], "wrong")
    v = await gvl.validation(grader_version=V)
    assert sorted(c["organization_id"] for c in v["by_company"]) == sorted([ORG, OTHER])
    assert (v["pooled"]["tp"], v["pooled"]["tn"], v["pooled"]["n"]) == (1, 1, 2)


async def test_an_empty_or_odd_question_costs_nothing(main_scratch, lab_scratch):  # noqa: F811
    assert await review_labels.golden_human_labels([]) == {}
    assert await review_labels.golden_human_labels(["", None, "turn-nothing"]) == {}
