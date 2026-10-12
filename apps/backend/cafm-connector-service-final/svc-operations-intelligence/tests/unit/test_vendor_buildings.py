"""Vendor ↔ building assignments (12 Oct 2026): who may make them, in which company, and what
each refusal answers. "Buildings served" was only inferred from certificates, work orders and
invoices; an admin's assignment is now stored (migrations/vendor_buildings.sql), from Hoist
vendors' buildings question or the Vendors page, and recorded in the vendor trail.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.api.routes import compliance as routes
from src.app import app
from src.db import get_session
from src.engines.auth import vendor_audit
from src.engines.auth.tokens import Principal
from src.engines.compliance import vendor_buildings as vb

ORG = UUID("11111111-1111-5111-8111-111111111111")
B1 = "aaaaaaaa-0000-4000-8000-000000000001"
B2 = "aaaaaaaa-0000-4000-8000-000000000002"
VID = "5c1e2d3f-0000-4000-8000-00000000abcd"


def principal(role: str, buildings=None) -> Principal:
    return Principal(
        user_id=uuid4(), email=f"{role}@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=True, building_ids=buildings,
    )


# ── the routes ─────────────────────────────────────────────────────────────────

class Engine:
    def __init__(self, answer=None):
        self.calls: list[tuple[str, dict]] = []
        self.answer = answer

    def fn(self, name):
        async def _f(session, **kw):
            self.calls.append((name, kw))
            return self.answer or {"ok": True}
        return _f


@pytest.fixture
def eng(monkeypatch):
    e = Engine()
    for name in ("list_assignments", "assign", "unassign"):
        monkeypatch.setattr(routes.vb_svc, name, e.fn(name))

    async def _s():
        yield object()
    app.dependency_overrides[get_session] = _s
    try:
        yield e
    finally:
        app.dependency_overrides.clear()


def as_(p: Principal) -> Principal:
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    return p


def post(body=None):
    return TestClient(app).post(f"/api/compliance/vendors/{VID}/buildings", json=body or {"building_ids": [B1], "source": "hoist"})


def test_a_plain_user_cannot_assign_or_unassign(eng):
    as_(principal("user"))
    assert post().status_code == 403
    assert TestClient(app).delete(f"/api/compliance/vendors/{VID}/buildings/{B1}").status_code == 403
    assert eng.calls == []


def test_an_admin_assigns_in_their_own_company(eng):
    me = as_(principal("admin"))
    r = post()
    assert r.status_code == 200, r.text
    name, kw = eng.calls[0]
    assert name == "assign"
    assert kw["organization_id"] == ORG and kw["vendor_id"] == VID
    assert kw["building_ids"] == [B1] and kw["source"] == "hoist" and kw["user_id"] == me.user_id


def test_everyone_signed_in_may_read_and_a_building_bound_user_sees_only_theirs(eng):
    as_(principal("user", buildings=(UUID(B2),)))
    r = TestClient(app).get("/api/compliance/vendor-buildings")
    assert r.status_code == 200, r.text
    assert eng.calls[0][1]["building_ids"] == (UUID(B2),)


@pytest.mark.parametrize("reason,code", [("not_set_up", 503), ("building_not_found", 404),
                                          ("vendor_deleted", 409), ("vendor_not_found", 404),
                                          ("no_buildings", 400)])
def test_each_refusal_answers_with_its_own_status(eng, reason, code):
    as_(principal("admin"))
    eng.answer = {"ok": False, "reason": reason, "error": "x"}
    assert post().status_code == code


# ── the engine ─────────────────────────────────────────────────────────────────

class Session:
    """Answers the engine's statements in order of kind; records every one."""

    def __init__(self, ready=True, buildings=None, inserted=1, ended=1):
        self.ready, self.buildings, self.inserted, self.ended = ready, buildings or [], inserted, ended
        self.sql: list[str] = []
        self.committed = self.rolled_back = False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        if "to_regclass" in sql:
            return SimpleNamespace(scalar=lambda: self.ready)
        if sql.startswith("SELECT b.building_id"):
            rows = [{"building_id": b, "name": n} for b, n in self.buildings]
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))
        if sql.startswith("INSERT INTO plenum_cafm.vendor_buildings"):
            return SimpleNamespace(rowcount=self.inserted)
        if sql.startswith("UPDATE plenum_cafm.vendor_buildings"):
            rows = [("Bishopsgate Tower",)] * self.ended
            return SimpleNamespace(rowcount=self.ended, all=lambda: rows)
        if sql.startswith("SELECT v.id::text FROM plenum_cafm.vendors"):
            vid = (params or {}).get("vid")
            return SimpleNamespace(scalar=lambda: vid)
        return SimpleNamespace(scalar=lambda: "Harbourside Lift Engineering Ltd")

    def begin_nested(self):
        class _Savepoint:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *exc):
                return False
        return _Savepoint()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.fixture
def quiet(monkeypatch):
    audits: list[dict] = []

    async def refusal(session, vendor_id, org, *, what=""):
        return None

    async def write_audit(session, **kw):
        audits.append(kw)
    monkeypatch.setattr(vb, "vendor_link_refusal", refusal)
    monkeypatch.setattr(vb, "write_audit", write_audit)
    return audits


async def test_a_database_without_the_table_says_so_and_writes_nothing(quiet):
    s = Session(ready=False)
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID, building_ids=[B1], user_id=None)
    assert out["reason"] == "not_set_up"
    assert not any(x.startswith(("INSERT", "UPDATE")) for x in s.sql)
    listed = await vb.list_assignments(s, organization_id=ORG)
    assert listed == {"ok": True, "available": False, "assignments": []}


async def test_each_building_is_assigned_once_and_the_trail_names_them(quiet):
    s = Session(buildings=[(B1, "Bishopsgate Tower"), (B2, "Manchester Town Hall")])
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID, building_ids=[B1, B2, B1], user_id="u-1", source="hoist")
    assert out["ok"] is True
    assert [b["name"] for b in out["assigned"]] == ["Bishopsgate Tower", "Manchester Town Hall"]
    assert sum(1 for x in s.sql if x.startswith("INSERT")) == 2
    assert s.committed
    assert len(quiet) == 1 and quiet[0]["action_type"] == vendor_audit.BUILDINGS_ASSIGNED
    assert quiet[0]["detail"]["vendor"] == {"id": VID, "name": "Harbourside Lift Engineering Ltd"}
    assert quiet[0]["detail"]["source"] == "hoist"


async def test_a_building_of_another_company_is_refused_and_nothing_is_written(quiet):
    s = Session(buildings=[(B1, "Bishopsgate Tower")])
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID, building_ids=[B1, B2], user_id="u-1")
    assert out["reason"] == "building_not_found"
    assert not any(x.startswith("INSERT") for x in s.sql) and not quiet


async def test_an_assignment_already_there_is_left_as_it_is_and_not_recorded_again(quiet):
    s = Session(buildings=[(B1, "Bishopsgate Tower")], inserted=0)
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID, building_ids=[B1], user_id="u-1")
    assert out["assigned"] == [] and out["already"] == [{"id": B1, "name": "Bishopsgate Tower"}]
    assert quiet == []


async def test_a_retired_or_unknown_vendor_is_refused(monkeypatch, quiet):
    async def refusal(session, vendor_id, org, *, what=""):
        return {"ok": False, "reason": "vendor_deleted", "error": "deleted"}
    monkeypatch.setattr(vb, "vendor_link_refusal", refusal)
    s = Session(buildings=[(B1, "Bishopsgate Tower")])
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID, building_ids=[B1], user_id="u-1")
    assert out["reason"] == "vendor_deleted"
    assert not any(x.startswith("INSERT") for x in s.sql)


async def test_unassigning_ends_the_row_and_records_it(quiet):
    s = Session()
    out = await vb.unassign(s, organization_id=ORG, vendor_id=VID, building_id=B1, user_id="u-1")
    assert out == {"ok": True, "unassigned": B1}
    assert any(x.startswith("UPDATE plenum_cafm.vendor_buildings SET removed_at = now()") for x in s.sql)
    assert quiet[0]["action_type"] == vendor_audit.BUILDING_UNASSIGNED
    s2 = Session(ended=0)
    out2 = await vb.unassign(s2, organization_id=ORG, vendor_id=VID, building_id=B1, user_id="u-1")
    assert out2["reason"] == "not_assigned" and s2.rolled_back


def test_the_trail_reads_an_assignment_with_its_buildings():
    e = vendor_audit._ops_event({"id": "x", "action_type": vendor_audit.BUILDINGS_ASSIGNED,
                                 "created_at": datetime(2026, 10, 12, tzinfo=timezone.utc), "actor": None,
                                 "detail": {"vendor": {"id": VID, "name": "Harbourside"},
                                            "buildings": [{"id": B1, "name": "Bishopsgate Tower"}], "source": "hoist"}})
    assert e["kind"] == "assigned" and e["source"] == "hoist"
    assert e["vendor"]["name"] == "Harbourside" and e["buildings"] == [{"id": B1, "name": "Bishopsgate Tower"}]


# ── second review, 12 Oct 2026 ─────────────────────────────────────────────────

class CanonSession(Session):
    """Answers the vendor-id read with the id as the vendors row holds it."""

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        if sql.startswith("SELECT v.id::text FROM plenum_cafm.vendors"):
            self.sql.append(sql)
            return SimpleNamespace(scalar=lambda: VID)
        if sql.startswith("UPDATE plenum_cafm.vendor_buildings"):
            self.sql.append(sql)
            rows = [("Bishopsgate Tower",)] * self.ended
            return SimpleNamespace(all=lambda: rows, rowcount=self.ended)
        return await super().execute(stmt, params)


async def test_the_vendor_id_is_stored_as_the_vendors_row_holds_it(quiet):
    inserted = []
    s = CanonSession(buildings=[(B1, "Bishopsgate Tower")])
    orig = s.execute

    async def spy(stmt, params=None):
        if str(stmt).lstrip().startswith("INSERT"):
            inserted.append(params)
        return await orig(stmt, params)
    s.execute = spy
    out = await vb.assign(s, organization_id=ORG, vendor_id=VID.upper(), building_ids=[B1], user_id="u-1")
    assert out["ok"] is True
    assert inserted[0]["vid"] == VID, "an upper-case spelling must not make a second live row"


async def test_an_unassignment_names_the_building_in_the_trail(quiet):
    s = CanonSession()
    out = await vb.unassign(s, organization_id=ORG, vendor_id=VID, building_id=B1, user_id="u-1")
    assert out["ok"] is True
    assert quiet[0]["detail"]["buildings"] == [{"id": B1, "name": "Bishopsgate Tower"}]


def test_a_delete_names_what_it_could_not_check():
    e = vendor_audit._ops_event({"id": "d", "action_type": vendor_audit.DELETED,
                                 "created_at": datetime(2026, 10, 12, tzinfo=timezone.utc), "actor": None,
                                 "detail": {"vendor": {"id": VID, "name": "X"}, "mode": "remove",
                                            "unavailable": ["Linked documents"]}})
    assert e["unavailable"] == ["Linked documents"]


def test_a_one_building_reset_keeps_the_admins_assignments():
    from src.engines.admin import org_data_reset
    assert "vendor_buildings" in org_data_reset.COMPANY_WIDE
