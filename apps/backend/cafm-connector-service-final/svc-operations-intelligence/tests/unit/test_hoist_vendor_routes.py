"""The two certificate routes behind Hoist vendors: who may call them and whose certificate.

POST /api/compliance/certificates/{id}/create-vendor and /link write to a certificate and to the
vendor list. They asked for no scope at all, so any signed-in caller could name another
company's certificate and create a vendor in that company, or re-point its certificate
(pre-push review, 12 Oct 2026). Both are now an admin's, and only for their own company's
certificate - the same rule Hoist vendors follows on screen. A superadmin viewing a company acts
in it. The engine is replaced here; what it writes is covered elsewhere.

Also here: a name that matches a vendor kept under an older, non-uuid id (`V-07`) is refused in
words instead of failing as a 500 when that id is written to the certificate's uuid column.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.api.routes import compliance as compliance_routes
from src.app import app
from src.db import get_session
from src.engines.auth.tokens import Principal
from src.engines.compliance import certificates as cert_svc

ORG = UUID("11111111-1111-5111-8111-111111111111")
MINE = "33333333-3333-5333-8333-333333333333"
THEIRS = "44444444-4444-5444-8444-444444444444"
ASSET = "55555555-5555-5555-8555-555555555555"


def principal(role: str) -> Principal:
    return Principal(
        user_id=uuid4(), email=f"{role}@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=True, building_ids=None,
    )


class Engine:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def create(self):
        async def _f(session, certificate_id, **kw):
            self.calls.append(("create", {"certificate_id": str(certificate_id), **kw}))
            return {"ok": True, "vendor_id": str(uuid4()), "created": True}
        return _f

    def link(self):
        async def _f(session, certificate_id, **kw):
            self.calls.append(("link", {"certificate_id": str(certificate_id), **kw}))
            return {"ok": True}
        return _f


@pytest.fixture
def env(monkeypatch):
    eng = Engine()
    owned: list[tuple[str, str, str]] = []

    async def assert_owned(session, scope, table, row_id, *, action="read", id_column="id"):
        owned.append((table, str(row_id), action))
        if str(row_id) == THEIRS:
            raise HTTPException(status_code=404, detail={"ok": False, "reason": "not_found"})

    monkeypatch.setattr(compliance_routes.access, "assert_owned", assert_owned)
    monkeypatch.setattr(compliance_routes.cert_svc, "create_vendor_for_certificate", eng.create())
    monkeypatch.setattr(compliance_routes.cert_svc, "link_certificate_document", eng.link())

    async def _s():
        yield object()
    app.dependency_overrides[get_session] = _s
    try:
        yield SimpleNamespace(engine=eng, owned=owned)
    finally:
        app.dependency_overrides.clear()


def as_(role: str) -> None:
    p = principal(role)

    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


def create(cert: str):
    return TestClient(app).post(f"/api/compliance/certificates/{cert}/create-vendor", json={"vendor_name": "Crown Gas Services"})


def link(cert: str, **body):
    return TestClient(app).post(f"/api/compliance/certificates/{cert}/link", json=body or {"vendor_id": str(uuid4())})


def test_a_plain_user_cannot_hoist_a_vendor_or_relink_a_certificate(env):
    as_("user")
    assert create(MINE).status_code == 403
    assert link(MINE).status_code == 403
    assert env.engine.calls == []


def test_an_admin_hoists_from_their_own_companys_certificate(env):
    as_("admin")
    r = create(MINE)
    assert r.status_code == 200, r.text
    assert env.engine.calls[0][0] == "create"
    assert ("compliance_certificates", MINE, "change") in env.owned
    r = link(MINE)
    assert r.status_code == 200, r.text
    assert env.engine.calls[1][0] == "link"


def test_another_companys_certificate_is_not_found_and_nothing_is_written(env):
    as_("admin")
    assert create(THEIRS).status_code == 404
    assert link(THEIRS).status_code == 404
    assert env.engine.calls == []


def test_an_asset_named_on_a_link_must_be_the_callers_too(env):
    as_("admin")
    r = link(MINE, asset_id=THEIRS)
    assert r.status_code == 404
    assert env.engine.calls == []
    r = link(MINE, asset_id=ASSET)
    assert r.status_code == 200, r.text
    assert ("assets", ASSET, "change") in env.owned


def test_a_superadmin_viewing_a_company_may_hoist(env):
    as_("superadmin")
    assert create(MINE).status_code == 200


# ── the engine: a name kept under an older vendor id ──────────────────────────


class FakeSession:
    def __init__(self, cert):
        self.cert = cert
        self.executed: list[str] = []
        self.flushed = False

    async def get(self, model, ident):
        return self.cert

    async def execute(self, stmt, params=None):
        self.executed.append(str(stmt))
        return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: None))

    async def flush(self):
        self.flushed = True

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.mark.asyncio
async def test_a_name_matching_a_legacy_vendor_id_is_refused_in_words_not_a_500(monkeypatch):
    cert = SimpleNamespace(organization_id=ORG, org_id=None, raw_metadata={}, document_id=None, vendor_id=None)
    session = FakeSession(cert)

    async def find_vendor_id(session, name, organization_id=None):
        return "V-07"
    monkeypatch.setattr(cert_svc, "find_vendor_id", find_vendor_id)
    out = await cert_svc.create_vendor_for_certificate(session, uuid4(), vendor_name="Gough & Kelly Limited")
    assert out["ok"] is False
    assert out["reason"] == "legacy_vendor_id"
    assert "Gough & Kelly Limited" in out["error"]
    assert cert.vendor_id is None, "the certificate is left as it was"
    assert session.flushed is False
    assert not any(s.lstrip().upper().startswith(("UPDATE", "INSERT")) for s in session.executed)
