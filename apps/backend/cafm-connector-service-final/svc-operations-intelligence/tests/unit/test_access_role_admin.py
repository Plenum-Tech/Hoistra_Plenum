"""An administrator gives a user a role and ticks the pages that go with it.

Inviting and editing are the only two places the access role and pages are written. These pin
what is accepted, what is stored, when a narrowing is stamped, and that a user without a role
is written exactly as before — including on a database that does not have the columns yet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import admin as admin_routes
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import invitations as invite_engine
from src.engines.auth import pages as P
from src.engines.auth.tokens import Principal

from test_access_role_identity import ALL_THREE, COLUMNS, NONE_YET, Result, ScriptedSession, run

ORG = UUID("11111111-1111-5111-8111-111111111111")
TARGET = uuid4()
ONE_USER = "WHERE id = :i AND organization_id = :o"


def admin() -> Principal:
    return Principal(
        user_id=uuid4(), email="admin@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role="admin",
        can_ingest=True, building_ids=None)


@pytest.fixture(autouse=True)
def _fresh():
    P.reset_cache()
    yield
    P.reset_cache()
    app.dependency_overrides.clear()


def client_with(session) -> TestClient:
    async def _session():
        yield session

    async def _admin():
        return admin()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _admin
    return TestClient(app)


def detail(response) -> dict:
    return response.json()["detail"]


# ── inviting ──────────────────────────────────────────────────────────────────

@pytest.fixture
def invited(monkeypatch):
    """Capture what the route hands the invitation engine and the audit trail."""
    seen: dict = {}

    async def _create(_session, **kw):
        seen["create"] = kw
        return {"ok": True, "user_id": str(TARGET)}

    async def _audit(_session, **kw):
        seen["audit"] = kw
    monkeypatch.setattr(admin_routes.invite_engine, "create", _create)
    monkeypatch.setattr(admin_routes, "write_audit", _audit)
    return seen


INVITE = {"full_name": "Ada Okafor", "email": "ada@example.com"}


def test_invite_without_a_role_is_a_normal_user(invited):
    r = client_with(ScriptedSession([(COLUMNS, ALL_THREE)])).post(
        "/api/admin/users/invite", json=INVITE)
    assert r.status_code == 201, r.text
    assert invited["create"]["access_role"] is None
    assert invited["create"]["pages"] is None


def test_invite_with_role_needs_a_page(invited):
    c = client_with(ScriptedSession([(COLUMNS, ALL_THREE)]))
    for body in ({**INVITE, "access_role": "general_user"},
                 {**INVITE, "access_role": "general_user", "pages": []}):
        r = c.post("/api/admin/users/invite", json=body)
        assert r.status_code == 400, r.text
        assert detail(r)["reason"] == "pages_required"
        assert detail(r)["error"] == "Tick at least one page for this role."
    assert "create" not in invited


def test_invite_with_role_passes_role_and_pages_in_order(invited):
    r = client_with(ScriptedSession([(COLUMNS, ALL_THREE)])).post(
        "/api/admin/users/invite",
        json={**INVITE, "access_role": "compliance_officer",
              "pages": ["compliance", "buildings", "compliance"]})
    assert r.status_code == 201, r.text
    assert invited["create"]["access_role"] == "compliance_officer"
    assert invited["create"]["pages"] == ["buildings", "compliance"]
    assert invited["audit"]["input_payload"]["access_role"] == "compliance_officer"
    assert invited["audit"]["input_payload"]["pages"] == ["buildings", "compliance"]


def test_invite_refuses_a_role_for_an_admin(invited):
    r = client_with(ScriptedSession([(COLUMNS, ALL_THREE)])).post(
        "/api/admin/users/invite",
        json={**INVITE, "role": "admin", "access_role": "general_user", "pages": ["energy"]})
    assert r.status_code == 400
    assert detail(r)["reason"] == "role_needs_plain_user"
    assert "create" not in invited


@pytest.mark.parametrize("body,reason", [
    ({"access_role": "night_porter", "pages": ["energy"]}, "unknown_access_role"),
    ({"access_role": "general_user", "pages": ["payroll"]}, "unknown_page"),
    ({"pages": ["energy"]}, "pages_without_role"),
])
def test_invite_refuses_a_bad_assignment(invited, body, reason):
    r = client_with(ScriptedSession([(COLUMNS, ALL_THREE)])).post(
        "/api/admin/users/invite", json={**INVITE, **body})
    assert r.status_code == 400
    assert detail(r)["reason"] == reason
    assert "create" not in invited


def test_invite_with_role_before_migration_is_409(invited):
    r = client_with(ScriptedSession([(COLUMNS, NONE_YET)])).post(
        "/api/admin/users/invite",
        json={**INVITE, "access_role": "general_user", "pages": ["energy"]})
    assert r.status_code == 409
    assert detail(r)["reason"] == "access_roles_not_set_up"
    assert "create" not in invited


def test_a_normal_invite_still_works_before_the_migration(invited):
    r = client_with(ScriptedSession([(COLUMNS, NONE_YET)])).post(
        "/api/admin/users/invite", json=INVITE)
    assert r.status_code == 201, r.text
    assert invited["create"]["access_role"] is None


# ── the invitation engine writes the user row ────────────────────────────────

def engine_session(*, ready=True, existing=None):
    return ScriptedSession([
        (COLUMNS, ALL_THREE if ready else NONE_YET),
        ("WHERE lower(email) = :e", Result([existing] if existing else [])),
        ("INSERT INTO plenum_cafm.users", Result([{"id": TARGET}])),
        ("INSERT INTO plenum_cafm.invitations", Result([{"id": uuid4()}])),
        ("FROM plenum_cafm.organizations", Result([{"name": "Northbridge"}])),
    ])


@pytest.fixture
def no_email(monkeypatch):
    async def _send(*_a, **_k):
        return {"ok": True, "status": "sent"}
    monkeypatch.setattr(invite_engine, "send_platform_email", _send)
    monkeypatch.setattr(invite_engine.settings, "email_dry_run", True, raising=False)


def create(session, **over):
    kw = dict(organization_id=ORG, email="ada@example.com", full_name="Ada Okafor",
              role="user", can_ingest=False, building_ids=[], invited_by=uuid4())
    kw.update(over)
    return run(invite_engine.create(session, **kw))


def access_writes(session):
    return [(s, p) for s, p in session.calls if "access_role = :ar" in s]


def test_engine_writes_role_and_pages_on_the_user_row(no_email):
    s = engine_session()
    create(s, access_role="compliance_officer", pages=["buildings", "compliance"])
    (sql, params), = access_writes(s)
    assert "UPDATE plenum_cafm.users" in sql
    assert params["ar"] == "compliance_officer"
    assert params["ap"] == ["buildings", "compliance"]
    assert params["i"] == TARGET


def test_engine_leaves_the_access_columns_alone_before_the_migration(no_email):
    s = engine_session(ready=False)
    create(s)
    assert not [sql for sql, _ in s.calls
                if ("access_role" in sql or "allowed_pages" in sql)
                and "information_schema" not in sql]


def test_engine_refuses_a_role_before_the_migration(no_email):
    s = engine_session(ready=False)
    with pytest.raises(invite_engine.InvitationError) as e:
        create(s, access_role="general_user", pages=["energy"])
    assert e.value.reason == "access_roles_not_set_up"
    assert e.value.http_status == 409
    assert not [sql for sql, _ in s.calls if "INSERT INTO plenum_cafm.users" in sql]


def test_engine_clears_an_earlier_role_when_reinvited_without_one(no_email):
    s = engine_session(existing={
        "id": TARGET, "organization_id": ORG, "status": "invited",
        "platform_role": "user", "access_role": "general_user", "allowed_pages": ["energy"]})
    create(s)
    (_sql, params), = access_writes(s)
    assert params["ar"] is None and params["ap"] is None
    assert params["narrowed"] is False


def test_engine_stamps_a_reinvite_that_takes_a_page_away(no_email):
    s = engine_session(existing={
        "id": TARGET, "organization_id": ORG, "status": "inactive",
        "platform_role": "user", "access_role": "general_user",
        "allowed_pages": ["energy", "assets"]})
    create(s, access_role="general_user", pages=["energy"])
    (_sql, params), = access_writes(s)
    assert params["narrowed"] is True


# ── editing ───────────────────────────────────────────────────────────────────

def patch_session(row=None, *, ready=True):
    base = {"id": TARGET, "platform_role": "user", "status": "active",
            "access_role": None, "allowed_pages": None}
    base.update(row or {})
    return ScriptedSession([
        (COLUMNS, ALL_THREE if ready else NONE_YET),
        (ONE_USER, Result([base])),
    ])


def patch(session, body):
    return client_with(session).patch(f"/api/admin/users/{TARGET}", json=body)


def user_update(session):
    hits = [(s, p) for s, p in session.calls if s.startswith("UPDATE plenum_cafm.users SET")]
    assert len(hits) == 1, [s for s, _ in session.calls]
    return hits[0]


def test_patch_absent_fields_change_nothing():
    s = patch_session({"access_role": "general_user", "allowed_pages": ["energy"]})
    r = patch(s, {"job_title": "Site lead"})
    assert r.status_code == 200, r.text
    sql, _ = user_update(s)
    assert "access_role" not in sql and "allowed_pages" not in sql
    assert "access_role" not in r.json()["changed"]


def test_patch_giving_a_normal_user_a_role_stamps_narrowed_at():
    s = patch_session()
    r = patch(s, {"access_role": "compliance_officer", "pages": ["compliance", "buildings"]})
    assert r.status_code == 200, r.text
    sql, params = user_update(s)
    assert params["ar"] == "compliance_officer"
    assert params["ap"] == ["buildings", "compliance"]
    assert "access_narrowed_at = now()" in sql
    assert r.json()["changed"]["access_role"] == "compliance_officer"
    assert r.json()["changed"]["pages"] == ["buildings", "compliance"]


def test_patch_removing_a_page_stamps_narrowed_at():
    s = patch_session({"access_role": "general_user", "allowed_pages": ["energy", "assets"]})
    r = patch(s, {"pages": ["energy"]})
    assert r.status_code == 200, r.text
    sql, params = user_update(s)
    assert params["ar"] == "general_user" and params["ap"] == ["energy"]
    assert "access_narrowed_at = now()" in sql


def test_patch_adding_a_page_does_not_stamp():
    s = patch_session({"access_role": "general_user", "allowed_pages": ["energy"]})
    r = patch(s, {"pages": ["energy", "assets"]})
    assert r.status_code == 200, r.text
    sql, params = user_update(s)
    assert params["ap"] == ["energy", "assets"]
    assert "access_narrowed_at" not in sql


def test_patch_role_only_keeps_the_pages():
    s = patch_session({"access_role": "general_user", "allowed_pages": ["energy", "assets"]})
    r = patch(s, {"access_role": "property_manager"})
    assert r.status_code == 200, r.text
    sql, params = user_update(s)
    assert params["ar"] == "property_manager" and params["ap"] == ["energy", "assets"]
    assert "access_narrowed_at" not in sql


def test_patch_clearing_role_clears_pages():
    s = patch_session({"access_role": "general_user", "allowed_pages": ["energy"]})
    r = patch(s, {"access_role": None})
    assert r.status_code == 200, r.text
    sql, params = user_update(s)
    assert params["ar"] is None and params["ap"] is None
    assert "access_narrowed_at" not in sql
    assert r.json()["changed"]["access_role"] is None
    assert r.json()["changed"]["pages"] is None


def test_patch_pages_without_any_role_is_refused():
    r = patch(patch_session(), {"pages": ["energy"]})
    assert r.status_code == 400
    assert detail(r)["reason"] == "pages_without_role"


def test_patch_role_with_no_pages_anywhere_is_refused():
    r = patch(patch_session(), {"access_role": "general_user"})
    assert r.status_code == 400
    assert detail(r)["reason"] == "pages_required"


def test_role_refused_for_admin_target():
    r = patch(patch_session({"platform_role": "admin"}),
              {"access_role": "general_user", "pages": ["energy"]})
    assert r.status_code == 400
    assert detail(r)["reason"] == "role_needs_plain_user"


def test_patch_role_before_migration_is_409():
    s = patch_session(ready=False)
    r = patch(s, {"access_role": "general_user", "pages": ["energy"]})
    assert r.status_code == 409
    assert detail(r)["reason"] == "access_roles_not_set_up"
    assert not [sql for sql, _ in s.calls if sql.startswith("UPDATE")]


def test_an_ordinary_edit_still_works_before_the_migration():
    s = patch_session(ready=False)
    r = patch(s, {"job_title": "Site lead"})
    assert r.status_code == 200, r.text
    assert not [sql for sql, _ in s.calls if "access_role" in sql and "information_schema" not in sql]


# ── the table ─────────────────────────────────────────────────────────────────

def listed(rows, *, ready=True):
    stamp = datetime(2026, 11, 1, tzinfo=timezone.utc)
    full = [{"id": str(uuid4()), "full_name": "X", "email": "x@example.com", "job_title": None,
             "status": "active", "role": "user", "can_ingest": False, "last_login_at": None,
             "invited_at": None, "activated_at": None, "created_at": stamp, **r} for r in rows]
    s = ScriptedSession([
        (COLUMNS, ALL_THREE if ready else NONE_YET),
        ("FROM plenum_cafm.users u", Result(full)),
    ])
    r = client_with(s).get("/api/admin/users")
    assert r.status_code == 200, r.text
    return r.json()["users"], s


def test_list_users_carries_role_and_pages():
    users, s = listed([
        {"access_role": "compliance_officer", "allowed_pages": ["compliance", "buildings"]},
        {"access_role": None, "allowed_pages": None},
        {"role": "admin", "access_role": "general_user", "allowed_pages": ["energy"]},
    ])
    assert users[0]["access_role"] == "compliance_officer"
    assert users[0]["access_role_label"] == "Compliance Officer"
    assert users[0]["pages"] == ["buildings", "compliance"]
    assert users[1]["access_role"] is None and users[1]["pages"] is None
    assert users[2]["access_role"] is None and users[2]["pages"] is None   # an admin has none
    assert "allowed_pages" not in users[0]
    assert "u.access_role" in s.sql("FROM plenum_cafm.users u")[0]


def test_list_users_before_the_migration_omits_the_columns():
    users, s = listed([{}], ready=False)
    assert users[0]["access_role"] is None and users[0]["pages"] is None
    assert "access_role" not in s.sql("FROM plenum_cafm.users u")[0]


# ── deleting ──────────────────────────────────────────────────────────────────

def test_delete_clears_the_access_columns():
    s = ScriptedSession([(COLUMNS, ALL_THREE),
                         (ONE_USER, Result([{"email": "x@example.com", "status": "active"}]))])
    r = client_with(s).delete(f"/api/admin/users/{TARGET}")
    assert r.status_code == 200, r.text
    scrub = [sql for sql, _ in s.calls if "SET status = 'deleted'" in sql][0]
    assert "access_role = NULL" in scrub and "allowed_pages = NULL" in scrub
    assert "access_narrowed_at = NULL" in scrub


def test_delete_before_the_migration_names_no_access_column():
    s = ScriptedSession([(COLUMNS, NONE_YET),
                         (ONE_USER, Result([{"email": "x@example.com", "status": "active"}]))])
    r = client_with(s).delete(f"/api/admin/users/{TARGET}")
    assert r.status_code == 200, r.text
    scrub = [sql for sql, _ in s.calls if "SET status = 'deleted'" in sql][0]
    assert "access_role" not in scrub
