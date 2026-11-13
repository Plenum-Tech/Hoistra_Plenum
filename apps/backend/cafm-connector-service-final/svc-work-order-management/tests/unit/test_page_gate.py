"""Every route says which page owns its data, and a caller without that page is refused.

The caller's pages come from the identity service with their buildings (services/principal.py).
services/page_access.py holds one row per route; these tests keep that table complete — a route
added later with no row fails here by name — and pin that a caller with every page is untouched.
"""
from __future__ import annotations

import re
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.app import app
from src.db import get_session
from src.services import maintenance as mx
from src.services import page_access as A
from src.services import principal as P

NON_PAGE = {"shared", "ingest", "admin", "public"}
PAGES = {"buildings", "compliance", "energy", "vendors", "ops", "assets"}


def served():
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path, r))
    return out


SERVED = served()
PAGE_OWNED = [(m, p) for m, p, _ in SERVED
              if A.tags_for(m, p) and not (set(A.tags_for(m, p)) & NON_PAGE)]


def caller(pages=None, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None, role=role,
                       building_ids=None, pages=pages)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its page check")

    async def get(self, *a, **k):
        raise AssertionError("route reached the database before its page check")

    async def commit(self):
        pass

    async def rollback(self):
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
    app.dependency_overrides[P.current_principal] = _dep


def refused(response) -> bool:
    if response.status_code != 403:
        return False
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return False
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    return detail.get("reason") == "page_not_allowed"


def call(client, method, path):
    return client.request(method, re.sub(r"\{[^}]+\}", str(uuid4()), path))


def test_every_route_has_tags():
    missing = sorted({(m, p) for m, p, _ in SERVED if A.tags_for(m, p) is None})
    assert not missing, f"routes with no row in services/page_access.py: {missing}"


def test_the_table_names_no_route_that_is_not_served():
    stale = sorted(set(A.PAGE_MAP) - {(m, p) for m, p, _ in SERVED})
    assert not stale, f"page_access.py has rows for routes this service does not serve: {stale}"


def test_every_tag_is_a_page_or_a_known_kind():
    for key, tags in A.PAGE_MAP.items():
        assert tags and set(tags) <= PAGES | NON_PAGE, (key, tags)
    for key, pages in A.NEEDS_ALL.items():
        assert key in A.PAGE_MAP and set(pages) <= PAGES, key


def _carries(dependant, target) -> bool:
    return dependant.call is target or any(_carries(d, target) for d in dependant.dependencies)


def test_every_page_tagged_route_carries_the_gate():
    bare = sorted({(m, p) for m, p, r in SERVED
                   if set(A.tags_for(m, p) or ()) & PAGES and not _carries(r.dependant, A.page_gate)})
    assert not bare, f"page-tagged routes without the gate: {bare}"


@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
def test_unrestricted_caller_passes_every_gate(client, role):
    as_(caller(role=role))
    assert not [(m, p) for m, p, _ in SERVED if refused(call(client, m, p))]


@pytest.mark.parametrize("held", [frozenset({"ops"}), frozenset({"assets"}),
                                  frozenset({"compliance", "buildings"}), frozenset()])
def test_a_role_user_is_refused_every_route_owned_by_another_page(client, held):
    as_(caller(pages=held))
    wrong = []
    for m, p in PAGE_OWNED:
        tags = set(A.tags_for(m, p))
        should_pass = bool(tags & held) and set(A.NEEDS_ALL.get((m, p), ())) <= held
        got = not refused(call(client, m, p))
        if got != should_pass:
            wrong.append((m, p, sorted(tags), "passed" if got else "refused"))
    assert not wrong


def test_the_refusal_is_the_agreed_body(client):
    as_(caller(pages=frozenset({"compliance"})))
    r = client.get("/api/work-orders/")
    assert r.status_code == 403
    body = r.json()
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
    assert detail["reason"] == "page_not_allowed" and detail["page"] == "ops"
    assert detail["error"] == "That is not part of the pages you have access to."


def test_the_work_order_chat_needs_a_signed_in_caller(client):
    # It answered anybody: the conversation history of any session id, with no token.
    r = client.get(f"/api/chat/{uuid4()}/history")
    assert r.status_code == 401


def test_the_gate_lets_a_holder_through_to_the_handler(client):
    for p in (caller(), caller(pages=frozenset({"ops"}))):
        as_(p)
        assert client.get("/api/work-orders/").status_code not in (401, 403, 422)


# ── maintenance decisions: an approvals item needs its own page ────────────────

def rows():
    return [
        {"work_order": "WO-1", "source": "Maintenance", "state": "Blocked"},
        {"work_order": "WO-2", "source": "Vendors", "state": "Deviation"},     # a blocked order
        {"work_order": None, "source": "Compliance", "state": "To raise"},
        {"work_order": None, "source": "Energy", "state": "To raise"},
        {"work_order": None, "source": "Vendors", "state": "To raise"},        # an approvals item
        {"work_order": None, "source": "Assets", "state": "To raise"},
        {"work_order": None, "source": "Something new", "state": "To raise"},
    ]


def test_decisions_are_untouched_for_every_page():
    r = rows()
    assert mx.held_decisions(r, None) is r


def test_compliance_decisions_dropped_without_compliance():
    kept = mx.held_decisions(rows(), frozenset({"ops"}))
    assert [d["source"] for d in kept] == ["Maintenance", "Vendors"]
    assert all(d["work_order"] for d in kept)       # work orders are this page's own rows


def test_an_approvals_item_shows_with_its_page():
    kept = mx.held_decisions(rows(), frozenset({"ops", "compliance", "assets"}))
    assert [d["source"] for d in kept] == ["Maintenance", "Vendors", "Compliance", "Assets"]


def test_the_source_chips_follow_the_pages():
    labels = ["Assets", "Compliance", "Energy", "Maintenance", "Vendors"]
    assert mx.held_sources(labels, None) == labels
    assert mx.held_sources(labels, frozenset({"ops"})) == ["Maintenance", "Vendors"]
    assert mx.held_sources(labels, frozenset({"ops", "energy"})) == ["Energy", "Maintenance", "Vendors"]
