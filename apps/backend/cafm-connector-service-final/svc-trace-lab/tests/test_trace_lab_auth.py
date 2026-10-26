"""trace_lab.auth: the caller is svc-deepagents' current_principal (reused), and the company and role
rules of G0 addendum 2.4. No database, no network: Principals are built directly."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException, Query
from fastapi.testclient import TestClient

from src.services import principal as principal_module
from src.services.principal import Principal
from trace_lab import auth

ORG = "00000000-0000-0000-0000-0000000000a1"
OTHER = "00000000-0000-0000-0000-0000000000b2"


def _p(role: str = "admin", org: str | None = ORG) -> Principal:
    return Principal(user_id=uuid.uuid4(), email="fm@example.com",
                     organization_id=uuid.UUID(org) if org else None, role=role,
                     can_ingest=True, building_ids=None)


def _raises(fn, *args) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        fn(*args)
    return e.value


def test_caller_reuses_svc_deepagents_current_principal():
    assert auth.caller.dependency is principal_module.current_principal


@pytest.mark.parametrize("role, org, named, acting", [
    ("superadmin", None, None, None),        # the platform view
    ("superadmin", None, OTHER, OTHER),      # acting for a company
    ("superadmin", ORG, None, None),         # a superadmin's own company is not implied
    ("admin", ORG, None, ORG),
    ("admin", ORG, ORG, ORG),                # naming your own company is the same as naming none
    ("admin", ORG, ORG.upper(), ORG),        # ids are compared canonically
    ("user", ORG, None, ORG),
])
def test_acting_org(role, org, named, acting):
    assert auth.acting_org(_p(role, org), named) == acting


@pytest.mark.parametrize("role", ["admin", "user"])
def test_only_a_superadmin_may_name_another_company(role):
    e = _raises(auth.acting_org, _p(role), OTHER)
    assert e.status_code == 403 and e.detail["reason"] == "bad_company"


def test_a_malformed_company_is_400():
    e = _raises(auth.acting_org, _p("superadmin", None), "not-a-uuid")
    assert e.status_code == 400 and e.detail["reason"] == "bad_company"


def test_require_reader():
    assert auth.require_reader(_p("admin"), None) == ORG
    assert auth.require_reader(_p("superadmin", None), None) is None
    assert auth.require_reader(_p("superadmin", None), OTHER) == OTHER
    for who in (_p("user"), _p("admin", None)):
        e = _raises(auth.require_reader, who, None)
        assert e.status_code == 403 and e.detail["reason"] == "reviewers_only"
    e = _raises(auth.require_reader, _p("admin"), OTHER)
    assert e.status_code == 403 and e.detail["reason"] == "bad_company"


def test_require_reviewer():
    assert auth.require_reviewer(_p("admin"), None) == ORG
    assert auth.require_reviewer(_p("superadmin", None), OTHER) == OTHER
    e = _raises(auth.require_reviewer, _p("user"), None)
    assert e.status_code == 403 and e.detail["reason"] == "reviewers_only"
    e = _raises(auth.require_reviewer, _p("superadmin", None), None)
    assert e.status_code == 400 and e.detail["reason"] == "name_a_company"
    e = _raises(auth.require_reviewer, _p("admin"), OTHER)
    assert e.status_code == 403 and e.detail["reason"] == "bad_company"


def test_require_platform():
    assert auth.require_platform(_p("superadmin", None)) is None
    assert auth.require_platform(_p("superadmin", ORG), None) is None
    for who, named in ((_p("superadmin", None), OTHER), (_p("admin"), None), (_p("user"), None)):
        e = _raises(auth.require_platform, who, named)
        assert e.status_code == 403 and e.detail["reason"] == "platform_only"


def test_errors_use_the_house_envelope():
    e = _raises(auth.require_reviewer, _p("user"), None)
    assert set(e.detail) == {"reason", "error"} and e.detail["error"] == auth.REVIEWERS_ONLY


def test_caller_works_as_a_route_dependency():
    app = FastAPI()

    @app.get("/who")
    async def who(principal: Principal = auth.caller, organization_id: str | None = Query(None)):
        return {"org": auth.require_reader(principal, organization_id)}

    app.dependency_overrides[principal_module.current_principal] = lambda: _p("admin")
    client = TestClient(app)
    assert client.get("/who").json() == {"org": ORG}
    r = client.get("/who", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"


def test_no_bearer_is_401_from_the_reused_resolver():
    app = FastAPI()

    @app.get("/who")
    async def who(principal: Principal = auth.caller):
        return {"ok": True}

    r = TestClient(app).get("/who")
    assert r.status_code == 401 and r.json()["detail"]["reason"] == "missing_token"
