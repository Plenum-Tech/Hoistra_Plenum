"""Merge retires the sources into a live target without rewriting labels; split retires the parent into 2-5
candidate children and writes only the owner's assignments (spec 5.2, 5.4). Needs the scratch database."""
from __future__ import annotations

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, taxonomy
from trace_lab.review_errors import Conflict, Forbidden, Invalid, Stale

pytestmark = requires_scratch


def _draft(name):
    return {"name": name, "definition": "Present when the answer counts cancelled jobs as open work.",
            "boundary": "", "level": "technical", "agent": "wo_engine"}


async def _cell(turn_id, mode_id):
    cells = (await labels.cells_for_turn(fx.owner(), None, turn_id))["cells"]
    return next(c for c in cells if c["mode_id"] == mode_id)


async def test_merge_into_an_existing_mode_keeps_the_labels_and_resolves_through_the_part(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-h000000000000001")
    async with lab_scratch.begin() as conn:
        target = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        source = await fx.add_mode(conn, "includes_cancelled_jobs")
        await fx.add_label(conn, "turn-h000000000000001", source, present=True)
        run = await fx.add_run(conn, "cells", mode_id=source)
        await fx.add_proposal(conn, run, turn_id="turn-h000000000000001", mode_id=target)
    out = await taxonomy.merge(fx.owner(), [source], into=target, new=None, base_versions={source: 1, target: 1})
    assert out["mode"]["id"] == target and out["mode"]["absorbed_at"] and out["closed_proposals"] == 1
    graph_src = (await taxonomy.get_mode(fx.owner(), None, source))["mode"]
    assert graph_src["status"] == "retired" and graph_src["merged_into"] == target
    cell = await _cell("turn-h000000000000001", target)
    assert (cell["state"], cell["via"]) == ("present", source)
    async with lab_scratch.connect() as conn:                         # nothing was rewritten
        n = (await conn.execute(text("SELECT count(*) FROM tracelab.labels"))).scalar()
        actions = (await conn.execute(text("SELECT mode_id = CAST(:t AS uuid), action FROM tracelab.mode_events "
                                           "ORDER BY seq"), {"t": target})).all()
    assert n == 1 and sorted(actions) == [(False, "merge"), (True, "merge")]


async def test_merge_into_a_new_mode_needs_two_sources(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        a = await fx.add_mode(conn, "includes_cancelled_jobs")
        b = await fx.add_mode(conn, "counts_closed_jobs_as_open")
    with pytest.raises(Invalid):
        await taxonomy.merge(fx.owner(), [a], into=None, new=_draft("counts_inactive_jobs"), base_versions={a: 1})
    out = await taxonomy.merge(fx.owner(), [a, b], into=None, new=_draft("includes_cancelled_jobs"),
                               base_versions={a: 1, b: 1})
    assert out["mode"]["name"] == "includes_cancelled_jobs" and out["mode"]["status"] == "candidate"
    assert len(out["mode"]["created_from"]) == 2


async def test_a_merge_names_exactly_one_target_and_never_the_source_itself(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        a = await fx.add_mode(conn, "includes_cancelled_jobs")
        b = await fx.add_mode(conn, "counts_closed_jobs_as_open")
    for kwargs in (dict(into=None, new=None), dict(into=b, new=_draft("counts_inactive_jobs")), dict(into=a, new=None)):
        with pytest.raises(Invalid):
            await taxonomy.merge(fx.owner(), [a], base_versions={a: 1, b: 1}, **kwargs)
    with pytest.raises(Invalid):
        await taxonomy.merge(fx.owner(), [], into=b, new=None, base_versions={b: 1})


async def test_a_frozen_or_stale_mode_cannot_be_merged(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        frozen = await fx.add_mode(conn, "frozen_mode_one", status="frozen")
        live = await fx.add_mode(conn, "includes_cancelled_jobs")
    with pytest.raises(Conflict) as e:
        await taxonomy.merge(fx.owner(), [frozen], into=live, new=None, base_versions={frozen: 1, live: 1})
    assert e.value.reason == "mode_frozen"
    async with lab_scratch.begin() as conn:
        other = await fx.add_mode(conn, "counts_closed_jobs_as_open")
    with pytest.raises(Stale):
        await taxonomy.merge(fx.owner(), [other], into=live, new=None, base_versions={other: 1, live: 7})


async def test_a_proposed_mode_cannot_be_merged_into_or_out_of(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        proposed = await fx.add_mode(conn, "proposed_mode_one", status="proposed")
        live = await fx.add_mode(conn, "includes_cancelled_jobs")
    for sources, into in (([proposed], live), ([live], proposed)):
        with pytest.raises(Conflict) as e:
            await taxonomy.merge(fx.owner(), sources, into=into, new=None, base_versions={proposed: 1, live: 1})
        assert e.value.reason == "mode_proposed"


async def test_a_merged_source_cannot_be_restored(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        target = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        source = await fx.add_mode(conn, "includes_cancelled_jobs")
    await taxonomy.merge(fx.owner(), [source], into=target, new=None, base_versions={source: 1, target: 1})
    with pytest.raises(Conflict) as e:
        await taxonomy.restore(fx.owner(), source, base_version=2)
    assert e.value.reason == "merged" and e.value.extra["live_mode_id"] == target


async def test_split_writes_only_the_assignments_and_children_inherit_the_parents_absent(lab_scratch, main_scratch):
    turns = ("turn-h100000000000001", "turn-h100000000000002", "turn-h100000000000003")
    async with main_scratch.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
    async with lab_scratch.begin() as conn:
        n1 = await fx.add_note(conn, turns[0])
        n2 = await fx.add_note(conn, turns[1])
    parent = (await taxonomy.create(fx.owner(), _draft("wrong_job_status_counts"), created_from=[n1, n2]))["mode"]
    await labels.set_label(fx.owner(), None, turns[2], parent["id"], present=False, base=None)
    out = await taxonomy.split(fx.owner(), parent["id"], [
        dict(_draft("counts_cancelled_jobs_as_open"), created_from=[n1]),
        dict(_draft("counts_on_hold_jobs_as_open"), created_from=[n2])],
        {turns[0]: [0], turns[1]: []}, base_version=parent["version"])
    assert out["parent"]["status"] == "retired" and out["labels_written"] == 1
    c0, c1 = (c["id"] for c in out["children"])
    assert all(c["split_from"] == parent["id"] and c["status"] == "candidate" for c in out["children"])
    assert (await _cell(turns[0], c0))["state"] == "present"
    assert (await _cell(turns[1], c1))["state"] == "unchecked"
    inherited = await _cell(turns[2], c1)
    assert (inherited["state"], inherited["via"]) == ("absent", parent["id"])
    with pytest.raises(Conflict) as e:
        await taxonomy.restore(fx.owner(), parent["id"], base_version=out["parent"]["version"])
    assert e.value.reason == "split_parent"
    async with lab_scratch.connect() as conn:
        written = (await conn.execute(text(
            "SELECT turn_id, source, approval, basis FROM tracelab.labels WHERE mode_id = CAST(:c AS uuid)"),
            {"c": c0})).mappings().all()
    assert [(r["turn_id"], r["source"], r["approval"]) for r in written] == [(turns[0], "human_structured_label", "single")]
    assert taxonomy.json_value(written[0]["basis"]) == {"via": "split", "from_mode": parent["id"]}


async def test_split_refuses_notes_from_outside_the_parent_and_unknown_turns(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-h200000000000001")
    async with lab_scratch.begin() as conn:
        n1 = await fx.add_note(conn, "turn-h200000000000001")
        stranger = await fx.add_note(conn, "turn-h200000000000001", reviewer=fx.admin(n=2))
    parent = (await taxonomy.create(fx.owner(), _draft("wrong_job_status_counts"), created_from=[n1]))["mode"]
    children = [dict(_draft("counts_cancelled_jobs_as_open"), created_from=[n1]),
                dict(_draft("counts_on_hold_jobs_as_open"), created_from=[stranger])]
    with pytest.raises(Invalid) as e:
        await taxonomy.split(fx.owner(), parent["id"], children, {}, base_version=parent["version"])
    assert e.value.reason == "created_from_not_in_parent"
    children[1]["created_from"] = [n1]
    with pytest.raises(Invalid) as e:
        await taxonomy.split(fx.owner(), parent["id"], children, {"turn-zzzz": [0]}, base_version=parent["version"])
    assert e.value.reason == "not_a_present_turn"
    with pytest.raises(Invalid) as e:
        await taxonomy.split(fx.owner(), parent["id"], children, {"turn-h200000000000001": [5]},
                             base_version=parent["version"])
    assert e.value.reason == "bad_child_index"
    after = await taxonomy.get_mode(fx.owner(), None, parent["id"])
    assert after["mode"]["status"] == "candidate"                      # every refusal left the parent alone


async def test_split_needs_two_to_five_distinct_children(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "wrong_job_status_counts")
    one = [dict(_draft("counts_cancelled_jobs_as_open"), created_from=[])]
    with pytest.raises(Invalid) as e:
        await taxonomy.split(fx.owner(), mid, one, {}, base_version=1)
    assert e.value.reason == "split_needs_two_to_five"
    twins = [dict(_draft("counts_cancelled_jobs_as_open"), created_from=[]) for _ in range(2)]
    with pytest.raises(Invalid) as e:
        await taxonomy.split(fx.owner(), mid, twins, {}, base_version=1)
    assert e.value.reason == "duplicate_child_names"


async def test_a_company_admin_cannot_merge_or_split(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        a = await fx.add_mode(conn, "includes_cancelled_jobs")
        b = await fx.add_mode(conn, "counts_closed_jobs_as_open")
    with pytest.raises(Forbidden) as e:
        await taxonomy.merge(fx.admin(), [a], into=b, new=None, base_versions={a: 1, b: 1})
    assert e.value.reason == "platform_only"
    kids = [dict(_draft("counts_cancelled_jobs_as_open"), created_from=[]),
            dict(_draft("counts_on_hold_jobs_as_open"), created_from=[])]
    with pytest.raises(Forbidden):
        await taxonomy.split(fx.admin(), a, kids, {}, base_version=1)
