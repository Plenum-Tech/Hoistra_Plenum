"""Every route says which page owns its data, and a caller without that page is refused.

URL prefixes do not match pages — /api/energy serves the buildings register, asset condition
and the energy register — so the owner of each route is written down, one row per route, in
engines/auth/page_map.py. These tests are what keep that table complete: a route added later
with no row fails here, by name, before it can ship open to everyone.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.api.routes import auth as auth_routes
from src.app import app
from src.db import get_session
from src.engines.auth import page_map
from src.engines.auth import pages as P
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
NON_PAGE = {P.SHARED, P.INGEST, P.ADMIN, P.PUBLIC}


def served() -> list[tuple[str, str, APIRoute]]:
    out = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path, r))
    return out


SERVED = served()
PAGE_OWNED = [(m, p) for m, p, _ in SERVED
              if (page_map.tags_for(m, p) or ()) and not (set(page_map.tags_for(m, p)) & NON_PAGE)]


def principal(pages=None, role="user"):
    return Principal(
        user_id=uuid4(), email="x@example.com", organization_id=ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=True, building_ids=None if role != "user" else (),
        access_role="general_user" if pages is not None else None, pages=pages)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its page check")

    async def commit(self):
        pass


@pytest.fixture
def client():
    async def _session():
        yield Exploding()
    app.dependency_overrides[get_session] = _session
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def as_(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


def call(client, method, path):
    url = re.sub(r"\{[^}]+\}", str(uuid4()), path)
    return client.request(method, url)


def refused_for_page(response) -> bool:
    if response.status_code != 403:
        return False
    try:
        return response.json()["detail"]["reason"] == "page_not_allowed"
    except Exception:  # noqa: BLE001
        return False


# ── the table is complete and well formed ─────────────────────────────────────

def test_every_route_has_tags():
    missing = sorted({(m, p) for m, p, _ in SERVED if page_map.tags_for(m, p) is None})
    assert not missing, (
        "These routes have no row in engines/auth/page_map.py. Add each with the page that "
        f"owns its data (or shared / ingest / admin / public): {missing}")


def test_the_table_names_no_route_that_is_not_served():
    live = {(m, p) for m, p, _ in SERVED}
    stale = sorted(set(page_map.PAGE_MAP) - live)
    assert not stale, f"page_map.py has rows for routes this service does not serve: {stale}"


def test_every_tag_is_a_page_or_a_known_kind():
    for key, tags in page_map.PAGE_MAP.items():
        assert tags, key
        assert set(tags) <= set(P.PAGES) | NON_PAGE, (key, tags)


def test_needs_all_names_page_owned_routes_and_pages():
    for key, pages in page_map.NEEDS_ALL.items():
        assert key in page_map.PAGE_MAP, key
        assert set(pages) <= set(P.PAGES), (key, pages)


def _carries(dependant, target) -> bool:
    return dependant.call is target or any(_carries(d, target) for d in dependant.dependencies)


def test_every_page_tagged_route_carries_the_gate():
    bare = sorted({(m, p) for m, p, r in SERVED
                   if set(page_map.tags_for(m, p) or ()) & set(P.PAGES)
                   and not _carries(r.dependant, auth_routes.page_gate)})
    assert not bare, f"page-tagged routes without the gate dependency: {bare}"


# ── an unrestricted caller is untouched ───────────────────────────────────────

@pytest.mark.parametrize("who", ["normal user", "admin", "superadmin"])
def test_unrestricted_caller_passes_every_gate(client, who):
    as_(principal(role={"normal user": "user"}.get(who, who)))
    refused = [(m, p) for m, p, _ in SERVED
               if page_map.tags_for(m, p) and P.PUBLIC not in page_map.tags_for(m, p)
               and refused_for_page(call(client, m, p))]
    assert not refused


# ── a restricted caller gets exactly their pages ──────────────────────────────

@pytest.mark.parametrize("held", [
    frozenset({"compliance", "buildings"}), frozenset({"energy"}), frozenset({"assets"}),
    frozenset({"vendors", "ops"}), frozenset(),
])
def test_a_role_user_is_refused_every_route_owned_by_another_page(client, held):
    as_(principal(pages=held))
    wrong = []
    for m, p in PAGE_OWNED:
        tags = set(page_map.tags_for(m, p))
        needs_all = set(page_map.NEEDS_ALL.get((m, p), ()))
        should_pass = bool(tags & held) and needs_all <= held
        got = not refused_for_page(call(client, m, p))
        if got != should_pass:
            wrong.append((m, p, sorted(tags), "passed" if got else "refused"))
    assert not wrong


def test_compliance_officer_is_refused_energy_and_vendors(client):
    as_(principal(pages=frozenset({"compliance", "buildings"})))
    for path in ("/api/energy/anomalies", "/api/energy/meters",
                 "/api/contract-performance/contracts", "/api/contract-performance/scorecards",
                 "/api/assets/replacement-candidates", "/api/work-orders/blockers"):
        r = client.get(path)
        assert r.status_code == 403, (path, r.status_code)
        d = r.json()["detail"]
        assert d["reason"] == "page_not_allowed", path
        assert d["error"] == "That is not part of the pages you have access to."
        assert d["ok"] is False and d["page"] in P.PAGES
    for path in ("/api/compliance/certificates", "/api/compliance/coverage/buildings",
                 f"/api/energy/buildings/{uuid4()}/graph", "/api/energy/graph/tables"):
        assert not refused_for_page(client.get(path)), path


def test_buildings_register_lives_under_energy_prefix_but_needs_buildings(client):
    graph = f"/api/energy/buildings/{uuid4()}/graph"
    as_(principal(pages=frozenset({"energy"})))
    assert refused_for_page(client.get(graph))
    assert refused_for_page(client.get("/api/energy/graph/shape"))
    assert not refused_for_page(client.get("/api/energy/anomalies"))
    as_(principal(pages=frozenset({"buildings"})))
    assert not refused_for_page(client.get(graph))
    assert refused_for_page(client.get("/api/energy/anomalies"))


def test_vendor_buildings_allowed_with_either_page(client):
    for held in ({"vendors"}, {"compliance"}):
        as_(principal(pages=frozenset(held)))
        assert not refused_for_page(client.get("/api/compliance/vendor-buildings")), held
    as_(principal(pages=frozenset({"energy"})))
    assert refused_for_page(client.get("/api/compliance/vendor-buildings"))


def test_action_needing_two_pages_refused_with_one(client):
    path = f"/api/compliance/certificates/{uuid4()}/create-vendor"
    as_(principal(pages=frozenset({"compliance"})))
    r = client.post(path, json={})
    assert refused_for_page(r)
    assert r.json()["detail"]["page"] == "vendors"
    as_(principal(pages=frozenset({"compliance", "vendors"})))
    assert not refused_for_page(client.post(path, json={}))


def test_unmapped_route_refused_for_restricted_caller(client, monkeypatch):
    key = ("GET", "/api/energy/anomalies")
    trimmed = {k: v for k, v in page_map.PAGE_MAP.items() if k != key}
    monkeypatch.setattr(page_map, "PAGE_MAP", trimmed)
    as_(principal(pages=frozenset(P.PAGES)))          # holds every page, still refused
    assert refused_for_page(client.get("/api/energy/anomalies"))
    as_(principal())                                  # unrestricted: untouched
    assert not refused_for_page(client.get("/api/energy/anomalies"))


def test_shared_and_ingest_routes_are_open_to_a_role_user(client):
    as_(principal(pages=frozenset({"energy"})))
    for method, path in (("GET", "/api/approvals"), ("GET", "/api/value/summary"),
                         ("GET", "/api/reports"), ("GET", "/api/crons"),
                         ("GET", "/api/documents"), ("GET", "/api/energy/buildings"),
                         ("GET", "/api/ingestion/cases")):
        assert not refused_for_page(client.request(method, path)), path


@pytest.mark.parametrize("pages", [None, frozenset({"compliance"})])
def test_the_gate_lets_a_holder_through_to_the_handler(client, pages):
    # Not merely "not refused": the request must arrive. The Exploding session answers 500
    # only when the handler itself ran, so a gate that broke the request on the way in — a
    # 422 for a parameter it invented, say — fails here.
    as_(principal(pages=pages))
    r = client.get("/api/compliance/certificates")
    assert r.status_code == 500, (r.status_code, r.text[:200])
