"""A record is found however it is typed — and a phrase that names nothing finds nothing.

The user added a building "JASMEET" and asked about "JASMEET BUILDING"; nothing was found because
each service compared names its own way (29 Oct 2026). This service compared work-order codes
exactly and case-sensitively ("wo-1042" was a 404 for WO-1042), read assets and locations with
one ``ILIKE '%whole phrase%'`` cut silently at ten rows, and let the email intake pick the closest
of twenty by spelling. These pin the shared ladder (name_match, copied byte-identical from
svc-deepagents) at every site: exact, compact, phrase at word start, every word in any order — and
for choosing the asset or location a new work order is raised against, never a close spelling.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.agent.tools.asset_tools import AssetTools
from src.app import app
from src.db import get_session
from src.models.asset import Asset
from src.models.location import Location
from src.models.work_order import WorkOrder
from src.services import principal as P
from src.services import work_order_status_track as track_mod
from src.services.work_order_flow import WorkOrderFlow
from src.shared import record_lookup

HERE = Path(__file__).resolve()
COPY = HERE.parents[2] / "src" / "shared" / "name_match.py"
SIBLING = HERE.parents[3] / "svc-deepagents" / "src" / "agents" / "name_match.py"

MINE, THEIRS = uuid4(), uuid4()


def make(building_ids, role="user", org=None):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=org,
                       role=role, building_ids=building_ids)


def compiled(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


def params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


class Fake:
    """A session answering each statement, in turn, with the next list of rows given ([] after)."""

    def __init__(self, *answers):
        self.answers, self.statements = [list(a) for a in answers], []

    class _R:
        def __init__(self, rows): self._rows = rows
        def scalars(self): return self
        def all(self): return self._rows
        def fetchall(self): return self._rows
        def scalar_one_or_none(self): return self._rows[0] if self._rows else None
        def scalar_one(self): return len(self._rows)

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        return self._R(self.answers.pop(0) if self.answers else [])

    async def commit(self): pass
    async def rollback(self): pass
    async def refresh(self, *a): pass
    async def flush(self): pass
    def add(self, *a): pass


def asset(name, code=None, building=MINE):
    return Asset(asset_id=uuid4(), asset_name=name, asset_code=code, building_id=building)


def location(name, building=MINE):
    return Location(location_id=uuid4(), name=name, type="building", building_id=building)


def wo(code, building=MINE, key=None):
    return WorkOrder(work_order_id=key or str(uuid4()), wo_code=code, building_id=building,
                     status="pending_approval")


@pytest.fixture
def client():
    yield TestClient(app, raise_server_exceptions=True)
    app.dependency_overrides.clear()


def wire(principal, session):
    async def _s():
        yield session

    async def _p():
        return principal
    app.dependency_overrides[get_session] = _s
    app.dependency_overrides[P.current_principal] = _p


# ── the copy is the shared matcher, byte for byte ─────────────────────────────

def test_name_match_is_the_svc_deepagents_copy_byte_for_byte():
    if not SIBLING.exists():
        pytest.skip(f"svc-deepagents is not beside this service ({SIBLING}), so the copy "
                    f"cannot be compared; run from the repository tree")
    assert COPY.read_bytes() == SIBLING.read_bytes(), (
        "src/shared/name_match.py has drifted from svc-deepagents/src/agents/name_match.py. "
        "Copy the svc-deepagents file over it unchanged — one matcher, every service.")


# ── the ladder, without a database ────────────────────────────────────────────

def _names(rows):
    return [getattr(r, "asset_name", None) or getattr(r, "name", None) for r in rows]


def _rank(query, rows):
    return record_lookup.rank(query, rows, lambda r: (r.name,))


def test_jasmeet_building_is_jasmeet_not_jasmeet_heights():
    rung, hits = _rank("JASMEET BUILDING", [location("Jasmeet Heights"), location("JASMEET")])
    assert _names(hits) == ["JASMEET"] and rung == 0


def test_building_7_is_not_building_70():
    rung, hits = _rank("Building 7", [location("Building 70"), location("Building 7")])
    assert _names(hits) == ["Building 7"]
    assert _rank("Building 7", [location("Building 70")]) == (None, [])


def test_the_building_names_nothing():
    rows = [location("JASMEET"), location("Building 7"), location("Building 70")]
    assert _rank("the building", rows) == (None, [])
    assert _rank("building", rows) == (None, [])


def test_a_phrase_with_no_name_in_it_reads_no_row_by_its_words():
    """"the building" must not read every row holding "building" and hope the ranking copes."""
    clause = record_lookup.candidate_clause((Location.name,), "the building")
    assert "ILIKE" not in compiled(clause).upper()


def test_no_close_spelling_rung():
    assert _rank("Jasmet", [location("JASMEET")]) == (None, [])


# ── work orders by code: GET and every route through _get_wo_or_404 ───────────

def test_a_code_typed_in_lower_case_without_its_hyphen_finds_the_work_order(client):
    row = wo("WO-1042")
    s = Fake([], [row])
    wire(make((MINE,)), s)
    r = client.get("/api/work-orders/wo1042")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["work_order_id"] == row.work_order_id


def test_wo_dash_1042_lower_case_finds_WO_dash_1042(client):
    row = wo("WO-1042")
    wire(make((MINE,)), Fake([], [row]))
    r = client.get("/api/work-orders/wo-1042")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["work_order_id"] == row.work_order_id


def test_the_closer_spelling_of_the_code_wins(client):
    exact, compact = wo("WO-1042"), wo("WO1042")
    wire(make((MINE,)), Fake([], [compact, exact]))
    r = client.get("/api/work-orders/wo-1042")
    assert r.status_code == 200, r.text[:300]
    assert r.json()["work_order_id"] == exact.work_order_id


def test_two_work_orders_sharing_a_code_are_refused_not_picked(client):
    a, b = wo("WO-1042"), wo("WO 1042")
    wire(make((MINE,)), Fake([], [a, b]))
    r = client.get("/api/work-orders/wo1042")
    assert r.status_code == 409, r.text[:300]
    err = r.json()["errors"][0]
    assert err["code"] == "work_order_ambiguous"
    assert a.work_order_id in err["message"] and b.work_order_id in err["message"]


def test_duplicate_exact_codes_are_a_409_not_a_500(client):
    """wo_code is not unique (948 distinct of 1,728). Two exact matches made
    scalar_one_or_none raise MultipleResultsFound — a 500."""
    a, b = wo("WO-7"), wo("WO-7")
    wire(make((MINE,)), Fake([a, b], [a, b]))
    r = client.get("/api/work-orders/WO-7")
    assert r.status_code == 409, r.text[:300]


def test_the_key_is_still_read_by_key_in_one_statement(client):
    row = wo("WO-1042")
    s = Fake([row])
    wire(make((MINE,)), s)
    r = client.get(f"/api/work-orders/{row.work_order_id}")
    assert r.status_code == 200, r.text[:300]
    assert len(s.statements) == 1
    sql = compiled(s.statements[0])
    assert "CAST(plenum_cafm.work_orders.id AS VARCHAR)" in sql
    assert "regexp_replace" not in sql


def test_the_loose_code_match_reads_only_the_callers_buildings(client):
    s = Fake([], [])
    wire(make((MINE,)), s)
    assert client.get("/api/work-orders/wo1042").status_code == 404
    loose = compiled(s.statements[1])
    assert "regexp_replace" in loose and "work_orders.building_id IN (" in loose


def test_the_loose_code_match_keeps_an_admin_inside_their_company(client):
    s = Fake([], [])
    wire(make(None, role="admin", org=uuid4()), s)
    assert client.get("/api/work-orders/wo1042").status_code == 404
    assert "plenum_cafm.buildings" in compiled(s.statements[1])


def test_an_exact_code_on_another_building_is_still_refused(client):
    wire(make((MINE,)), Fake([wo("WO-9", building=THEIRS)]))
    r = client.get("/api/work-orders/WO-9")
    assert r.status_code == 403, r.text[:300]


def test_history_by_code_is_read_under_the_key(client):
    row = wo("WO-1042")
    s = Fake([], [row], [])
    wire(make((MINE,)), s)
    r = client.get("/api/work-orders/wo1042/history")
    assert r.status_code == 200, r.text[:300]
    hist = s.statements[2]
    assert "status_history" in compiled(hist)
    assert row.work_order_id in params(hist).values()
    assert "wo1042" not in params(hist).values()


# ── /status-track ─────────────────────────────────────────────────────────────

def test_status_track_by_code_tracks_the_keyed_row(client, monkeypatch):
    row = wo("WO-1042")
    seen = []

    async def fake_build(session, work_order_id):
        seen.append(work_order_id)
        return {"found": True, "work_order_id": work_order_id}
    monkeypatch.setattr(track_mod, "build_work_order_status_track", fake_build)
    wire(make((MINE,)), Fake([], [row]))
    r = client.get("/api/work-orders/wo-1042/status-track")
    assert r.status_code == 200, r.text[:300]
    assert seen == [row.work_order_id]


def test_status_track_of_a_work_order_on_another_building_is_refused(client):
    """The route took the caller and never used it: any signed-in user read any track."""
    wire(make((MINE,)), Fake([wo("WO-9", building=THEIRS)]))
    r = client.get("/api/work-orders/WO-9/status-track")
    assert r.status_code == 403, r.text[:300]


@pytest.fixture
def no_approval_steps(monkeypatch):
    async def _none(session, work_order_id):
        return []
    monkeypatch.setattr(track_mod, "_load_approval_steps", _none)


async def test_the_status_track_service_accepts_a_code(no_approval_steps):
    row = wo("WO-1042")
    s = Fake([], [row], [], [])
    track = await track_mod.build_work_order_status_track(s, "wo-1042")
    assert track["found"] is True
    assert track["work_order_id"] == row.work_order_id
    # the journey and history reads after the lookup use the key, not the text typed
    for st in s.statements[2:]:
        assert "wo-1042" not in params(st).values()


async def test_the_status_track_service_refuses_an_ambiguous_code(no_approval_steps):
    a, b = wo("WO-1042"), wo("WO 1042")
    track = await track_mod.build_work_order_status_track(Fake([], [a, b]), "wo1042")
    assert track["found"] is False and track.get("ambiguous") is True
    assert {c["work_order_id"] for c in track["candidates"]} == {a.work_order_id, b.work_order_id}


# ── search_assets / search_locations (the chat agent's tools) ─────────────────

async def test_chiller_1_jasmeet_finds_jasmeet_chiller_1_only():
    rows = [asset("Jasmeet Chiller-10"), asset("Jasmeet Chiller-1"), asset("AHU-1 Jasmeet")]
    out = await AssetTools(Fake(rows)).search_assets("chiller 1 jasmeet")
    assert out["found"] is True
    assert [r["asset_name"] for r in out["results"]] == ["Jasmeet Chiller-1"]


async def test_a_code_typed_without_hyphens_finds_the_asset():
    s = Fake([asset("Boiler 1", "PT-B-301-A-BOILER-01"), asset("Boiler 2", "PT-B-301-A-BOILER-02")])
    out = await AssetTools(s).search_assets("ptb301aboiler01")
    assert [r["asset_code"] for r in out["results"]] == ["PT-B-301-A-BOILER-01"]
    assert out["match"] == "compact"
    assert "regexp_replace" in compiled(s.statements[0])


async def test_a_hyphenated_code_finds_an_asset_stored_without_them():
    out = await AssetTools(Fake([asset("Boiler", "ptb301aboiler01")])).search_assets(
        "PT-B-301-A-BOILER-01")
    assert [r["asset_code"] for r in out["results"]] == ["ptb301aboiler01"]


async def test_assets_that_match_equally_are_all_returned_and_flagged():
    a, b = asset("Chiller 1", "CH-1-A"), asset("Chiller 1", "CH-1-B")
    out = await AssetTools(Fake([a, b])).search_assets("chiller 1")
    assert out["count"] == 2 and out["total"] == 2 and out.get("ambiguous") is True
    assert "never pick one" in out["message"]


async def test_a_long_list_is_cut_only_with_a_count():
    rows = [asset("Pump", f"P-{i:03d}") for i in range(60)]
    s = Fake(rows)
    out = await AssetTools(s).search_assets("pump")
    assert out["total"] == 60 and out["count"] == record_lookup.SHOW_LIMIT
    assert f"Showing {record_lookup.SHOW_LIMIT} of 60" in out["message"]
    assert "LIMIT 10" not in compiled(s.statements[0])


async def test_a_misspelt_asset_is_not_guessed():
    out = await AssetTools(Fake([asset("Jasmeet Chiller-1")])).search_assets("Jasmet Chiller-1")
    assert out["found"] is False


async def test_jasmeet_building_finds_the_location_jasmeet():
    out = await AssetTools(Fake([location("Jasmeet Heights"), location("JASMEET")])) \
        .search_locations("JASMEET BUILDING")
    assert [r["name"] for r in out["results"]] == ["JASMEET"]


async def test_location_building_7_is_not_building_70():
    out = await AssetTools(Fake([location("Building 70"), location("Building 7")])) \
        .search_locations("Building 7")
    assert [r["name"] for r in out["results"]] == ["Building 7"]


async def test_the_building_finds_no_location():
    s = Fake([location("Building 7"), location("Building 70"), location("JASMEET")])
    out = await AssetTools(s).search_locations("the building")
    assert out["found"] is False
    assert all("ILIKE" not in compiled(st).upper() for st in s.statements)


# ── the email intake: which asset and location a new work order is raised against ──

def _flow():
    return WorkOrderFlow.__new__(WorkOrderFlow)     # the resolvers need no model client


async def test_email_intake_resolves_chiller_1_jasmeet():
    target = asset("Jasmeet Chiller-1", "JCH-1")
    rows = [asset("Jasmeet Chiller-10", "JCH-10"), target]
    out = await _flow()._resolve_asset("chiller 1 jasmeet", Fake(rows))
    assert out["resolved"] is True and out["asset_id"] == str(target.asset_id)


async def test_email_intake_refuses_to_pick_between_equal_assets():
    a, b = asset("Chiller 1", "CH-1-A"), asset("Chiller 1", "CH-1-B")
    out = await _flow()._resolve_asset("Chiller 1", Fake([a, b]))
    assert out["resolved"] is False and out["reason"] == "asset_ambiguous"
    assert len(out["candidates"]) == 2


async def test_email_intake_does_not_guess_a_misspelt_asset():
    """It used to take the closest of twenty above 0.55 similarity."""
    out = await _flow()._resolve_asset("Jasmet Chiller-1", Fake([asset("Jasmeet Chiller-1")]))
    assert out["resolved"] is False


async def test_email_intake_resolves_jasmeet_building_to_jasmeet():
    target = location("JASMEET")
    out = await _flow()._resolve_location("JASMEET BUILDING",
                                          Fake([location("Jasmeet Heights"), target]))
    assert out["resolved"] is True and out["location_id"] == str(target.location_id)


async def test_email_intake_does_not_resolve_building_7_to_building_70():
    out = await _flow()._resolve_location("Building 7", Fake([location("Building 70")]))
    assert out["resolved"] is False


async def test_a_cut_read_never_resolves_a_partial_match():
    """More candidates than were read: one phrase match among those read is not proof that it
    is the only one."""
    rows = [asset("Main Boiler Room Pump")] + [
        asset(f"Boiler Main Pump {i}") for i in range(record_lookup.CANDIDATE_LIMIT)]
    out = await _flow()._resolve_asset("main boiler", Fake(rows))
    assert out["resolved"] is False
