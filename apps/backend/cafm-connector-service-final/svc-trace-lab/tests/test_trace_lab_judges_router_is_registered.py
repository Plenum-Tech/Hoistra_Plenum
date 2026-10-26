"""The judges router is part of the service: app.ROUTERS holds it, every path is served under /api/trace-lab and not
without the prefix, none repeats a path another router serves, it holds exactly the seventeen routes, nobody who is not
signed in gets past the caller check, and the module adds no dependency and touches nothing outside Trace Lab.
No database, no network."""
from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from trace_lab import app as app_module
from trace_lab import routes_judges
from trace_lab.settings import Settings

PREFIX = "/api/trace-lab"
PATHS = [("GET", "/checks"), ("GET", "/checks/turns/{turn_id}"), ("GET", "/modes/{mode_id}/judge-readiness"),
         ("POST", "/modes/{mode_id}/splits"), ("GET", "/modes/{mode_id}/splits/active"), ("GET", "/judges"),
         ("POST", "/judges"), ("GET", "/judges/{judge_id}"), ("POST", "/judges/{judge_id}/preview"),
         ("GET", "/judges/{judge_id}/estimate"), ("POST", "/judges/{judge_id}/runs"), ("GET", "/judge-runs/{run_id}"),
         ("PATCH", "/judge-runs/{run_id}/disagreements/{turn_id}"), ("POST", "/judges/{judge_id}/freeze"),
         ("PATCH", "/judges/{judge_id}/decision"), ("PATCH", "/judges/{judge_id}/threshold"), ("GET", "/failure-report")]


def _filled(path):
    return path.format(turn_id="turn-1", mode_id="m-1", judge_id="j-1", run_id="r-1")


def test_the_judges_router_is_in_the_service_routers():
    assert routes_judges.router in app_module.ROUTERS


def test_the_judges_router_holds_exactly_these_routes():
    own = {(m, r.path) for r in routes_judges.router.routes for m in r.methods}
    assert own == set(PATHS) and len(PATHS) == 17
    assert not any(r.path.startswith(PREFIX) for r in routes_judges.router.routes)       # the app adds the prefix


def test_the_service_serves_them_under_the_prefix_and_not_without_it():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code not in (404, 405), (method, path)
        assert client.request(method, _filled(path)).status_code == 404


def test_no_judges_path_repeats_a_path_another_router_serves():
    others = [(m, r.path) for router in app_module.ROUTERS if router is not routes_judges.router
              for r in router.routes for m in r.methods]
    assert not set(others) & set(PATHS), set(others) & set(PATHS)
    assert len(others) == len(set(others))


def test_every_route_refuses_a_caller_who_is_not_signed_in():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code in (401, 403, 422), (method, path)


def test_the_judges_routes_answer_the_other_routers_unchanged():
    """The existing mode paths still go to the taxonomy router: /modes/{id}/split is not /modes/{id}/splits."""
    taxonomy_paths = {(m, r.path) for router in app_module.ROUTERS if router is not routes_judges.router
                      for r in router.routes for m in r.methods if r.path.startswith("/modes")}
    assert ("POST", "/modes/{mode_id}/split") in taxonomy_paths
    assert ("POST", "/modes/{mode_id}/splits") not in taxonomy_paths


def test_the_module_imports_only_the_standard_library_fastapi_pydantic_and_trace_lab():
    tree = ast.parse(Path(routes_judges.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"__future__", "dataclasses", "math", "time", "collections", "typing", "fastapi", "pydantic", "src"}, roots
    assert "slowapi" not in roots
