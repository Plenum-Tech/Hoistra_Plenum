"""The answer to a sign-in already says which pages the person holds.

The client decides what to show the moment it is signed in — whether to offer the instance
picker, which registers to read. If it had to wait for a second call to learn the pages, a
person with an access role would be shown the picker, and their browser would ask for every
page's data, before it found out. So login, refresh and email verification each carry the
access role and the pages, with ``pages_known`` saying this answer is the authority on them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.api.schemas.auth import PublicUser
from src.app import app
from src.db import get_session
from src.engines.auth import keys
from src.engines.auth import pages as P
from src.engines.auth.tokens import Principal, TokenPair

from test_access_role_identity import ALL_THREE, COLUMNS, NONE_YET, Result, ScriptedSession, run

USER = uuid4()
ORG = UUID("11111111-1111-5111-8111-111111111111")
ROW = "SELECT access_role, allowed_pages FROM plenum_cafm.users"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    P.reset_cache()
    monkeypatch.setattr(keys, "_CACHE", keys.KeyShape(
        users="uuid", organizations="uuid", users_id_self_assigning=True))
    yield
    P.reset_cache()
    app.dependency_overrides.clear()


def session(row=None, *, ready=True):
    return ScriptedSession([(COLUMNS, ALL_THREE if ready else NONE_YET),
                            (ROW, Result([row] if row else []))])


def user(role="user"):
    return {"id": str(USER), "email": "a@b.com", "full_name": "A", "status": "active",
            "email_verified": True, "role": role}


def test_the_public_user_can_carry_the_pages():
    u = PublicUser(**user())
    assert u.access_role is None and u.pages is None and u.pages_known is False
    u = PublicUser(**user(), access_role="general_user", pages=["energy"], pages_known=True)
    assert u.model_dump()["pages"] == ["energy"]


def test_for_user_reads_the_row():
    s = session({"access_role": "compliance_officer", "allowed_pages": ["compliance", "buildings"]})
    assert run(P.for_user(s, str(USER), "user")) == ("compliance_officer",
                                                     frozenset({"compliance", "buildings"}))
    assert run(P.for_user(session({"access_role": None, "allowed_pages": None}), str(USER), "user")) == (None, None)


def test_for_user_asks_nothing_about_an_admin_or_before_the_migration():
    s = session({"access_role": "general_user", "allowed_pages": ["energy"]})
    assert run(P.for_user(s, str(USER), "admin")) == (None, None)
    assert not s.sql(ROW)
    s = session(ready=False)
    assert run(P.for_user(s, str(USER), "user")) == (None, None)
    assert not s.sql(ROW)


def test_a_sign_in_answer_gains_the_pages():
    out = {"ok": True, "user": user(), "tokens": {}}
    s = session({"access_role": "compliance_officer", "allowed_pages": ["compliance", "buildings"]})
    run(auth_routes._with_pages(s, out))
    assert out["user"]["access_role"] == "compliance_officer"
    assert out["user"]["access_role_label"] == "Compliance Officer"
    assert out["user"]["pages"] == ["buildings", "compliance"]
    assert out["user"]["pages_known"] is True


def test_a_normal_users_sign_in_says_every_page():
    out = {"ok": True, "user": user(), "tokens": {}}
    run(auth_routes._with_pages(session({"access_role": None, "allowed_pages": None}), out))
    assert out["user"]["pages"] is None and out["user"]["access_role"] is None
    assert out["user"]["pages_known"] is True


def test_a_failed_read_never_fails_the_sign_in():
    class Broken:
        async def execute(self, *a, **k):
            raise RuntimeError("database gone")
    out = {"ok": True, "user": user(), "tokens": {}}
    assert run(auth_routes._with_pages(Broken(), out)) is out
    # Not known: the client waits for /api/auth/me rather than assuming every page.
    assert "pages_known" not in out["user"] or out["user"]["pages_known"] is False


def test_login_carries_the_pages(monkeypatch):
    async def _sign_in(_session, **_k):
        return {"ok": True, "user": user(),
                "tokens": {"access_token": "a", "refresh_token": "r", "token_type": "bearer",
                           "expires_in": 60}}
    monkeypatch.setattr(auth_routes.acc, "sign_in", _sign_in)
    s = session({"access_role": "general_user", "allowed_pages": ["energy", "assets"]})

    async def _session():
        yield s
    app.dependency_overrides[get_session] = _session
    body = TestClient(app).post("/api/auth/login", json={"email": "a@b.com", "password": "x" * 12}).json()
    assert body["user"]["access_role"] == "general_user"
    assert body["user"]["pages"] == ["energy", "assets"]
    assert body["user"]["pages_known"] is True


def test_refresh_carries_the_pages_from_the_principal(monkeypatch):
    held = Principal(user_id=USER, email="a@b.com", organization_id=ORG, session_id=None,
                     issued_at=datetime.now(timezone.utc), password_changed_at=0, role="user",
                     access_role="property_manager", pages=frozenset({"ops", "buildings"}))

    async def _rotate(_session, **_k):
        return TokenPair(access_token="a", refresh_token="r", token_type="bearer", expires_in=60,
                         session_id=uuid4())

    async def _principal(_session, _token):
        return held

    async def _find(_session, _email, **_k):
        return {"email": "a@b.com"}
    monkeypatch.setattr(auth_routes.token_engine, "rotate_session", _rotate)
    monkeypatch.setattr(auth_routes.token_engine, "principal_from_token", _principal)
    monkeypatch.setattr(auth_routes.acc, "find_by_email", _find)
    monkeypatch.setattr(auth_routes.acc, "public_user", lambda row: user())

    async def _session():
        yield ScriptedSession([])
    app.dependency_overrides[get_session] = _session
    body = TestClient(app).post("/api/auth/refresh", json={"refresh_token": "r" * 40}).json()
    assert body["user"]["access_role"] == "property_manager"
    assert body["user"]["access_role_label"] == "Property Manager"
    assert body["user"]["pages"] == ["buildings", "ops"]
    assert body["user"]["pages_known"] is True
