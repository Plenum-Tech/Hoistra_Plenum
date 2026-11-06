"""Every route asks who is calling, and a run answers only to the people it belongs to.

Measured 6 Nov 2026 against the deployed app, with no token: GET /api/migration listed 70 runs for
one company and 62 for another, and 33 of 35 migration routes answered anyone -- the uploaded
workbook, the downloads, answering gates, re-running nodes, retrying DDL. services/principal.py
had recorded the same on 30 Sep and closed cancel alone; route by route is how 33 stayed open.
services/run_access.py closes the whole service in one middleware, and these tests hold it there.

The REAL app is built and driven with real requests, so a route cannot be missed by a test that
lists routes by hand: the first test walks app.routes itself. Two things are faked -- who a token
belongs to (operations-intelligence's /api/auth/me) and which company owns a run (a row in
migration_jobs) -- because neither is what is under test.
"""
from __future__ import annotations

import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.services import run_access
from src.services.principal import Principal

ORG_A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")
U_ADMIN_A = uuid.uuid4()
U_USER_A = uuid.uuid4()          # has the ingestion right, started RUN_MINE
U_VIEWER_A = uuid.uuid4()        # no ingestion right, also started RUN_VIEWERS
U_ADMIN_B = uuid.uuid4()
U_SUPER = uuid.uuid4()

RUN_MINE = str(uuid.uuid4())     # company A, started by U_USER_A
RUN_VIEWERS = str(uuid.uuid4())  # company A, started by U_VIEWER_A
RUN_NOBODYS = str(uuid.uuid4())  # company A, from before starters were recorded
RUN_B = str(uuid.uuid4())        # company B

PEOPLE = {
    "super":   Principal(U_SUPER, "s@x", None, "superadmin", True),
    "admin-a": Principal(U_ADMIN_A, "a@x", ORG_A, "admin", True),
    "user-a":  Principal(U_USER_A, "u@x", ORG_A, "user", True),
    "viewer-a": Principal(U_VIEWER_A, "v@x", ORG_A, "user", False),
    "admin-b": Principal(U_ADMIN_B, "b@x", ORG_B, "admin", True),
}
RUNS = {RUN_MINE: (ORG_A, U_USER_A), RUN_VIEWERS: (ORG_A, U_VIEWER_A),
        RUN_NOBODYS: (ORG_A, None), RUN_B: (ORG_B, U_ADMIN_B)}

#: The middleware's own refusals. A response carrying one of these was stopped at the door; any
#: other response -- a handler's 500 for want of a database included -- got past it.
REFUSALS = {"missing_token", "invalid_token", "not_found", "not_your_run", "no_ingest_right",
            "platform_only", "admin_only"}


@pytest.fixture(scope="module")
def app():
    from src.app import create_app
    return create_app()


@pytest.fixture
def client(app, monkeypatch):
    async def resolve(authorization):
        from fastapi import HTTPException
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, detail={"ok": False, "error": "Sign in.",
                                             "reason": "missing_token"})
        if token not in PEOPLE:
            raise HTTPException(401, detail={"ok": False, "error": "Sign in again.",
                                             "reason": "invalid_token"})
        return PEOPLE[token]

    async def run_owner(table, run_id):
        if run_id in RUNS:
            return True, *RUNS[run_id]
        return False, None, None

    monkeypatch.setattr(run_access, "resolve", resolve)
    monkeypatch.setattr(run_access, "_run_owner", run_owner)
    return TestClient(app, raise_server_exceptions=False)


def _reason(res) -> str | None:
    try:
        body = res.json()
    except ValueError:
        return None
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail.get("reason") if isinstance(detail, dict) else None


def call(client, who, method, path):
    headers = {"Authorization": f"Bearer {who}"} if who else {}
    return client.request(method, path, headers=headers)


# -- the door: every route, walked from the app itself -----------------------------------------------

def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: RUN_B if "id" in m.group(0) else "x", path)


def test_no_route_answers_without_a_token(app, client):
    """Walks app.routes rather than a list typed here, so tomorrow's route is in it too."""
    open_ = {(m, p) for m, p in run_access.OPEN}
    answered = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            if (method, route.path) in open_ or route.path.startswith(("/docs", "/redoc", "/openapi")):
                continue
            res = call(client, None, method, _concrete(route.path))
            if res.status_code != 401:
                answered.append(f"{method} {route.path} -> {res.status_code}")
    assert not answered, "answered without a token:\n  " + "\n  ".join(answered)


def test_the_health_check_still_answers_anyone(client):
    """The ingress and the roll script read it with no token."""
    assert _reason(call(client, None, "GET", "/health")) not in REFUSALS


# -- whose run it is ---------------------------------------------------------------------------------

@pytest.mark.parametrize("method,suffix", [("GET", "status"), ("GET", "source"),
                                           ("POST", "gate/final"), ("POST", "retry-ddl"),
                                           ("DELETE", "")])
def test_another_companys_run_is_not_there(client, method, suffix):
    """404, not 403: the answer must not confirm that a run with that id exists elsewhere."""
    res = call(client, "admin-a", method, f"/api/migration/{RUN_B}/{suffix}".rstrip("/"))
    assert res.status_code == 404 and _reason(res) == "not_found"


def test_a_run_that_does_not_exist_answers_the_same(client):
    res = call(client, "admin-a", "GET", f"/api/migration/{uuid.uuid4()}/status")
    assert res.status_code == 404 and _reason(res) == "not_found"


def test_an_admin_reaches_every_run_of_their_company(client):
    for run in (RUN_MINE, RUN_VIEWERS, RUN_NOBODYS):
        assert _reason(call(client, "admin-a", "POST", f"/api/migration/{run}/advance")) not in REFUSALS


def test_a_user_reaches_the_run_they_started(client):
    assert _reason(call(client, "user-a", "GET", f"/api/migration/{RUN_MINE}/status")) not in REFUSALS
    assert _reason(call(client, "user-a", "POST", f"/api/migration/{RUN_MINE}/gate/final")) not in REFUSALS


def test_a_user_does_not_reach_a_colleagues_run(client):
    res = call(client, "user-a", "GET", f"/api/migration/{RUN_VIEWERS}/status")
    assert res.status_code == 403 and _reason(res) == "not_your_run"


def test_a_run_from_before_starters_were_recorded_is_an_admins(client):
    """Nobody's is not anybody's."""
    res = call(client, "user-a", "GET", f"/api/migration/{RUN_NOBODYS}/status")
    assert res.status_code == 403 and _reason(res) == "not_your_run"


def test_without_the_ingestion_right_a_user_can_look_but_not_act(client):
    assert _reason(call(client, "viewer-a", "GET", f"/api/migration/{RUN_VIEWERS}/status")) not in REFUSALS
    res = call(client, "viewer-a", "POST", f"/api/migration/{RUN_VIEWERS}/advance")
    assert res.status_code == 403 and _reason(res) == "no_ingest_right"


def test_a_superadmin_reaches_any_companys_run(client):
    assert _reason(call(client, "super", "GET", f"/api/migration/{RUN_B}/status")) not in REFUSALS


def test_the_other_run_kinds_follow_the_same_rule(client):
    """schema-mapping and Fiix ingestion runs are runs too."""
    for kind in ("schema-mapping", "fiix-ingestion"):
        res = call(client, "admin-a", "GET", f"/api/{kind}/{RUN_B}")
        assert res.status_code == 404, (kind, res.status_code)


# -- what nobody outside calls ------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [("POST", "/api/testing/upload"),
                                         ("GET", "/api/activity-log"),
                                         ("GET", "/api/debug/fiix-config"),
                                         ("POST", "/api/registry/approve-canonical"),
                                         ("POST", "/api/migration")])
def test_platform_tools_are_a_superadmins(client, method, path):
    res = call(client, "admin-a", method, path)
    assert res.status_code == 403 and _reason(res) == "platform_only"
    assert _reason(call(client, "super", method, path)) not in REFUSALS


def test_connecting_the_cmms_is_an_admins(client):
    res = call(client, "user-a", "POST", "/api/platforms/fiix/test-connection")
    assert res.status_code == 403 and _reason(res) == "admin_only"
    assert _reason(call(client, "admin-a", "POST", "/api/platforms/fiix/test-connection")) not in REFUSALS


# -- the company a list or a start is for ---------------------------------------------------------------

def test_a_company_cannot_be_named_by_someone_outside_it():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        run_access.company_for(PEOPLE["admin-a"], str(ORG_B))
    assert e.value.status_code == 403


def test_naming_nothing_means_your_own_company():
    assert run_access.company_for(PEOPLE["user-a"], None) == ORG_A
    assert run_access.company_for(PEOPLE["user-a"], str(ORG_A)) == ORG_A


def test_a_superadmin_names_the_company_they_act_for():
    assert run_access.company_for(PEOPLE["super"], str(ORG_B)) == ORG_B


def test_a_list_is_narrowed_to_your_own_runs_unless_you_are_an_admin():
    assert run_access.only_mine(PEOPLE["user-a"]) == U_USER_A
    assert run_access.only_mine(PEOPLE["admin-a"]) is None
    assert run_access.only_mine(PEOPLE["super"]) is None


def test_starting_needs_the_ingestion_right():
    from fastapi import HTTPException
    run_access.may_start(PEOPLE["user-a"])
    run_access.may_start(PEOPLE["admin-a"])
    with pytest.raises(HTTPException) as e:
        run_access.may_start(PEOPLE["viewer-a"])
    assert e.value.status_code == 403


def test_a_start_cannot_choose_the_company_by_leaving_it_out(client):
    """The upload form defaulted organization_id to the platform's default company, so leaving
    it out put your rows in someone else's. Refused for a viewer before any file is read."""
    res = client.post("/api/migration/start-with-upload",
                      headers={"Authorization": "Bearer viewer-a"},
                      files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert res.status_code == 403


# -- a browser on another origin sees the 401, not a CORS failure ---------------------------------------

def test_a_refusal_carries_cors_headers(app, client):
    """The access middleware is registered BEFORE CORS so CORS wraps it. The other way round, a
    401 leaves with no CORS header and the browser reports a CORS failure instead -- which the
    client's refresh-on-401 never sees."""
    from src.config import get_settings
    origins = get_settings().cors_origins or []
    origin = "https://app.example" if "*" in origins else (origins[0] if origins else None)
    if origin is None:
        pytest.skip("CORS is not configured")
    res = client.get("/api/migration", headers={"Origin": origin})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") in ("*", origin)


# -- mounted the way the container mounts it -----------------------------------------------------------

ROOT = "/backend/schema-mapper"


@pytest.fixture
def mounted(app, client):
    """The app as deployed: behind nginx, run with --root-path /backend/schema-mapper. Every
    test above passed while the middleware matched on the FULL path and so, in the container,
    refused /health and never ran the per-run company check (found 6 Nov 2026 by probing the
    built image)."""
    return TestClient(app, root_path=ROOT, raise_server_exceptions=False)


def _under_root(c, who, method, path):
    headers = {"Authorization": f"Bearer {who}"} if who else {}
    return c.request(method, ROOT + path, headers=headers)


def test_mounted_the_health_check_is_still_open(mounted):
    res = _under_root(mounted, None, "GET", "/health")
    assert _reason(res) not in REFUSALS and res.status_code != 401


def test_mounted_another_companys_run_is_still_not_there(mounted):
    """The case that matters: signed in, and asking for somebody else's company's run."""
    res = _under_root(mounted, "admin-a", "GET", f"/api/migration/{RUN_B}/status")
    assert res.status_code == 404 and _reason(res) == "not_found"


def test_mounted_platform_tools_are_still_a_superadmins(mounted):
    res = _under_root(mounted, "admin-a", "POST", "/api/testing/upload")
    assert res.status_code == 403 and _reason(res) == "platform_only"


@pytest.mark.parametrize("scope,expect", [
    ({"path": "/backend/schema-mapper/api/migration/x", "root_path": "/backend/schema-mapper"},
     "/api/migration/x"),
    ({"path": "/api/migration/x", "root_path": "/backend/schema-mapper"}, "/api/migration/x"),
    ({"path": "/backend/schema-mapper", "root_path": "/backend/schema-mapper"}, "/"),
    ({"path": "/health", "root_path": ""}, "/health"),
])
def test_the_route_path_is_read_the_same_whichever_way_the_server_fills_the_scope(scope, expect):
    """Servers differ on whether scope['path'] already carries the root path."""
    req = SimpleNamespace(scope=scope, url=SimpleNamespace(path=scope["path"]))
    assert run_access.route_path(req) == expect


# -- the pure rule, every role against every situation -------------------------------------------------

@pytest.mark.parametrize("who,method,run,expect", [
    ("super",    "POST", RUN_B,       None),
    ("admin-a",  "POST", RUN_B,       404),
    ("admin-a",  "POST", RUN_NOBODYS, None),
    ("user-a",   "GET",  RUN_MINE,    None),
    ("user-a",   "POST", RUN_MINE,    None),
    ("user-a",   "GET",  RUN_VIEWERS, 403),
    ("user-a",   "GET",  RUN_NOBODYS, 403),
    ("viewer-a", "GET",  RUN_VIEWERS, None),
    ("viewer-a", "POST", RUN_VIEWERS, 403),
    ("admin-b",  "GET",  RUN_MINE,    404),
])
def test_the_rule(who, method, run, expect):
    org, starter = RUNS[run]
    got = run_access.decide(PEOPLE[who], method, org, starter)
    assert (got[0] if got else None) == expect
