"""The register on a throwaway Postgres: next free code per area, one active revision per code, withdraw, seeding never
overwrites, a company sees only its own motivating turns, a mode citing a revised code is flagged. Skips without
TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import evidence, requirements
from trace_lab.review_errors import Invalid, NotFound

pytestmark = requires_scratch
IMPL = [{"doc": "apps/backend/cafm-connector-service-final/svc-deepagents/skills/query-builder/fm-lens.md",
         "section": "", "line": 17, "quote": "No action list under a one-line answer."}]


async def _make(area="WO", statement="A work-order count excludes cancelled jobs.", level="technical", **kw):
    return await requirements.create(fx.owner(), area=area, statement=statement, level=level,
                                     implementations=kw.pop("implementations", []),
                                     motivating_turn_ids=kw.pop("motivating_turn_ids", []), **kw)


async def test_create_takes_the_next_free_code_in_the_area(lab_scratch):
    a = await _make(implementations=IMPL)
    b = await _make(statement="A work-order answer names the period it counts.", level="experience")
    c = await _make(area="EN", statement="An energy answer states its units.")
    assert (a["requirement"]["code"], b["requirement"]["code"], c["requirement"]["code"]) == ("WO-1", "WO-2", "EN-1")
    assert a["requirement"]["implementations"][0]["quote_status"] in ("ok", "drift", "unverifiable_in_image")
    assert a["requirement"]["status"] == "active" and a["requirement"]["revision"] == 1


async def test_two_creates_at_once_never_share_a_code(lab_scratch):
    outs = await asyncio.gather(*[_make(statement=f"Requirement number {n} is a testable sentence.") for n in range(4)])
    assert sorted(o["requirement"]["code"] for o in outs) == ["WO-1", "WO-2", "WO-3", "WO-4"]


async def test_a_revision_supersedes_the_active_row(lab_scratch):
    await _make(area="EN", statement="An energy answer states its units.", level="experience")
    out = await requirements.revise(fx.owner(), "EN-1", statement="An energy answer states its units and period.",
                                    implementations=[], motivating_turn_ids=[], note="period added")
    assert out["requirement"]["revision"] == 2 and out["requirement"]["status"] == "active"
    assert out["requirement"]["level"] == "experience" and out["requirement"]["note"] == "period added"
    rows = await requirements.list_requirements()
    assert {(r["code"], r["revision"], r["status"]) for r in rows if r["code"] == "EN-1"} == {
        ("EN-1", 1, "superseded"), ("EN-1", 2, "active")}
    assert [r["revision"] for r in rows if r["code"] == "EN-1"] == [2, 1]            # newest first
    async with lab_scratch.connect() as conn:
        assert (await requirements.active_codes(conn, ["EN-1", "EN-9"]))["EN-1"]["revision"] == 2


async def test_a_draft_revision_leaves_the_active_row_alone(lab_scratch):
    await _make(area="EN", statement="An energy answer states its units.")
    out = await requirements.revise(fx.owner(), "EN-1", statement="An energy answer states units and period.",
                                    implementations=[], motivating_turn_ids=[], note="proposed wording",
                                    activate=False)
    assert out["requirement"]["status"] == "draft"
    async with lab_scratch.connect() as conn:
        assert (await requirements.active_codes(conn, ["EN-1"]))["EN-1"]["revision"] == 1
    assert [r["status"] for r in await requirements.list_requirements(status="active")] == ["active"]


async def test_withdraw_leaves_no_active_revision(lab_scratch):
    await _make(area="DOC", statement="A document answer names the file it quotes.")
    out = await requirements.withdraw(fx.owner(), "DOC-1", note="covered by GEN-1")
    assert out["requirement"]["status"] == "withdrawn" and out["flagged_modes"] == []
    async with lab_scratch.connect() as conn:
        assert await requirements.active_codes(conn, ["DOC-1"]) == {}
    again = await requirements.withdraw(fx.owner(), "DOC-1", note="still covered")      # nothing active: no change
    assert again["requirement"]["status"] == "withdrawn" and again["requirement"]["note"] == "covered by GEN-1"
    with pytest.raises(NotFound):
        await requirements.withdraw(fx.owner(), "DOC-7", note="no such code")
    with pytest.raises(NotFound):
        await requirements.withdraw(fx.owner(), "NOPE-1", note="not even a code")


async def test_a_withdrawn_code_can_be_revised_back_to_active(lab_scratch):
    await _make(area="DOC", statement="A document answer names the file it quotes.")
    await requirements.withdraw(fx.owner(), "DOC-1", note="covered by GEN-1")
    out = await requirements.revise(fx.owner(), "DOC-1", statement="A document answer names the file and page.",
                                    implementations=[], motivating_turn_ids=[], note="reinstated")
    assert out["requirement"]["revision"] == 2 and out["requirement"]["status"] == "active"


async def test_seed_inserts_once_and_never_overwrites(lab_scratch):
    first = await requirements.seed(fx.owner())
    assert len(first["inserted"]) == 23 and first["skipped"] == []
    await requirements.revise(fx.owner(), "GEN-2", statement="The answer leads with the result, then the detail.",
                              implementations=[], motivating_turn_ids=[], note="owner edit")
    second = await requirements.seed(fx.owner())
    assert second["inserted"] == [] and len(second["skipped"]) == 23
    rows = [r for r in await requirements.list_requirements() if r["code"] == "GEN-2"]
    assert [(r["revision"], r["status"], r["origin"]) for r in rows] == [(2, "active", "ui"), (1, "superseded", "seed")]
    assert all(r["origin"] == "seed" for r in await requirements.list_requirements() if r["code"] == "GEN-1")


async def test_seed_reports_the_drift_of_a_quote_its_doc_lost(lab_scratch, monkeypatch):
    real = requirements.load_seed()
    broken = [dict(real[0], implementations=[dict(real[0]["implementations"][0], quote="A sentence no doc holds.")])]
    monkeypatch.setattr(requirements, "load_seed", lambda: broken)
    out = await requirements.seed(fx.owner())
    assert out["inserted"] == ["GEN-1"] and [d["code"] for d in out["drift"]] == ["GEN-1"]


async def test_a_bad_request_is_refused_before_anything_is_written(lab_scratch):
    def bad(**over):
        kw = dict(area="GEN", statement="Every answer cites its source rows.", level="technical", implementations=[],
                  motivating_turn_ids=[])
        kw.update(over)
        return requirements.create(fx.owner(), **kw)

    for over, reason in (
            ({"implementations": [{"doc": "../../etc/passwd", "section": "", "line": None, "quote": "root:x:0:0"}]},
             "bad_doc_path"),
            ({"implementations": [{"doc": "/etc/passwd", "quote": "root"}]}, "bad_doc_path"),
            ({"implementations": [{"doc": IMPL[0]["doc"], "quote": "  "}]}, "quote_required"),
            ({"implementations": ["fm-lens.md"]}, "bad_implementation"),
            ({"area": "HR"}, "unknown_area"), ({"level": "vibes"}, "unknown_level"),
            ({"statement": "Too short"}, "statement_length"), ({"statement": "x" * 601}, "statement_length")):
        with pytest.raises(Invalid) as e:
            await bad(**over)
        assert e.value.reason == reason and e.value.status == 422 and e.value.error
    assert await requirements.list_requirements() == []
    with pytest.raises(Invalid):
        await requirements.revise(fx.owner(), "GEN-1", statement="Every answer cites its source rows.",
                                  implementations=[], motivating_turn_ids=[], note="  ")


async def test_the_stored_sources_keep_only_their_four_fields(lab_scratch):
    sent = [dict(IMPL[0], quote_status="ok", extra="x"), dict(IMPL[0], line=0, section=None)]
    out = await _make(implementations=sent)
    stored = [{k: v for k, v in i.items() if k != "quote_status"} for i in out["requirement"]["implementations"]]
    assert stored == [IMPL[0], dict(IMPL[0], line=None, section="")]


async def test_a_full_area_is_refused(lab_scratch):
    async with lab_scratch.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.requirements (id, code, revision, statement, area, level, status, origin) VALUES "
            "(gen_random_uuid(), 'WO-999', 1, 'The last free number is taken.', 'WO', 'technical', 'active', 'ui')"))
    with pytest.raises(Invalid) as e:
        await _make()
    assert e.value.reason == "area_full"


async def test_a_confirmed_mode_citing_the_code_is_flagged_on_revise_and_withdraw(lab_scratch):
    await _make(statement="A work-order count excludes cancelled jobs.")
    async with lab_scratch.begin() as conn:
        confirmed = await fx.add_mode(conn, "counts_cancelled_as_open", status="confirmed", requirement_ids=("WO-1",))
        await fx.add_mode(conn, "counts_stale_jobs_as_open", status="candidate", requirement_ids=("WO-1",))
        await fx.add_mode(conn, "counts_old_jobs_as_open", status="retired", requirement_ids=("WO-1",))
    rev = await requirements.revise(fx.owner(), "WO-1", statement="A work-order count excludes cancelled and void jobs.",
                                    implementations=[], motivating_turn_ids=[], note="void added")
    assert [m["id"] for m in rev["flagged_modes"]] == [confirmed]
    listed = [r for r in await requirements.list_requirements(status="active") if r["code"] == "WO-1"][0]
    assert sorted(m["name"] for m in listed["citing_modes"]) == ["counts_cancelled_as_open", "counts_stale_jobs_as_open"]
    out = await requirements.withdraw(fx.owner(), "WO-1", note="dropped")
    assert [m["name"] for m in out["flagged_modes"]] == ["counts_cancelled_as_open"]


async def test_a_company_sees_only_its_own_motivating_turns(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-00000000000000a1", fx.ORG_A)
        await fx.add_turn(conn, "turn-00000000000000b1", fx.ORG_B)
    await _make(motivating_turn_ids=["turn-00000000000000a1", "turn-00000000000000b1", "turn-0000000000purged",
                                     "turn-00000000000000a1"])
    # the chat has purged this turn's row; Trace Lab's snapshot still proves whose it was
    async with lab_scratch.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) VALUES "
            "('turn-0000000000purged', CAST(:o AS uuid), CAST('{}' AS jsonb), 'annotated')"), {"o": fx.ORG_A})
    (platform,) = await requirements.list_requirements()
    assert platform["motivating_turn_ids"] == ["turn-00000000000000a1", "turn-00000000000000b1",
                                               "turn-0000000000purged"]
    (company_a,) = await requirements.list_requirements(org=fx.ORG_A)
    assert company_a["motivating_turn_ids"] == ["turn-00000000000000a1", "turn-0000000000purged"]
    (company_b,) = await requirements.list_requirements(org=fx.ORG_B)
    assert company_b["motivating_turn_ids"] == ["turn-00000000000000b1"]


async def test_the_status_filter_refuses_an_unknown_status(lab_scratch):
    with pytest.raises(Invalid) as e:
        await requirements.list_requirements(status="deleted")
    assert e.value.reason == "unknown_status"


async def _snapshot_orgs(lab_scratch) -> dict[str, tuple[str, str]]:
    async with lab_scratch.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT turn_id, CAST(organization_id AS text), reason FROM tracelab.evidence_turns"))).all()
    return {r[0]: (r[1], r[2]) for r in rows}


async def test_a_cited_turn_is_snapshotted_for_its_own_company_and_survives_the_purge(lab_scratch, main_scratch):
    a, b = "turn-00000000000000a2", "turn-00000000000000b2"
    async with main_scratch.begin() as conn:
        for tid, org in ((a, fx.ORG_A), (b, fx.ORG_B)):
            await fx.add_turn(conn, tid, org)
            await fx.add_span(conn, tid, org)
    await _make(motivating_turn_ids=[a, b, "turn-0000000000never"])
    assert await _snapshot_orgs(lab_scratch) == {a: (fx.ORG_A, "requirement"), b: (fx.ORG_B, "requirement")}
    async with main_scratch.begin() as conn:                                     # the chat's 90-day purge
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_spans"))
        await conn.execute(text("DELETE FROM plenum_cafm.agent_trace_turns"))
    (company_a,) = await requirements.list_requirements(org=fx.ORG_A)
    assert company_a["motivating_turn_ids"] == [a]
    (company_b,) = await requirements.list_requirements(org=fx.ORG_B)
    assert company_b["motivating_turn_ids"] == [b]
    got = await evidence.load([a], organization_id=fx.ORG_A)
    assert got[a].source == "snapshot" and len(got[a].spans) == 1


async def test_a_revision_snapshots_the_turns_it_cites(lab_scratch, main_scratch):
    t = "turn-00000000000000a3"
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, t, fx.ORG_A)
    await _make(area="EN", statement="An energy answer states its units.")
    assert await _snapshot_orgs(lab_scratch) == {}
    await requirements.revise(fx.owner(), "EN-1", statement="An energy answer states its units and period.",
                              implementations=[], motivating_turn_ids=[t, t, "  "], note="period added")
    assert await _snapshot_orgs(lab_scratch) == {t: (fx.ORG_A, "requirement")}


async def test_nothing_is_written_when_the_evidence_cannot_be_kept(lab_scratch, main_scratch, monkeypatch):
    t = "turn-00000000000000a4"
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, t, fx.ORG_A)

    async def broken(*a, **k):
        raise RuntimeError("lab database down")

    monkeypatch.setattr(evidence, "snapshot", broken)
    with pytest.raises(RuntimeError):
        await _make(motivating_turn_ids=[t])
    assert await requirements.list_requirements() == []
