"""The Watch routes (Phase 7): who may do what, the house error envelope, 503 while the schema is unverified, the
per-person limit on every route that spends model money, and that every route hands the store exactly what the caller
sent. The monitor and the eval-case store are faked: no database, no network, no model.

Who (auth.py's rule; the routes add no reason code). READ the monitoring batches, charts and trends: a company admin
(own company), or a superadmin acting for a company, or - acting for none - the platform view. START, RESUME, PREVIEW and
open a review batch from monitoring: the platform team only, which is any superadmin (Phase 8, decision A). A superadmin
acting for a company does it FOR THAT COMPANY (the batch, its verdicts and its review batches carry the company); acting
for none, the batch is platform-wide, as before. A company admin never starts one (403 platform_only). EVAL CASES,
repeats, eval runs and release checks belong to one company: a company admin, or a superadmin acting for it.
"""
from __future__ import annotations

import inspect
import uuid
from urllib.parse import parse_qsl

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import db, eval_cases, monitor, review_errors, routes_monitor

B = "/api/trace-lab"
ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
MODE = str(uuid.uuid4())
BATCH = str(uuid.uuid4())
CASE = str(uuid.uuid4())
RUN = str(uuid.uuid4())

CASE_BODY = {"source": {"type": "live_turn", "turn_id": "turn-1"}, "mode_id": MODE, "expected_answer": "Four jobs."}
REPORT = {"golden_run_id": RUN, "run_key": "run-20261015T101500Z-0000000a", "decision": "pass",
          "reason": "all 1 regression cases passed 5 of 5 runs", "k": 5, "regression": [], "capability": [],
          "runs_without_verdict": [], "changes_since_last": None, "fingerprint": {}, "graders": {}}

#: Every route: (method, path, body, who may call it, the status it answers). "team" is the platform team only (any
#: superadmin, acting for a company or not), "reader" a company admin, a superadmin acting for a company, or the platform
#: view, "company" one company's own.
ROUTES = [
    ("GET", "/monitor/plan", None, "team", 200),
    ("POST", "/monitor/batches", {}, "team", 202),
    ("POST", f"/monitor/batches/{BATCH}/resume", {}, "team", 202),
    ("GET", "/monitor/batches", None, "reader", 200),
    ("GET", f"/monitor/batches/{BATCH}", None, "reader", 200),
    ("GET", f"/monitor/chart?mode_id={MODE}", None, "reader", 200),
    ("GET", "/monitor/code-checks", None, "reader", 200),
    ("GET", "/monitor/automation", None, "reader", 200),
    ("POST", f"/monitor/batches/{BATCH}/review-batch", {"mode_id": MODE}, "team", 201),
    ("GET", "/eval-cases", None, "company", 200),
    ("POST", "/eval-cases", CASE_BODY, "company", 201),
    ("GET", f"/eval-cases/{CASE}", None, "company", 200),
    ("PATCH", f"/eval-cases/{CASE}", {"base": 1, "expected_answer": "Five jobs."}, "company", 200),
    ("POST", f"/eval-cases/{CASE}/confirm", {"base": 1}, "company", 200),
    ("POST", f"/eval-cases/{CASE}/retire", {"base": 1, "reason": "Not a failure."}, "company", 200),
    ("POST", f"/eval-cases/{CASE}/baseline", {}, "company", 202),
    ("POST", f"/eval-cases/{CASE}/repeat", {}, "company", 202),
    ("POST", "/turns/turn-1/repeat", {"mode_ids": [MODE]}, "company", 202),
    ("POST", f"/eval-runs/{RUN}/grade", {}, "company", 202),
    ("GET", f"/eval-runs/{RUN}", None, "company", 200),
    ("POST", "/release-checks", {}, "company", 202),
    ("GET", "/release-checks", None, "company", 200),
    ("GET", f"/release-checks/{RUN}", None, "company", 200),
]
SPENDING = [r for r in ROUTES if r[0] == "POST" and (r[1].endswith(("/resume", "/baseline", "/repeat", "/grade"))
                                                      or r[1] in ("/monitor/batches", "/release-checks"))]


def _p(role: str, org: str | None = ORG) -> Principal:
    return Principal(user_id=uuid.uuid4(), email=f"{role}@acme.test", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def _client(principal: Principal, *, ready: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(routes_monitor.router, prefix=B)
    app.dependency_overrides[current_principal] = lambda: principal

    async def _ready() -> None:
        return None

    async def _down() -> None:
        raise HTTPException(status_code=503, headers={"Retry-After": "30"},
                            detail={"reason": "trace_lab_unavailable", "available": False, "status": "behind",
                                    "error": "Trace Lab is not available right now. The chat is not affected."})

    app.dependency_overrides[db.require_ready] = _ready if ready else _down
    return TestClient(app)


def _who(kind: str) -> Principal:
    """A person who may call a route of this kind."""
    return _p("admin") if kind == "company" else _p("superadmin", None)


def _call(c: TestClient, method: str, path: str, body, params=None, **kw):
    """One request. A request's own params replace the URL's query, so the two are merged here."""
    route, _, fixed = path.partition("?")
    merged = {**dict(parse_qsl(fixed)), **(params or {})}
    return c.request(method, B + route, json=body, params=merged or None, **kw)


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """The monitor and the eval-case store, replaced: every call is recorded as (name, args, kwargs)."""
    seen: dict = {"calls": []}

    def record(module, name, result):
        real = inspect.signature(getattr(module, name))                   # the route must call the real store as it is

        async def fake(*args, **kwargs):
            real.bind(*args, **kwargs)
            seen["calls"].append((name, args, kwargs))
            seen[name] = kwargs
            return result

        monkeypatch.setattr(module, name, fake)

    record(monitor, "plan_preview", {"period_label": "2026-W41", "n_window": 10, "usd_estimate": 0.5})
    record(monitor, "start_batch", {"batch": {"id": BATCH}, "job": {"id": "job-1", "status": "queued"}})
    record(monitor, "resume_batch", {"batch": {"id": BATCH, "status": "queued"}})
    record(monitor, "list_batches", {"batches": [], "due": {"period_label": "2026-W41", "is_due": True}})
    record(monitor, "batch_detail", {"batch": {"id": BATCH}, "flagged": []})
    record(monitor, "chart", {"mode": {"id": MODE}, "points": []})
    record(monitor, "code_check_trend", {"checks": []})
    record(monitor, "automation_trend", {"weeks": [], "definitions": {}})
    record(monitor, "open_review_batches", ["rb-1"])
    record(eval_cases, "list_cases", [{"id": CASE}])
    record(eval_cases, "create_case", {"id": CASE, "status": "proposed"})
    record(eval_cases, "get_case", {"case": {"id": CASE}, "runs": [], "twin": None})
    record(eval_cases, "edit_case", {"id": CASE, "version": 2})
    record(eval_cases, "confirm_case", {"id": CASE, "status": "draft"})
    record(eval_cases, "retire_case", {"id": CASE, "status": "retired"})
    record(eval_cases, "start_baseline", {"golden_run": {"id": RUN}})
    record(eval_cases, "start_repeat", {"golden_run": {"id": RUN}, "case": {"id": CASE}})
    record(eval_cases, "repeat_turn", {"golden_run": {"id": RUN}, "case": {"id": CASE}})
    record(eval_cases, "start_grading", {"job": {"id": "job-2", "status": "running", "usd_cap": 2.0}})
    record(eval_cases, "eval_run", {"run": {"id": RUN}, "case_runs": []})
    record(eval_cases, "start_release_check", {"golden_run": {"id": RUN}, "cases": {"regression": 1, "capability": 0}})
    record(eval_cases, "list_release_checks", [{"golden_run_id": RUN, "decision": "pass"}])
    record(eval_cases, "release_report", REPORT)
    routes_monitor.SPENDING.reset()
    yield seen
    routes_monitor.SPENDING.reset()


def _store_calls(stubs) -> list[str]:
    return [c[0] for c in stubs["calls"]]


# -- the routes at all -----------------------------------------------------------------------------------------------

def test_every_route_answers_for_the_person_it_is_for(stubs):
    assert len(ROUTES) == 23
    for method, path, body, kind, status in ROUTES:
        r = _call(_client(_who(kind)), method, path, body)
        assert r.status_code == status, (method, path, r.status_code, r.text)


# -- who may start (the platform team's: any superadmin) -------------------------------------------------------------

def test_the_platform_team_not_acting_starts_and_resumes_platform_wide_batches(stubs):
    me = _client(_p("superadmin", None))
    r = me.post(f"{B}/monitor/batches", json={})
    assert r.status_code == 202 and r.json() == {"ok": True, "batch": {"id": BATCH},
                                                 "job": {"id": "job-1", "status": "queued"}}
    assert stubs["start_batch"]["organization_id"] is None
    r = me.post(f"{B}/monitor/batches/{BATCH}/resume", json={})
    assert r.status_code == 202 and r.json()["batch"]["status"] == "queued"
    assert stubs["resume_batch"] == {"org": None}
    assert stubs["calls"][-1][1] == (BATCH,)
    me.get(f"{B}/monitor/plan")
    assert stubs["plan_preview"]["organization_id"] is None
    me.post(f"{B}/monitor/batches/{BATCH}/review-batch", json={"mode_id": MODE})
    assert stubs["open_review_batches"]["org"] is None                           # the platform view: no company limit


def test_a_superadmin_acting_for_a_company_plans_starts_resumes_and_opens_review_batches_for_that_company(stubs):
    me = _client(_p("superadmin", None))
    q = {"organization_id": ORG}
    r = me.get(f"{B}/monitor/plan", params=q)
    assert r.status_code == 200 and stubs["plan_preview"]["organization_id"] == ORG
    r = me.post(f"{B}/monitor/batches", json={}, params=q)
    assert r.status_code == 202 and r.json()["batch"] == {"id": BATCH}
    assert stubs["start_batch"]["organization_id"] == ORG
    r = me.post(f"{B}/monitor/batches/{BATCH}/resume", json={}, params=q)
    assert r.status_code == 202 and stubs["resume_batch"] == {"org": ORG} and stubs["calls"][-1][1] == (BATCH,)
    r = me.post(f"{B}/monitor/batches/{BATCH}/review-batch", json={"mode_id": MODE}, params=q)
    assert r.status_code == 201 and r.json() == {"ok": True, "review_batch_ids": ["rb-1"]}
    name, args, kwargs = stubs["calls"][-1]
    assert name == "open_review_batches" and args == (BATCH, MODE) and kwargs["org"] == ORG
    assert kwargs["actor_email"] == "superadmin@acme.test" and uuid.UUID(kwargs["actor_id"])


def test_a_superadmin_whose_own_company_is_set_is_still_the_platform_team_until_a_company_is_named(stubs):
    me = _client(_p("superadmin", ORG))
    assert me.post(f"{B}/monitor/batches", json={}).status_code == 202
    assert stubs["start_batch"]["organization_id"] is None                           # naming none is naming none
    assert me.post(f"{B}/monitor/batches", json={}, params={"organization_id": OTHER}).status_code == 202
    assert stubs["start_batch"]["organization_id"] == OTHER


def test_the_company_a_superadmin_names_must_be_a_company_id(stubs):
    me = _client(_p("superadmin", None))
    for method, path, body in (("GET", "/monitor/plan", None), ("POST", "/monitor/batches", {}),
                               ("POST", f"/monitor/batches/{BATCH}/resume", {}),
                               ("POST", f"/monitor/batches/{BATCH}/review-batch", {"mode_id": MODE})):
        r = _call(me, method, path, body, params={"organization_id": "nope"})
        assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_company", path
    assert stubs["calls"] == []


@pytest.mark.parametrize("method,path,body,kind,status", [r for r in ROUTES if r[3] == "team"])
def test_company_admins_cannot_start_preview_resume_or_open_review_batches(stubs, method, path, body, kind, status):
    for q in ({}, {"organization_id": ORG}):                                         # naming their own is naming none
        r = _call(_client(_p("admin")), method, path, body, params=q)
        assert r.status_code == 403 and r.json()["detail"] == {                          # R19: the same reason, the same envelope
            "reason": "platform_only", "error": "Only the platform team can do this, outside any one company."}, q
    r = _call(_client(_p("admin")), method, path, body, params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"        # another company is never theirs
    assert stubs["calls"] == []


@pytest.mark.parametrize("method,path,body,kind,status", [r for r in ROUTES if r[3] == "team"])
def test_every_team_route_serves_a_superadmin_acting_or_not_and_nobody_else(stubs, method, path, body, kind, status):
    for q in ({}, {"organization_id": ORG}):
        assert _call(_client(_p("superadmin", None)), method, path, body, params=q).status_code == status, q
    assert len(stubs["calls"]) == 2
    for who in (_p("admin"), _p("user"), _p("admin", None)):
        assert _call(_client(who), method, path, body).status_code == 403, who.role
    assert len(stubs["calls"]) == 2


@pytest.mark.parametrize("method,path,body,kind,status", ROUTES)
def test_plain_users_get_403_everywhere(stubs, method, path, body, kind, status):
    r = _call(_client(_p("user")), method, path, body)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"
    assert stubs["calls"] == []


# -- who may read (a company's own, or the platform's view) ----------------------------------------------------------

@pytest.mark.parametrize("path", ["/monitor/batches", f"/monitor/batches/{BATCH}", f"/monitor/chart?mode_id={MODE}",
                                  "/monitor/code-checks", "/monitor/automation"])
def test_a_company_admin_reads_their_own_company_and_the_platform_reads_everything(stubs, path):
    name = {"/monitor/batches": "list_batches", "/monitor/code-checks": "code_check_trend",
            "/monitor/automation": "automation_trend", "/monitor/chart": "chart"}.get(path.split("?")[0], "batch_detail")
    for who, q, org in ((_p("admin"), {}, ORG), (_p("superadmin", None), {}, None),
                        (_p("superadmin", None), {"organization_id": ORG}, ORG)):
        assert _call(_client(who), "GET", path, None, params=q).status_code == 200
        assert stubs[name]["org"] == org, (who.role, q)


def test_a_company_admin_cannot_read_another_company(stubs):
    for path in ("/monitor/batches", f"/monitor/batches/{BATCH}", "/monitor/code-checks", "/monitor/automation",
                 f"/monitor/chart?mode_id={MODE}", "/eval-cases", "/release-checks", f"/eval-runs/{RUN}"):
        r = _call(_client(_p("admin")), "GET", path, None, params={"organization_id": OTHER})
        assert r.status_code == 403 and r.json()["detail"] == {
            "reason": "bad_company", "error": "Only the platform team can open another company."}, path
    assert _client(_p("admin")).get(f"{B}/monitor/batches", params={"organization_id": ORG}).status_code == 200
    assert _client(_p("superadmin", None)).get(f"{B}/monitor/batches", params={"organization_id": "nope"}).status_code == 400
    assert len(stubs["calls"]) == 1                                                     # only the admin's own company


def test_an_admin_with_no_company_never_reads_the_platform_batches(stubs):
    nobody = _client(_p("admin", None))
    for method, path, body, kind, status in ROUTES:
        r = _call(nobody, method, path, body)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only", (method, path)
    assert stubs["calls"] == []


def test_the_window_and_paging_reach_the_monitor(stubs):
    me = _client(_p("admin"))
    me.get(f"{B}/monitor/batches")
    assert stubs["list_batches"] == {"org": ORG, "limit": 20, "before": None}
    me.get(f"{B}/monitor/batches", params={"limit": 5, "before": "2026-10-01T00:00:00Z"})
    assert stubs["list_batches"]["limit"] == 5 and stubs["list_batches"]["before"].year == 2026
    for bad in ({"limit": 0}, {"limit": 101}, {"before": "yesterday"}):
        assert me.get(f"{B}/monitor/batches", params=bad).status_code == 422
    me.get(f"{B}/monitor/batches/{BATCH}")
    assert stubs["batch_detail"] == {"org": ORG, "mode_id": None, "offset": 0}
    me.get(f"{B}/monitor/batches/{BATCH}", params={"mode_id": MODE, "offset": 50})
    assert stubs["batch_detail"] == {"org": ORG, "mode_id": MODE, "offset": 50}
    assert me.get(f"{B}/monitor/batches/{BATCH}", params={"offset": -1}).status_code == 422
    assert me.get(f"{B}/monitor/batches/{BATCH}", params={"mode_id": "m" * 65}).status_code == 422
    me.get(f"{B}/monitor/chart", params={"mode_id": MODE})
    assert stubs["chart"] == {"org": ORG, "mode_id": MODE, "periods": 12}
    me.get(f"{B}/monitor/chart", params={"mode_id": MODE, "periods": 52})
    assert stubs["chart"]["periods"] == 52
    assert me.get(f"{B}/monitor/chart").status_code == 422                                # a chart is for one mode
    assert me.get(f"{B}/monitor/chart", params={"mode_id": MODE, "periods": 53}).status_code == 422
    me.get(f"{B}/monitor/code-checks")
    assert stubs["code_check_trend"] == {"org": ORG, "periods": 12}
    me.get(f"{B}/monitor/automation", params={"weeks": 4})
    assert stubs["automation_trend"] == {"org": ORG, "weeks": 4}
    assert me.get(f"{B}/monitor/automation", params={"weeks": 0}).status_code == 422


def test_reads_wrap_the_stores_answer_with_ok(stubs):
    me = _client(_p("admin"))
    assert me.get(f"{B}/monitor/batches").json() == {
        "ok": True, "batches": [], "due": {"period_label": "2026-W41", "is_due": True}}
    assert me.get(f"{B}/monitor/code-checks").json() == {"ok": True, "checks": []}
    assert me.get(f"{B}/monitor/automation").json() == {"ok": True, "weeks": [], "definitions": {}}
    assert me.get(f"{B}/monitor/chart", params={"mode_id": MODE}).json() == {"ok": True, "mode": {"id": MODE}, "points": []}
    assert me.get(f"{B}/monitor/batches/{BATCH}").json() == {"ok": True, "batch": {"id": BATCH}, "flagged": []}


# -- the plan preview and the start ----------------------------------------------------------------------------------

def test_the_plan_preview_reads_the_window_the_rate_and_the_groups(stubs):
    me = _client(_p("superadmin", None))
    r = me.get(f"{B}/monitor/plan")
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["n_window"] == 10
    assert stubs["plan_preview"] == {"organization_id": None, "since": None, "until": None, "sample_rate": monitor.DEFAULT_RATE,
                                     "seed": monitor.DEFAULT_SEED, "risk_groups": None, "mode_ids": None}
    me.get(f"{B}/monitor/plan", params={"since": "2026-10-05T00:00:00Z", "until": "2026-10-12T00:00:00Z", "rate": 0.5,
                                        "seed": 3, "risk_groups": "thumbs_down, failed,", "mode_ids": f"{MODE},"})
    got = stubs["plan_preview"]
    assert (got["since"].day, got["until"].day, got["sample_rate"], got["seed"]) == (5, 12, 0.5, 3)
    assert got["risk_groups"] == ["thumbs_down", "failed"] and got["mode_ids"] == [MODE]
    me.get(f"{B}/monitor/plan", params={"risk_groups": ",", "mode_ids": " "})           # nothing named is not "none"
    assert stubs["plan_preview"]["risk_groups"] is None and stubs["plan_preview"]["mode_ids"] is None
    for bad in ({"rate": 0}, {"rate": 1.5}, {"seed": -1}, {"seed": 2**31}, {"since": "soon"}, {"risk_groups": "x" * 201}):
        assert me.get(f"{B}/monitor/plan", params=bad).status_code == 422, bad


def test_a_batch_start_hands_the_monitor_exactly_what_was_sent(stubs):
    me = _client(_p("superadmin", None))
    me.post(f"{B}/monitor/batches", json={})
    got = stubs["start_batch"]
    assert got["organization_id"] is None and got["since"] is None and got["until"] is None
    assert (got["sample_rate"], got["seed"]) == (monitor.DEFAULT_RATE, monitor.DEFAULT_SEED)
    assert got["risk_groups"] is None and got["mode_ids"] is None and got["usd_cap"] is None
    assert got["actor_email"] == "superadmin@acme.test" and uuid.UUID(got["actor_id"])
    me.post(f"{B}/monitor/batches", json={"since": "2026-10-05T00:00:00Z", "until": "2026-10-12T00:00:00Z",
                                           "sample_rate": 0.5, "seed": 9, "risk_groups": ["failed"], "mode_ids": [MODE],
                                           "usd_cap": 3})
    got = stubs["start_batch"]
    assert (got["sample_rate"], got["seed"], got["risk_groups"], got["mode_ids"], got["usd_cap"]) == (
        0.5, 9, ["failed"], [MODE], 3)
    assert (got["since"].day, got["until"].day) == (5, 12)


@pytest.mark.parametrize("body", [{"sample_rate": 0}, {"sample_rate": 1.01}, {"seed": -1}, {"seed": 2**31},
                                  {"usd_cap": 0}, {"usd_cap": 101}, {"risk_groups": ["a"] * 11},
                                  {"mode_ids": ["m"] * 51}, {"mode_ids": ["m" * 65]}, {"since": "soon"},
                                  {"mode_ids": "all"}])
def test_a_batch_start_refuses_a_body_it_cannot_read(stubs, body):
    assert _client(_p("superadmin", None)).post(f"{B}/monitor/batches", json=body).status_code == 422
    assert stubs["calls"] == []


def test_a_review_batch_is_opened_for_one_mode_by_the_platform_team(stubs):
    r = _client(_p("superadmin", None)).post(f"{B}/monitor/batches/{BATCH}/review-batch", json={"mode_id": MODE})
    assert r.status_code == 201 and r.json() == {"ok": True, "review_batch_ids": ["rb-1"]}
    name, args, kwargs = stubs["calls"][-1]
    assert name == "open_review_batches" and args == (BATCH, MODE)
    assert kwargs["actor_email"] == "superadmin@acme.test" and uuid.UUID(kwargs["actor_id"])
    for bad in ({}, {"mode_id": ""}, {"mode_id": "m" * 37}):
        assert _client(_p("superadmin", None)).post(f"{B}/monitor/batches/{BATCH}/review-batch", json=bad).status_code == 422


# -- the house envelope and readiness --------------------------------------------------------------------------------

def test_watch_errors_use_the_house_envelope(stubs, monkeypatch):
    async def busy(**kw):
        raise monitor.WatchError(409, "over_cap", "This batch is estimated above its cap.", usd_estimate=9.0, usd_cap=5.0)

    monkeypatch.setattr(monitor, "start_batch", busy)
    r = _client(_p("superadmin", None)).post(f"{B}/monitor/batches", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "over_cap", "error": "This batch is estimated above its cap.",
                                  "usd_estimate": 9.0, "usd_cap": 5.0}


@pytest.mark.parametrize("method,path,body,kind,status", ROUTES)
def test_every_route_turns_a_watch_error_into_the_envelope(stubs, monkeypatch, method, path, body, kind, status):
    async def refuse(*a, **kw):
        raise monitor.WatchError(404, "not_found", "That was not found.", hint="x")

    for module, name in [(monitor, n) for n in ("plan_preview", "start_batch", "resume_batch", "list_batches",
                                                "batch_detail", "chart", "code_check_trend", "automation_trend",
                                                "open_review_batches")] + [
            (eval_cases, n) for n in ("list_cases", "create_case", "get_case", "edit_case", "confirm_case", "retire_case",
                                      "start_baseline", "start_repeat", "repeat_turn", "start_grading", "eval_run",
                                      "start_release_check", "list_release_checks", "release_report")]:
        monkeypatch.setattr(module, name, refuse)
    r = _call(_client(_who(kind)), method, path, body)
    assert r.status_code == 404 and r.json()["detail"] == {"reason": "not_found", "error": "That was not found.",
                                                          "hint": "x"}


@pytest.mark.parametrize("method,path,body,kind,status", ROUTES)
def test_every_route_keeps_a_review_errors_own_status_and_sentence(stubs, monkeypatch, method, path, body, kind, status):
    async def refuse(*a, **kw):
        raise review_errors.Invalid("window_outside_retention")

    for module, name in [(monitor, n) for n in ("plan_preview", "start_batch", "resume_batch", "list_batches",
                                                "batch_detail", "chart", "code_check_trend", "automation_trend",
                                                "open_review_batches")] + [
            (eval_cases, n) for n in ("list_cases", "create_case", "get_case", "edit_case", "confirm_case", "retire_case",
                                      "start_baseline", "start_repeat", "repeat_turn", "start_grading", "eval_run",
                                      "start_release_check", "list_release_checks", "release_report")]:
        monkeypatch.setattr(module, name, refuse)
    r = _call(_client(_who(kind)), method, path, body)
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "window_outside_retention"
    assert r.json()["detail"]["error"] == review_errors.REVIEW_ERRORS["window_outside_retention"]


def test_a_refusal_from_the_golden_runner_passes_through_untouched(stubs, monkeypatch):
    async def refuse(*a, **kw):
        raise db.http_error(409, "run_open", "A run is already open.", run_id="r1")

    monkeypatch.setattr(eval_cases, "start_release_check", refuse)
    r = _client(_p("admin")).post(f"{B}/release-checks", json={})
    assert r.status_code == 409 and r.json()["detail"] == {"reason": "run_open", "error": "A run is already open.",
                                                          "run_id": "r1"}


@pytest.mark.parametrize("method,path,body,kind,status", ROUTES)
def test_every_route_answers_503_while_the_schema_is_unverified(stubs, method, path, body, kind, status):
    r = _call(_client(_who(kind), ready=False), method, path, body)
    assert r.status_code == 503 and r.json()["detail"]["available"] is False
    assert r.headers.get("retry-after") == "30"
    assert stubs["calls"] == []


def test_the_caller_is_checked_before_readiness(stubs):
    assert _client(_p("user"), ready=False).get(f"{B}/monitor/batches").status_code == 403
    r = _client(_p("admin"), ready=False).post(f"{B}/monitor/batches", json={})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"        # a stranger learns nothing
    r = _client(_p("superadmin", None), ready=False).post(f"{B}/release-checks", json={})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"


# -- eval cases belong to one company --------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,body,kind,status", [r for r in ROUTES if r[3] == "company"])
def test_eval_cases_need_a_company(stubs, method, path, body, kind, status):
    r = _call(_client(_p("superadmin", None)), method, path, body)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"       # the platform view has no cases
    assert stubs["calls"] == []
    r = _call(_client(_p("superadmin", None)), method, path, body, params={"organization_id": ORG})
    assert r.status_code == status and len(stubs["calls"]) == 1                            # acting for a company it has


def test_the_company_reaches_every_eval_call(stubs):
    me = _client(_p("admin"))
    me.get(f"{B}/eval-cases")
    assert stubs["calls"][-1][1] == (ORG,) and stubs["list_cases"] == {"status": None, "kind": None, "mode_id": None}
    me.get(f"{B}/eval-cases", params={"status": "active", "kind": "regression", "mode_id": MODE})
    assert stubs["list_cases"] == {"status": "active", "kind": "regression", "mode_id": MODE}
    for bad in ({"status": "done"}, {"kind": "other"}, {"mode_id": "m" * 37}):
        assert me.get(f"{B}/eval-cases", params=bad).status_code == 422
    r = me.get(f"{B}/eval-cases")
    assert r.json() == {"ok": True, "cases": [{"id": CASE}]}
    me.get(f"{B}/eval-cases/{CASE}")
    assert stubs["calls"][-1][1] == (ORG, CASE)
    assert me.get(f"{B}/eval-cases/{CASE}").json() == {"ok": True, "case": {"id": CASE}, "runs": [], "twin": None}
    me.get(f"{B}/eval-runs/{RUN}")
    assert stubs["calls"][-1][0] == "eval_run" and stubs["calls"][-1][1] == (ORG, RUN)
    assert me.get(f"{B}/eval-runs/{RUN}").json() == {"ok": True, "run": {"id": RUN}, "case_runs": []}
    me.get(f"{B}/release-checks")
    assert stubs["calls"][-1][1] == (ORG,) and stubs["list_release_checks"] == {"limit": 20}
    assert me.get(f"{B}/release-checks").json() == {"ok": True, "checks": [{"golden_run_id": RUN, "decision": "pass"}]}
    me.get(f"{B}/release-checks", params={"limit": 100})
    assert stubs["list_release_checks"] == {"limit": 100}
    assert me.get(f"{B}/release-checks", params={"limit": 101}).status_code == 422


def test_creating_a_case_hands_the_store_the_source_and_the_expectation(stubs):
    me = _client(_p("admin"))
    r = me.post(f"{B}/eval-cases", json=CASE_BODY)
    assert r.status_code == 201 and r.json() == {"ok": True, "case": {"id": CASE, "status": "proposed"}}
    assert stubs["calls"][-1][1] == (ORG,)
    got = stubs["create_case"]
    assert got["source"] == {"type": "live_turn", "turn_id": "turn-1", "golden_case_id": None}
    assert (got["mode_id"], got["expected_answer"], got["evaluation"]) == (MODE, "Four jobs.", "objective")
    assert got["input_override"] is None and got["initial_state"] is None and got["expected"] == {}
    assert got["expected_values"] is None and got["actor_email"] == "admin@acme.test" and uuid.UUID(got["actor_id"])
    me.post(f"{B}/eval-cases", json={"source": {"type": "golden_case", "golden_case_id": CASE},
                                      "input": {"question": "How many open jobs?", "followups": ["And closed?"]},
                                      "initial_state": {"a": 1}, "expected": {"checks": [{"check": "no_answer"}]},
                                      "evaluation": "judgment", "expected_values": "jobs = 4"})
    got = stubs["create_case"]
    assert got["source"]["type"] == "golden_case" and got["source"]["golden_case_id"] == CASE
    assert got["input_override"] == {"question": "How many open jobs?", "followups": ["And closed?"]}
    assert got["initial_state"] == {"a": 1} and got["expected"] == {"checks": [{"check": "no_answer"}]}
    assert (got["evaluation"], got["expected_values"], got["mode_id"]) == ("judgment", "jobs = 4", None)


@pytest.mark.parametrize("body", [{}, {"source": {"type": "memory"}}, {"source": {"type": "live_turn", "turn_id": "t" * 201}},
                                  {"source": {"type": "golden_case", "golden_case_id": "g" * 37}},
                                  dict(CASE_BODY, evaluation="vibes"), dict(CASE_BODY, expected_answer="x" * 6001),
                                  dict(CASE_BODY, expected_values="x" * 4001), dict(CASE_BODY, mode_id="m" * 37)])
def test_creating_a_case_refuses_a_body_it_cannot_read(stubs, body):
    assert _client(_p("admin")).post(f"{B}/eval-cases", json=body).status_code == 422
    assert stubs["calls"] == []


def test_editing_confirming_and_retiring_hand_the_store_the_version_they_saw(stubs):
    me = _client(_p("admin"))
    r = me.patch(f"{B}/eval-cases/{CASE}", json={"base": 3, "input": {"question": "Q?"}, "initial_state": {"a": 1},
                                                  "expected": {"assertions": ["x"]}, "expected_answer": "A.",
                                                  "evaluation": "judgment", "expected_values": "v = 1"})
    assert r.status_code == 200 and r.json() == {"ok": True, "case": {"id": CASE, "version": 2}}
    assert stubs["calls"][-1][1] == (ORG, CASE)
    got = stubs["edit_case"]
    assert got["base"] == 3 and got["input_override"] == {"question": "Q?"} and got["initial_state"] == {"a": 1}
    assert got["expected"] == {"assertions": ["x"]} and got["expected_answer"] == "A."
    assert (got["evaluation"], got["expected_values"], got["actor_email"]) == ("judgment", "v = 1", "admin@acme.test")
    me.patch(f"{B}/eval-cases/{CASE}", json={"base": 1})
    assert stubs["edit_case"]["input_override"] is None and stubs["edit_case"]["evaluation"] is None
    assert me.patch(f"{B}/eval-cases/{CASE}", json={}).status_code == 422                 # no version, no write
    assert me.patch(f"{B}/eval-cases/{CASE}", json={"base": 0}).status_code == 422
    assert me.patch(f"{B}/eval-cases/{CASE}", json={"base": 1, "evaluation": "vibes"}).status_code == 422

    r = me.post(f"{B}/eval-cases/{CASE}/confirm", json={"base": 2, "expected_answer": "Four.", "expected_values": "n = 4"})
    assert r.status_code == 200 and r.json() == {"ok": True, "case": {"id": CASE, "status": "draft"}}
    assert stubs["calls"][-1][1] == (ORG, CASE)
    assert stubs["confirm_case"]["base"] == 2 and stubs["confirm_case"]["expected_answer"] == "Four."
    assert stubs["confirm_case"]["evaluation"] == "objective" and stubs["confirm_case"]["expected_values"] == "n = 4"
    assert stubs["confirm_case"]["actor_email"] == "admin@acme.test"
    assert me.post(f"{B}/eval-cases/{CASE}/confirm", json={}).status_code == 422

    r = me.post(f"{B}/eval-cases/{CASE}/retire", json={"base": 4, "reason": "The failure was fixed."})
    assert r.status_code == 200 and r.json() == {"ok": True, "case": {"id": CASE, "status": "retired"}}
    assert stubs["calls"][-1][1] == (ORG, CASE)
    assert stubs["retire_case"] == {"base": 4, "reason": "The failure was fixed."}
    for bad in ({"base": 4}, {"base": 4, "reason": "no"}, {"base": 4, "reason": "x" * 501}, {"reason": "Not needed."}):
        assert me.post(f"{B}/eval-cases/{CASE}/retire", json=bad).status_code == 422


# -- running, grading and release checks (the caller's own bearer, never stored) -------------------------------------

def test_baseline_runs_as_the_caller_with_their_bearer(stubs):
    me = _client(_p("superadmin", None))
    r = me.post(f"{B}/eval-cases/{CASE}/baseline?organization_id={ORG}", json={"usd_cap": 3},
                headers={"Authorization": "Bearer abc"})
    assert r.status_code == 202 and r.json() == {"ok": True, "golden_run": {"id": RUN}}
    ctx = stubs["calls"][-1][1][0]
    assert isinstance(ctx, eval_cases.RunContext)
    assert (ctx.org, ctx.authorization) == (ORG, "Bearer abc") and ctx.principal.role == "superadmin"
    assert stubs["calls"][-1][1][1] == CASE and stubs["start_baseline"] == {"usd_cap": 3}
    me.post(f"{B}/eval-cases/{CASE}/baseline?organization_id={ORG}", json={})
    assert stubs["start_baseline"] == {"usd_cap": None} and stubs["calls"][-1][1][0].authorization is None


def test_repeating_an_eval_case_or_an_answer_hands_the_runner_the_same_context(stubs):
    me = _client(_p("admin"))
    r = me.post(f"{B}/eval-cases/{CASE}/repeat", json={}, headers={"Authorization": "Bearer t1"})
    assert r.status_code == 202 and r.json()["golden_run"]["id"] == RUN
    ctx, case_id = stubs["calls"][-1][1]
    assert (ctx.org, ctx.authorization, case_id) == (ORG, "Bearer t1", CASE)
    assert stubs["start_repeat"] == {"n": eval_cases.REPEAT_DEFAULT_N, "usd_cap": None}
    me.post(f"{B}/eval-cases/{CASE}/repeat", json={"n": 4, "usd_cap": 1.5})
    assert stubs["start_repeat"] == {"n": 4, "usd_cap": 1.5}

    r = me.post(f"{B}/turns/turn-1/repeat", json={"mode_ids": [MODE]}, headers={"Authorization": "Bearer t2"})
    assert r.status_code == 202 and r.json()["case"] == {"id": CASE}
    ctx, turn_id = stubs["calls"][-1][1]
    assert (ctx.org, ctx.authorization, turn_id) == (ORG, "Bearer t2", "turn-1")
    got = stubs["repeat_turn"]
    assert (got["n"], got["mode_ids"], got["expected_answer"], got["evaluation"], got["usd_cap"]) == (
        eval_cases.REPEAT_DEFAULT_N, [MODE], None, "objective", None)
    assert got["actor_email"] == "admin@acme.test" and uuid.UUID(got["actor_id"])
    me.post(f"{B}/turns/turn-1/repeat", json={"n": 3, "mode_ids": [MODE, "m2"], "expected_answer": "Four.",
                                               "evaluation": "judgment", "usd_cap": 2})
    got = stubs["repeat_turn"]
    assert (got["n"], got["mode_ids"], got["expected_answer"], got["evaluation"], got["usd_cap"]) == (
        3, [MODE, "m2"], "Four.", "judgment", 2)


@pytest.mark.parametrize("path,body", [
    (f"/eval-cases/{CASE}/repeat", {"n": eval_cases.REPEAT_MIN_N - 1}),
    (f"/eval-cases/{CASE}/repeat", {"n": eval_cases.REPEAT_MAX_N + 1}),
    (f"/eval-cases/{CASE}/repeat", {"usd_cap": 0}),
    (f"/eval-cases/{CASE}/baseline", {"usd_cap": 101}),
    ("/turns/turn-1/repeat", {}),
    ("/turns/turn-1/repeat", {"mode_ids": []}),
    ("/turns/turn-1/repeat", {"mode_ids": ["m"] * 11}),
    ("/turns/turn-1/repeat", {"mode_ids": [MODE], "evaluation": "vibes"}),
    ("/turns/turn-1/repeat", {"mode_ids": [MODE], "expected_answer": "x" * 6001}),
    (f"/eval-runs/{RUN}/grade", {"usd_cap": -1}),
    ("/release-checks", {"k": 0}),
    ("/release-checks", {"k": eval_cases.RELEASE_MAX_K + 1}),
    ("/release-checks", {"usd_cap": 0}),
    ("/release-checks", {"include_capability": "maybe"}),
])
def test_a_run_request_it_cannot_read_is_refused_before_it_spends(stubs, path, body):
    assert _client(_p("admin")).post(B + path, json=body).status_code == 422
    assert stubs["calls"] == []


def test_grading_a_run_names_the_company_the_caller_and_the_cap(stubs):
    r = _client(_p("admin")).post(f"{B}/eval-runs/{RUN}/grade", json={"usd_cap": 1.25})
    assert r.status_code == 202 and r.json() == {"ok": True, "job": {"id": "job-2", "status": "running", "usd_cap": 2.0}}
    assert stubs["calls"][-1][1] == (ORG, RUN)
    assert stubs["start_grading"]["usd_cap"] == 1.25 and stubs["start_grading"]["actor_email"] == "admin@acme.test"
    assert uuid.UUID(stubs["start_grading"]["actor_id"])
    _client(_p("admin")).post(f"{B}/eval-runs/{RUN}/grade", json={})
    assert stubs["start_grading"]["usd_cap"] is None


def test_a_release_check_starts_with_its_defaults_or_the_callers_choices(stubs):
    me = _client(_p("admin"))
    r = me.post(f"{B}/release-checks", json={}, headers={"Authorization": "Bearer r1"})
    assert r.status_code == 202 and r.json()["cases"] == {"regression": 1, "capability": 0}
    ctx = stubs["calls"][-1][1][0]
    assert (ctx.org, ctx.authorization) == (ORG, "Bearer r1")
    assert stubs["start_release_check"] == {"k": eval_cases.RELEASE_DEFAULT_K, "include_capability": True, "usd_cap": None}
    me.post(f"{B}/release-checks", json={"k": 3, "include_capability": False, "usd_cap": 4})
    assert stubs["start_release_check"] == {"k": 3, "include_capability": False, "usd_cap": 4}


# -- the release report as a download --------------------------------------------------------------------------------

def test_release_report_downloads_as_markdown_or_json(stubs):
    me = _client(_p("admin"))
    r = me.get(f"{B}/release-checks/{RUN}", params={"format": "md", "company_name": "Northbridge"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="release-check-run-20261015T101500Z-0000000a.md"'
    assert r.text.startswith("# Release check — Northbridge — run-20261015T101500Z-0000000a")
    assert r.headers["cache-control"] == "private, no-store"                              # a report is not kept by a proxy
    assert stubs["calls"][-1][1] == (ORG, RUN)
    j = me.get(f"{B}/release-checks/{RUN}")
    assert j.status_code == 200 and j.json()["decision"] == "pass" and j.json()["ok"] is True
    assert j.headers["content-disposition"] == 'attachment; filename="release-check-run-20261015T101500Z-0000000a.json"'
    assert j.headers["cache-control"] == "private, no-store"
    assert me.get(f"{B}/release-checks/{RUN}", params={"format": "pdf"}).status_code == 422


def test_the_markdown_heading_names_the_company_on_one_line(stubs):
    me = _client(_p("admin"))
    assert me.get(f"{B}/release-checks/{RUN}", params={"format": "md"}).text.startswith(
        "# Release check — your company — run-")
    assert me.get(f"{B}/release-checks/{RUN}", params={"format": "md", "company_name": "   "}).text.startswith(
        "# Release check — your company — run-")
    r = me.get(f"{B}/release-checks/{RUN}", params={"format": "md", "company_name": "Acme\n\n## Decision: PASS\r\n  Ltd"})
    assert r.text.splitlines()[0] == "# Release check — Acme ## Decision: PASS Ltd — run-20261015T101500Z-0000000a"
    assert me.get(f"{B}/release-checks/{RUN}", params={"format": "md", "company_name": "x" * 121}).status_code == 422


def test_a_report_filename_never_carries_what_a_header_cannot(stubs, monkeypatch):
    async def report(org, rid):
        return dict(REPORT, run_key='run "x"\r\nSet-Cookie: a=b/../é')

    monkeypatch.setattr(eval_cases, "release_report", report)
    r = _client(_p("admin")).get(f"{B}/release-checks/{RUN}")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="release-check-run-x') and disposition.endswith('.json"')
    assert "\r" not in disposition and "\n" not in disposition and disposition.count('"') == 2
    assert "/" not in disposition and "Set-Cookie" not in r.headers

    for odd, shown in (("run-7 ", "run-7"), ("..", "release-check-" + RUN), ("///", "release-check-" + RUN),
                       ("é", "release-check-" + RUN)):
        async def odd_key(org, rid, odd=odd):
            return dict(REPORT, run_key=odd)

        monkeypatch.setattr(eval_cases, "release_report", odd_key)
        want = shown if shown.startswith("release-check-") else f"release-check-{shown}"
        r = _client(_p("admin")).get(f"{B}/release-checks/{RUN}")
        assert r.headers["content-disposition"] == f'attachment; filename="{want}.json"', odd    # no stray dash or dot ends it

    async def keyless(org, rid):
        return dict(REPORT, run_key=None)

    monkeypatch.setattr(eval_cases, "release_report", keyless)
    r = _client(_p("admin")).get(f"{B}/release-checks/{RUN}")
    assert r.headers["content-disposition"] == f'attachment; filename="release-check-{RUN}.json"'


# -- the limit on spending -------------------------------------------------------------------------------------------

def test_the_limit_is_ten_a_minute():
    assert (routes_monitor.SPEND_LIMIT, routes_monitor.SPEND_WINDOW_S) == (10, 60.0)


def test_the_spending_routes_are_exactly_these():
    assert sorted((m, p.replace(BATCH, "{b}").replace(CASE, "{c}")) for m, p, *_ in SPENDING) == sorted([
        ("POST", "/monitor/batches"), ("POST", "/monitor/batches/{b}/resume"), ("POST", "/eval-cases/{c}/baseline"),
        ("POST", "/eval-cases/{c}/repeat"), ("POST", "/turns/turn-1/repeat"), ("POST", f"/eval-runs/{RUN}/grade"),
        ("POST", "/release-checks")])


@pytest.mark.parametrize("method,path,body,kind,status", SPENDING)
def test_every_spending_route_is_limited_per_person_and_per_route(stubs, method, path, body, kind, status):
    me = _client(_who(kind))
    for _ in range(routes_monitor.SPEND_LIMIT):
        assert _call(me, method, path, body).status_code == status
    r = _call(me, method, path, body)
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "rate_limited" and r.json()["detail"]["error"]
    assert int(r.headers["retry-after"]) == r.json()["detail"]["retry_after_s"] >= 1
    assert len(stubs["calls"]) == routes_monitor.SPEND_LIMIT                              # the 11th never spent a cent
    other = next(x for x in SPENDING if x[1] != path and x[3] == kind)
    assert _call(me, other[0], other[1], other[2]).status_code == other[4]                # each route has its own budget
    assert _call(_client(_who(kind)), method, path, body).status_code == status          # another person is not held up


def test_reads_and_the_other_writes_are_never_limited(stubs):
    me = _client(_p("admin"))
    for _ in range(routes_monitor.SPEND_LIMIT * 2):
        assert me.get(f"{B}/eval-cases").status_code == 200
        assert me.get(f"{B}/release-checks/{RUN}").status_code == 200
        assert me.post(f"{B}/eval-cases", json=CASE_BODY).status_code == 201
        assert me.post(f"{B}/eval-cases/{CASE}/confirm", json={"base": 1}).status_code == 200
    boss = _client(_p("superadmin", None))
    for _ in range(routes_monitor.SPEND_LIMIT * 2):
        assert boss.post(f"{B}/monitor/batches/{BATCH}/review-batch", json={"mode_id": MODE}).status_code == 201
        assert boss.get(f"{B}/monitor/plan").status_code == 200


def test_the_limit_is_a_sliding_minute(stubs, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(routes_monitor.SPENDING, "clock", lambda: now[0])
    me = _client(_p("admin"))
    for _ in range(routes_monitor.SPEND_LIMIT):
        assert me.post(f"{B}/release-checks", json={}).status_code == 202
        now[0] += 1.0
    now[0] = 1000.0 + routes_monitor.SPEND_WINDOW_S - 0.5
    r = me.post(f"{B}/release-checks", json={})
    assert r.status_code == 429 and r.json()["detail"]["retry_after_s"] == 1
    now[0] = 1000.0 + routes_monitor.SPEND_WINDOW_S
    assert me.post(f"{B}/release-checks", json={}).status_code == 202                    # exactly one came free
    assert me.post(f"{B}/release-checks", json={}).status_code == 429


def test_a_superadmins_budget_is_per_person_and_route_whichever_company_it_acts_for(stubs):
    me = _client(_p("superadmin", None))
    for i in range(routes_monitor.SPEND_LIMIT):
        q = {"organization_id": ORG if i % 2 else OTHER}
        assert me.post(f"{B}/monitor/batches", json={}, params=q).status_code == 202
    for q in ({}, {"organization_id": ORG}, {"organization_id": str(uuid.uuid4())}):      # naming another company is no new budget
        r = me.post(f"{B}/monitor/batches", json={}, params=q)
        assert r.status_code == 429 and r.json()["detail"]["reason"] == "rate_limited", q
    assert len(stubs["calls"]) == routes_monitor.SPEND_LIMIT


def test_a_stranger_cannot_use_up_anyones_budget(stubs):
    admin_calls = routes_monitor.SPEND_LIMIT + 3
    c = _client(_p("admin"))
    for _ in range(admin_calls):
        assert c.post(f"{B}/monitor/batches", json={}).status_code == 403                # an admin may not start monitoring
    for _ in range(admin_calls):
        assert _client(_p("user")).post(f"{B}/release-checks", json={}).status_code == 403
        assert _client(_p("superadmin", None)).post(f"{B}/release-checks", json={}).status_code == 400
    assert stubs["calls"] == []
    assert _client(_p("superadmin", None)).post(f"{B}/monitor/batches", json={}).status_code == 202
    assert _client(_p("admin")).post(f"{B}/release-checks", json={}).status_code == 202


def test_a_call_the_body_check_refuses_costs_no_budget(stubs):
    me = _client(_p("admin"))
    for _ in range(routes_monitor.SPEND_LIMIT + 5):
        assert me.post(f"{B}/release-checks", json={"k": 0}).status_code == 422
    assert me.post(f"{B}/release-checks", json={}).status_code == 202


def test_a_call_the_store_refuses_still_counts(stubs, monkeypatch):
    async def refuse(*a, **kw):
        raise monitor.WatchError(409, "batch_running", "A batch is running.")

    monkeypatch.setattr(monitor, "start_batch", refuse)
    me = _client(_p("superadmin", None))
    for _ in range(routes_monitor.SPEND_LIMIT):
        assert me.post(f"{B}/monitor/batches", json={}).status_code == 409                # work was attempted: it is counted
    assert me.post(f"{B}/monitor/batches", json={}).status_code == 429
