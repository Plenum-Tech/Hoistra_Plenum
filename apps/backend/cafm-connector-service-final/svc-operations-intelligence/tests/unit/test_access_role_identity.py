"""The pages a caller holds are read with the caller, on every request.

The access role and its pages live on the account row and travel on the principal exactly as
the buildings do: resolved once where the token is checked, carried on the Scope, and returned
by /api/auth/me so the other services and the client can shape themselves from it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import access, keys
from src.engines.auth import pages as P
from src.engines.auth import tokens as T

ORG = UUID("11111111-1111-5111-8111-111111111111")
USER = uuid4()
PWD_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
NARROWED = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)


class Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        row = self.first()
        if isinstance(row, dict):
            return next(iter(row.values()), None)
        return row

    def scalars(self):
        return Result([next(iter(r.values())) if isinstance(r, dict) else r for r in self._rows])


class ScriptedSession:
    """Answers each statement by the first needle its SQL contains, and remembers what ran."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, sql, params=None):
        text_ = str(sql)
        self.calls.append((text_, dict(params or {})))
        for needle, answer in self.script:
            if needle in text_:
                return answer(params or {}) if callable(answer) else answer
        return Result([])

    def sql(self, needle):
        return [s for s, _ in self.calls if needle in s]

    def add(self, _row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


COLUMNS = "column_name IN ('access_role'"
ALL_THREE = Result([{"n": 3}])
NONE_YET = Result([{"n": 0}])


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    P.reset_cache()
    monkeypatch.setattr(keys, "_CACHE", keys.KeyShape(
        users="uuid", organizations="uuid", users_id_self_assigning=True))
    yield
    P.reset_cache()
    app.dependency_overrides.clear()


def user_row(**over):
    row = {
        "id": USER, "email": "a@b.com", "organization_id": ORG, "status": "active",
        "password_changed_at": PWD_AT, "role": "user", "can_ingest": False,
        "selected_building_id": None, "revoked_at": None, "expires_at": None,
        "revoked_reason": None,
    }
    row.update(over)
    return row


def token():
    return T.issue_access_token(user_id=USER, email="a@b.com", organization_id=ORG,
                                session_id=None, password_changed_at=PWD_AT)[0]


def principal_for(row, *, ready=True):
    session = ScriptedSession([
        (COLUMNS, ALL_THREE if ready else NONE_YET),
        ("FROM plenum_cafm.users u", Result([row])),
        ("FROM plenum_cafm.user_buildings", Result([])),
    ])
    return run(T.principal_from_token(session, token())), session


# ── are the columns there? ────────────────────────────────────────────────────

def test_columns_ready_true_is_cached_for_the_process():
    session = ScriptedSession([(COLUMNS, ALL_THREE)])
    assert run(P.columns_ready(session)) is True
    assert run(P.columns_ready(session)) is True
    assert len(session.sql(COLUMNS)) == 1


def test_columns_missing_is_rechecked_after_a_minute(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(P, "_monotonic", lambda: clock[0])
    session = ScriptedSession([(COLUMNS, NONE_YET)])
    assert run(P.columns_ready(session)) is False
    assert run(P.columns_ready(session)) is False
    assert len(session.sql(COLUMNS)) == 1          # not asked again inside the minute
    clock[0] += 61
    session.script = [(COLUMNS, ALL_THREE)]        # the migration has been applied since
    assert run(P.columns_ready(session)) is True
    assert len(session.sql(COLUMNS)) == 2


def test_a_failed_check_reads_as_not_ready_rather_than_raising():
    def boom(_params):
        raise RuntimeError("information_schema unavailable")
    session = ScriptedSession([(COLUMNS, boom)])
    assert run(P.columns_ready(session)) is False


# ── the principal ─────────────────────────────────────────────────────────────

def test_a_normal_user_is_unrestricted():
    p, _ = principal_for(user_row(access_role=None, allowed_pages=None, access_narrowed_at=None))
    assert p.access_role is None
    assert p.pages is None
    assert p.access_narrowed_at is None


def test_a_role_user_principal_carries_role_and_pages():
    p, session = principal_for(user_row(
        access_role="compliance_officer", allowed_pages=["compliance", "buildings"],
        access_narrowed_at=NARROWED))
    assert p.access_role == "compliance_officer"
    assert p.pages == frozenset({"compliance", "buildings"})
    assert p.access_narrowed_at == NARROWED
    assert "u.access_role" in session.sql("FROM plenum_cafm.users u")[0]


def test_an_admin_row_with_a_role_is_unrestricted():
    p, _ = principal_for(user_row(
        role="admin", access_role="compliance_officer", allowed_pages=["compliance"],
        access_narrowed_at=NARROWED))
    assert p.access_role is None
    assert p.pages is None


def test_junk_in_the_columns_fails_closed():
    p, _ = principal_for(user_row(
        access_role="general_user", allowed_pages=["payroll"], access_narrowed_at=None))
    assert p.pages == frozenset()


def test_signin_query_omits_the_columns_when_they_do_not_exist():
    # Before the migration is applied, naming the columns would fail every request and
    # nobody could sign in. Everybody is a normal user until the columns exist.
    p, session = principal_for(user_row(), ready=False)
    assert p.pages is None and p.access_role is None
    users_sql = session.sql("FROM plenum_cafm.users u")[0]
    assert "access_role" not in users_sql
    assert "allowed_pages" not in users_sql


# ── the scope ─────────────────────────────────────────────────────────────────

def principal(role="user", pages=None, access_role=None, narrowed_at=None, buildings=()):
    return T.Principal(
        user_id=USER, email="a@b.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=False, building_ids=buildings, access_role=access_role, pages=pages,
        access_narrowed_at=narrowed_at)


def test_scope_allows_every_page_when_unrestricted():
    s = access.scope_for(principal())
    assert s.pages is None
    assert all(s.allows_page(p) for p in P.PAGES)
    assert s.allows_any("energy", "assets")


def test_scope_refuses_an_unheld_page():
    s = access.scope_for(principal(pages=frozenset({"compliance", "buildings"}),
                                   access_role="compliance_officer", narrowed_at=NARROWED))
    assert s.access_role == "compliance_officer"
    assert s.access_narrowed_at == NARROWED
    assert s.allows_page("compliance") and s.allows_page("buildings")
    assert not s.allows_page("energy")
    assert s.allows_any("energy", "compliance")
    assert not s.allows_any("energy", "vendors")
    assert not s.allows_any()


def test_assert_page_refuses_with_the_agreed_body():
    s = access.scope_for(principal(pages=frozenset({"compliance"})))
    access.assert_page(s, "energy", "compliance")           # any-of: held
    with pytest.raises(HTTPException) as e:
        access.assert_page(s, "energy", "vendors")
    assert e.value.status_code == 403
    assert e.value.detail == {
        "ok": False, "reason": "page_not_allowed", "page": "energy",
        "error": "That is not part of the pages you have access to."}


def test_assert_pages_all_needs_every_page():
    s = access.scope_for(principal(pages=frozenset({"assets"})))
    access.assert_pages_all(s, "assets")
    with pytest.raises(HTTPException) as e:
        access.assert_pages_all(s, "assets", "ops")
    assert e.value.detail["reason"] == "page_not_allowed"
    assert e.value.detail["page"] == "ops"
    access.assert_pages_all(access.scope_for(principal()), "assets", "ops")   # unrestricted


def test_unscoped_scope_is_unrestricted(monkeypatch):
    from src import config
    monkeypatch.setattr(config.settings, "auth_enforce_scope", False)

    async def _never():
        raise AssertionError("not reached when enforcement is off")
    scope_dep, _ingest = access.bind(_never)[:2]
    s = run(scope_dep(organization_id=None))
    assert s.pages is None and s.allows_page("energy")


# ── /api/auth/me and /api/auth/roles ──────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    async def _session():
        yield ScriptedSession([])
    app.dependency_overrides[get_session] = _session

    async def _find(_session, _email, **_k):
        return {"email": "a@b.com"}
    monkeypatch.setattr(auth_routes.acc, "find_by_email", _find)
    monkeypatch.setattr(auth_routes.acc, "public_user", lambda row: {"email": row["email"]})
    return TestClient(app)


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


def test_me_reports_pages_null_for_a_normal_user(client):
    as_(principal())
    user = client.get("/api/auth/me").json()["user"]
    assert user["access_role"] is None
    assert user["access_role_label"] is None
    assert user["pages"] is None
    assert user["access_narrowed_at"] is None


def test_me_reports_role_and_pages_in_order(client):
    as_(principal(pages=frozenset({"assets", "compliance", "buildings"}),
                  access_role="compliance_officer", narrowed_at=NARROWED))
    user = client.get("/api/auth/me").json()["user"]
    assert user["access_role"] == "compliance_officer"
    assert user["access_role_label"] == "Compliance Officer"
    assert user["pages"] == ["buildings", "compliance", "assets"]
    assert user["access_narrowed_at"] == NARROWED.isoformat()


def test_me_reports_an_empty_list_for_a_role_with_no_page(client):
    as_(principal(pages=frozenset(), access_role="general_user"))
    assert client.get("/api/auth/me").json()["user"]["pages"] == []


def test_roles_lists_the_access_roles_and_pages(client):
    body = client.get("/api/auth/roles").json()
    assert body["access_roles"] == [
        {"key": "portfolio_management", "label": "Portfolio Management"},
        {"key": "property_manager", "label": "Property Manager"},
        {"key": "general_user", "label": "General User"},
        {"key": "compliance_officer", "label": "Compliance Officer"},
    ]
    assert body["pages"] == ["buildings", "compliance", "energy", "vendors", "ops", "assets"]
    assert "roles" in body and "default" in body          # what it returned before, kept
