"""The taxonomy router is part of the service: app.ROUTERS holds it, every path is served under /api/trace-lab, none repeats
a path another router serves, and nobody who is not signed in gets past the caller check. No database, no network."""
from __future__ import annotations

from fastapi.testclient import TestClient

from trace_lab import app as app_module
from trace_lab import routes_taxonomy
from trace_lab.settings import Settings

PREFIX = "/api/trace-lab"
PATHS = [("GET", "/modes"), ("GET", "/modes/{mode_id}"), ("POST", "/modes"), ("PATCH", "/modes/{mode_id}"),
         ("POST", "/modes/merge"), ("POST", "/modes/{mode_id}/rename"), ("POST", "/modes/{mode_id}/split"),
         ("POST", "/modes/{mode_id}/retire"), ("POST", "/modes/{mode_id}/restore"),
         ("GET", "/notes/unfiled"), ("POST", "/notes/{annotation_id}/file"), ("POST", "/notes/{annotation_id}/dispose"),
         ("POST", "/notes/{annotation_id}/undispose"),
         ("GET", "/labels/matrix"), ("GET", "/turns/{turn_id}/labels"), ("PUT", "/labels/{turn_id}/{mode_id}"),
         ("GET", "/labels/{turn_id}/{mode_id}/history"),
         ("GET", "/proposals/estimate"), ("POST", "/proposal-runs"), ("GET", "/proposal-runs"),
         ("GET", "/proposal-runs/{run_id}"), ("POST", "/proposal-runs/{run_id}/resume"),
         ("POST", "/proposal-runs/{run_id}/pause"), ("GET", "/proposals"), ("POST", "/proposals/{proposal_id}/decide"),
         ("POST", "/proposals/bulk/check"), ("POST", "/proposals/bulk/approve"),
         ("GET", "/requirements"), ("POST", "/requirements"), ("POST", "/requirements/seed"),
         ("POST", "/requirements/{code}/revisions"), ("POST", "/requirements/{code}/withdraw")]


def _filled(path):
    return path.format(mode_id="m-1", annotation_id="a-1", turn_id="turn-1", run_id="r-1", proposal_id="p-1", code="GEN-1")


def test_the_taxonomy_router_is_in_the_service_routers():
    assert routes_taxonomy.router in app_module.ROUTERS


def test_the_taxonomy_router_holds_exactly_these_routes():
    own = {(m, r.path) for r in routes_taxonomy.router.routes for m in r.methods}
    assert own == set(PATHS) and len(PATHS) == 32


def test_the_service_serves_them_under_the_prefix_and_not_without_it():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code not in (404, 405), (method, path)
        assert client.request(method, _filled(path)).status_code == 404


def test_no_taxonomy_path_repeats_a_path_another_router_serves():
    others = [(m, r.path) for router in app_module.ROUTERS if router is not routes_taxonomy.router
              for r in router.routes for m in r.methods]
    assert not set(others) & set(PATHS), set(others) & set(PATHS)
    assert len(others) == len(set(others))


def test_every_route_refuses_a_caller_who_is_not_signed_in():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code in (401, 403, 422), (method, path)
