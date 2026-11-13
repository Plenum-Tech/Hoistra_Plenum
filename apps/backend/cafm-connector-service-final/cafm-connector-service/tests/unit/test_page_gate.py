"""Each record kind says which page owns it, and a caller without that page is refused.

The connector's CRUD surface is one REST resource per record kind — assets, vendors, work
orders — so ownership is written per resource in api/page_access.py. These tests keep that
table complete (a resource added later with no row fails here by name) and pin that a caller
with every page is untouched.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SECRETS_AES_KEY", "00" * 32)

from cafm_connector.api import page_access as A  # noqa: E402
from cafm_connector.api import security  # noqa: E402
from cafm_connector.api.routes.plenum_cafm import plenum_router  # noqa: E402
from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db  # noqa: E402

ORG = UUID("11111111-1111-1111-1111-111111111111")
PAGES = {"buildings", "compliance", "energy", "vendors", "ops", "assets"}
NON_PAGE = {"shared", "ingest", "admin", "public"}
PREFIX = "/api/v1/plenum"


def caller(pages=None, role="user"):
    return security.Principal(user_id=uuid4(), email="dev@example.com", organization_id=ORG,
                              role=role, building_ids=None, pages=pages)


class Exploding:
    def __getattr__(self, name):
        raise AssertionError(f"route reached the database ({name}) before its page check")


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(plenum_router, prefix=PREFIX)

    async def _db():
        yield Exploding()
    a.dependency_overrides[get_plenum_db] = _db
    return a


def as_(app, p):
    async def _me():
        return p
    app.dependency_overrides[security.current_principal] = _me
    return TestClient(app, raise_server_exceptions=False)


def served(app):
    return [(m, r.path) for r in app.routes if isinstance(r, APIRoute)
            for m in sorted(r.methods - {"HEAD", "OPTIONS"})]


def refused(response) -> bool:
    if response.status_code != 403:
        return False
    detail = response.json().get("detail")
    return isinstance(detail, dict) and detail.get("reason") == "page_not_allowed"


def call(client, method, path):
    return client.request(method, re.sub(r"\{[^}]+\}", str(uuid4()), path))


def test_every_resource_has_an_owner(app):
    missing = sorted({A.resource_of(p) for _m, p in served(app) if A.tags_for(p) is None})
    assert not missing, f"resources with no row in api/page_access.py: {missing}"


def test_the_table_names_no_resource_that_is_not_served(app):
    live = {A.resource_of(p) for _m, p in served(app)}
    assert not sorted(set(A.RESOURCE_PAGES) - live)


def test_every_tag_is_a_page_or_a_known_kind():
    for resource, tags in A.RESOURCE_PAGES.items():
        assert tags and set(tags) <= PAGES | NON_PAGE, (resource, tags)


def _carries(dependant, target) -> bool:
    return dependant.call is target or any(_carries(d, target) for d in dependant.dependencies)


def test_every_route_carries_the_gate(app):
    bare = [(m, p) for m, p in served(app)
            if not _carries(next(r for r in app.routes if isinstance(r, APIRoute) and r.path == p
                                 and m in r.methods).dependant, A.page_gate)]
    assert not bare


@pytest.mark.parametrize("role", ["user", "admin", "superadmin"])
def test_unrestricted_caller_passes_every_gate(app, role):
    client = as_(app, caller(role=role))
    assert not [(m, p) for m, p in served(app) if refused(call(client, m, p))]


@pytest.mark.parametrize("held", [frozenset({"assets"}), frozenset({"vendors"}), frozenset({"ops"}),
                                  frozenset({"compliance", "buildings"}), frozenset()])
def test_a_role_user_is_refused_every_resource_owned_by_another_page(app, held):
    client = as_(app, caller(pages=held))
    wrong = []
    for m, p in served(app):
        tags = set(A.tags_for(p))
        should_pass = bool(tags & NON_PAGE) or bool(tags & held)
        got = not refused(call(client, m, p))
        if got != should_pass:
            wrong.append((m, p, sorted(tags), "passed" if got else "refused"))
    assert not wrong


def test_the_refusal_is_the_agreed_body(app):
    r = as_(app, caller(pages=frozenset({"compliance"}))).get(PREFIX + "/vendors")
    assert r.status_code == 403
    assert r.json()["detail"] == {
        "ok": False, "reason": "page_not_allowed", "page": "vendors",
        "error": "That is not part of the pages you have access to."}


def test_an_unknown_resource_is_refused_to_a_restricted_caller(app, monkeypatch):
    trimmed = {k: v for k, v in A.RESOURCE_PAGES.items() if k != "assets"}
    monkeypatch.setattr(A, "RESOURCE_PAGES", trimmed)
    assert refused(as_(app, caller(pages=frozenset(PAGES))).get(PREFIX + "/assets"))
    assert not refused(as_(app, caller()).get(PREFIX + "/assets"))
