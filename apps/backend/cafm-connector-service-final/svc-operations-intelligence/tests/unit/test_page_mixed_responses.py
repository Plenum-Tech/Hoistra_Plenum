"""The routes that answer with rows from several pages narrow them for a role user.

Each test drives the real route with its engine stubbed, so what is pinned is the wiring: the
caller's pages reach the filter (shared/page_filter.py), the filter's result is what goes out,
and a caller with every page gets the engine's answer untouched.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api.routes import approvals as approvals_routes
from src.api.routes import auth as auth_routes
from src.api.routes import compliance as compliance_routes
from src.api.routes import contract_performance as cp_routes
from src.api.routes import crons as crons_routes
from src.api.routes import document_search as documents_routes
from src.api.routes import energy as energy_routes
from src.api.routes import reports as reports_routes
from src.api.routes import value as value_routes
from src.app import app
from src.db import get_session
from src.engines import document_search as doc_svc
from src.engines.auth.tokens import Principal
from src.shared import approvals as approvals_svc

from test_page_filter import building_row, score, tree, value_payload

ORG = UUID("11111111-1111-5111-8111-111111111111")
NARROWED = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
COMPLIANCE = frozenset({"compliance", "buildings"})


def principal(pages=None, narrowed_at=None):
    # building_ids None keeps the building boundary out of the way: these are about pages.
    return Principal(
        user_id=uuid4(), email="x@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role="user",
        can_ingest=False, building_ids=None, pages=pages,
        access_role="general_user" if pages is not None else None,
        access_narrowed_at=narrowed_at)


class NoDatabase:
    async def execute(self, *a, **k):
        raise AssertionError("these tests stub the engines; nothing should reach the database")

    async def get(self, *a, **k):
        raise AssertionError("nothing should reach the database")

    async def commit(self):
        pass


@pytest.fixture
def client():
    async def _session():
        yield NoDatabase()
    app.dependency_overrides[get_session] = _session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep
    return p


def stub(monkeypatch, target, name, value):
    async def _fn(*_a, **_k):
        return copy.deepcopy(value)
    monkeypatch.setattr(target, name, _fn)


# ── the Pending queue ─────────────────────────────────────────────────────────

def queue_item(feature, entity=None):
    return SimpleNamespace(id=uuid4(), source_feature=feature, related_entity_type=entity)


class QueueSession:
    def __init__(self, items):
        self.items, self.limits = items, []

    async def execute(self, query, *_a, **_k):
        self.limits.append(query._limit_clause.value)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.items)))


def listed(items, pages, limit=100):
    session = QueueSession(items)
    scope = SimpleNamespace(restricted=False, pages=pages)
    out = asyncio.new_event_loop().run_until_complete(
        approvals_svc.list_queue(session, limit=limit, scope=scope))
    return out, session


def test_pending_queue_shows_only_held_rows():
    items = [queue_item("A"), queue_item("C"), queue_item("B"), queue_item("A")]
    out, _ = listed(items, COMPLIANCE)
    assert out == [items[0], items[3]]


def test_pending_queue_is_untouched_for_every_page():
    items = [queue_item("A"), queue_item("C")]
    out, session = listed(items, None, limit=7)
    assert out == items
    assert session.limits == [7]


def test_a_role_users_queue_is_filled_from_a_wider_read():
    # Held rows may sit behind unheld ones: the cut is made after the filter, not before.
    items = [queue_item("C") for _ in range(5)] + [queue_item("A") for _ in range(4)]
    out, session = listed(items, COMPLIANCE, limit=3)
    assert out == items[5:8]
    assert session.limits[0] > 3


class OneItem:
    def __init__(self, item):
        self.item = item

    async def get(self, _model, _id):
        return self.item


def decide(item, pages):
    scope = SimpleNamespace(pages=pages)
    return asyncio.new_event_loop().run_until_complete(approvals_svc.decide_queue_item(
        OneItem(item), item.id, decision="nonsense", scope=scope))


def test_deciding_an_unheld_approval_is_refused():
    with pytest.raises(HTTPException) as e:
        decide(queue_item("C", "energy_anomaly"), COMPLIANCE)
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "page_not_allowed"
    assert e.value.detail["page"] == "energy"


def test_deciding_a_held_approval_goes_on_to_the_decision():
    out = decide(queue_item("A", "compliance_certificate"), COMPLIANCE)
    assert out == {"ok": False, "error": "decision must be approve | edit | dismiss"}
    assert decide(queue_item("C"), None)["error"].startswith("decision must be")


@pytest.mark.parametrize("module,path", [
    (approvals_routes, "/api/approvals/{id}/decide"),
    (compliance_routes, "/api/compliance/approvals/{id}/decide"),
    (energy_routes, "/api/energy/approvals/{id}/decide"),
    (cp_routes, "/api/contract-performance/approvals/{id}/decide"),
])
def test_every_decide_route_hands_the_engine_the_callers_scope(client, monkeypatch, module, path):
    seen = {}

    async def _decide(_session, _item_id, **kw):
        seen.update(kw)
        return {"ok": True}
    monkeypatch.setattr(module.approvals_svc, "decide_queue_item", _decide)
    as_(principal(pages=frozenset({"compliance", "energy", "vendors"})))
    r = client.post(path.format(id=uuid4()), json={"decision": "dismiss"})
    assert r.status_code == 200, r.text
    assert seen["scope"].pages == frozenset({"compliance", "energy", "vendors"})


def test_a_draft_about_an_unheld_item_is_not_sent(client, monkeypatch):
    seen = {}

    async def _send(_session, **kw):
        seen.update(kw)
        return {"ok": True}
    monkeypatch.setattr(approvals_routes.approvals_svc, "send_approval_email_draft", _send)
    as_(principal(pages=COMPLIANCE))
    r = client.post("/api/approvals/send-email",
                    json={"to": "pm@example.com", "subject": "s", "body": "b"})
    assert r.status_code == 200, r.text
    assert seen["pages"] == COMPLIANCE


# ── the value card ────────────────────────────────────────────────────────────

def test_value_summary_follows_the_pages(client, monkeypatch):
    stub(monkeypatch, value_routes.value_svc, "read_value_summary", value_payload())
    as_(principal(pages=frozenset({"energy", "assets"})))
    body = client.get("/api/value/summary").json()
    assert [m["key"] for m in body["ledger"]["modules"]] == ["energy", "assets"]
    assert body["ledger"]["total_detected"] == 500.0
    assert [h["key"] for h in body["pnl"]["heads"]] == ["energy"]


def test_normal_user_value_summary_is_unchanged(client, monkeypatch):
    stub(monkeypatch, value_routes.value_svc, "read_value_summary", value_payload())
    as_(principal())
    assert client.get("/api/value/summary").json() == value_payload()


def test_savings_needs_every_page_it_draws_on(client, monkeypatch):
    stub(monkeypatch, value_routes.savings_svc, "read_savings", {"ok": True})
    as_(principal(pages=frozenset({"ops", "energy", "assets"})))
    r = client.get("/api/value/savings")
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "page_not_allowed"
    assert r.json()["detail"]["page"] == "vendors"
    as_(principal(pages=frozenset({"ops", "energy", "assets", "vendors"})))
    assert client.get("/api/value/savings").json() == {"ok": True}
    as_(principal())
    assert client.get("/api/value/savings").json() == {"ok": True}


# ── scheduled jobs ────────────────────────────────────────────────────────────

JOBS = [{"id": "j1", "job_key": "compliance_expiry_scan", "module": "Compliance", "name": "a"},
        {"id": "j2", "job_key": "energy_anomaly_scan", "module": "Energy", "name": "b"},
        {"id": "j3", "job_key": "question", "module": "Orchestrator", "name": "c"}]
RUNS = [{"id": "r1", "job_id": "j1", "job_key": "compliance_expiry_scan"},
        {"id": "r2", "job_id": "j2", "job_key": "energy_anomaly_scan"},
        {"id": "r3", "job_id": "j3", "job_key": "question", "answer": "everything"}]
STATS = {"runs_today": 9, "failed_today": 1, "emails_week": 4, "timezone": "UTC"}


@pytest.fixture
def crons(monkeypatch):
    svc = crons_routes.cron_svc
    stub(monkeypatch, svc, "list_jobs", JOBS)
    stub(monkeypatch, svc, "recent_runs", RUNS)
    stub(monkeypatch, svc, "stats", STATS)
    stub(monkeypatch, svc, "daily_status", {"timezone": "UTC", "days": ["d"], "jobs": [
        {"job_id": "j1", "name": "a"}, {"job_id": "j2", "name": "b"}, {"job_id": "j3", "name": "c"}]})
    stub(monkeypatch, svc, "job_runs", [{"id": "r"}])
    stub(monkeypatch, svc, "job_events", [{"id": "e"}])

    async def _get(_session, _org, job_id):
        return next((dict(j) for j in JOBS if j["id"] == {"1": "j1", "2": "j2", "3": "j3"}[str(job_id)[-1]]), None)
    monkeypatch.setattr(svc, "get_job", _get)


def job_uuid(n):
    return f"00000000-0000-4000-8000-00000000000{n}"


def test_cron_list_follows_the_pages(client, crons):
    as_(principal(pages=COMPLIANCE))
    body = client.get("/api/crons").json()
    assert [j["id"] for j in body["jobs"]] == ["j1"]
    assert [r["id"] for r in body["recent_runs"]] == ["r1"]
    assert body["stats"] == {"runs_today": None, "failed_today": None, "emails_week": None,
                             "timezone": "UTC"}


def test_cron_list_is_unchanged_for_every_page(client, crons):
    as_(principal())
    body = client.get("/api/crons").json()
    assert body["jobs"] == JOBS and body["recent_runs"] == RUNS and body["stats"] == STATS


def test_question_cron_hidden_from_role_user(client, crons):
    as_(principal(pages=frozenset({"compliance", "energy", "vendors", "ops", "assets", "buildings"})))
    body = client.get("/api/crons").json()
    assert "j3" not in [j["id"] for j in body["jobs"]]
    assert "r3" not in [r["id"] for r in body["recent_runs"]]
    assert client.get(f"/api/crons/{job_uuid(3)}/runs").status_code == 404
    assert client.get(f"/api/crons/{job_uuid(3)}/events").status_code == 404


def test_cron_catalogue_and_daily_follow_the_pages(client, crons):
    as_(principal(pages=COMPLIANCE))
    keys = {j["module"] for j in client.get("/api/crons/catalogue").json()["jobs"]}
    assert keys == {"Compliance"}
    assert [j["job_id"] for j in client.get("/api/crons/daily").json()["jobs"]] == ["j1"]
    assert client.get(f"/api/crons/{job_uuid(1)}/runs").status_code == 200
    assert client.get(f"/api/crons/{job_uuid(2)}/runs").status_code == 404
    assert client.get(f"/api/crons/{job_uuid(2)}/events").status_code == 404
    as_(principal())
    assert len(client.get("/api/crons/catalogue").json()["jobs"]) == len(crons_routes.cron_svc.CATALOGUE)
    assert client.get(f"/api/crons/{job_uuid(3)}/runs").status_code == 200


# ── report cards ──────────────────────────────────────────────────────────────

def a_run(minutes):
    return SimpleNamespace(ran_at=NARROWED + timedelta(minutes=minutes), tag=f"{minutes:+d}")


@pytest.fixture
def cards(monkeypatch):
    svc = reports_routes.card_svc
    card = SimpleNamespace(id=uuid4(), report_id=uuid4(), status="idle")
    report = SimpleNamespace(id=card.report_id)
    runs = [a_run(10), a_run(-10), a_run(-20)]
    stub(monkeypatch, svc, "get_card", card)
    monkeypatch.setattr(svc, "get_card", _const(card))
    monkeypatch.setattr(svc, "card_runs", _const(runs))
    monkeypatch.setattr(svc, "list_reports", _const([report]))
    monkeypatch.setattr(svc, "list_cards", _const([card]))
    monkeypatch.setattr(svc, "latest_runs", _const({card.id: runs}))
    monkeypatch.setattr(svc, "run_to_dict", lambda r: {"tag": r.tag})
    monkeypatch.setattr(svc, "card_to_dict", lambda c, rs=None: {"runs": [r.tag for r in rs or []]})
    monkeypatch.setattr(svc, "report_to_dict", lambda r, cs=None: {"cards": cs or []})
    return card


def _const(value):
    async def _fn(*_a, **_k):
        return value
    return _fn


def test_report_runs_before_narrowing_are_hidden(client, cards):
    as_(principal(pages=COMPLIANCE, narrowed_at=NARROWED))
    assert client.get(f"/api/reports/cards/{cards.id}/runs").json()["runs"] == [{"tag": "+10"}]
    assert client.get(f"/api/reports/cards/{cards.id}").json()["card"]["runs"] == ["+10"]
    assert client.get("/api/reports").json()["reports"][0]["cards"][0]["runs"] == ["+10"]


def test_report_runs_are_whole_without_a_narrowing(client, cards):
    for p in (principal(), principal(pages=COMPLIANCE)):
        as_(p)
        assert len(client.get(f"/api/reports/cards/{cards.id}/runs").json()["runs"]) == 3
        assert client.get("/api/reports").json()["reports"][0]["cards"][0]["runs"] == ["+10", "-10", "-20"]


# ── documents ─────────────────────────────────────────────────────────────────

DOCS = {"ok": True, "documents": [{"document_id": "1", "type": "compliance_certificate"},
                                  {"document_id": "2", "type": "contract"},
                                  {"document_id": "3", "type": "drawing"}], "personal_hidden": 0}


def test_a_buildings_documents_follow_the_pages(client, monkeypatch):
    stub(monkeypatch, documents_routes.reg, "list_documents", DOCS)
    as_(principal(pages=COMPLIANCE))
    body = client.get(f"/api/documents?building_id={uuid4()}").json()
    assert [d["document_id"] for d in body["documents"]] == ["1", "3"]
    as_(principal())
    assert len(client.get(f"/api/documents?building_id={uuid4()}").json()["documents"]) == 3


def test_document_search_is_given_the_callers_pages(client, monkeypatch):
    seen = {}

    async def _search(_session, **kw):
        seen.update(kw)
        return {"ok": True}
    monkeypatch.setattr(documents_routes.doc_svc, "search", _search)
    as_(principal(pages=COMPLIANCE))
    assert client.get("/api/documents/search?q=fire+alarm").status_code == 200
    assert seen["pages"] == COMPLIANCE


def test_document_search_drops_unheld_types(monkeypatch):
    found = [{"document_id": "1", "title": "Cert", "file_name": "c.pdf", "doc_type": "certificate",
              "chunks": 0, "sensitivity": None},
             {"document_id": "2", "title": "MSA", "file_name": "m.pdf", "doc_type": "contract",
              "chunks": 0, "sensitivity": None}]
    monkeypatch.setattr(doc_svc, "linked_documents", _const(found))
    go = asyncio.new_event_loop().run_until_complete
    out = go(doc_svc.search(NoDatabase(), question="notice period", organization_id=ORG,
                            vendor="Acme", pages=COMPLIANCE))
    assert [d["document_id"] for d in out["not_indexed"]] == ["1"]
    assert out["documents_found"] == 1
    out = go(doc_svc.search(NoDatabase(), question="notice period", organization_id=ORG, vendor="Acme"))
    assert out["documents_found"] == 2


# ── buildings ─────────────────────────────────────────────────────────────────

def test_buildings_list_gives_names_to_everyone_and_the_register_to_holders(client, monkeypatch):
    table = {"ok": True, "root": "buildings", "buildings": [building_row()]}
    stub(monkeypatch, energy_routes.bld_svc, "list_buildings", table)
    as_(principal(pages=frozenset({"energy"})))
    (row,) = client.get("/api/energy/buildings").json()["buildings"]
    assert row["name"] == "Harbour Point" and "eui_kwh_per_m2" not in row and "graph_counts" not in row
    as_(principal(pages=COMPLIANCE))
    (row,) = client.get("/api/energy/buildings").json()["buildings"]
    assert row["graph_counts"] == {"floors": 6, "certificates": 12, "documents": 7}
    as_(principal())
    assert client.get("/api/energy/buildings").json() == table


def test_hoist_score_follows_the_pages(client, monkeypatch):
    stub(monkeypatch, energy_routes.bld_svc, "list_buildings", {"ok": True, "root": "buildings", "buildings": []})
    monkeypatch.setattr(energy_routes.hoist_svc, "portfolio_hoist_score", lambda *_a, **_k: score())
    as_(principal(pages=COMPLIANCE))
    body = client.get("/api/energy/hoist-score").json()
    assert [d["key"] for d in body["domains"]] == ["compliance"]
    assert body["score"] is None
    as_(principal())
    assert client.get("/api/energy/hoist-score").json()["score"] == 60


def test_building_graph_drops_unheld_node_kinds(client, monkeypatch):
    bid = uuid4()
    monkeypatch.setattr(energy_routes, "_building_or_404", _const(bid))
    stub(monkeypatch, energy_routes.bld_tree, "building_tree", tree())
    as_(principal(pages=COMPLIANCE))
    body = client.get(f"/api/energy/buildings/{bid}/graph").json()
    assert [b["table"] for b in body["branches"]] == ["floors", "documents"]
    as_(principal())
    assert client.get(f"/api/energy/buildings/{bid}/graph").json() == tree()


# ── an asset ──────────────────────────────────────────────────────────────────

def test_an_assets_holder_without_energy_gets_no_anomaly_on_the_asset(client, monkeypatch):
    detail = {"ok": True, "asset": {"id": "a1"}, "anomaly": {"type": "night_load", "gbp": 900},
              "value": {"value_at_risk": 5}, "readings": [], "failure_assessment": {}}
    monkeypatch.setattr(energy_routes.anom_svc, "resolve_asset", _const("a1"))
    monkeypatch.setattr(energy_routes.position_svc, "building_ids_for", _const(None))
    stub(monkeypatch, energy_routes.ai_svc, "asset_detail", detail)
    as_(principal(pages=frozenset({"assets"})))
    body = client.get("/api/energy/assets/a1/intelligence").json()
    assert body["anomaly"] is None
    assert body["value"] == {"value_at_risk": 5}          # the asset's own figures stay
    for p in (principal(pages=frozenset({"assets", "energy"})), principal()):
        as_(p)
        assert client.get("/api/energy/assets/a1/intelligence").json() == detail
