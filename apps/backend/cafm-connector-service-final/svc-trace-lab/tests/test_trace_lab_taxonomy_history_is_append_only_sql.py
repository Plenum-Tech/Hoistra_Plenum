"""0004_taxonomy.sql on a throwaway Postgres (tracelab_scratch): mode_events refuse UPDATE, DELETE and TRUNCATE; labels
only gain superseded_by, once; one live head per cell; one pending suggestion per cell; bulk approval only on
AI-derived sources; a proposal names a company exactly when it cites a turn; a company admin's proposed mode and its
rejection hold together. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401

pytestmark = requires_scratch

T1, T2, T3, T4 = (f"turn-000000000000000{n}" for n in (1, 2, 3, 4))


async def test_mode_events_refuse_update_delete_and_truncate(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        await conn.execute(text("INSERT INTO tracelab.mode_events (mode_id, action, actor_id) "
                                "VALUES (CAST(:m AS uuid), 'create', CAST(:u AS uuid))"), {"m": mid, "u": fx.OWNER_ID})
    for stmt in ("UPDATE tracelab.mode_events SET action = 'edit'",
                 "DELETE FROM tracelab.mode_events",
                 "TRUNCATE tracelab.mode_events"):
        with pytest.raises(DBAPIError):
            async with lab_scratch.begin() as conn:
                await conn.execute(text(stmt))
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.mode_events"))).scalar_one() == 1


async def test_mode_events_name_a_proposal_and_refuse_an_unknown_action(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="proposed")
        for action in ("propose", "accept", "reject"):
            await conn.execute(text("INSERT INTO tracelab.mode_events (mode_id, action, actor_id) "
                                    "VALUES (CAST(:m AS uuid), :a, CAST(:u AS uuid))"),
                               {"m": mid, "a": action, "u": fx.OWNER_ID})
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await conn.execute(text("INSERT INTO tracelab.mode_events (mode_id, action, actor_id) "
                                    "VALUES (CAST(:m AS uuid), 'delete', CAST(:u AS uuid))"),
                               {"m": mid, "u": fx.OWNER_ID})


async def test_a_label_only_gains_superseded_by_once(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        lid = await fx.add_label(conn, T1, mid)
        await conn.execute(text("UPDATE tracelab.labels SET superseded_by = CAST(:n AS uuid) WHERE id = CAST(:i AS uuid)"),
                           {"n": str(uuid.uuid4()), "i": lid})
    with pytest.raises(DBAPIError):
        async with lab_scratch.begin() as conn:
            await conn.execute(text("UPDATE tracelab.labels SET superseded_by = CAST(:n AS uuid) WHERE id = CAST(:i AS uuid)"),
                               {"n": str(uuid.uuid4()), "i": lid})
    for stmt in ("UPDATE tracelab.labels SET note = 'edited'", "DELETE FROM tracelab.labels",
                 "TRUNCATE tracelab.labels"):
        with pytest.raises(DBAPIError):
            async with lab_scratch.begin() as conn:
                await conn.execute(text(stmt))


async def test_a_fresh_label_cannot_be_edited_while_it_is_the_head(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        await fx.add_label(conn, T1, mid)
    with pytest.raises(DBAPIError):
        async with lab_scratch.begin() as conn:
            await conn.execute(text("UPDATE tracelab.labels SET present = false"))


async def test_one_live_head_per_turn_and_mode(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        await fx.add_label(conn, T2, mid)
    with pytest.raises(IntegrityError, match="ux_tl_labels_head"):
        async with lab_scratch.begin() as conn:
            await fx.add_label(conn, T2, mid, present=False)


async def test_one_pending_suggestion_per_cell_and_kind(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        run = await fx.add_run(conn, "cells")
        await fx.add_proposal(conn, run, turn_id=T3, mode_id=mid)
    with pytest.raises(IntegrityError, match="ux_tl_prop_pending_cell"):
        async with lab_scratch.begin() as conn:
            await fx.add_proposal(conn, run, turn_id=T3, mode_id=mid, suggestion="absent")
    async with lab_scratch.begin() as conn:       # a decided one does not block a new pending one
        await fx.add_proposal(conn, run, turn_id=T3, mode_id=mid, suggestion="absent", status="dismissed")
        await fx.add_proposal(conn, run, turn_id="turn-000000000000000f", mode_id=mid)


async def test_bulk_approval_only_on_ai_derived_sources(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
        run = await fx.add_run(conn, "cells")
        prop = await fx.add_proposal(conn, run, turn_id=T4, mode_id=mid, status="accepted")
    with pytest.raises(IntegrityError, match="ck_tl_labels_bulk"):
        async with lab_scratch.begin() as conn:
            await fx.add_label(conn, T4, mid, approval="bulk")
    async with lab_scratch.begin() as conn:
        await fx.add_label(conn, T4, mid, approval="bulk", source="accepted_label_proposal", proposal_id=prop)


async def test_a_label_from_a_suggestion_or_a_proposal_cites_it(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open")
    for source, present in (("accepted_suggestion", True), ("rejected_suggestion", False),
                            ("accepted_label_proposal", True)):
        with pytest.raises(IntegrityError, match="ck_tl_labels_from_proposal"):
            async with lab_scratch.begin() as conn:
                await fx.add_label(conn, T1, mid, source=source, present=present)


async def test_a_proposal_names_a_company_exactly_when_it_cites_a_turn(lab_scratch):
    async with lab_scratch.begin() as conn:
        run = await fx.add_run(conn, "grouping")
    with pytest.raises(IntegrityError, match="ck_tl_prop_org_turn"):
        async with lab_scratch.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracelab.proposals (id, run_id, kind, cache_key, organization_id, suggestion, "
                "annotation_ids) VALUES (CAST(:id AS uuid), CAST(:r AS uuid), 'group', 'k', CAST(:o AS uuid), "
                "'no_mode_fits', ARRAY[CAST(:a AS uuid)])"),
                {"id": str(uuid.uuid4()), "r": run, "o": fx.ORG_A, "a": str(uuid.uuid4())})
    async with lab_scratch.begin() as conn:       # a platform grouping proposal cites notes and no company
        await fx.add_proposal(conn, run, kind="group", suggestion="no_mode_fits", annotation_ids=[str(uuid.uuid4())])


async def test_a_proposed_mode_names_its_company_and_a_rejected_one_its_reason(lab_scratch):
    async with lab_scratch.begin() as conn:
        await fx.add_mode(conn, "proposed_by_a_company", status="proposed")
        await fx.add_mode(conn, "rejected_with_reason", status="rejected")
    with pytest.raises(IntegrityError, match="ck_tl_modes_proposer"):
        async with lab_scratch.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracelab.modes (id, name, definition, status, level, agent, created_from, created_by) "
                "VALUES (CAST(:id AS uuid), 'proposed_without_company', 'Present when something is wrong.', 'proposed', "
                "'technical', 'wo_engine', ARRAY[CAST(:a AS uuid)], CAST(:u AS uuid))"),
                {"id": str(uuid.uuid4()), "a": str(uuid.uuid4()), "u": fx.OWNER_ID})
    with pytest.raises(IntegrityError, match="ck_tl_modes_rejected"):
        async with lab_scratch.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracelab.modes (id, name, definition, status, level, agent, created_from, created_by) "
                "VALUES (CAST(:id AS uuid), 'rejected_without_reason', 'Present when something is wrong.', 'rejected', "
                "'technical', 'wo_engine', ARRAY[CAST(:a AS uuid)], CAST(:u AS uuid))"),
                {"id": str(uuid.uuid4()), "a": str(uuid.uuid4()), "u": fx.OWNER_ID})
    with pytest.raises(IntegrityError, match="ck_tl_modes_rejected"):      # a reason on a mode that is not rejected
        async with lab_scratch.begin() as conn:
            await conn.execute(text(
                "INSERT INTO tracelab.modes (id, name, definition, status, level, agent, created_from, created_by, "
                "rejected_reason) VALUES (CAST(:id AS uuid), 'reason_without_rejection', 'Present when something is wrong.', "
                "'candidate', 'technical', 'wo_engine', ARRAY[CAST(:a AS uuid)], CAST(:u AS uuid), 'because')"),
                {"id": str(uuid.uuid4()), "a": str(uuid.uuid4()), "u": fx.OWNER_ID})


async def test_a_rejected_or_retired_name_can_be_used_again_but_a_live_one_cannot(lab_scratch):
    async with lab_scratch.begin() as conn:
        await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="rejected")
        await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="retired")
        await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="proposed")
    with pytest.raises(IntegrityError, match="ux_tl_modes_live_name"):
        async with lab_scratch.begin() as conn:
            await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="candidate")


async def test_the_data_level_is_accepted_and_an_unknown_one_is_not(lab_scratch):
    async with lab_scratch.begin() as conn:
        await fx.add_mode(conn, "stale_meter_reading", level="data", agent="energy_intelligence")
    with pytest.raises(IntegrityError):
        async with lab_scratch.begin() as conn:
            await fx.add_mode(conn, "made_up_level_mode", level="cosmic")


async def test_the_fixtures_write_every_table_they_name(lab_scratch):
    """The Phase 5 fixture helpers fit the real tables: one row of each kind goes in and a sampled turn follows."""
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        note = await fx.add_note(conn, T1)
        run = await fx.add_run(conn, "depth_scan", mode_id=mid, org=fx.ORG_A)
        prop = await fx.add_proposal(conn, run, kind="depth_scan", turn_id=T1, mode_id=mid, status="accepted")
        await fx.add_label(conn, T1, mid, source="accepted_label_proposal", proposal_id=prop, annotation_id=note)
        await fx.add_batch_item(conn, T1)
        counts = {t: (await conn.execute(text(f"SELECT count(*) FROM tracelab.{t}"))).scalar_one()
                  for t in ("modes", "annotations", "proposal_runs", "proposals", "labels", "sample_items")}
    assert counts == {"modes": 1, "annotations": 1, "proposal_runs": 1, "proposals": 1, "labels": 1, "sample_items": 1}


async def test_chat_turn_fixtures_write_the_chat_tables(main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, T1)
        await fx.add_span(conn, T1)
        n = (await conn.execute(text("SELECT count(*) FROM plenum_cafm.agent_trace_spans WHERE turn_id = :t"),
                                {"t": T1})).scalar_one()
    assert n == 1
