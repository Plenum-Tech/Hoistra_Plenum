"""A shorter correction loop (Bala, 6 Nov 2026) on a throwaway Postgres.

Ask -> annotate -> the agent proposes -> a person approves. Approving a proposed failure mode now also accepts the labels
the agent suggested for it and adds its rule to SPEC.md as a new approved version, in the same click (before, a person had
to accept each label in Labels, then draft, propose and approve a specification version). A suggested label on a mode
that is already confirmed is accepted or rejected from the Loop page. A failure mode a person added by hand gets its rule
with one click. Expected answers are added from the Loop page, and a regression question follows its source question's
expected answer when that one is newer, so the order of the steps no longer matters.

The logic is unchanged: the agent only proposes, a person approves, a rule is still a reviewed specification version,
and expected answers still name a rule of the approved specification and the records they were read from.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labelling, loop, runs
from trace_lab.review_errors import Forbidden, Invalid
from trace_lab_m2_fixtures import principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
NOW = datetime.now(timezone.utc).replace(microsecond=0)


async def _turn(main, tid, *, question, uid, answer="Northgate and SafeLift are due for renewal."):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, 's-1', CAST(:org AS uuid), CAST(:uid AS uuid), 'ann@a.test', :q, :a, 'single:compliance',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "org": ORG, "uid": uid, "q": question, "a": answer, "at": NOW - timedelta(hours=1)})


async def _people():
    boss = await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)
    admin = await labelling.access(principal(ORG, "admin", email="adm@a.test"), None, all_companies_ok=True)
    return boss, admin


async def _questions(main, lab, *questions):
    uid = str(uuid.uuid4())                                  # one person, one day: one run
    for i, q in enumerate(questions, start=1):
        await _turn(main, f"turn-s{i}", question=q, uid=uid)
    await runs.sync_live(None)
    boss, _ = await _people()
    run = (await runs.list_runs(boss))["runs"][0]["code"]
    by_text = {q["question"]: q["code"] for q in (await runs.list_questions(boss, run))["questions"]}
    return [by_text[q] for q in questions]


async def _agent_mode(lab, name, definition, lineage=None):
    async with lab.begin() as conn:
        return (await conn.execute(text(
            "INSERT INTO tracelab.lab_failure_modes (lineage_id, name, definition, created_by_email) "
            "VALUES (CAST(:lin AS uuid), :n, :d, 'trace-lab-agent') RETURNING id::text"),
            {"lin": lineage, "n": name, "d": definition})).scalar_one()


async def _agent_label(lab, code, lineage):
    async with lab.begin() as conn:
        return (await conn.execute(text(
            "INSERT INTO tracelab.lab_label_cells (question_id, mode_lineage_id, value, author_email, author_role, source) "
            "VALUES (tracelab.question_id(:c), CAST(:m AS uuid), 'positive', 'trace-lab-agent', 'coding agent', "
            "'coding_agent') RETURNING id::text"), {"c": code, "m": lineage})).scalar_one()


async def _positives(lab, lineage):
    async with lab.connect() as conn:
        return (await loop._positives(conn, lineage)).get(lineage, [])


async def test_approving_a_proposed_mode_adds_its_rule_and_accepts_its_labels(main_scratch, lab_scratch):  # noqa: F811
    (code,) = await _questions(main_scratch, lab_scratch, "Which certificates are due for renewal?")
    boss, admin = await _people()
    proposed = await _agent_mode(lab_scratch, "stale_status_field_trusted",
                                 "Filters on a stored status a scan may not have updated.")
    await _agent_label(lab_scratch, code, proposed)          # a new mode's lineage is its own id
    assert await _positives(lab_scratch, proposed) == [], "a suggestion is not a label"

    with pytest.raises(Forbidden):
        await loop.decide(admin, "failure_mode", proposed, decision="approved")
    out = await loop.decide(boss, "failure_mode", proposed, decision="approved")
    assert out["labels_accepted"] == 1 and out["spec"]["code"] == "S-1"
    assert len(await _positives(lab_scratch, proposed)) == 1, "the approver's own label now counts"

    state = await loop.spec_state(boss)
    rule = state["approved"]["rules"][0]
    assert (rule["code"], rule["mode_lineage_id"]) == ("S-1", proposed)
    assert rule["text"].startswith("An answer must not show this failure: Filters on a stored status")
    assert state["approved"]["review"]["by"] == "boss@plenum.test", "a reviewed version, approved by the approver"
    assert state["pending"] == [], "nothing left to draft, propose or approve"

    # A change to the confirmed mode keeps its rule: no second rule, no new version.
    change = await _agent_mode(lab_scratch, "stale_status_field_trusted", "Trusts a stored status.", lineage=proposed)
    again = await loop.decide(boss, "failure_mode", change, decision="approved")
    assert again["spec"] is None
    assert (await loop.spec_state(boss))["approved"]["version"] == state["approved"]["version"]


async def test_a_suggested_label_on_a_confirmed_mode_is_decided_from_the_loop(main_scratch, lab_scratch):  # noqa: F811
    yes, no = await _questions(main_scratch, lab_scratch, "Which certificates are expiring soon?",
                               "Which certificates expire this month?")
    boss, admin = await _people()
    mode = await runs.save_mode(boss, name="stale_status_field_trusted", definition="Trusts a stored status.")
    lin = mode["lineage_id"]
    a = await _agent_label(lab_scratch, yes, lin)
    b = await _agent_label(lab_scratch, no, lin)
    waiting = {x["id"]: x for x in (await loop.proposals(boss))["labels"]}
    assert set(waiting) == {a, b} and waiting[a]["question"] == yes and waiting[a]["mode_name"] == "stale_status_field_trusted"

    await loop.decide(admin, "label", a, decision="approved")       # a company admin labels their own questions
    await loop.decide(boss, "label", b, decision="rejected")
    assert len(await _positives(lab_scratch, lin)) == 1
    assert [q["code"] for q in (await loop.overview(boss))["modes"][0]["questions"]] == [yes], "only the accepted one"
    assert (await loop.proposals(boss))["labels"] == [], "a decided suggestion is not shown again"
    with pytest.raises(Invalid):
        await loop.decide(boss, "label", a, decision="approved")


async def test_a_mode_added_by_hand_gets_its_rule_in_one_click(main_scratch, lab_scratch):  # noqa: F811
    boss, admin = await _people()
    mode = await runs.save_mode(boss, name="grouping_read_as_filter", definition="Reads a grouping as a filter.")
    with pytest.raises(Forbidden):
        await loop.add_rule(admin, mode["lineage_id"])
    out = await loop.add_rule(boss, mode["lineage_id"])
    assert out["code"] == "S-1" and (await loop.spec_state(boss))["approved"]["version"] == out["version"]
    with pytest.raises(Invalid) as e:
        await loop.add_rule(boss, mode["lineage_id"])
    assert e.value.reason == "already_in_spec"


async def test_the_loop_page_lists_each_modes_questions_and_a_regression_follows_the_newer_expected_answer(
        main_scratch, lab_scratch):  # noqa: F811
    (code,) = await _questions(main_scratch, lab_scratch, "Which certificates are due for renewal?")
    boss, admin = await _people()
    mode = await runs.save_mode(boss, name="stale_status_field_trusted", definition="Trusts a stored status.")
    await runs.set_cell(admin, code, mode=mode["lineage_id"], value="positive")
    await loop.add_rule(boss, mode["lineage_id"])

    row = (await loop.overview(boss))["modes"][0]
    # 10 Nov 2026: each question also carries its current expected answer, for the Loop page's Edit (none yet here).
    assert row["questions"] == [{"code": code, "text": "Which certificates are due for renewal?", "has_expected": False,
                                 "company": ORG, "expected": None}]
    assert row["companies"] == [ORG]

    made = await loop.make_regression(boss, mode["lineage_id"], company=ORG)
    assert made["with_expected"] == 0, "made before the expected answer was written"
    await runs.set_expected(admin, code, expected="EMCOR UK (21 Dec 2026), Mitie (28 Dec 2026) are due for renewal.",
                            evidence={"spec_rule": "S-1", "data": "register, 31-60 days", "values": ["EMCOR UK", "Mitie"]})
    assert (await loop.overview(boss))["modes"][0]["questions"][0]["has_expected"] is True
    cmp = await loop.compare(boss, made["run"])
    q = cmp["questions"][0]
    assert q["expected"].startswith("EMCOR UK"), "the regression question follows its source's newer expected answer"
    assert q["before"]["check"]["status"] == "mismatch" and q["verdict"] == "not_rerun"
