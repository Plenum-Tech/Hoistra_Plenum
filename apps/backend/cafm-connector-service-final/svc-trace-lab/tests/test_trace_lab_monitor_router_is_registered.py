"""The Watch router is part of the service: app.ROUTERS holds it, every path is served under /api/trace-lab and not
without the prefix, none repeats a path another router serves, it holds exactly the twenty-three routes, nobody who is
not signed in gets past the caller check, every route reaches the schema check, the eval-case grading handler is
registered once the service is imported (a restart settles a grading job by that name), and the module adds no
dependency and touches nothing outside Trace Lab. No database, no network."""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from trace_lab import app as app_module
from trace_lab import db, routes_monitor
from trace_lab.settings import Settings

PREFIX = "/api/trace-lab"
PATHS = [("GET", "/monitor/plan"), ("POST", "/monitor/batches"), ("POST", "/monitor/batches/{batch_id}/resume"),
         ("GET", "/monitor/batches"), ("GET", "/monitor/batches/{batch_id}"), ("GET", "/monitor/chart"),
         ("GET", "/monitor/code-checks"), ("GET", "/monitor/automation"),
         ("POST", "/monitor/batches/{batch_id}/review-batch"), ("GET", "/eval-cases"), ("POST", "/eval-cases"),
         ("GET", "/eval-cases/{case_id}"), ("PATCH", "/eval-cases/{case_id}"), ("POST", "/eval-cases/{case_id}/confirm"),
         ("POST", "/eval-cases/{case_id}/retire"), ("POST", "/eval-cases/{case_id}/baseline"),
         ("POST", "/eval-cases/{case_id}/repeat"), ("POST", "/turns/{turn_id}/repeat"),
         ("POST", "/eval-runs/{golden_run_id}/grade"), ("GET", "/eval-runs/{golden_run_id}"),
         ("POST", "/release-checks"), ("GET", "/release-checks"), ("GET", "/release-checks/{golden_run_id}")]


def _filled(path):
    return path.format(batch_id="b-1", case_id="c-1", turn_id="turn-1", golden_run_id="r-1")


def test_the_watch_router_is_in_the_service_routers():
    assert routes_monitor.router in app_module.ROUTERS


def test_the_watch_router_holds_exactly_these_routes():
    own = {(m, r.path) for r in routes_monitor.router.routes for m in r.methods}
    assert own == set(PATHS) and len(PATHS) == 23 == len(routes_monitor.router.routes)
    assert not any(r.path.startswith(PREFIX) for r in routes_monitor.router.routes)       # the app adds the prefix


def test_the_service_serves_them_under_the_prefix_and_not_without_it():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code not in (404, 405), (method, path)
        assert client.request(method, _filled(path)).status_code == 404


def test_no_watch_path_repeats_a_path_another_router_serves():
    others = [(m, r.path) for router in app_module.ROUTERS if router is not routes_monitor.router
              for r in router.routes for m in r.methods]
    assert not set(others) & set(PATHS), set(others) & set(PATHS)
    assert len(others) == len(set(others))


def test_every_route_refuses_a_caller_who_is_not_signed_in():
    client = TestClient(app_module.create_app(Settings()))
    for method, path in PATHS:
        assert client.request(method, PREFIX + _filled(path)).status_code in (401, 403, 422), (method, path)


def _depends_on(dependant, target) -> bool:
    return dependant.call is target or any(_depends_on(d, target) for d in dependant.dependencies)


def test_every_route_reaches_the_schema_check_through_its_caller_check():
    for route in routes_monitor.router.routes:
        assert _depends_on(route.dependant, db.require_ready), route.path


def _fresh(code: str) -> str:
    """The stdout of a fresh interpreter (this process has imported the route module and the store already)."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    return out.stdout.strip().splitlines()[-1]


def test_importing_the_service_registers_the_grading_handler_and_loads_no_database_module():
    """A golden_grade job names its handler, and a restart settles a queued one by that name before any route has run,
    so the handler must exist once the app is imported. The store itself (it loads svc-deepagents' Skill lab, and with
    it that package's database module, which the app's import must not) is imported only when first used."""
    out = _fresh("import sys, trace_lab.app\n"
                 "from trace_lab import runner\n"
                 "h = runner.GRADE_HANDLERS['eval_cases']\n"
                 "print(h.__module__, 'src.database' in sys.modules, 'trace_lab.eval_cases' in sys.modules)")
    assert out == "trace_lab.routes_monitor False False"


def test_loading_the_store_puts_its_own_handler_in_place_of_the_stand_in():
    out = _fresh("import trace_lab.app, trace_lab.eval_cases as e\n"
                 "from trace_lab import runner\n"
                 "print(runner.GRADE_HANDLERS['eval_cases'] is e.grade_job)")
    assert out == "True"


def test_importing_the_routes_never_replaces_the_stores_own_handler():
    out = _fresh("import trace_lab.eval_cases as e\n"
                 "import trace_lab.routes_monitor\n"
                 "from trace_lab import runner\n"
                 "print(runner.GRADE_HANDLERS['eval_cases'] is e.grade_job)")
    assert out == "True"


async def test_the_stand_in_hands_the_job_to_the_stores_handler(monkeypatch):
    from trace_lab import eval_cases

    seen = []

    async def grade_job(job):
        seen.append(job)
        return "capped"

    monkeypatch.setattr(eval_cases, "grade_job", grade_job)
    assert await routes_monitor._grade_job("the-job") == "capped" and seen == ["the-job"]


def test_the_limits_the_routes_hold_are_the_stores():
    from trace_lab import eval_cases

    assert (routes_monitor.REPEAT_DEFAULT_N, routes_monitor.REPEAT_MIN_N, routes_monitor.REPEAT_MAX_N) == (
        eval_cases.REPEAT_DEFAULT_N, eval_cases.REPEAT_MIN_N, eval_cases.REPEAT_MAX_N)
    assert (routes_monitor.RELEASE_DEFAULT_K, routes_monitor.RELEASE_MAX_K) == (
        eval_cases.RELEASE_DEFAULT_K, eval_cases.RELEASE_MAX_K)


def test_the_module_imports_only_the_standard_library_fastapi_pydantic_and_trace_lab():
    tree = ast.parse(Path(routes_monitor.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            roots.add((node.module or "").split(".")[0])
    assert roots <= {"__future__", "datetime", "re", "math", "typing", "types", "fastapi", "pydantic", "src"}, roots
    assert "slowapi" not in roots and "httpx" not in roots and "sqlalchemy" not in roots     # no I/O of its own


def test_the_module_writes_no_sql_and_names_no_database():
    source = Path(routes_monitor.__file__).read_text(encoding="utf-8")
    for needle in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "plenum_cafm", "lab_engine", "main_engine"):
        assert needle not in source, needle
    assert not re.search(r"\btext\(", source)                                     # no raw SQL text of its own
