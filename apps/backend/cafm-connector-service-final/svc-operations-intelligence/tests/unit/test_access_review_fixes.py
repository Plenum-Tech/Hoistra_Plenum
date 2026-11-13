"""Gaps an independent review found in the access-role boundary (13 Nov 2026), each pinned.

Every one was a way a person held to some pages could still read - or write - another page's
records, or a way an unreadable answer turned into "every page". The fix for each is beside
the code; these are what keep them fixed.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.api.routes import energy as energy_routes
from src.api.routes import ingestion as ingestion_routes
from src.app import app
from src.db import get_session
from src.engines.auth import page_map
from src.engines.auth import pages as P
from src.engines.auth.tokens import Principal
from src.shared import page_filter as F

from test_access_role_identity import ALL_THREE, COLUMNS, Result, ScriptedSession, run
from test_page_filter import building_row

ORG = UUID("11111111-1111-5111-8111-111111111111")
COMPLIANCE = frozenset({"compliance", "buildings"})


def principal(pages=None, can_ingest=False, role="user"):
    return Principal(
        user_id=uuid4(), email="x@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=can_ingest, building_ids=None, pages=pages,
        access_role="general_user" if pages is not None else None)


class NoDatabase:
    async def execute(self, *a, **k):
        raise AssertionError("nothing should reach the database")

    async def commit(self):
        pass


@pytest.fixture
def client():
    async def _session():
        yield NoDatabase()
    app.dependency_overrides[get_session] = _session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from src.engines.auth import keys
    P.reset_cache()
    monkeypatch.setattr(keys, "_CACHE", keys.KeyShape(
        users="uuid", organizations="uuid", users_id_self_assigning=True))
    yield
    P.reset_cache()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    return p


def stub(monkeypatch, target, name, value):
    async def _fn(*_a, **_k):
        return copy.deepcopy(value)
    monkeypatch.setattr(target, name, _fn)


def reason(response):
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - a plain-text 500 means the handler was reached
        return None
    return detail.get("reason") if isinstance(detail, dict) else None


# ── adding data needs the ingest right, for a person held to pages ─────────────

INGEST_ROUTES = [("GET", "/api/ingestion/cases"), ("GET", "/api/ingestion/audit"),
                 ("POST", "/api/ingestion/import"), ("POST", "/api/contract-performance/invoices/extract-verify"),
                 ("POST", f"/api/compliance/documents/{uuid4()}/membership")]


@pytest.mark.parametrize("method,path", INGEST_ROUTES)
def test_a_role_user_without_the_ingest_right_is_refused_an_ingest_route(client, method, path):
    as_(principal(pages=COMPLIANCE, can_ingest=False))
    r = client.request(method, path)
    assert r.status_code == 403 and reason(r) == "cannot_ingest", (path, r.status_code, r.text[:160])


@pytest.mark.parametrize("method,path", INGEST_ROUTES)
def test_the_ingest_routes_are_as_they_were_for_everyone_else(client, method, path):
    # A role user who may ingest gets past the gate; an unrestricted caller is never asked
    # by the gate (a handler that always asked for the ingest right still does, as before).
    as_(principal(pages=COMPLIANCE, can_ingest=True))
    assert reason(client.request(method, path)) not in ("cannot_ingest", "page_not_allowed"), path
    as_(principal(can_ingest=False))
    assert reason(client.request(method, path)) != "page_not_allowed", path


def test_the_gate_itself_never_asks_an_unrestricted_caller_for_the_ingest_right(client, monkeypatch):
    stub(monkeypatch, ingestion_routes.val, "list_cases", [])
    monkeypatch.setattr(ingestion_routes, "_allowed", _const(None))
    as_(principal(can_ingest=False))
    assert client.get("/api/ingestion/cases").status_code == 200


def _const(value):
    async def _fn(*_a, **_k):
        return value
    return _fn


# ── held documents ────────────────────────────────────────────────────────────

CASES = [{"id": "1", "doc_type": "compliance_certificate", "open": True},
         {"id": "2", "doc_type": "contract", "open": True},
         {"id": "3", "doc_type": "invoice", "open": False},
         {"id": "4", "doc_type": None, "open": True}]


def test_held_documents_follow_the_pages(client, monkeypatch):
    stub(monkeypatch, ingestion_routes.val, "list_cases", CASES)
    monkeypatch.setattr(ingestion_routes, "_allowed", _const(None))
    as_(principal(pages=COMPLIANCE, can_ingest=True))
    body = client.get("/api/ingestion/cases").json()
    assert [c["id"] for c in body["cases"]] == ["1", "4"]
    assert body["count"] == 2 and body["open"] == 2
    as_(principal())
    assert len(client.get("/api/ingestion/cases").json()["cases"]) == 4


def test_one_held_document_of_another_page_does_not_exist_for_them(client, monkeypatch):
    stub(monkeypatch, ingestion_routes.val, "get", {"id": "2", "doc_type": "contract", "organization_id": str(ORG)})
    as_(principal(pages=COMPLIANCE, can_ingest=True))
    r = client.get(f"/api/ingestion/cases/{uuid4()}")
    assert r.status_code == 404 and reason(r) == "case_not_found"
    as_(principal())
    assert client.get(f"/api/ingestion/cases/{uuid4()}").status_code == 200


def test_the_filing_trail_shows_a_role_user_only_what_they_filed(client, monkeypatch):
    me = principal(pages=COMPLIANCE, can_ingest=True)
    trail = {"ok": True, "count": 3, "by_outcome": {"accepted": 3}, "outcomes": [], "roles": [],
             "actors": [{"user_id": str(me.user_id), "count": 1}, {"user_id": "other", "count": 2}],
             "entries": [{"id": "a", "actor_user_id": str(me.user_id), "outcome": "accepted", "detail": {}},
                         {"id": "b", "actor_user_id": "other", "outcome": "accepted", "detail": {}},
                         {"id": "c", "actor_user_id": "other", "outcome": "rejected", "detail": {}}]}
    stub(monkeypatch, ingestion_routes.ingestion_audit, "list_events", trail)
    monkeypatch.setattr(ingestion_routes, "_allowed", _const(None))
    as_(me)
    body = client.get("/api/ingestion/audit").json()
    assert [e["id"] for e in body["entries"]] == ["a"]
    assert body["count"] == 1 and body["by_outcome"] == {"accepted": 1}
    assert [a["user_id"] for a in body["actors"]] == [str(me.user_id)]
    as_(principal())
    assert client.get("/api/ingestion/audit").json() == trail


# ── what a document is checked against ────────────────────────────────────────

def test_the_building_ontology_carries_only_held_registers(client, monkeypatch):
    onto = {"building_id": "b1", "name": "Harbour Point", "names": ["HP"], "codes": ["B-101"], "floors": ["1"],
            "vendors": ["Acme"], "assets": ["AHU-1"], "equipment": ["x"], "meters": ["M1"],
            "certificate_types": ["EICR"], "certificate_numbers": ["E-1"], "contract_refs": ["C-1"],
            "document_names": ["a.pdf"], "counts": {"vendors": 1, "assets": 1, "meters": 1, "certificates": 1,
                                                    "contracts": 1, "floors": 1, "documents": 1}}
    monkeypatch.setattr(ingestion_routes.onto, "load", _const(SimpleNamespace(as_dict=lambda: copy.deepcopy(onto))))
    monkeypatch.setattr(ingestion_routes.access, "assert_building", lambda *a, **k: None)
    as_(principal(pages=frozenset({"buildings"})))
    got = client.get(f"/api/ingestion/buildings/{uuid4()}/ontology").json()["ontology"]
    assert got["name"] == "Harbour Point" and got["floors"] == ["1"]
    for key in ("vendors", "assets", "equipment", "meters", "certificate_types", "certificate_numbers", "contract_refs"):
        assert got[key] == [], key
    assert got["counts"] == {"floors": 1, "documents": 1}
    as_(principal(pages=frozenset({"buildings", "compliance"})))
    got = client.get(f"/api/ingestion/buildings/{uuid4()}/ontology").json()["ontology"]
    assert got["certificate_types"] == ["EICR"] and got["vendors"] == []
    as_(principal())
    assert client.get(f"/api/ingestion/buildings/{uuid4()}/ontology").json()["ontology"] == onto


def test_ontology_keys_all_have_an_owner_or_are_the_buildings_own():
    assert set(F.ONTOLOGY_PAGE.values()) <= set(P.PAGES)


# ── the asset and energy question bar ─────────────────────────────────────────

def test_the_ask_routes_belong_to_both_pages():
    for key in (("POST", "/api/energy/ask"), ("GET", "/api/energy/ask/suggestions")):
        assert set(page_map.PAGE_MAP[key]) == {"energy", "assets"}


def test_a_question_is_answered_only_from_a_page_the_caller_holds(client, monkeypatch):
    seen = []

    async def _ask(_session, **kw):
        seen.append(kw["question"])
        return {"ok": True, "understood": True}
    monkeypatch.setattr(energy_routes.ask_svc, "ask", _ask)
    monkeypatch.setattr(energy_routes.position_svc, "building_ids_for", _const(None))
    energy_q = next(s.question for s in energy_routes.ask_svc.SKILLS if s.page == "energy")
    assets_q = next(s.question for s in energy_routes.ask_svc.SKILLS if s.page == "assets")
    as_(principal(pages=frozenset({"assets"})))
    r = client.post("/api/energy/ask", json={"question": energy_q})
    assert r.status_code == 403 and reason(r) == "page_not_allowed" and r.json()["detail"]["page"] == "energy"
    assert client.post("/api/energy/ask", json={"question": assets_q}).status_code == 200
    as_(principal(pages=frozenset({"energy"})))
    assert client.post("/api/energy/ask", json={"question": energy_q}).status_code == 200
    assert reason(client.post("/api/energy/ask", json={"question": assets_q})) == "page_not_allowed"
    as_(principal())
    assert client.post("/api/energy/ask", json={"question": energy_q}).status_code == 200
    assert seen == [assets_q, energy_q, energy_q]


def test_the_chips_offered_are_the_held_pages(client):
    as_(principal(pages=frozenset({"assets"})))
    pages_ = {s["page"] for s in client.get("/api/energy/ask/suggestions").json()["suggestions"]}
    assert pages_ == {"assets"}
    as_(principal())
    assert {s["page"] for s in client.get("/api/energy/ask/suggestions").json()["suggestions"]} == {"assets", "energy"}


# ── an asset's condition, without the energy page ─────────────────────────────

def test_anomaly_figures_are_left_out_of_asset_condition_without_energy(client, monkeypatch):
    rows = {"ok": True, "assets": [{"asset_id": "a", "band": "watch", "anomalies_open": 3, "anomaly_weeks": 6,
                                    "anomaly_annual_cost": 900.0, "why": "over reference"}],
            "summary": {"threat": 1, "anomalous_in_control": 2}}
    stub(monkeypatch, energy_routes.cond_svc, "assess", {"assets": rows["assets"], "rules": {}, "method": "m"})
    stub(monkeypatch, energy_routes.cond_svc, "summary", rows)
    monkeypatch.setattr(energy_routes.position_svc, "building_ids_for", _const(None))
    as_(principal(pages=frozenset({"assets"})))
    for path in ("/api/energy/condition/assets", "/api/energy/condition/summary"):
        got = client.get(path).json()
        asset = got["assets"][0]
        assert asset["band"] == "watch" and asset["why"] == "over reference"
        assert asset["anomalies_open"] is None and asset["anomaly_weeks"] is None and asset["anomaly_annual_cost"] is None
    assert client.get("/api/energy/condition/summary").json()["summary"] == {"threat": 1, "anomalous_in_control": None}
    for who in (principal(pages=frozenset({"assets", "energy"})), principal()):
        as_(who)
        assert client.get("/api/energy/condition/summary").json() == rows
        assert client.get("/api/energy/condition/assets").json()["assets"] == rows["assets"]


def test_without_anomalies_is_recursive_and_leaves_the_original_alone():
    payload = {"a": [{"anomaly": {"x": 1}, "keep": 1}], "anomaly_count": 2}
    out = F.without_anomalies(payload)
    assert out == {"a": [{"anomaly": None, "keep": 1}], "anomaly_count": None}
    assert payload["anomaly_count"] == 2


# ── one building ──────────────────────────────────────────────────────────────

def test_one_building_is_narrowed_like_the_list(client, monkeypatch):
    stub(monkeypatch, energy_routes.bld_svc, "get_building", {"ok": True, "building": building_row()})
    as_(principal(pages=COMPLIANCE))
    got = client.get("/api/energy/buildings/b1").json()["building"]
    assert got["graph_counts"] == {"floors": 6, "certificates": 12, "documents": 7}
    assert got["hoist_score_covered"] == ["compliance"]
    as_(principal(pages=frozenset({"energy"})))
    got = client.get("/api/energy/buildings/b1")
    assert got.status_code == 403 and reason(got) == "page_not_allowed"
    as_(principal())
    assert client.get("/api/energy/buildings/b1").json()["building"] == building_row()


def test_the_graph_lists_only_documents_whose_type_is_held():
    def branch(rows):
        return {"table": "documents", "count": len(rows), "available": True, "truncated": False, "children": [],
                "rows": [{"id": str(i), "label": n, "detail": t} for i, (n, t) in enumerate(rows)]}
    tree = {"ok": True, "total_records": 4, "unavailable": [],
            "branches": [branch([("cert.pdf", "compliance_certificate"), ("msa.pdf", "contract"),
                                 ("plan.dwg", "drawing"), ("bill.pdf", "meter_readings")])]}
    out = F.building_tree(tree, frozenset({"buildings"}))
    docs = out["branches"][0]
    assert [r["label"] for r in docs["rows"]] == ["plan.dwg"]
    assert docs["count"] == 1 and out["total_records"] == 1
    out = F.building_tree(tree, frozenset({"buildings", "compliance", "energy"}))
    assert [r["label"] for r in out["branches"][0]["rows"]] == ["cert.pdf", "plan.dwg", "bill.pdf"]
    assert F.building_tree(tree, None) is tree


# ── owners the first pass got wrong ───────────────────────────────────────────

def test_route_owners_the_review_corrected():
    assert page_map.PAGE_MAP[("GET", "/api/energy/buildings/{building_id}/cost-drivers")] == ("assets",)
    assert set(page_map.PAGE_MAP[("GET", "/api/approvals/vendor-contact")]) == {"vendors", "compliance", "ops"}


def test_a_meter_readings_upload_is_the_energy_pages():
    assert F.document_page("meter_readings") == "energy"


# ── reading the row fails closed ──────────────────────────────────────────────

def test_pages_on_a_row_with_no_role_still_restrict():
    # A row someone edited by hand: the role is gone, the pages are not. Never "every page".
    assert P.resolve("user", None, ["energy"]) == (None, frozenset({"energy"}))
    assert P.resolve("user", "", ["energy", "payroll"]) == (None, frozenset({"energy"}))
    assert P.resolve("user", None, []) == (None, frozenset())
    assert P.resolve("user", None, None) == (None, None)
    assert P.resolve("admin", None, ["energy"]) == (None, None)


def test_a_failed_column_check_is_not_believed():
    calls = []

    def boom(_params):
        calls.append(1)
        raise RuntimeError("information_schema unavailable")
    session = ScriptedSession([(COLUMNS, boom)])
    assert run(P.columns_ready(session)) is False
    assert P.check_failed() is True
    session.script = [(COLUMNS, ALL_THREE)]
    assert run(P.columns_ready(session)) is True          # asked again at once, not in a minute
    assert P.check_failed() is False and len(calls) == 1


def test_a_request_is_refused_while_the_pages_cannot_be_read(client):
    from src.engines.auth import tokens as T

    class Broken:
        async def execute(self, sql, params=None):
            if "information_schema" in str(sql):
                raise RuntimeError("database blip")
            raise AssertionError("the account was read although its pages could not be")
    token = T.issue_access_token(user_id=uuid4(), email="a@b.com", organization_id=ORG,
                                 session_id=None, password_changed_at=None)[0]
    with pytest.raises(P.PagesUnreadable):
        run(T.principal_from_token(Broken(), token))


# ── promotion ─────────────────────────────────────────────────────────────────

def test_promoting_to_admin_clears_the_role_and_pages():
    from src.engines.auth import accounts as acc
    s = ScriptedSession([(COLUMNS, ALL_THREE)])
    run(acc.clear_access_role(s, uuid4()))
    (sql, _params), = [(q, p) for q, p in s.calls if q.lstrip().startswith("UPDATE")]
    assert "access_role = NULL" in sql and "allowed_pages = NULL" in sql and "access_narrowed_at = NULL" in sql
    import inspect
    body = inspect.getsource(acc.set_role)
    assert "clear_access_role" in body


# ── a blocked vendor is blocked wherever the vendor shows ─────────────────────

def test_vendor_coverage_gives_a_vendors_holder_the_blocked_state_and_nothing_else(client, monkeypatch):
    from src.api.routes import compliance as compliance_routes
    cover = {"ok": True, "country_code": "UK", "vendors": [
        {"vendor_id": "v1", "vendor_name": "AquaSafe", "block_state": "Blocked", "cleared": False,
         "certificates_total": 3, "coverage_pct": 40, "missing": ["GAS_SAFE"]}]}
    stub(monkeypatch, compliance_routes.coverage_svc, "vendor_coverage", cover)
    assert set(page_map.PAGE_MAP[("GET", "/api/compliance/coverage/vendors")]) == {"vendors", "compliance"}
    as_(principal(pages=frozenset({"vendors"})))
    got = client.get("/api/compliance/coverage/vendors").json()
    assert got["vendors"] == [{"vendor_id": "v1", "vendor_name": "AquaSafe", "block_state": "Blocked", "cleared": False}]
    for who in (principal(pages=frozenset({"compliance"})), principal()):
        as_(who)
        assert client.get("/api/compliance/coverage/vendors").json() == cover
    as_(principal(pages=frozenset({"energy"})))
    assert reason(client.get("/api/compliance/coverage/vendors")) == "page_not_allowed"
