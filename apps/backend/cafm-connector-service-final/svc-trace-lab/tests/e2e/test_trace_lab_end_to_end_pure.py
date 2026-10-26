"""The parts of the end-to-end story that need no database, checked on their own so that a failure in the long story
points at Trace Lab and not at the story: the Golden Rule counter comparison finds what it promises to find, the fake
models read the REAL prompts Trace Lab builds, the identity stand-in feeds svc-deepagents' real principal lookup, the
world the story seeds has the shape its assertions assume, and every route Trace Lab mounts is either driven by the
story or named here as not driven."""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

import story_env as env
from src.services import principal as principal_module
from story_env import CANCELLED, N_REV, NOW, ORG_A, ORG_B, PEOPLE, FakeModels, build_world, diff_snapshots
from trace_lab import app as app_module
from trace_lab import grader, judges, proposals

SQL_STORY = Path(__file__).with_name("test_trace_lab_end_to_end_sql.py")


# ── the counter comparison ──────────────────────────────────────────────────────────────────

def snap(rows: dict, ins=0, upd=0, dele=0) -> dict:
    return {"rows": dict(rows), "count": len(rows), "ins": ins, "upd": upd, "del": dele}


def test_identical_snapshots_differ_in_nothing():
    a = {"t": snap({"1": "h1", "2": "h2"}, ins=2)}
    assert diff_snapshots(a, {"t": snap({"1": "h1", "2": "h2"}, ins=2)}) == {}


def test_an_insert_an_update_and_a_delete_are_each_found_with_their_counters():
    before = {"t": snap({"1": "h1", "2": "h2", "3": "h3"}, ins=3), "u": snap({}), "v": snap({"x": "hx"}, ins=1)}
    after = {"t": snap({"1": "h1", "2": "CHANGED", "4": "h4"}, ins=4, upd=1, dele=1), "u": snap({}),
             "v": snap({"x": "hx"}, ins=1)}
    assert diff_snapshots(before, after) == {
        "t": {"added": ["4"], "removed": ["3"], "changed": ["2"], "ins": 1, "upd": 1, "del": 1}}


def test_a_counter_that_moves_with_no_row_change_still_shows():
    """A row written and deleted again leaves the rows alone and the counters moved: that is a write."""
    before = {"t": snap({"1": "h"}, ins=1)}
    after = {"t": snap({"1": "h"}, ins=2, dele=1)}
    assert diff_snapshots(before, after) == {"t": {"added": [], "removed": [], "changed": [], "ins": 1, "upd": 0, "del": 1}}


def test_a_new_or_a_vanished_table_is_a_difference():
    assert diff_snapshots({}, {"t": snap({"1": "h"}, ins=1)})["t"]["added"] == ["1"]
    assert diff_snapshots({"t": snap({"1": "h"}, ins=1)}, {})["t"]["removed"] == ["1"]


# ── the fakes read what Trace Lab really sends ──────────────────────────────────────────────

def test_the_fake_grader_reads_the_real_grader_prompt():
    case = {"evaluation": "objective", "question": "How many floors?", "followups": [], "expected_answer": "22 floors.",
            "expected_values": [], "verified_on": None}
    prompt = grader.prompt_for(case, question="How many floors does Harbour Point have?", answer="Harbour Point has 22 floors.",
                               cards=[], transcript=[])
    models = FakeModels()
    models.grade_rules = [("floors", ["22 floors"])]
    assert models._grade(prompt)["verdict"] == "pass"
    wrong = grader.prompt_for(case, question="How many floors does Harbour Point have?", answer="About 20 floors.", cards=[],
                              transcript=[])
    reply = models._grade(wrong)
    assert reply["verdict"] == "fail" and reply["mismatches"] and len(reply["critique"]) >= grader.MIN_CRITIQUE
    assert [c["answer"] for c in models.grader_calls] == ["Harbour Point has 22 floors.", "About 20 floors."]
    with pytest.raises(AssertionError, match="no grading rule"):
        FakeModels()._grade(prompt)


def test_the_fake_proposer_reads_the_real_cells_prompt_and_quotes_what_it_was_shown():
    evidence = f"ANSWER\nThere are 10 open work orders, {CANCELLED} (ref-ra000)."
    models = FakeModels()
    present = models._cells(proposals.CELLS_USER.format(mode_handles="M1, M2", evidence_text=evidence))["labels"]
    assert [(x["mode"], x["decision"], x["quote"], x["location"]) for x in present] == [
        ("M1", "present", CANCELLED, "answer"), ("M2", "present", CANCELLED, "answer")]
    absent = models._cells(proposals.CELLS_USER.format(mode_handles="M1", evidence_text="ANSWER\nThere are 10 (ref-ra001)."))
    assert [x["decision"] for x in absent["labels"]] == ["absent"] and models.cells_calls[1]["handles"] == ["M1"]
    assert len({x["evidence"] for x in present + absent["labels"]}) >= 2, "distinct evidence per answer (the bulk audit needs it)"


async def test_the_fake_judge_follows_the_text_and_can_be_told_to_miss():
    models = FakeModels()
    fail = await models.ask_judge("m", "prompt", f"The count is 5, {CANCELLED} (ref-j001).")
    ok = await models.ask_judge("m", "prompt", "The count is 5 (ref-j002).")
    assert (fail.failure_present, ok.failure_present, fail.status) == (1, 0, "decided") and fail.usd == models.judge_usd
    models.judge_misses = {"ref-j001", "ref-j002"}
    assert (await models.ask_judge("m", "p", f"{CANCELLED} (ref-j001)")).failure_present == 0
    assert (await models.ask_judge("m", "p", "fine (ref-j002)")).failure_present == 1


# ── identity: the real principal lookup, answered from a table ──────────────────────────────

async def test_the_identity_stand_in_feeds_the_real_principal_lookup(monkeypatch):
    monkeypatch.setattr(principal_module, "_request", env.fake_identity)
    principal_module._cache.clear()
    try:
        p = await principal_module.resolve("Bearer " + PEOPLE["admin_a1"].token)
        assert (str(p.organization_id), p.role, p.is_admin, p.email) == (ORG_A, "admin", True, "ada@alpha.test")
        owner = await principal_module.resolve("Bearer " + PEOPLE["platform"].token)
        assert owner.role == "superadmin" and owner.building_ids is None
        user = await principal_module.resolve("Bearer " + PEOPLE["user_a"].token)
        assert user.is_admin is False
        for bad in ("Bearer not-a-token", None, "Basic abc", "Bearer "):
            with pytest.raises(HTTPException) as err:
                await principal_module.resolve(bad)
            assert err.value.status_code == 401, bad
    finally:
        principal_module._cache.clear()


def test_the_people_are_distinct_and_the_companies_are_two():
    assert len({p.token for p in PEOPLE.values()}) == len({p.user_id for p in PEOPLE.values()}) == len(PEOPLE)
    assert {p.org for p in PEOPLE.values() if p.role == "admin"} == {ORG_A, ORG_B}
    assert [p.name for p in PEOPLE.values() if p.role == "superadmin"] == ["platform"]


# ── the world the story seeds ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def world():
    return build_world()


def test_the_world_has_the_shape_the_stages_assume(world):
    rows = world["rows"]
    assert len(rows) == 5 + N_REV + 6 + 60 + 40 + 8
    assert len({r["tid"] for r in rows}) == len(rows), "turn ids are unique"
    assert len({r["session"] for r in rows}) == len(rows) - 2, "one session each, but the two two-turn chats"
    assert len({r["question"] for r in rows}) == len(rows), "no two questions alike (judge splits group by wording)"
    by = {k: [r for r in rows if r["tid"].startswith(k)] for k in ("turn-ca", "turn-cb", "turn-ra", "turn-rb", "turn-ja",
                                                                   "turn-jb", "turn-ma", "turn-mb")}
    assert [len(by[k]) for k in by] == [3, 2, N_REV, 6, 50, 10, 40, 8]
    assert {r["org"] for k in ("turn-ca", "turn-ra", "turn-ja", "turn-ma") for r in by[k]} == {ORG_A}
    assert {r["org"] for k in ("turn-cb", "turn-rb", "turn-jb", "turn-mb") for r in by[k]} == {ORG_B}
    assert sum(1 for x in world["judge"] if x["bad"]) == 30 == sum(1 for x in world["judge"] if not x["bad"])
    assert sum(1 for x in world["judge"] if x["org"] == ORG_B) == 10
    assert sum(1 for x in world["review_a"] if x["bad"]) == N_REV // 3
    assert sum(1 for x in world["monitor_a"] if x["bad"]) == 10


def test_every_purpose_lives_in_its_own_time_window(world):
    rows = {r["tid"]: r for r in world["rows"]}
    inside = lambda ids, lo, hi: all(lo <= rows[t]["at"] < hi for t in ids)               # noqa: E731
    review = [r["tid"] for r in world["review_a"]] + world["review_b"]
    assert inside(review, NOW - timedelta(days=5), NOW - timedelta(days=2)), "the review window of stage 5"
    mon = [r["tid"] for r in world["monitor_a"]] + world["monitor_b"]
    assert inside(mon, NOW - timedelta(hours=30), NOW - timedelta(minutes=90)), "the monitoring window of stage 8"
    judge = [r["tid"] for r in world["judge"]]
    assert all(rows[t]["at"] < NOW - timedelta(days=5) for t in judge), "the labelled pool is older than the review window"
    live = [t for t in rows if t.startswith(("turn-ca", "turn-cb"))]
    assert all(rows[t]["at"] > NOW - timedelta(hours=2) for t in live), "the live conversations are newer than two hours"
    assert not set(review) & set(mon) and not set(judge) & set(review + mon)


def test_faulty_answers_carry_the_fault_and_every_pooled_answer_a_ref_token(world):
    faulty = {r["tid"] for r in world["review_a"] if r["bad"]} | {r["tid"] for r in world["judge"] if r["bad"]} | {
        r["tid"] for r in world["monitor_a"] if r["bad"]}
    for r in world["rows"]:
        if r["tid"].startswith(("turn-ra", "turn-ja", "turn-jb", "turn-ma")):
            assert (CANCELLED in r["answer"]) == (r["tid"] in faulty), r["tid"]
            assert re.search(r"\(ref-[a-z]+\d+\)", r["answer"]), r["tid"]
        else:
            assert CANCELLED not in r["answer"], r["tid"]


def test_the_credentials_are_where_the_stages_expect_them(world):
    rows = {r["tid"]: r for r in world["rows"]}
    assert env.SECRET_BEARER in rows["turn-ca003"]["answer"] and env.SECRET_TOKEN in rows["turn-ca003"]["tool_in"]["api_key"]
    assert env.TRACE_SECRET in rows["turn-ra005"]["question"] and env.TRACE_SECRET in rows["turn-ra005"]["tool_in"]["api_key"]
    assert sum(1 for r in rows.values() if env.TRACE_SECRET in r["question"]) == 1


# ── every route Trace Lab mounts ────────────────────────────────────────────────────────────

#: Routes the end-to-end story does not call at all, and why. "Driven" means the story calls the route: for several
#: (a set's activate and retire, a batch's withdraw, filing a note, deciding one suggestion, retiring a case) the story
#: calls it only to prove company B is refused, and the happy path is covered by the named unit tests. A new route must
#: be driven by the story or be added here.
NOT_DRIVEN = {
    "/modes/merge": "taxonomy edits other than propose, accept, reject, confirm are covered by their own tests",
    "/modes/{mode_id}/rename": "same", "/modes/{mode_id}/split": "same", "/modes/{mode_id}/retire": "same",
    "/modes/{mode_id}/restore": "same",
    "/notes/{annotation_id}/dispose": "setting a note aside: covered by test_trace_lab_notes_filing_edges_sql",
    "/notes/{annotation_id}/undispose": "same",
    "/proposal-runs/{run_id}/pause": "pause is covered by the proposal run tests",
    "/requirements/{code}/revisions": "register edits other than the seed", "/requirements/{code}/withdraw": "same",
    "/monitor/batches/{batch_id}/resume": "resuming an interrupted batch needs a killed worker",
    "/eval-cases/{case_id}/confirm": "confirming a proposed case: covered by test_trace_lab_eval_cases_sql",
    "/turns/{turn_id}/repeat": "repeat of one answer: covered by test_trace_lab_eval_cases_sql",
    "/lab/me": "step labelling (20 Oct): covered by test_trace_lab_labelling_sql", "/lab/members": "same",
    "/lab/members/{user_id}": "same", "/lab/questions": "same", "/lab/questions/{key:path}": "same",
    "/lab/turns/{turn_id}/step-labels": "same", "/lab/step-labels": "same", "/lab/step-labels.csv": "same",
    "/lab/turns/{turn_id}/step-label-groups": "group labels (21 Oct): covered by test_trace_lab_labelling_sql",
    "/lab/turns/{turn_id}/step-label-groups/{group_id}": "same",
    "/lab/runs": "runs (22 Oct): covered by test_trace_lab_runs_sql", "/lab/runs/sync": "same", "/lab/runs/import": "same",
    "/lab/runs/{code}": "same", "/lab/runs/{code}/questions": "same", "/lab/runs/{code}/labels": "same",
    "/lab/progress": "same", "/lab/q/{code}": "same", "/lab/q/{code}/expected": "same", "/lab/q/{code}/traces": "same", "/lab/q/{code}/candidates": "same",
    "/lab/q/{code}/cells": "same", "/lab/modes": "same", "/lab/modes/{lineage_id}": "same",
    "/lab/platform-members": "same",
}


def _route_patterns() -> dict[str, re.Pattern]:
    """Each route of every router the app mounts under /api/trace-lab, as a pattern its URL would match in the story's
    source (a path parameter matches any word)."""
    out: dict[str, re.Pattern] = {}
    for router in app_module.ROUTERS:
        for route in router.routes:
            template = route.path
            parts = re.split(r"(\{[^}]+\})", template)
            pattern = "".join("[^/\\s?]+" if p.startswith("{") else re.escape(p) for p in parts)
            out[template] = re.compile(pattern + r"(?![\w/{.-])")
    return out


def test_every_route_is_driven_by_the_story_or_named_as_not_driven():
    source = SQL_STORY.read_text()
    uncovered = sorted(t for t, rx in _route_patterns().items() if not rx.search(source))
    assert set(uncovered) <= set(NOT_DRIVEN), f"routes the story neither drives nor names: {sorted(set(uncovered) - set(NOT_DRIVEN))}"
    stale = sorted(t for t in NOT_DRIVEN if t not in uncovered)
    assert not stale, f"named as not driven, but the story does drive them: {stale}"
