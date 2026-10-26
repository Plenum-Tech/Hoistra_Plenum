"""Mode events and their effects (spec 5.2): create files origin notes, live names are unique, a stale version
and a frozen meaning are refused, a rescope stops earlier labels counting, retire needs a reason, restore checks
conflicts, and a company admin reading a mode sees only their company. Needs the scratch database."""
from __future__ import annotations

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, taxonomy
from trace_lab.review_errors import Conflict, Invalid, NotFound, Stale

pytestmark = requires_scratch
DRAFT = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "", "level": "technical", "agent": "wo_engine"}


async def _notes(lab, main, n=1, org=fx.ORG_A, prefix="f"):
    ids = []
    for i in range(n):
        t = f"turn-{prefix}{org[:1]}{i:014d}"
        async with main.begin() as conn:
            await fx.add_turn(conn, t, org)
        async with lab.begin() as conn:
            ids.append(await fx.add_note(conn, t, org))
    return ids


async def test_create_files_each_origin_note_as_a_present_open_code_label(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch, 2)
    out = await taxonomy.create(fx.owner(), DRAFT, created_from=notes)
    assert out["labels_written"] == 2 and out["mode"]["status"] == "candidate"
    async with lab_scratch.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT source, present, approval, CAST(annotation_id AS text) AS a, note, quote FROM tracelab.labels "
            "WHERE mode_id = CAST(:m AS uuid)"), {"m": out["mode"]["id"]})).mappings().all()
        events = (await conn.execute(text("SELECT action FROM tracelab.mode_events WHERE mode_id = CAST(:m AS uuid)"),
                                     {"m": out["mode"]["id"]})).scalars().all()
    assert {(r["source"], r["present"], r["approval"]) for r in rows} == {("human_open_code", True, "single")}
    assert {r["a"] for r in rows} == set(notes) and {r["quote"] for r in rows} == {"12 open work orders"}
    assert events == ["create"]


async def test_create_keeps_the_origin_turns_evidence_and_the_notes_session(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-f900000000000001", session_id="sess-9")
        await fx.add_span(conn, "turn-f900000000000001", output={"n": 12})
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, "turn-f900000000000001")
    out = await taxonomy.create(fx.owner(), DRAFT, created_from=[note])
    async with lab_scratch.connect() as conn:
        label = (await conn.execute(text("SELECT session_id FROM tracelab.labels WHERE mode_id = CAST(:m AS uuid)"),
                                    {"m": out["mode"]["id"]})).mappings().one()
        kept = (await conn.execute(text("SELECT reason FROM tracelab.evidence_turns WHERE turn_id = :t"),
                                   {"t": "turn-f900000000000001"})).scalar()
    assert label["session_id"] == "sess-9" and kept == "labelled"


async def test_live_names_are_unique(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch, 2)
    await taxonomy.create(fx.owner(), DRAFT, created_from=notes[:1])
    with pytest.raises(Conflict) as e:
        await taxonomy.create(fx.owner(), DRAFT, created_from=notes[1:])
    assert e.value.reason == "name_taken"


async def test_a_bad_draft_is_refused_before_anything_is_written(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch)
    for bad, reason in ((dict(DRAFT, name="Bad Name"), "bad_name"), (dict(DRAFT, definition="short"), "bad_definition"),
                        (dict(DRAFT, level="nope"), "unknown_level"), (dict(DRAFT, agent="nope"), "unknown_agent")):
        with pytest.raises(Invalid) as e:
            await taxonomy.create(fx.owner(), bad, created_from=notes)
        assert e.value.reason == reason
    assert await taxonomy.list_modes(fx.owner(), None) == []


async def test_an_inactive_requirement_is_refused(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch)
    with pytest.raises(Invalid) as e:
        await taxonomy.create(fx.owner(), dict(DRAFT, requirement_ids=["GEN-9"]), created_from=notes)
    assert e.value.reason == "inactive_requirement"


async def test_a_stale_version_is_refused(lab_scratch, main_scratch):
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=await _notes(lab_scratch, main_scratch)))["mode"]
    with pytest.raises(Stale) as e:
        await taxonomy.edit(fx.owner(), mode["id"], {"boundary": "Not on hold."}, base_version=mode["version"] + 1)
    assert e.value.current["id"] == mode["id"]
    out = await taxonomy.edit(fx.owner(), mode["id"], {"boundary": "Not on hold."}, base_version=mode["version"])
    assert out["mode"]["boundary"] == "Not on hold." and out["mode"]["version"] == mode["version"] + 1


async def test_an_unknown_mode_is_not_found(lab_scratch, main_scratch):
    for bad in ("not-a-uuid", "00000000-0000-4000-8000-000000000000"):
        with pytest.raises(NotFound):
            await taxonomy.edit(fx.owner(), bad, {"boundary": "x"}, base_version=1)
    assert await taxonomy.get_mode(fx.owner(), None, "not-a-uuid") is None


async def test_a_frozen_meaning_cannot_change_but_rename_and_retire_still_work(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "frozen_mode_one", status="frozen")
    with pytest.raises(Conflict) as e:
        await taxonomy.edit(fx.owner(), mid, {"definition": "Present when something entirely different happens."},
                            base_version=1)
    assert e.value.reason == "mode_frozen"
    assert (await taxonomy.rename(fx.owner(), mid, "frozen_mode_renamed", base_version=1))["mode"]["version"] == 2
    assert (await taxonomy.retire(fx.owner(), mid, reason="no longer seen", base_version=2))["mode"]["status"] == "retired"


async def test_a_status_the_lifecycle_does_not_allow_is_refused(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
    for target in ("frozen", "retired", "proposed", "rejected"):
        with pytest.raises(Invalid) as e:
            await taxonomy.edit(fx.owner(), mid, {"status": target}, base_version=1)
        assert e.value.reason == "status_transition" and e.value.extra["to"] == target


async def test_a_rescope_returns_a_confirmed_mode_to_candidate_and_old_labels_stop_counting(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-f200000000000001")
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        await fx.add_label(conn, "turn-f200000000000001", mid, present=True)
        run = await fx.add_run(conn, "cells", mode_id=mid)
        await fx.add_proposal(conn, run, turn_id="turn-f200000000000001", mode_id=mid)
    out = await taxonomy.edit(fx.owner(), mid,
                              {"definition": "Present when the answer counts cancelled or on-hold jobs as open."},
                              base_version=1, rescope=True)
    assert out["mode"]["status"] == "candidate" and out["closed_proposals"] == 1
    cells = (await labels.cells_for_turn(fx.owner(), None, "turn-f200000000000001"))["cells"]
    assert next(c for c in cells if c["mode_id"] == mid)["state"] == "unchecked"


async def test_retire_needs_a_reason_and_closes_pending_proposals(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-f300000000000001")
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        run = await fx.add_run(conn, "cells", mode_id=mid)
        await fx.add_proposal(conn, run, turn_id="turn-f300000000000001", mode_id=mid)
    for bad in ("  ", "", "x" * 501):
        with pytest.raises(Invalid):
            await taxonomy.retire(fx.owner(), mid, reason=bad, base_version=1)
    out = await taxonomy.retire(fx.owner(), mid, reason="never seen again", base_version=1)
    assert out["mode"]["status"] == "retired" and out["closed_proposals"] == 1


async def test_restore_brings_a_retired_mode_back_as_a_candidate(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch)
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=notes))["mode"]
    retired = (await taxonomy.retire(fx.owner(), mode["id"], reason="duplicate", base_version=mode["version"]))["mode"]
    back = (await taxonomy.restore(fx.owner(), mode["id"], base_version=retired["version"]))["mode"]
    assert back["status"] == "candidate" and back["retired_at"] is None


async def test_restore_refuses_a_name_a_live_mode_now_has(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch, 2)
    first = (await taxonomy.create(fx.owner(), DRAFT, created_from=notes[:1]))["mode"]
    await taxonomy.retire(fx.owner(), first["id"], reason="duplicate", base_version=first["version"])
    await taxonomy.create(fx.owner(), DRAFT, created_from=notes[1:])
    with pytest.raises(Conflict) as e:
        await taxonomy.restore(fx.owner(), first["id"], base_version=first["version"] + 1)
    assert e.value.reason == "name_taken"


async def test_rename_refuses_a_live_name_and_a_bad_one(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        a = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        await fx.add_mode(conn, "includes_cancelled_jobs")
    with pytest.raises(Conflict) as e:
        await taxonomy.rename(fx.owner(), a, "includes_cancelled_jobs", base_version=1)
    assert e.value.reason == "name_taken"
    with pytest.raises(Invalid) as e2:
        await taxonomy.rename(fx.owner(), a, "Not A Name", base_version=1)
    assert e2.value.reason == "bad_name"
    same = await taxonomy.rename(fx.owner(), a, "counts_cancelled_jobs_as_open", base_version=1)
    assert same["mode"]["version"] == 1


async def test_mark_frozen_is_one_way_and_idempotent(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    async with lab_scratch.begin() as conn:
        await taxonomy.mark_frozen(conn, mid, reason="judge v1 frozen", actor_id=fx.OWNER_ID)
        await taxonomy.mark_frozen(conn, mid, reason="judge v1 frozen", actor_id=fx.OWNER_ID)
    mode = await taxonomy.get_mode(fx.owner(), None, mid)
    assert mode["mode"]["status"] == "frozen" and mode["mode"]["version"] == 2


async def test_a_company_admin_sees_only_their_company_in_a_mode(lab_scratch, main_scratch):
    a = await _notes(lab_scratch, main_scratch, 1, fx.ORG_A, prefix="g")
    b = await _notes(lab_scratch, main_scratch, 1, fx.ORG_B, prefix="g")
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=a + b))["mode"]
    seen_by_b = await taxonomy.get_mode(fx.admin(fx.ORG_B), fx.ORG_B, mode["id"])
    assert {n["organization_id"] for n in seen_by_b["notes"]} == {fx.ORG_B}
    assert seen_by_b["mode"]["created_from"] == b and seen_by_b["mode"]["created_from_count"] == 2
    seen_by_owner = await taxonomy.get_mode(fx.owner(), None, mode["id"])
    assert {n["organization_id"] for n in seen_by_owner["notes"]} == {fx.ORG_A, fx.ORG_B}
    listed = next(m for m in await taxonomy.list_modes(fx.admin(fx.ORG_B), fx.ORG_B) if m["id"] == mode["id"])
    assert listed["created_from"] == b and listed["agent_label"] == "Work orders"


async def test_a_company_admin_sees_only_the_safe_parts_of_a_modes_events(lab_scratch, main_scratch):
    notes = await _notes(lab_scratch, main_scratch)
    mode = (await taxonomy.create(fx.owner(), DRAFT, created_from=notes))["mode"]
    await taxonomy.edit(fx.owner(), mode["id"], {"boundary": "Not on hold."}, base_version=mode["version"])
    owner_view = await taxonomy.get_mode(fx.owner(), None, mode["id"])
    admin_view = await taxonomy.get_mode(fx.admin(), fx.ORG_A, mode["id"])
    assert "changes" in owner_view["events"][0]["detail"] and "created_from" in owner_view["events"][1]["detail"]
    for e in admin_view["events"]:
        assert set(e["detail"]) <= set(taxonomy.SAFE_EVENT_KEYS)
    assert admin_view["events"][1]["detail"] == {"name": DRAFT["name"]}


async def test_list_modes_counts_the_reviewed_answers_of_the_scope(lab_scratch, main_scratch):
    from trace_lab import annotations
    turns = ["turn-f400000000000001", "turn-f400000000000002", "turn-f400000000000003"]
    async with main_scratch.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
    for t in turns:
        await annotations.save_verdict(fx.admin(), fx.ORG_A, t, verdict="right", note=None, anchor=None, base=None)
    await labels.set_label(fx.admin(), fx.ORG_A, turns[0], mid, present=True, base=None)
    await labels.set_label(fx.admin(), fx.ORG_A, turns[1], mid, present=False, base=None)
    listed = next(m for m in await taxonomy.list_modes(fx.admin(), fx.ORG_A) if m["id"] == mid)
    assert listed["counts"] == {"present": 1, "absent": 1, "unchecked": 1}
    other = next(m for m in await taxonomy.list_modes(fx.admin(fx.ORG_B), fx.ORG_B) if m["id"] == mid)
    assert other["counts"] == {"present": 0, "absent": 0, "unchecked": 0}
    assert listed["contract_missing"] == list(taxonomy.CONTRACT_ITEMS)


async def test_a_retired_mode_is_listed_only_when_asked_for_and_names_its_live_mode(lab_scratch, main_scratch):
    async with lab_scratch.begin() as conn:
        target = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        source = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired", merged_into=target)
    assert [m["id"] for m in await taxonomy.list_modes(fx.owner(), None)] == [target]
    both = {m["id"]: m for m in await taxonomy.list_modes(fx.owner(), None, include_retired=True)}
    assert set(both) == {target, source}
    assert both[source]["live_mode_id"] == target and both[source]["counts"] is None
