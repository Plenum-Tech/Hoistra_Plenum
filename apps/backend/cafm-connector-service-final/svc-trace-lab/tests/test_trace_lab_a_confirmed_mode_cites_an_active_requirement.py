"""A confirmed mode cites at least one active requirement; a revision supersedes the active row and flags the
modes confirmed against the older revision; a withdrawn code flags them too (spec 8.1). Needs the scratch database."""
from __future__ import annotations

import pytest

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, requirements, taxonomy
from trace_lab.review_errors import Invalid

pytestmark = requires_scratch


async def _confirmable(lab, main, *, requirement_ids):
    turns = [f"turn-e0{i:014d}" for i in range(6)]
    async with main.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
    async with lab.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", boundary="Not when the job is on hold.",
                                evaluator_type="code")
    for t in turns[:3]:
        await labels.set_label(fx.owner(), None, t, mid, present=True, base=None)
    for t in turns[3:]:
        await labels.set_label(fx.owner(), None, t, mid, present=False, base=None)
    return mid, {"requirement_ids": requirement_ids, "positive_turn_ids": turns[:3],
                 "close_negative_turn_ids": turns[3:], "status": "confirmed"}


async def test_the_confirm_gate_needs_an_active_requirement(lab_scratch, main_scratch):
    mid, changes = await _confirmable(lab_scratch, main_scratch, requirement_ids=[])
    with pytest.raises(Invalid) as e:
        await taxonomy.edit(fx.owner(), mid, changes, base_version=1)
    assert e.value.extra["missing"] == ["requirement"]


async def test_a_revision_flags_modes_confirmed_against_the_old_one(lab_scratch, main_scratch):
    await requirements.create(fx.owner(), area="WO", statement="A work-order count excludes cancelled jobs.",
                              level="technical", implementations=[], motivating_turn_ids=[])
    mid, changes = await _confirmable(lab_scratch, main_scratch, requirement_ids=["WO-1"])
    await taxonomy.edit(fx.owner(), mid, changes, base_version=1)
    out = await requirements.revise(fx.owner(), "WO-1",
                                    statement="A work-order count excludes cancelled and on-hold jobs.",
                                    implementations=[], motivating_turn_ids=[], note="on-hold added")
    assert [m["id"] for m in out["flagged_modes"]] == [mid]
    mode = next(m for m in await taxonomy.list_modes(fx.owner(), None) if m["id"] == mid)
    assert mode["requirement_flags"][0]["kind"] == "revised"
    assert mode["requirement_flags"][0]["active_revision"] == 2


async def test_a_withdrawn_code_flags_its_modes(lab_scratch, main_scratch):
    await requirements.create(fx.owner(), area="WO", statement="A work-order count excludes cancelled jobs.",
                              level="technical", implementations=[], motivating_turn_ids=[])
    mid, changes = await _confirmable(lab_scratch, main_scratch, requirement_ids=["WO-1"])
    await taxonomy.edit(fx.owner(), mid, changes, base_version=1)
    await requirements.withdraw(fx.owner(), "WO-1", note="replaced")
    mode = next(m for m in await taxonomy.list_modes(fx.owner(), None) if m["id"] == mid)
    assert mode["requirement_flags"][0]["kind"] == "not_active"
    detail = (await taxonomy.get_mode(fx.owner(), None, mid))["mode"]["requirement_flags"]
    assert detail[0]["kind"] == "not_active"
