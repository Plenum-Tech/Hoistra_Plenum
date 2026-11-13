"""The connector and import routes act for the caller's company, and a role user needs the ingest
right to use them (13 Nov review).

They took the company from the request: an import named its own ``organization_id`` and target
table, so any signed-in caller could write rows into another company; the connector list
returned every company's connectors; and a caller held to some pages, with no right to ingest,
could import into a register they cannot see. Notifications, tagged as a person's own, listed
everybody's.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("JWT_SECRET", "test-secret")

from cafm_connector.api import security as P  # noqa: E402
from cafm_connector.api.dependencies import get_service  # noqa: E402
from cafm_connector.api.routes import connectors as C  # noqa: E402

ORG, OTHER = uuid4(), uuid4()


def caller(role="user", pages=None, can_ingest=False, org=ORG):
    return P.Principal(user_id=uuid4(), email="a@b.com", organization_id=org, role=role,
                       building_ids=None, pages=pages, can_ingest=can_ingest)


def connector(org):
    return SimpleNamespace(id="c1", name="n", source_type="csv", description=None, is_active=True,
                           created_at=datetime(2026, 11, 13),
                           options={"organization_id": str(org)} if org else {})


class _Result:
    def __init__(self, obj):
        self.obj = obj

    def scalar_one_or_none(self):
        return self.obj


class FakeService:
    def __init__(self, conn=None, job=None):
        self.conn, self.job = conn, job
        self.listed = []
        self.saved = []
        self.ran = []
        self.deleted = []
        self._session = SimpleNamespace(execute=self._execute)

    async def _execute(self, *_a, **_k):
        return _Result(self.job)

    async def list_connectors(self, organization_id=None):
        self.listed.append(organization_id)
        return []

    async def get_connector(self, connector_id):
        return self.conn

    async def save_connector(self, **kw):
        self.saved.append(kw)
        return connector(ORG)

    async def delete_connector(self, connector_id):
        self.deleted.append(connector_id)

    async def create_import_job(self, **kw):
        self.ran.append(kw)
        return SimpleNamespace(id="j1", status="queued", created_at=datetime(2026, 11, 13))

    async def get_job_status(self, job_id):
        raise AssertionError("read another company's job")


def make(principal, svc):
    app = FastAPI()
    app.include_router(C.router)

    async def _p():
        return principal

    async def _s():
        return svc
    app.dependency_overrides[P.current_principal] = _p
    app.dependency_overrides[get_service] = _s
    return TestClient(app, raise_server_exceptions=False)


def test_the_list_is_the_callers_company():
    svc = FakeService()
    make(caller(), svc).get("/connectors")
    assert svc.listed == [str(ORG)]


def test_naming_another_company_in_the_list_is_refused():
    svc = FakeService()
    r = make(caller(), svc).get("/connectors", params={"organization_id": str(OTHER)})
    assert r.status_code == 403 and not svc.listed


def test_a_superadmin_lists_the_company_named_or_every_one():
    svc = FakeService()
    c = make(caller(role="superadmin"), svc)
    c.get("/connectors", params={"organization_id": str(OTHER)})
    c.get("/connectors")
    assert svc.listed == [str(OTHER), None]


def test_a_saved_connector_is_stamped_with_the_callers_company():
    svc = FakeService()
    r = make(caller(), svc).post("/connectors", json={
        "name": "n", "source_type": "csv", "connection_params": {}, "credentials": {}, "options": {}})
    assert r.status_code == 201, r.text
    assert svc.saved[0]["options"]["organization_id"] == str(ORG)


def test_saving_a_connector_for_another_company_is_refused():
    svc = FakeService()
    r = make(caller(), svc).post("/connectors", json={
        "name": "n", "source_type": "csv", "connection_params": {}, "credentials": {},
        "options": {"organization_id": str(OTHER)}})
    assert r.status_code == 403 and not svc.saved


def test_a_file_import_for_another_company_is_refused_before_anything_is_uploaded():
    svc = FakeService()
    r = make(caller(), svc).post("/imports/file/run", files={"file": ("a.csv", b"x\n1\n")},
                                 data={"source_type": "csv", "organization_id": str(OTHER)})
    assert r.status_code == 403


@pytest.mark.parametrize("method,path", [
    ("GET", "/connectors/c1"), ("DELETE", "/connectors/c1")])
def test_another_companys_connector_is_not_found(method, path):
    svc = FakeService(conn=connector(OTHER))
    r = make(caller(), svc).request(method, path)
    assert r.status_code == 404 and not svc.deleted


def test_a_connector_with_no_company_is_a_superadmins_only():
    assert make(caller(), FakeService(conn=connector(None))).get("/connectors/c1").status_code == 404
    assert make(caller(role="superadmin"),
                FakeService(conn=connector(None))).get("/connectors/c1").status_code == 200


def test_your_own_companys_connector_is_found():
    assert make(caller(), FakeService(conn=connector(ORG))).get("/connectors/c1").status_code == 200


def test_importing_from_another_companys_connector_is_not_found():
    svc = FakeService(conn=connector(OTHER))
    r = make(caller(), svc).post("/imports/run", json={"connector_id": "c1"})
    assert r.status_code == 404 and not svc.ran


@pytest.mark.parametrize("method,path", [
    ("GET", "/imports/j1/status"), ("GET", "/imports/j1/log"), ("PUT", "/imports/j1/cancel")])
def test_another_companys_job_is_not_found(method, path):
    svc = FakeService(conn=connector(OTHER), job=SimpleNamespace(id="j1", connector_id="c1",
                                                                 status="queued"))
    assert make(caller(), svc).request(method, path).status_code == 404


def test_a_role_user_without_the_ingest_right_is_refused_every_import_route():
    svc = FakeService(conn=connector(ORG))
    c = make(caller(pages=frozenset({"assets"})), svc)
    for method, path in (("GET", "/connectors"), ("POST", "/imports/run"),
                         ("GET", "/connectors/c1")):
        r = c.request(method, path, json={"connector_id": "c1"} if method == "POST" else None)
        assert r.status_code == 403, (path, r.status_code)
        assert r.json()["detail"]["reason"] == "no_ingest_right"
    assert not svc.listed and not svc.ran


def test_a_role_user_with_the_ingest_right_and_a_normal_user_without_it_get_through():
    for p in (caller(pages=frozenset({"assets"}), can_ingest=True), caller()):
        svc = FakeService()
        assert make(p, svc).get("/connectors").status_code == 200


def test_the_ingest_right_travels_with_the_caller():
    me = {"user": {"id": str(uuid4()), "email": "a@b.com", "organization_id": str(ORG),
                   "role": "user", "building_ids": None, "can_ingest": True}}
    assert P._from_me(me).can_ingest is True
    me["user"]["can_ingest"] = False
    assert P._from_me(me).can_ingest is False


# ── notifications are a person's own ───────────────────────────────────────────────

def test_a_role_users_notifications_are_their_own():
    from cafm_connector.api.routes.plenum_cafm import inventory_notifications as N
    me = caller(pages=frozenset({"assets"}))
    assert N.own_only(me) == me.user_id
    assert N.own_only(caller()) is None
    assert N.own_only(caller(role="admin")) is None
