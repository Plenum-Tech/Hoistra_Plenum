"""What a second review of the access-role work found (13 Nov 2026), each pinned.

  * deploying the build must never alter plenum_cafm.users by itself;
  * a role user arriving while the column check is still in flight is not "every page";
  * a token refresh that cannot read the pages is a 503, not a 500;
  * routes that act on a record by id are bounded to the caller's company (pre-existing gap);
  * the schema and backfill routes are an administrator's;
  * the graph table counts are the caller's company's;
  * smaller leaks across pages: work-order blockers, an asset's utility bill, a certificate
    that would create a vendor, report runs answered under the old pages.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src import db
from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import access, keys, page_map
from src.engines.auth import pages as P
from src.engines.auth import tokens as T
from src.engines.auth.tokens import Principal

from test_access_role_identity import ALL_THREE, COLUMNS, NONE_YET, Result, ScriptedSession, run

ORG = UUID("11111111-1111-5111-8111-111111111111")
OTHER = UUID("22222222-2222-5222-8222-222222222222")


def principal(pages=None, role="user", org=ORG, can_ingest=False):
    return Principal(
        user_id=uuid4(), email="x@example.com", organization_id=org, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=can_ingest, building_ids=None, pages=pages,
        access_role="general_user" if pages is not None else None)


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    return p


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    P.reset_cache()
    monkeypatch.setattr(keys, "_CACHE", keys.KeyShape(
        users="uuid", organizations="uuid", users_id_self_assigning=True))
    yield
    P.reset_cache()
    app.dependency_overrides.clear()


def client_with(session):
    async def _session():
        yield session
    app.dependency_overrides[get_session] = _session
    return TestClient(app, raise_server_exceptions=False)


def reason(resp):
    body = resp.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    return (detail or {}).get("reason") if isinstance(detail, dict) else None


# ── 2 · deploying never alters the users table ────────────────────────────────

ACCESS_COLUMNS = ("access_role", "allowed_pages", "access_narrowed_at")


def test_startup_migrations_never_touch_the_access_role_columns(monkeypatch):
    ran: list[str] = []

    async def record(stmt):
        ran.append(stmt)
    monkeypatch.setattr(db, "_run_ddl_statement", record)
    failed = run(db.apply_sql_migrations(use_ledger=False))
    assert failed == {}
    assert ran, "the other migrations must still apply on start"
    assert not [s for s in ran if any(c in s for c in ACCESS_COLUMNS)]
    # Every file the start applies is still applied: the folder is read as before.
    on_start = sorted(db._MIGRATIONS_DIR.glob("*.sql"))
    assert len(on_start) >= 50
    assert any("platform_role" in s for s in ran)


def test_the_access_role_migration_is_kept_to_be_run_by_hand():
    manual = db._MIGRATIONS_DIR / "manual" / "users_access_role.sql"
    assert manual.exists()
    assert manual not in set(db._MIGRATIONS_DIR.glob("*.sql"))
    sql = manual.read_text(encoding="utf-8")
    assert "SET lock_timeout" in sql
    for col in ACCESS_COLUMNS:
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql
    assert not (db._MIGRATIONS_DIR / "users_access_role.sql").exists()


# ── 1 · a role user during the first column check ─────────────────────────────

class SlowColumns:
    """Answers the column check after a pause, and fails anything else."""

    def __init__(self, answer):
        self.answer = answer

    async def execute(self, sql, params=None):
        if "information_schema" in str(sql):
            await asyncio.sleep(0.05)
            return self.answer
        raise AssertionError("not expected")


class NoRead:
    async def execute(self, sql, params=None):
        raise AssertionError("the account was read while its pages could not be told")


def token():
    return T.issue_access_token(user_id=uuid4(), email="a@b.com", organization_id=ORG,
                                session_id=None, password_changed_at=None)[0]


def test_a_caller_arriving_while_the_first_check_is_in_flight_is_refused_not_unrestricted():
    async def scenario():
        first = asyncio.create_task(P.columns_ready(SlowColumns(ALL_THREE)))
        await asyncio.sleep(0.01)
        with pytest.raises(P.PagesUnreadable):
            await T.principal_from_token(NoRead(), token())
        assert await first is True
    run(scenario())
    assert P.check_failed() is False


def test_once_the_first_check_is_done_callers_resolve_their_pages():
    run(P.columns_ready(SlowColumns(ALL_THREE)))
    from test_access_role_identity import principal_for, user_row
    p, _ = principal_for(user_row(access_role="general_user", allowed_pages=["energy"],
                                  access_narrowed_at=None))
    assert p.pages == frozenset({"energy"})


def test_a_recheck_in_flight_before_the_migration_keeps_the_known_answer(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(P, "_monotonic", lambda: clock[0])
    assert run(P.columns_ready(ScriptedSession([(COLUMNS, NONE_YET)]))) is False
    clock[0] += 61

    async def scenario():
        recheck = asyncio.create_task(P.columns_ready(SlowColumns(NONE_YET)))
        await asyncio.sleep(0.01)
        # Known absent a minute ago: still a normal user, not a refusal every minute.
        assert await P.columns_ready(NoRead()) is False
        assert P.check_failed() is False
        assert await recheck is False
    run(scenario())


def test_the_service_answers_the_column_check_before_its_first_request():
    from src import app as app_module
    assert "columns_ready" in inspect.getsource(app_module.lifespan)


# ── 7 · refresh ────────────────────────────────────────────────────────────────

def test_a_refresh_that_cannot_read_the_pages_is_503_not_500(monkeypatch):
    pair = SimpleNamespace(access_token="a", refresh_token="r", token_type="bearer", expires_in=900)

    async def rotate(*_a, **_k):
        return pair

    async def unreadable(*_a, **_k):
        raise P.PagesUnreadable("blip")
    monkeypatch.setattr(T, "rotate_session", rotate)
    monkeypatch.setattr(T, "principal_from_token", unreadable)
    resp = client_with(NoRead()).post("/api/auth/refresh", json={"refresh_token": "x" * 20})
    assert resp.status_code == 503
    assert reason(resp) == "access_unreadable"


# ── 3 · acting on a record by id is bounded to the caller's company ───────────

class Item(SimpleNamespace):
    pass


class GetSession:
    def __init__(self, item):
        self.item = item

    async def get(self, _model, _id):
        return self.item


def scope_of(p, requested=None):
    return access.scope_for(p, requested)


def test_deciding_another_companys_queue_item_is_not_found():
    item = Item(organization_id=OTHER, source_feature="A", related_entity_type=None)
    from src.shared import approvals as A
    out = run(A.decide_queue_item(GetSession(item), uuid4(), decision="bogus",
                                  scope=scope_of(principal())))
    assert out == {"ok": False, "error": "queue item not found"}
    # Own company: past the check (to the decision's own validation).
    item.organization_id = ORG
    out = run(A.decide_queue_item(GetSession(item), uuid4(), decision="bogus",
                                  scope=scope_of(principal())))
    assert "decision must be" in out["error"]
    # A superadmin, as today, may decide any company's item.
    item.organization_id = OTHER
    out = run(A.decide_queue_item(GetSession(item), uuid4(), decision="bogus",
                                  scope=scope_of(principal(role="superadmin"))))
    assert "decision must be" in out["error"]


def test_the_one_click_token_path_is_unchanged():
    from src.engines.compliance import tokens as tok
    assert "scope=" not in inspect.getsource(tok.redeem_approval_token)


class OwnedRow:
    """The ownership read assert_owned makes, answered with one company."""

    def __init__(self, org):
        self.org = org

    async def execute(self, sql, params=None):
        if "information_schema" in str(sql):
            raise AssertionError("scope columns are seeded")
        return Result([{"organization_id": str(self.org)}])

    async def get(self, *_a):
        return None

    async def commit(self):
        pass


BY_ID = [
    ("compliance_certificates", "patch", "/api/compliance/certificates/{id}/remedial-status",
     {"remedial_status": "done"}, ("compliance", "cert_svc", "set_remedial_status")),
    ("compliance_certificates", "post", "/api/compliance/certificates/{id}/confirm",
     {}, ("compliance", "cert_svc", "confirm_certificate")),
    ("compliance_certificates", "post", "/api/compliance/certificates/{id}/extract-vendor-profile",
     None, ("compliance", "cert_svc", "extract_vendor_profile")),
    ("contract_sla_parameters", "patch", "/api/contract-performance/contracts/{id}",
     {"updates": {"status": "draft"}}, ("contract_performance", "params_svc", "update_contract_parameters")),
    ("contract_sla_parameters", "post", "/api/contract-performance/contracts/{id}/confirm",
     {}, ("contract_performance", "params_svc", "confirm_contract_parameters")),
    ("contract_sla_parameters", "post", "/api/contract-performance/contracts/{id}/reopen",
     {}, ("contract_performance", "params_svc", "reopen_contract_parameters")),
    ("energy_anomalies", "post", "/api/energy/anomalies/{id}/act",
     {"action": "acknowledge"}, ("energy", "anom_svc", "act_on_anomaly")),
]


@pytest.mark.parametrize("table,method,path,body,target", BY_ID, ids=[b[2] for b in BY_ID])
def test_acting_on_a_record_by_id_is_bounded_to_the_callers_company(
        monkeypatch, table, method, path, body, target):
    import importlib
    module = importlib.import_module(f"src.api.routes.{target[0]}")
    engine = getattr(module, target[1])
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        return {"ok": True}
    monkeypatch.setattr(engine, target[2], fake)
    if target[2] == "extract_vendor_profile":
        # The handler reads the certificate itself before asking the engine.
        cert = SimpleNamespace(id=uuid4())

        class WithCert(OwnedRow):
            async def get(self, *_a):
                return cert
        session_cls = WithCert
    else:
        session_cls = OwnedRow
    monkeypatch.setitem(access._row_scope_columns, table, frozenset({"organization_id"}))
    url = path.format(id=uuid4())

    def call(session):
        c = client_with(session)
        kw = {"json": body} if body is not None else {}
        return getattr(c, method)(url, **kw)

    as_(principal())
    resp = call(session_cls(OTHER))
    assert resp.status_code == 404 and not calls
    resp = call(session_cls(ORG))
    assert resp.status_code == 200 and calls == [1]
    as_(principal(role="superadmin"))
    resp = call(session_cls(OTHER))
    assert resp.status_code == 200 and calls == [1, 1]


SCHEMA_ROUTES = [
    ("/api/energy/sites/uuid-migration", "sites_uuid", "plan_migration"),
    ("/api/energy/buildings/backfill-from-sites", "bld_backfill", "backfill_buildings_from_sites"),
    ("/api/energy/buildings/link-locations", "bld_create", "link_buildings_to_locations"),
]


@pytest.mark.parametrize("path,engine,fn", SCHEMA_ROUTES, ids=[r[0] for r in SCHEMA_ROUTES])
def test_schema_and_backfill_routes_are_an_administrators(monkeypatch, path, engine, fn):
    from src.api.routes import energy as E
    calls = []

    async def fake(*a, **k):
        calls.append(1)
        return {"ok": True}
    monkeypatch.setattr(getattr(E, engine), fn, fake)
    c = client_with(NoRead())
    as_(principal())
    resp = c.post(path)
    assert resp.status_code == 403 and reason(resp) == "admin_required" and not calls
    for role in ("admin", "superadmin"):
        as_(principal(role=role))
        assert c.post(path).status_code == 200
    assert calls == [1, 1]


def test_graph_table_counts_are_asked_for_the_callers_company(monkeypatch):
    from src.api.routes import energy as E
    seen = []

    async def fake(_session, *, organization_id=None):
        seen.append(organization_id)
        return {"ok": True}
    monkeypatch.setattr(E.bld_tree, "graph_tables", fake)
    c = client_with(NoRead())
    for role in ("user", "admin"):
        as_(principal(role=role))
        assert c.get("/api/energy/graph/tables").status_code == 200
    as_(principal(role="user"))
    assert c.get(f"/api/energy/graph/tables?organization_id={OTHER}").status_code == 403
    as_(principal(role="superadmin"))
    c.get("/api/energy/graph/tables")
    c.get(f"/api/energy/graph/tables?organization_id={OTHER}")
    # A plain user and an admin: their company. A superadmin: the platform, as today, or the
    # company they are acting as.
    assert seen == [ORG, ORG, None, OTHER]


def test_graph_table_counts_for_a_company_count_only_its_rows(monkeypatch):
    from src.engines.energy import building_tree as BT
    shape = {
        "assets": {"exists": True, "key": "id", "columns": {"id", "organization_id", "created_at"}},
        "floors": {"exists": True, "key": "id", "columns": {"id", "building_id"}},
        "spaces": {"exists": True, "key": "id", "columns": {"id", "name"}},
    }

    async def fake_shape(_s):
        return shape
    monkeypatch.setattr(BT, "graph_shape", fake_shape)

    class Counting:
        def __init__(self):
            self.sql = []

        async def execute(self, sql, params=None):
            self.sql.append((str(sql), dict(params or {})))
            if "information_schema" in str(sql):
                return Result([{"table_name": t, "n": 3} for t in shape])
            return Result([{f"v{i}": 7 for i in range(len(BT._HISTORY))}])

        def begin_nested(self):
            class _N:
                async def __aenter__(self_):
                    return None

                async def __aexit__(self_, *a):
                    return False
            return _N()

    s = Counting()
    out = run(BT.graph_tables(s, organization_id=ORG))
    by = {e["table"]: e for e in out["tables"]}
    counts = {sql: p for sql, p in s.sql if "count(*)" in sql}
    assert any("FROM plenum_cafm.assets" in q and "organization_id" in q for q in counts)
    assert any("FROM plenum_cafm.floors" in q and "plenum_cafm.buildings" in q for q in counts)
    assert not any("FROM plenum_cafm.spaces" in q for q in counts)
    assert by["spaces"]["rows"] is None and by["spaces"]["why"]
    assert by["assets"]["rows"] == 7
    # No company named: counted exactly as before.
    s2 = Counting()
    run(BT.graph_tables(s2))
    counted = [q for q, _ in s2.sql if "count(*)" in q and "information_schema" not in q]
    assert len(counted) == 3
    assert all(":gt_org" not in q and "plenum_cafm.buildings" not in q for q in counted)


# ── 4 · work-order blockers carry only the held pages' reasons ────────────────

def test_blockers_keep_only_the_reasons_of_the_held_pages():
    from src.engines import work_order_blockers as WB
    today = datetime(2026, 11, 13).date()
    row = {"block_state": "Blocked", "block_reason": "Vendor blocked",
           "certs": [{"type": "GAS_SAFE", "status": "Lapsed", "expiry": "2026-10-01"}],
           "contracts": [{"name": "Main FM", "end": "2026-09-01", "status": "active"}],
           "unconfirmed_sla": []}
    every = WB.classify(row, today)
    assert {b["kind"] for b in WB.held(every, None)} == {"vendor_blocked", "accreditation_lapsed", "contract_expired"}
    assert {b["kind"] for b in WB.held(every, frozenset({"ops"}))} == {"vendor_blocked"}
    assert {b["kind"] for b in WB.held(every, frozenset({"ops", "compliance"}))} == {"vendor_blocked", "accreditation_lapsed"}
    assert {b["kind"] for b in WB.held(every, frozenset({"ops", "vendors"}))} == {"vendor_blocked", "contract_expired"}


def test_the_blockers_route_hands_the_callers_pages_to_the_engine(monkeypatch):
    from src.api.routes import work_order_blockers as R
    seen = []

    async def fake(_s, **k):
        seen.append(k.get("pages", "absent"))
        return {"ok": True}
    monkeypatch.setattr(R.svc, "read_blockers", fake)
    c = client_with(NoRead())
    as_(principal())
    c.get("/api/work-orders/blockers")
    as_(principal(pages=frozenset({"ops"})))
    c.get("/api/work-orders/blockers")
    assert seen == [None, frozenset({"ops"})]


# ── 5 · an asset's utility bill is the energy register's ──────────────────────

def test_an_assets_utility_bill_needs_energy_as_well():
    assert page_map.NEEDS_ALL[("GET", "/api/energy/assets/{asset_id}/utility-bill")] == ("assets", "energy")
    c = client_with(NoRead())
    as_(principal(pages=frozenset({"assets"})))
    resp = c.get(f"/api/energy/assets/{uuid4()}/utility-bill")
    assert resp.status_code == 403 and reason(resp) == "page_not_allowed"


# ── 6 · a certificate saved by someone without Vendors creates no vendor ──────

@pytest.mark.parametrize("who,expected", [
    (dict(), True),
    (dict(pages=frozenset({"compliance", "vendors"})), True),
    (dict(pages=frozenset({"compliance"})), False),
])
def test_saving_a_certificate_creates_a_vendor_only_for_a_vendors_holder(monkeypatch, who, expected):
    from src.api.routes import compliance as C
    seen = []

    async def fake(_s, _data, **k):
        seen.append(k.get("create_vendor"))
        return {"ok": True}
    monkeypatch.setattr(C.cert_svc, "upsert_certificate", fake)
    as_(principal(**who))
    resp = client_with(NoRead()).post("/api/compliance/certificates", json={"cert_scope": "Building", "certificate_type_code": "GAS_SAFE"})
    assert resp.status_code == 200, resp.text
    assert seen == [expected]


def test_the_certificate_engine_only_matches_a_vendor_when_told_not_to_create_one():
    from src.engines.compliance import certificates as cert
    src = inspect.getsource(cert.upsert_certificate)
    assert "create_vendor" in inspect.signature(cert.upsert_certificate).parameters
    assert "create_if_missing=bool(create_vendor and vendor_org and company)" in src


# ── 8 · report runs answered under the old pages ──────────────────────────────

def test_report_runs_started_before_the_narrowing_or_inside_the_cache_window_are_hidden():
    from src.api.routes import reports as R
    cut = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)

    def run_row(started_after_cut_s, duration_s):
        start = cut + timedelta(seconds=started_after_cut_s)
        return SimpleNamespace(ran_at=start + timedelta(seconds=duration_s),
                               duration_ms=int(duration_s * 1000))
    straddles = run_row(-30, 90)          # began under the old pages, finished after
    in_window = run_row(20, 10)           # other services may still hold the old pages
    fresh = run_row(120, 10)
    rows = [straddles, in_window, fresh]
    narrowed = access.Scope(user_id=uuid4(), role="user", organization_id=ORG, building_ids=None,
                            can_ingest=False, access_role="general_user",
                            pages=frozenset({"energy"}), access_narrowed_at=cut)
    assert R._shown(narrowed, rows) == [fresh]
    normal = access.Scope(user_id=uuid4(), role="user", organization_id=ORG, building_ids=None,
                          can_ingest=False)
    assert R._shown(normal, rows) == rows
