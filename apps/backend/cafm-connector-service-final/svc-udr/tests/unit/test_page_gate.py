"""Every route says who may reach it, and a caller held to some pages is kept to them.

The generic table routes are administrator-only, so a caller with an access role (always a
plain user) never reaches them. What is left for a signed-in user is the saved spaces, which
are names, and the saved run history, whose snapshots hold rows of whatever table was asked —
any page's register — so a caller held to some pages is given none of it.
"""
from __future__ import annotations

import os
import re
from uuid import uuid4

os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")

import pytest  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.app import app  # noqa: E402
from src.services import page_access as A  # noqa: E402
from src.services import principal as P  # noqa: E402

PAGES = {"buildings", "compliance", "energy", "vendors", "ops", "assets"}
NON_PAGE = {"shared", "ingest", "admin", "public"}
SERVED = [(m, r.path, r) for r in app.routes if isinstance(r, APIRoute)
          for m in sorted(r.methods - {"HEAD", "OPTIONS"})]


def caller(pages=None, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None, role=role,
                       building_ids=None, pages=pages)


@pytest.fixture
def client():
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
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(detail, dict) and detail.get("reason") == "page_not_allowed"


def call(client, method, path):
    return client.request(method, re.sub(r"\{[^}]+\}", str(uuid4()), path))


def test_every_route_has_tags():
    missing = sorted({(m, p) for m, p, _ in SERVED if A.tags_for(m, p) is None})
    assert not missing, f"routes with no row in services/page_access.py: {missing}"


def test_the_table_names_no_route_that_is_not_served():
    assert not sorted(set(A.PAGE_MAP) - {(m, p) for m, p, _ in SERVED})


def test_every_tag_is_a_page_or_a_known_kind():
    for key, tags in A.PAGE_MAP.items():
        assert tags and set(tags) <= PAGES | NON_PAGE, (key, tags)
    for key, pages in A.NEEDS_ALL.items():
        assert key in A.PAGE_MAP and set(pages) <= PAGES


def _carries(dependant, target) -> bool:
    return dependant.call is target or any(_carries(d, target) for d in dependant.dependencies)


def test_every_page_tagged_route_carries_the_gate():
    bare = sorted({(m, p) for m, p, r in SERVED
                   if set(A.tags_for(m, p) or ()) & PAGES and not _carries(r.dependant, A.page_gate)})
    assert not bare


def test_the_saved_run_history_needs_every_page(client):
    runs = [(m, p) for m, p, _ in SERVED if p.startswith("/api/udr/")]
    assert len(runs) == 4
    for held in (frozenset({"compliance", "buildings"}), frozenset(PAGES - {"energy"}), frozenset()):
        as_(caller(pages=held))
        assert all(refused(call(client, m, p)) for m, p in runs), held
    r = client.get("/api/udr/runs")
    assert r.json()["detail"]["error"] == "That is not part of the pages you have access to."


@pytest.mark.parametrize("who", [caller(), caller(role="admin"), caller(pages=frozenset(PAGES))])
def test_a_holder_of_every_page_is_not_refused(client, who):
    as_(who)
    assert not [(m, p) for m, p, _ in SERVED if refused(call(client, m, p))]


def test_saved_spaces_stay_open_to_a_role_user(client):
    as_(caller(pages=frozenset({"compliance"})))
    assert not refused(client.get("/api/spaces"))
