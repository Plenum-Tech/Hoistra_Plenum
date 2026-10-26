"""The golden routes: a reviewer of the company first, then readiness; uploads read whole before anything is saved (a
dry run saves nothing); set routes inside one company; the runner called with the page's bearer and the run checked
as this company's first; the expected outcome closed until revealed; grader validation pooled and reported per
company. Stores, views and the runner are faked: no database, no network, no model."""
from __future__ import annotations

import hashlib
import inspect
import uuid

import fastapi.params
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import db, golden, golden_store, golden_views, grader, grader_validation, routes_conversations, runner
from trace_lab import routes_golden as routes

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
RUN = "33333333-3333-4333-8333-333333333333"
B = "/api/trace-lab"
CSV = b"ID,Question,Expected answer\n1,How many floors does the tower have?,22\n2,What is the postcode?,EC2M 9ZZ\n"


def principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email=f"{role}@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


class FakeEngine:
    def __init__(self):
        self.conns = []

    def begin(self):
        engine = self

        class _Ctx:
            async def __aenter__(self):
                conn = object()
                engine.conns.append(conn)
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def client(who, *, ready=True):
    async def fake_ready():
        if not ready:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes.router, prefix=B)
    app.dependency_overrides[db.require_ready] = fake_ready
    if who is not None:
        app.dependency_overrides[current_principal] = lambda: who
    return TestClient(app)


def recorder(monkeypatch, module, name, value):
    calls = []

    async def fn(*a, **kw):
        calls.append((a, kw))
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(module, name, fn)
    return calls


def own_run(monkeypatch, *, missing=False):
    async def require_run(org, run_id):
        if missing or org != ORG:
            raise golden_store.NotFound(run_id)
        return {"id": run_id, "organization_id": org}

    monkeypatch.setattr(golden_views, "require_run", require_run)


def test_the_names_these_routes_build_on():
    # Phase 2 (auth, db, routes_conversations) and Phase 3 lane B (runner) own these; if one is missing or shaped
    # differently, stop and report (never redefine it here).
    assert isinstance(routes_conversations.reviewer, object) and callable(routes_conversations.reviewer)
    assert callable(db.require_ready) and callable(db.http_error) and callable(db.lab_engine)
    shapes = {"estimate": ("org", "principal", "body"), "start_run": ("org", "principal", "authorization", "body"),
              "step": ("run_id", "principal", "authorization"), "pause": ("run_id", "principal", "reason"),
              "resume": ("run_id", "principal", "usd_cap"), "cancel": ("run_id", "principal"),
              "regrade": ("run_id", "principal", "only")}
    for name, params in shapes.items():
        fn = getattr(runner, name)
        assert inspect.iscoroutinefunction(fn), name
        assert all(p in inspect.signature(fn).parameters for p in params), (name, inspect.signature(fn))
    assert runner.HARD_MAX_K == 20


def test_every_route_is_behind_the_reviewer_or_reader_check():
    seen = {}
    for r in routes.router.routes:
        deps = [d.call for d in r.dependant.dependencies]
        assert routes_conversations.reviewer in deps or routes.reader in deps, r.path
        seen.setdefault(r.path, set()).update(r.methods)
    assert seen == {
        "/golden-sets": {"GET", "POST"}, "/golden-sets/template.csv": {"GET"},
        "/golden-sets/templates/compliance.csv": {"GET"}, "/golden-sets/{set_id}/cases": {"GET"},
        "/golden-sets/{set_id}/activate": {"POST"}, "/golden-sets/{set_id}/retire": {"POST"},
        "/golden-runs/estimate": {"POST"}, "/golden-runs": {"GET", "POST"}, "/golden-runs/compare": {"GET"},
        "/golden-runs/{run_id}": {"GET"}, "/golden-runs/{run_id}/results/{result_id}": {"GET"},
        "/golden-runs/{run_id}/step": {"POST"}, "/golden-runs/{run_id}/pause": {"POST"},
        "/golden-runs/{run_id}/resume": {"POST"}, "/golden-runs/{run_id}/cancel": {"POST"},
        "/golden-runs/{run_id}/regrade": {"POST"}, "/golden/turns/{turn_id}": {"GET"},
        "/golden/turns/{turn_id}/reveal": {"POST"}, "/golden/grader": {"GET"}}


def test_the_router_is_registered_in_the_trace_lab_app():
    from trace_lab import app as app_module

    assert routes.router in app_module.ROUTERS
    c = TestClient(app_module.app)              # no "with": the lifespan (engines, schema check) never runs
    for path in ("/golden-sets", "/golden-runs", "/golden/grader", f"/golden-runs/{RUN}"):
        assert c.get(B + path).status_code == 401, path      # served: no bearer


@pytest.mark.parametrize("who,org_param,status,reason", [
    (principal("user"), None, 403, "reviewers_only"), (principal("superadmin", None), None, 400, "name_a_company"),
    (principal("admin"), OTHER, 403, "bad_company")])
def test_who_may_call(monkeypatch, who, org_param, status, reason):
    recorder(monkeypatch, golden_store, "list_sets", {"active": None, "versions": [], "regression": None})
    c = client(who)
    params = {"organization_id": org_param} if org_param else None
    for method, path in (("get", "/golden-sets"), ("get", "/golden-sets/template.csv"), ("get", "/golden-runs"),
                         ("post", f"/golden-runs/{RUN}/step"), ("get", "/golden/turns/turn-1")):
        r = getattr(c, method)(B + path, params=params)
        assert (r.status_code, r.json()["detail"]["reason"]) == (status, reason), path
    assert client(None).get(B + "/golden-sets").status_code == 401


def test_503_while_trace_lab_is_not_ready_but_only_to_reviewers():
    assert client(principal(), ready=False).get(B + "/golden-sets").status_code == 503
    assert client(principal("user"), ready=False).get(B + "/golden-sets").status_code == 403


def test_an_upload_is_read_whole_and_a_dry_run_saves_nothing(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(db, "lab_engine", lambda: engine)
    saved = recorder(monkeypatch, golden_store, "save_version",
                     {"set": {"id": "s1", "version": 4}, "diff": {"added": ["1", "2"]}})
    c = client(principal())
    files = {"file": ("bishopsgate.csv", CSV, "text/csv")}
    dry = c.post(B + "/golden-sets", params={"dry_run": "true"}, files=files)
    body = dry.json()
    assert dry.status_code == 200 and body["saved"] is False and body["cases"] == 2 and saved == []
    assert body["preview"][0]["question"].startswith("How many floors") and "expected_answer" not in body["preview"][0]
    assert body["flags"] == {"evaluation_defaulted": 2, "unverified": 2} and body["source_kind"] == "csv"
    r = c.post(B + "/golden-sets", files=files, data={"name": "Bishopsgate questions", "note": "Re-verified in October"})
    assert r.status_code == 201 and r.json()["saved"] is True and r.json()["set"] == {"id": "s1", "version": 4}
    (conn, org, result), kw = saved[0]
    assert conn is engine.conns[-1] and org == ORG and len(result.cases) == 2
    assert kw["filename"] == "bishopsgate.csv" and kw["source_sha256"] == hashlib.sha256(CSV).hexdigest()
    assert kw["name"] == "Bishopsgate questions" and kw["actor"].email == "admin@example.com"
    assert kw["note"] == "Re-verified in October"


def test_row_errors_and_unsafe_files_are_refused_with_a_sentence(monkeypatch):
    saved = recorder(monkeypatch, golden_store, "save_version", {})
    c = client(principal())
    bad = c.post(B + "/golden-sets", files={"file": ("s.csv", b"ID,Question,Expected answer\n1,,22\n", "text/csv")})
    assert bad.status_code == 422 and bad.json()["detail"]["reason"] == "bad_rows" and bad.json()["detail"]["errors"]
    pdf = c.post(B + "/golden-sets", files={"file": ("s.pdf", b"%PDF-1.7 not a sheet", "application/pdf")})
    assert pdf.status_code == 422 and pdf.json()["detail"]["reason"] == "bad_file" and pdf.json()["detail"]["error"]
    monkeypatch.setattr(golden, "MAX_BYTES", 10)
    big = c.post(B + "/golden-sets", files={"file": ("s.csv", CSV, "text/csv")})
    assert big.status_code == 413 and big.json()["detail"]["reason"] == "file_too_large"
    assert saved == []


def test_the_set_routes_stay_inside_the_company(monkeypatch):
    lists = recorder(monkeypatch, golden_store, "list_sets", {"active": None, "versions": [], "regression": None})
    cases = recorder(monkeypatch, golden_store, "get_cases", golden_store.NotFound("x"))
    recorder(monkeypatch, golden_store, "activate", golden_store.Conflict("The Regression cases set is kept by eval cases."))
    recorder(monkeypatch, golden_store, "retire", {"id": "s1", "status": "retired"})
    c = client(principal())
    assert c.get(B + "/golden-sets").json() == {"ok": True, "active": None, "versions": [], "regression": None}
    r = c.get(B + "/golden-sets/s1/cases", params={"q": "floors", "flag": "stale", "limit": 20, "offset": 40})
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
    assert cases[-1] == ((ORG, "s1"), {"q": "floors", "flag": "stale", "limit": 20, "offset": 40})
    assert c.get(B + "/golden-sets/s1/cases", params={"flag": "everything"}).status_code == 422
    act = c.post(B + "/golden-sets/s1/activate")
    assert act.status_code == 409 and act.json()["detail"]["reason"] == "regression_set"
    assert c.post(B + "/golden-sets/s1/retire").json() == {"ok": True, "set": {"id": "s1", "status": "retired"}}
    assert client(principal("superadmin", None)).get(B + "/golden-sets", params={"organization_id": OTHER}).status_code == 200
    assert lists[-1][0] == (OTHER,)


def test_the_templates_are_csv_downloads(monkeypatch):
    monkeypatch.setattr(golden, "compliance_bank_template_csv", lambda: "ID,Question\nCQB-A1,q\n")
    c = client(principal())
    t = c.get(B + "/golden-sets/template.csv")
    assert t.status_code == 200 and t.headers["content-type"].startswith("text/csv")
    assert t.text == golden.template_csv() and "attachment" in t.headers["content-disposition"]
    s = c.get(B + "/golden-sets/templates/compliance.csv")
    assert s.status_code == 200 and s.text.startswith("ID,Question") and "compliance" in s.headers["content-disposition"]


def test_estimate_and_start_pass_the_body_the_company_and_the_pages_bearer(monkeypatch):
    est = recorder(monkeypatch, runner, "estimate", {"cases": 29, "k": 3, "est_total_usd": 3.2})
    start = recorder(monkeypatch, runner, "start_run", {"run": {"id": RUN, "status": "running"}})
    c = client(principal())
    r = c.post(B + "/golden-runs/estimate", json={"k": 3, "context_default": "Bishopsgate Tower"})
    assert r.status_code == 200 and r.json() == {"ok": True, "cases": 29, "k": 3, "est_total_usd": 3.2}
    kw = est[-1][1]
    assert kw["org"] == ORG and kw["body"].k == 3 and kw["body"].purpose == "adhoc"
    assert kw["body"].context_default == "Bishopsgate Tower" and kw["principal"].email == "admin@example.com"
    r = c.post(B + "/golden-runs", json={"k": 2, "purpose": "baseline", "usd_cap": 5},
               headers={"Authorization": "Bearer page-token"})
    assert r.status_code == 201 and r.json() == {"ok": True, "run": {"id": RUN, "status": "running"}}
    kw = start[-1][1]
    assert (kw["org"], kw["authorization"], kw["body"].k, kw["body"].purpose, kw["body"].usd_cap) == \
        (ORG, "Bearer page-token", 2, "baseline", 5.0)
    for bad in ({"k": 21}, {"k": 0}, {"purpose": "check"}, {"usd_cap": 0}):
        assert c.post(B + "/golden-runs", json=bad).status_code == 422, bad
    assert c.post(B + "/golden-runs", json={"k": 20, "purpose": "repeat"}).status_code == 201
    recorder(monkeypatch, runner, "start_run", golden_store.NotFound("no active set"))
    assert c.post(B + "/golden-runs", json={}).status_code == 404


def test_run_reads_go_to_the_views_with_the_company(monkeypatch):
    summaries = recorder(monkeypatch, golden_views, "run_summaries", [{"run_id": RUN}])
    compare = recorder(monkeypatch, golden_views, "compare", {"ok": True, "flips": {}})
    detail = recorder(monkeypatch, golden_views, "run_detail", {"ok": True, "cases": []})
    result = recorder(monkeypatch, golden_views, "result_detail", {"ok": True, "messages": []})
    c = client(principal())
    assert c.get(B + "/golden-runs", params={"limit": 20}).json() == {"ok": True, "runs": [{"run_id": RUN}]}
    (org,), kw = summaries[-1]
    assert org == ORG and (kw["since"], kw["until"], kw["limit"], kw["reviewer"].email) == (None, None, 20, "admin@example.com")
    assert c.get(B + "/golden-runs", params={"limit": 101}).status_code == 422
    assert c.get(B + "/golden-runs/compare", params={"base": RUN, "head": "h"}).json() == {"ok": True, "flips": {}}
    assert compare[-1][0] == (ORG, RUN, "h")
    assert c.get(B + f"/golden-runs/{RUN}").json() == {"ok": True, "cases": []} and detail[-1][0] == (ORG, RUN)
    assert detail[-1][1]["reviewer"].email == "admin@example.com"
    assert c.get(B + f"/golden-runs/{RUN}/results/x1").status_code == 200 and result[-1][0] == (ORG, RUN, "x1")
    recorder(monkeypatch, golden_views, "run_detail", golden_store.NotFound(RUN))
    r = c.get(B + f"/golden-runs/{RUN}")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


def test_run_control_checks_the_run_is_this_companys_first(monkeypatch):
    step = recorder(monkeypatch, runner, "step", {"claimed": True, "item": {"case_key": "1", "rep": 1}})
    pause = recorder(monkeypatch, runner, "pause", {"run": {"status": "paused"}})
    resume = recorder(monkeypatch, runner, "resume", {"run": {"status": "running"}})
    cancel = recorder(monkeypatch, runner, "cancel", {"run": {"status": "cancelled"}})
    regrade = recorder(monkeypatch, runner, "regrade", {"results": 2})
    own_run(monkeypatch)
    c = client(principal())
    r = c.post(B + f"/golden-runs/{RUN}/step", headers={"Authorization": "Bearer fresh-token"})
    assert r.status_code == 202 and r.json() == {"ok": True, "claimed": True, "item": {"case_key": "1", "rep": 1}}
    assert step[-1][0] == (RUN,) and step[-1][1]["authorization"] == "Bearer fresh-token"
    assert c.post(B + f"/golden-runs/{RUN}/pause").json() == {"ok": True, "run": {"status": "paused"}}
    assert pause[-1][1]["reason"] == "paused"
    c.post(B + f"/golden-runs/{RUN}/pause", json={"reason": "page_closed"})
    assert pause[-1][1]["reason"] == "page_closed"
    c.post(B + f"/golden-runs/{RUN}/pause", json={"reason": "something else"})
    assert pause[-1][1]["reason"] == "paused"
    assert c.post(B + f"/golden-runs/{RUN}/resume", json={"usd_cap": 8}).json()["run"]["status"] == "running"
    assert resume[-1][1]["usd_cap"] == 8.0 and c.post(B + f"/golden-runs/{RUN}/resume", json={"usd_cap": 0}).status_code == 422
    assert c.post(B + f"/golden-runs/{RUN}/cancel").json()["run"]["status"] == "cancelled"
    r = c.post(B + f"/golden-runs/{RUN}/regrade", json={"only": "all"})
    assert r.status_code == 202 and regrade[-1][1]["only"] == "all"
    assert c.post(B + f"/golden-runs/{RUN}/regrade", json={"only": "some"}).status_code == 422
    recorder(monkeypatch, runner, "step", {"done": True, "run": {"id": RUN}})
    done = c.post(B + f"/golden-runs/{RUN}/step")
    assert done.status_code == 200 and done.json() == {"ok": True, "done": True, "run": {"id": RUN}}
    step = recorder(monkeypatch, runner, "step", {"claimed": False, "reason": "in_flight"})
    calls = (len(step), len(pause), len(resume), len(cancel), len(regrade))
    sa = client(principal("superadmin", None))
    for path in ("step", "pause", "cancel"):                                 # another company's run: never reaches it
        r = sa.post(B + f"/golden-runs/{RUN}/{path}", params={"organization_id": OTHER})
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"
    assert (len(step), len(pause), len(resume), len(cancel), len(regrade)) == calls


def test_the_expected_outcome_and_reveal(monkeypatch):
    seen = recorder(monkeypatch, golden_views, "expected_outcome", {"ok": True, "available": True, "revealed": False})
    reveal = recorder(monkeypatch, golden_views, "reveal", {"ok": True, "available": True, "revealed": True})
    c = client(principal())
    assert c.get(B + "/golden/turns/turn-1").json()["revealed"] is False and seen[-1][0] == (ORG, "turn-1")
    r = c.post(B + "/golden/turns/turn-1/reveal")
    assert r.status_code == 200 and r.json()["revealed"] is True and reveal[-1][0] == (ORG, "turn-1")
    recorder(monkeypatch, golden_views, "reveal", {"ok": True, "available": False, "reason": "not_a_golden_turn"})
    r = c.post(B + "/golden/turns/turn-live/reveal")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_a_golden_turn"


def test_grader_validation_is_pooled_and_each_company_sees_its_own_row(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    asked = []

    async def validation(*, grader_version, evaluation="judgment"):
        asked.append((grader_version, evaluation))
        return {"pooled": {"tpr": 0.95, "tnr": 0.9, "n_pass": 40, "n_fail": 30}, "passes": True, "reason": "validated",
                "by_company": [{"organization_id": ORG, "n_pass": 25}, {"organization_id": OTHER, "n_pass": 15}]}

    monkeypatch.setattr(grader_validation, "validation", validation)
    mine = client(principal()).get(B + "/golden/grader").json()
    assert mine["current"] == {"grader_version": grader.grader_version(), "model": "claude-sonnet-5"}
    assert [c["organization_id"] for c in mine["validation"]["by_company"]] == [ORG]
    assert mine["validation"]["pooled"]["n_pass"] == 40 and mine["thresholds"] == grader_validation.thresholds()
    platform = client(principal("superadmin", None)).get(B + "/golden/grader", params={"evaluation": "objective"}).json()
    assert [c["organization_id"] for c in platform["validation"]["by_company"]] == [ORG, OTHER]
    assert asked[-1] == (grader.grader_version(), "objective")
    acting = client(principal("superadmin", None)).get(B + "/golden/grader", params={"organization_id": OTHER}).json()
    assert [c["organization_id"] for c in acting["validation"]["by_company"]] == [OTHER]
    r = client(principal("user")).get(B + "/golden/grader")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"
