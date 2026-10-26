"""The review router is part of the service: app.ROUTERS holds it, every review path is served under /api/trace-lab,
none repeats a path another router serves, the expected-outcome reveal route is not here, and the app wires the
human-label source for grader validation. No database, no network."""
from __future__ import annotations

from fastapi.testclient import TestClient

from trace_lab import app as app_module
from trace_lab import grader_validation, review_labels, routes_review
from trace_lab.settings import Settings

PREFIX = "/api/trace-lab"
PATHS = [("GET", "/annotations"), ("PUT", "/turns/{turn_id}/verdict"), ("DELETE", "/turns/{turn_id}/verdict"),
         ("GET", "/turns/{turn_id}/annotation-history"), ("POST", "/comments"), ("PUT", "/comments/{lineage_id}"),
         ("DELETE", "/comments/{lineage_id}"), ("GET", "/queue"), ("GET", "/sampling/plan"),
         ("GET", "/sampling/preview"), ("POST", "/sampling/batches"), ("GET", "/sampling/batches"),
         ("GET", "/sampling/batches/{batch_id}"), ("POST", "/sampling/batches/{batch_id}/withdraw"),
         ("GET", "/sampling/manifest"), ("GET", "/map"), ("GET", "/progress")]


def _status(client, method, path):
    """What the mounted app answers: 404 and 405 mean the path or the method is not served; anything else is a route
    (here 401/403/422, since no one is signed in)."""
    filled = path.format(turn_id="turn-1", lineage_id="l-1", batch_id="b-1")
    return client.request(method, PREFIX + filled).status_code


def test_the_review_router_is_in_the_service_routers():
    assert routes_review.router in app_module.ROUTERS


def test_the_review_router_serves_the_review_paths_under_the_prefix():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert _status(client, method, path) not in (404, 405), (method, path)
        assert client.request(method, path.format(turn_id="t", lineage_id="l", batch_id="b")).status_code == 404   # not unprefixed
    assert client.post(PREFIX + "/turns/turn-1/reveal").status_code == 404           # the expected-outcome panel owns reveal


def test_the_review_router_holds_exactly_the_seventeen_routes():
    own = {(m, r.path) for r in routes_review.router.routes for m in r.methods}
    assert own == set(PATHS)


def test_no_review_path_repeats_a_path_another_router_serves():
    others = [(m, r.path) for router in app_module.ROUTERS if router is not routes_review.router
              for r in router.routes for m in r.methods]
    assert not set(others) & {(m, p) for m, p in PATHS}, set(others) & set(PATHS)
    assert len(others) == len(set(others))


def test_every_review_route_refuses_a_caller_who_is_not_signed_in():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert _status(client, method, path) in (401, 403, 422), (method, path)       # never a 200 or a crash


def test_the_app_registers_the_human_label_source_for_grader_validation(monkeypatch):
    monkeypatch.setattr(grader_validation, "_SOURCES", [])
    app_module.create_app(Settings())
    assert review_labels.golden_human_labels in grader_validation._SOURCES
    app_module.create_app(Settings())                                            # idempotent
    assert grader_validation._SOURCES.count(review_labels.golden_human_labels) == 1


def test_a_failing_registration_never_stops_the_app(monkeypatch):
    def boom():
        raise RuntimeError("nope")

    monkeypatch.setattr(review_labels, "register", boom)
    assert app_module.create_app(Settings()) is not None
