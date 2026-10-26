"""The confirm gate (spec 5.1): boundary, evaluator type, an active requirement, 3 singly-approved positives,
3 singly-approved close negatives or a waiver. created_from takes only wrong verdict versions. Only the platform team
may confirm. The pure contract tests need no database; the rest need the scratch database."""
from __future__ import annotations

import pytest

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, requirements, taxonomy
from trace_lab.review_errors import Forbidden, Invalid

BASE = {"boundary": "", "evaluator_type": None, "requirement_ids": [], "positive_turn_ids": [],
        "close_negative_turn_ids": [], "close_negatives_waiver": None}


def _cell(state, approval="single"):
    return labels.Cell(state, "l-" + state, "human_structured_label", approval, None, approval == "single")


def _complete(**over):
    m = dict(BASE, boundary="Not when the job is on hold.", evaluator_type="code", requirement_ids=["WO-1"],
             positive_turn_ids=["p1", "p2", "p3"], close_negative_turn_ids=["n1", "n2", "n3"])
    m.update(over)
    return m


CELLS = {"p1": _cell("present"), "p2": _cell("present"), "p3": _cell("present"),
         "n1": _cell("absent"), "n2": _cell("absent"), "n3": _cell("absent")}
ACTIVE = {"WO-1": {"revision": 1, "id": "r1", "created_at": None}}


def test_each_missing_item_is_listed():
    assert taxonomy.contract_missing(BASE, {}, {}) == list(taxonomy.CONTRACT_ITEMS)


def test_a_complete_contract_is_ready():
    assert taxonomy.contract_missing(_complete(), CELLS, ACTIVE) == []


def test_positives_need_single_approval():
    cells = dict(CELLS, p3=_cell("present", approval="bulk"))
    assert taxonomy.contract_missing(_complete(), cells, ACTIVE) == ["positives"]


def test_the_waiver_stands_in_for_close_negatives():
    mode = _complete(close_negative_turn_ids=[], close_negatives_waiver="Only two close answers exist so far.")
    assert taxonomy.contract_missing(mode, CELLS, ACTIVE) == []


def test_an_inactive_requirement_does_not_count():
    assert taxonomy.contract_missing(_complete(), CELLS, {}) == ["requirement"]


DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}


@requires_scratch
async def test_created_from_accepts_only_wrong_verdict_versions(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-d000000000000001")
    async with lab_scratch.begin() as conn:
        right = await fx.add_note(conn, "turn-d000000000000001", verdict="right")
        gone = await fx.add_note(conn, "turn-d000000000000001", reviewer=fx.admin(n=2), tombstone=True)
    for bad in ([right], [gone], ["not-a-uuid"], []):
        with pytest.raises(Invalid) as e:
            await taxonomy.create(fx.owner(), DRAFT, created_from=bad)
        assert e.value.reason == "not_a_wrong_note"


@requires_scratch
async def test_confirm_is_refused_until_the_contract_is_complete(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-d000000000000002")
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, "turn-d000000000000002")
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=[note]))["mode"]
    with pytest.raises(Invalid) as e:
        await taxonomy.edit(fx.owner(), mode["id"], {"status": "confirmed"}, base_version=mode["version"])
    assert e.value.reason == "contract_incomplete"
    assert e.value.extra["missing"] == list(taxonomy.CONTRACT_ITEMS)


@requires_scratch
async def test_a_complete_contract_confirms_and_records_the_requirement_revision(lab_scratch, main_scratch):
    await requirements.create(fx.owner(), area="WO", statement="A work-order count excludes cancelled jobs.",
                              level="technical", implementations=[], motivating_turn_ids=[])
    turns = [f"turn-d1{i:014d}" for i in range(6)]
    async with main_scratch.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, turns[0])
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=[note]))["mode"]    # turns[0]: open-code present
    for t in turns[1:3]:
        await labels.set_label(fx.owner(), None, t, mode["id"], present=True, base=None)
    for t in turns[3:]:
        await labels.set_label(fx.owner(), None, t, mode["id"], present=False, base=None)
    out = await taxonomy.edit(fx.owner(), mode["id"], {
        "boundary": "Not when the job is on hold.", "evaluator_type": "code", "requirement_ids": ["WO-1"],
        "positive_turn_ids": turns[:3], "close_negative_turn_ids": turns[3:], "status": "confirmed"},
        base_version=mode["version"])
    assert out["mode"]["status"] == "confirmed" and out["mode"]["confirmed_at"]
    detail = (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["events"][0]["detail"]
    assert detail["status"] == ["candidate", "confirmed"] and detail["requirement_revisions"] == {"WO-1": 1}
    ready = (await taxonomy.get_mode(fx.owner(), None, mode["id"]))["contract"]
    assert ready == {"ready": True, "missing": []}


@requires_scratch
async def test_a_company_admin_cannot_confirm_or_otherwise_move_a_mode(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
    with pytest.raises(Forbidden) as e:
        await taxonomy.edit(fx.admin(), mid, {"status": "confirmed"}, base_version=1)
    assert e.value.reason == "platform_only"
    mode = await taxonomy.get_mode(fx.owner(), None, mid)
    assert mode["mode"]["status"] == "candidate" and mode["mode"]["version"] == 1
