"""A document is linked only to a vendor that exists, is the caller's company's, and is not deleted;
a building's vendor list leaves a deleted vendor out.

document_register.link accepted any entity_id for a vendor link: a document could be linked to
a vendor that never existed, to another company's, or — after a vendor was deleted (Hussain,
12 Oct 2026) — straight back onto the retired row, whose links the delete had just ended. The
building ontology is what an upload is checked against ("does this vendor work here?"), and a
deleted vendor must not vouch for a document. No database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.engines import document_register as reg
from src.engines.ingestion import ontology
from src.shared.vendor_identity import live_vendor_sql

ORG = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("99999999-9999-9999-9999-999999999999")
DOC = "55555555-5555-5555-5555-555555555555"
USER = UUID("22222222-2222-2222-2222-222222222222")


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Session:
    """``vendor`` is the vendors row the link check reads (None = no such vendor); a new link's
    INSERT returns an id. Records every statement."""

    def __init__(self, vendor=None, broken=False):
        self.vendor, self.broken = vendor, broken
        self.sql: list[tuple[str, dict]] = []
        self.added: list = []
        self.committed = False

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append((sql, dict(params or {})))
        if "FROM plenum_cafm.vendors v" in sql:
            if self.broken:
                raise RuntimeError("connection reset")
            return _Result([self.vendor] if self.vendor else [])
        if sql.lstrip().startswith("INSERT INTO plenum_cafm.document_links"):
            return _Result([("66666666-6666-6666-6666-666666666666",)])
        return _Result()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True


async def _link(s, vendor_id="V-01"):
    return await reg.link(s, document_id=DOC, entity_type="vendor", entity_id=vendor_id,
                          relation="insurance", organization_id=ORG, user_id=USER)


def _inserted(s: _Session) -> bool:
    return any(q.lstrip().startswith("INSERT") for q, _ in s.sql)


# ── the link ────────────────────────────────────────────────────────────────────────────


async def test_a_document_links_to_the_companys_live_vendor():
    s = _Session(vendor={"organization_id": str(ORG), "live": True})
    out = await _link(s)
    assert out["ok"] is True and out["created"] is True
    sql, params = next((q, p) for q, p in s.sql if "FROM plenum_cafm.vendors v" in q)
    assert live_vendor_sql("v") in sql and params == {"vid": "V-01"}


async def test_a_vendor_that_does_not_exist_takes_no_link():
    s = _Session(vendor=None)
    with pytest.raises(reg.RegisterError) as e:
        await _link(s, "nope")
    assert e.value.reason == "not_found" and str(e.value) == "No such vendor in your company."
    assert not _inserted(s) and not s.committed


async def test_another_companys_vendor_answers_as_if_it_did_not_exist():
    s = _Session(vendor={"organization_id": str(OTHER), "live": True})
    with pytest.raises(reg.RegisterError) as e:
        await _link(s)
    assert e.value.reason == "not_found"
    assert not _inserted(s)


async def test_a_vendor_naming_no_company_is_not_handed_to_one():
    s = _Session(vendor={"organization_id": None, "live": True})
    with pytest.raises(reg.RegisterError) as e:
        await _link(s)
    assert e.value.reason == "not_found"


async def test_a_deleted_vendor_takes_no_new_link():
    s = _Session(vendor={"organization_id": str(ORG), "live": False})
    with pytest.raises(reg.RegisterError) as e:
        await _link(s)
    assert e.value.reason == "vendor_deleted"
    assert str(e.value) == "This vendor has been deleted, so documents can no longer be linked to it."
    assert not _inserted(s) and not s.committed


async def test_a_vendor_that_cannot_be_checked_takes_no_link():
    s = _Session(broken=True)
    with pytest.raises(reg.RegisterError) as e:
        await _link(s)
    assert e.value.reason == "vendor_unreadable"
    assert not _inserted(s)


async def test_a_building_link_is_not_checked_against_the_vendors():
    s = _Session()
    out = await reg.link(s, document_id=DOC, entity_type="building", entity_id=str(UUID(int=7)),
                         relation=None, organization_id=ORG, user_id=USER)
    assert out["ok"] is True
    assert not any("plenum_cafm.vendors" in q for q, _ in s.sql)


# ── the route ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from src.api.routes import auth as auth_routes
    from src.api.routes import document_search as routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def owned(*_a, **_k):
        return {"document_id": DOC}

    raised: dict = {}

    async def link(*_a, **_k):
        raise reg.RegisterError(raised["reason"], raised["message"])

    async def _session():
        yield _Session()

    async def _principal():
        return Principal(user_id=USER, email="admin@example.com", organization_id=ORG,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role="admin", can_ingest=True, building_ids=None)

    monkeypatch.setattr(routes.reg, "owned_document", owned)
    monkeypatch.setattr(routes.reg, "link", link)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        yield TestClient(app), raised
    finally:
        app.dependency_overrides.clear()


def test_linking_to_a_deleted_vendor_is_a_conflict(client):
    c, raised = client
    raised.update(reason="vendor_deleted",
                  message="This vendor has been deleted, so documents can no longer be linked to it.")
    r = c.post(f"/api/documents/{DOC}/links", json={"entity_type": "vendor", "entity_id": "V-01"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"ok": False, "reason": "vendor_deleted", "error": raised["message"]}


def test_a_vendor_that_could_not_be_checked_is_a_503(client):
    c, raised = client
    raised.update(reason="vendor_unreadable", message="x")
    r = c.post(f"/api/documents/{DOC}/links", json={"entity_type": "vendor", "entity_id": "V-01"})
    assert r.status_code == 503


# ── the building ontology ───────────────────────────────────────────────────────────────


class _OntologySession:
    def __init__(self):
        self.sql: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if "FROM plenum_cafm.buildings b" in sql and "LIMIT 1" in sql:
            return _Result([{"building_id": str(UUID(int=7)), "name": "Harbour Point"}])
        return _Result()


async def test_a_buildings_vendors_leave_out_a_deleted_vendor():
    s = _OntologySession()
    await ontology.load(s, UUID(int=7))
    vendors_sql = next(q for q in s.sql if "SELECT DISTINCT v.vendor_name" in q)
    assert live_vendor_sql("v") in vendors_sql


async def test_where_a_vendor_works_is_never_answered_for_a_deleted_one():
    s = _OntologySession()
    await ontology.vendor_buildings(s, "Acme Lifts Ltd")
    assert live_vendor_sql("v") in s.sql[0]
