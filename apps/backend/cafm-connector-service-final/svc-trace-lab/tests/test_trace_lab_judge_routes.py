"""The judges routes (Phase 6): who may do what (the platform team owns judges, splits, runs and the failure report; a
company admin, or a superadmin acting for a company, reads judge summaries and runs the code checks on their own
company's answers; plain users get nothing), the house error envelope, 503 while the schema is unverified, the
per-person limit on the two routes that spend model money, and that every route hands the store exactly what the caller
sent. The store is faked: no database, no network, no model."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import checks, db, evidence, judges, routes_judges

B = "/api/trace-lab"
ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
MODE = str(uuid.uuid4())
JUDGE = str(uuid.uuid4())
RUN = str(uuid.uuid4())
ROW = {"id": JUDGE, "mode_id": MODE, "mode_name": "invented_codes", "version": 0, "judge_key": "invented_codes-v0",
       "kind": "llm", "status": "draft", "model": "claude-sonnet-5", "prompt_hash": "abc123def456", "change_note": "",
       "rev": 1, "split_id": str(uuid.uuid4()), "split_counts": {}, "split_min_labels": 30}
SECRET = {"prompt_hash", "model", "change_note", "stop_reason", "prompt_text", "prompt_parts", "rev"}

#: Every route, as (method, path, body, who may call it): "reader" for the two a company reads, "owner" for the rest.
ROUTES = [
    ("GET", "/checks", None, "reader"),
    ("GET", "/checks/turns/turn-1", None, "reader"),
    ("GET", "/judges", None, "reader"),
    ("GET", f"/modes/{MODE}/judge-readiness", None, "owner"),
    ("POST", f"/modes/{MODE}/splits", {}, "owner"),
    ("GET", f"/modes/{MODE}/splits/active", None, "owner"),
    ("POST", "/judges", {"mode_id": MODE, "kind": "code", "check_key": "no_answer"}, "owner"),
    ("GET", f"/judges/{JUDGE}", None, "owner"),
    ("POST", f"/judges/{JUDGE}/preview", {"turn_id": "turn-1"}, "owner"),
    ("GET", f"/judges/{JUDGE}/estimate", None, "owner"),
    ("POST", f"/judges/{JUDGE}/runs", {"split": "dev"}, "owner"),
    ("GET", f"/judge-runs/{RUN}", None, "owner"),
    ("PATCH", f"/judge-runs/{RUN}/disagreements/turn-1", {"triage": "judge_wrong", "base": 1}, "owner"),
    ("POST", f"/judges/{JUDGE}/freeze", {"stop_reason": "Stable on dev twice.", "base": 1}, "owner"),
    ("PATCH", f"/judges/{JUDGE}/decision", {"decision": "use", "base": 1}, "owner"),
    ("PATCH", f"/judges/{JUDGE}/threshold", {"threshold": 0.4, "base": 1}, "owner"),
    ("GET", "/failure-report", None, "owner"),
]
OWNER_ROUTES = [r for r in ROUTES if r[3] == "owner"]


def _p(role: str, org: str | None = ORG) -> Principal:
    return Principal(user_id=uuid.uuid4(), email=f"{role}@acme.test", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


def _client(principal: Principal, *, ready: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(routes_judges.router, prefix=B)
    app.dependency_overrides[current_principal] = lambda: principal

    async def _ready() -> None:
        return None

    async def _down() -> None:
        raise HTTPException(status_code=503, headers={"Retry-After": "30"},
                            detail={"reason": "trace_lab_unavailable", "available": False, "status": "behind",
                                    "error": "Trace Lab is not available right now. The chat is not affected."})

    app.dependency_overrides[db.require_ready] = _ready if ready else _down
    return TestClient(app)


@pytest.fixture(autouse=True)
def stubs(monkeypatch):
    """The judges store, replaced: every call is recorded as (name, args, kwargs) and answers a fixed value."""
    seen: dict = {"calls": []}

    def record(name, result):
        async def fake(*args, **kwargs):
            seen["calls"].append((name, args, kwargs))
            seen[name] = kwargs
            return result(*args, **kwargs) if callable(result) else result

        monkeypatch.setattr(judges, name, fake)

    def list_judges(principal, org, *, mode_id=None, full):
        return {"judges": [judges.judge_summary(ROW, in_use=False, full=full)], "modes": []}

    def register(principal, **kw):
        raise judges.JudgeError(409, "no_active_split", "Create the split for this failure mode first.", judge_id=JUDGE)

    def failure_report(principal, *, fmt="json"):
        return "# Failure report\n" if fmt == "md" else {"generated_at": "2026-10-15T12:00:00+00:00", "modes": []}

    def load_judge_turns(ids):
        return {i: checks.NormalizedTurn(turn_id=i, organization_id=OTHER, question="How many open jobs?",
                                         answer="There are 4 open jobs.") for i in ids}

    def turn_facts(ids):
        return {i: {"turn_id": i, "organization_id": OTHER} for i in ids}

    record("list_judges", list_judges)
    record("readiness", {"mode": {"id": MODE}, "labels": {}, "minimum": {}, "split": None, "judges": [],
                         "next_step": "label_more"})
    record("create_split", {"id": "split-1", "version": 1})
    record("get_active_split", {"id": "split-1", "version": 1})
    record("register", register)
    record("get_judge", {"judge": {"id": JUDGE}, "runs": []})
    record("preview", {"verdict": "Fail", "critique": "Invented a code.", "usd": 0.01})
    record("estimate", {"items": 12, "cached": 2, "to_call": 10, "usd_estimate": 0.2})
    record("start_run", {"run": {"id": RUN, "status": "queued"}, "job": {"id": "job-1", "status": "queued"}})
    record("get_run", {"id": RUN, "status": "done", "disagreements": []})
    record("triage", {"id": RUN, "untriaged": 0})
    record("freeze", {"id": JUDGE, "status": "frozen"})
    record("set_decision", {"id": JUDGE, "decision": "use"})
    record("set_threshold", {"id": JUDGE, "monitor_threshold": 0.4})
    record("failure_report", failure_report)
    record("load_judge_turns", load_judge_turns)
    record("turn_facts", turn_facts)
    routes_judges.SPENDING.reset()
    yield seen
    routes_judges.SPENDING.reset()


def _store_calls(stubs) -> list[str]:
    return [c[0] for c in stubs["calls"]]


# -- who may read ----------------------------------------------------------------------------------------------------

def test_company_admin_reads_summaries_without_prompts(stubs):
    r = _client(_p("superadmin", None)).get(f"{B}/judges")
    assert r.status_code == 200 and stubs["list_judges"]["full"] is True
    assert stubs["calls"][-1][1][1] is None
    assert r.json()["judges"][0]["prompt_hash"] == "abc123def456"
    for who, q in ((_p("admin"), {}), (_p("superadmin", None), {"organization_id": ORG})):
        r = _client(who).get(f"{B}/judges", params=q)
        assert r.status_code == 200 and stubs["list_judges"]["full"] is False and stubs["calls"][-1][1][1] == ORG
        assert not (SECRET & set(r.json()["judges"][0]))


def test_the_judges_table_can_be_narrowed_to_one_mode(stubs):
    _client(_p("superadmin", None)).get(f"{B}/judges", params={"mode_id": MODE})
    assert stubs["list_judges"]["mode_id"] == MODE
    assert _client(_p("superadmin", None)).get(f"{B}/judges", params={"mode_id": "x" * 65}).status_code == 422


def test_judge_writes_and_owner_pages_are_platform_only(stubs):
    for who, q in ((_p("admin"), {}), (_p("superadmin", None), {"organization_id": ORG})):
        c = _client(who)
        for method, path, body, _kind in OWNER_ROUTES:
            r = c.request(method, B + path, params=q, json=body)
            assert r.status_code == 403, (method, path, r.status_code)
            assert r.json()["detail"]["reason"] == "platform_only", (method, path)
    assert stubs["calls"] == []                                    # not one of them reached the store
    for method, path, body, _kind in OWNER_ROUTES:
        if method == "GET":
            r = _client(_p("superadmin", None)).request(method, B + path)
            assert r.status_code == 200, (method, path, r.text)


def test_plain_user_is_refused_everywhere(stubs):
    c = _client(_p("user"))
    for method, path, body, _kind in ROUTES:
        r = c.request(method, B + path, json=body)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only", (method, path)
    assert stubs["calls"] == []
    nobody = _client(_p("admin", None))                            # an admin with no company is not a reviewer either
    r = nobody.get(f"{B}/judges")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"


def test_admin_naming_another_company_is_refused(stubs):
    r = _client(_p("admin")).get(f"{B}/judges", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"] == {
        "reason": "bad_company", "error": "Only the platform team can open another company."}
    assert _client(_p("admin")).get(f"{B}/checks", params={"organization_id": OTHER}).status_code == 403
    assert _client(_p("superadmin", None)).get(f"{B}/judges", params={"organization_id": "nope"}).status_code == 400
    assert stubs["calls"] == []


def test_checks_list_names_every_check():
    r = _client(_p("admin")).get(f"{B}/checks")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert [x["evaluator_key"] for x in r.json()["checks"]] == [c.evaluator_key for c in checks.CHECKS.values()]
    first = r.json()["checks"][0]
    assert set(first) == {"key", "version", "evaluator_key", "label", "description", "scope"}


def test_turn_checks_stay_inside_the_company(stubs):
    assert _client(_p("admin")).get(f"{B}/checks/turns/turn-1").status_code == 404           # the turn is OTHER's
    assert "load_judge_turns" not in _store_calls(stubs)                                    # and was never opened for ORG
    assert _client(_p("superadmin", None)).get(f"{B}/checks/turns/turn-1", params={"organization_id": ORG}).status_code == 404
    r = _client(_p("admin", OTHER)).get(f"{B}/checks/turns/turn-1")
    assert r.status_code == 200 and r.json()["turn_id"] == "turn-1"
    assert [x["key"] for x in r.json()["results"]] == [k for k, c in checks.CHECKS.items() if c.scope == "live"]
    assert all(set(x) == {"key", "version", "status", "findings"} for x in r.json()["results"])
    calls = len(stubs["calls"])
    r = _client(_p("superadmin", None)).get(f"{B}/checks/turns/turn-1")                     # the platform sees any company's
    assert r.status_code == 200 and _store_calls(stubs)[calls:] == ["load_judge_turns"]


def test_a_turn_that_is_not_there_is_a_404(stubs, monkeypatch):
    async def none(ids):
        return {}

    monkeypatch.setattr(judges, "load_judge_turns", none)
    monkeypatch.setattr(judges, "turn_facts", none)
    for who in (_p("superadmin", None), _p("admin")):
        r = _client(who).get(f"{B}/checks/turns/turn-9")
        assert r.status_code == 404 and r.json()["detail"] == {"reason": "turn_not_found", "error": "Answer not found."}
    assert _client(_p("admin")).get(f"{B}/checks/turns/{'t' * (evidence.MAX_ID_LENGTH + 1)}").status_code == 422


def test_turn_checks_never_open_a_turn_whose_company_changed_between_the_two_reads(stubs, monkeypatch):
    async def facts(ids):
        return {i: {"turn_id": i, "organization_id": ORG} for i in ids}

    monkeypatch.setattr(judges, "turn_facts", facts)                       # the head says ORG, the loaded turn says OTHER
    assert _client(_p("admin")).get(f"{B}/checks/turns/turn-1").status_code == 404


# -- the house envelope and readiness --------------------------------------------------------------------------------

def test_judge_errors_use_the_house_envelope():
    r = _client(_p("superadmin", None)).post(f"{B}/judges", json={"mode_id": MODE, "kind": "code", "check_key": "no_answer"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "no_active_split", "error": "Create the split for this failure mode first.",
                                  "judge_id": JUDGE}


@pytest.mark.parametrize("method,path,body", [(m, p, b) for m, p, b, _ in OWNER_ROUTES if m != "GET"])
def test_every_owner_write_turns_a_judge_error_into_the_envelope(stubs, monkeypatch, method, path, body):
    async def refuse(*a, **kw):
        raise judges.JudgeError(409, "stale_base", "This judge changed in another tab; reload it.")

    for name in ("create_split", "register", "preview", "start_run", "triage", "freeze", "set_decision", "set_threshold"):
        monkeypatch.setattr(judges, name, refuse)
    r = _client(_p("superadmin", None)).request(method, B + path, json=body)
    assert r.status_code == 409 and r.json()["detail"] == {"reason": "stale_base",
                                                          "error": "This judge changed in another tab; reload it."}


@pytest.mark.parametrize("method,path", [(m, p) for m, p, _b, _k in OWNER_ROUTES if m == "GET"])
def test_every_owner_read_turns_a_judge_error_into_the_envelope(monkeypatch, method, path):
    async def missing(*a, **kw):
        raise judges.JudgeError(404, "judge_not_found", "No such judge.")

    for name in ("readiness", "get_active_split", "get_judge", "estimate", "get_run", "failure_report"):
        monkeypatch.setattr(judges, name, missing)
    r = _client(_p("superadmin", None)).request(method, B + path)
    assert r.status_code == 404 and r.json()["detail"] == {"reason": "judge_not_found", "error": "No such judge."}


def test_routes_answer_503_while_the_schema_is_unverified(stubs):
    c = _client(_p("superadmin", None), ready=False)
    for method, path, body, _kind in ROUTES:
        r = c.request(method, B + path, json=body)
        assert r.status_code == 503 and r.json()["detail"]["available"] is False, (method, path)
        assert r.headers.get("retry-after") == "30"
    assert stubs["calls"] == []


def test_the_caller_is_checked_before_readiness(stubs):
    r = _client(_p("user"), ready=False).get(f"{B}/judges")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"       # a stranger learns nothing
    r = _client(_p("admin"), ready=False).get(f"{B}/judges/{JUDGE}")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"


# -- the limit on spending -------------------------------------------------------------------------------------------

def test_the_two_spending_routes_are_limited_per_person_and_per_route(stubs):
    me = _client(_p("superadmin", None))
    for _ in range(routes_judges.SPEND_LIMIT):
        assert me.post(f"{B}/judges/{JUDGE}/preview", json={"turn_id": "turn-1"}).status_code == 200
    r = me.post(f"{B}/judges/{JUDGE}/preview", json={"turn_id": "turn-1"})
    assert r.status_code == 429 and r.json()["detail"]["reason"] == "rate_limited" and r.json()["detail"]["error"]
    assert int(r.headers["retry-after"]) == r.json()["detail"]["retry_after_s"] >= 1
    assert _store_calls(stubs).count("preview") == routes_judges.SPEND_LIMIT                 # the 11th never spent a cent
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202   # runs have their own budget
    assert me.get(f"{B}/judges/{JUDGE}/estimate").status_code == 200                       # reads are never limited
    # another superadmin (the same company, another person) is not held up by my budget
    assert _client(_p("superadmin", None)).post(f"{B}/judges/{JUDGE}/preview", json={"turn_id": "turn-1"}).status_code == 200


def test_runs_stop_at_the_same_limit(stubs):
    me = _client(_p("superadmin", None))
    for _ in range(routes_judges.SPEND_LIMIT):
        assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 429
    assert _store_calls(stubs).count("start_run") == routes_judges.SPEND_LIMIT


def test_the_limit_is_a_sliding_minute(stubs, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(routes_judges.SPENDING, "clock", lambda: now[0])
    me = _client(_p("superadmin", None))
    for _ in range(routes_judges.SPEND_LIMIT):
        assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202
        now[0] += 1.0                                   # one a second: the first leaves the minute after 60 s
    now[0] = 1000.0 + routes_judges.SPEND_WINDOW_S - 0.5
    r = me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"})
    assert r.status_code == 429 and r.json()["detail"]["retry_after_s"] == 1                 # 0.5 s left, shown as 1
    now[0] = 1000.0 + routes_judges.SPEND_WINDOW_S
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202   # exactly one came free
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 429


def test_a_refused_call_is_not_counted_and_does_not_lengthen_the_wait(stubs, monkeypatch):
    now = [50.0]
    monkeypatch.setattr(routes_judges.SPENDING, "clock", lambda: now[0])
    me = _client(_p("superadmin", None))
    for _ in range(routes_judges.SPEND_LIMIT):
        me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"})
    for _ in range(25):
        assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 429
    now[0] += routes_judges.SPEND_WINDOW_S + 0.01
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202


def test_a_stranger_cannot_use_up_the_owners_budget(stubs):
    for who, q in ((_p("user"), {}), (_p("admin"), {}), (_p("superadmin", None), {"organization_id": ORG})):
        c = _client(who)
        for _ in range(routes_judges.SPEND_LIMIT + 3):
            assert c.post(f"{B}/judges/{JUDGE}/runs", params=q, json={"split": "dev"}).status_code == 403
    assert stubs["calls"] == []
    assert _client(_p("superadmin", None)).post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202


def test_a_call_the_body_check_refuses_costs_no_budget(stubs):
    me = _client(_p("superadmin", None))
    for _ in range(routes_judges.SPEND_LIMIT + 5):
        assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "train"}).status_code == 422
    assert me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"}).status_code == 202


def test_the_limiter_window_is_bounded_and_forgets_idle_people():
    now = [0.0]
    w = routes_judges.RateWindow(2, 10.0, clock=lambda: now[0])
    assert w.hit("a") is None and w.hit("a") is None
    assert w.hit("a") == pytest.approx(10.0)
    for i in range(routes_judges.RateWindow.SWEEP_AT + 5):
        assert w.hit(f"person-{i}") is None
    now[0] = 100.0
    assert w.hit("late") is None
    assert len(w._hits) < 10                                  # everyone idle for a window was dropped


def test_forgetting_idle_people_never_forgets_someone_still_inside_the_window():
    now = [0.0]
    w = routes_judges.RateWindow(2, 10.0, clock=lambda: now[0])
    assert w.hit("busy") is None and w.hit("busy") is None
    for i in range(routes_judges.RateWindow.SWEEP_AT + 5):
        w.hit(f"person-{i}")
    now[0] = 4.0                                              # the sweep runs on this call; "busy" is 4 s into its minute
    assert w.hit("busy") == pytest.approx(6.0)
    assert "busy" in w._hits and all(k == "busy" or k.startswith("person-") for k in w._hits)


# -- what each route hands the store ---------------------------------------------------------------------------------

def test_each_route_hands_the_store_what_the_caller_sent(stubs):
    me = _client(_p("superadmin", None))
    r = me.get(f"{B}/modes/{MODE}/judge-readiness")
    assert r.status_code == 200 and r.json()["next_step"] == "label_more" and r.json()["ok"] is True
    assert stubs["calls"][-1][1][1] == MODE

    r = me.post(f"{B}/modes/{MODE}/splits", json={})
    assert r.status_code == 201 and r.json() == {"ok": True, "split": {"id": "split-1", "version": 1}}
    assert stubs["create_split"] == {"fractions": judges.SPLIT_FRACTIONS, "seed": judges.SPLIT_SEED,
                                     "override_min_labels": False, "override_reason": None}
    me.post(f"{B}/modes/{MODE}/splits", json={"fractions": [0.5, 0.25, 0.25], "seed": 3, "override_min_labels": True,
                                              "override_reason": "Thin data."})
    assert stubs["create_split"] == {"fractions": (0.5, 0.25, 0.25), "seed": 3, "override_min_labels": True,
                                     "override_reason": "Thin data."}

    r = me.get(f"{B}/modes/{MODE}/splits/active")
    assert r.status_code == 200 and r.json() == {"ok": True, "split": {"id": "split-1", "version": 1}}
    assert stubs["calls"][-1][1] == (MODE,)

    r = me.get(f"{B}/judges/{JUDGE}")
    assert r.json() == {"ok": True, "judge": {"id": JUDGE}, "runs": []} and stubs["calls"][-1][1] == (JUDGE,)

    r = me.get(f"{B}/judges/{JUDGE}/estimate")
    assert r.json()["to_call"] == 10 and stubs["calls"][-1][1] == (JUDGE, "dev")           # the dev split unless told
    me.get(f"{B}/judges/{JUDGE}/estimate", params={"split": "test"})
    assert stubs["calls"][-1][1] == (JUDGE, "test")
    assert me.get(f"{B}/judges/{JUDGE}/estimate", params={"split": "train"}).status_code == 422

    r = me.post(f"{B}/judges/{JUDGE}/preview", json={"turn_id": "turn-1"})
    assert r.json()["verdict"] == "Fail" and stubs["calls"][-1][1][1:] == (JUDGE, "turn-1")

    r = me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "test", "usd_cap": 2.5})
    assert r.status_code == 202 and r.json()["run"]["id"] == RUN and r.json()["ok"] is True
    assert stubs["start_run"] == {"split": "test", "usd_cap": 2.5}
    me.post(f"{B}/judges/{JUDGE}/runs", json={"split": "dev"})
    assert stubs["start_run"] == {"split": "dev", "usd_cap": None}

    r = me.get(f"{B}/judge-runs/{RUN}")
    assert r.json() == {"ok": True, "run": {"id": RUN, "status": "done", "disagreements": []}}

    r = me.patch(f"{B}/judge-runs/{RUN}/disagreements/turn-1", json={"triage": "label_wrong", "note": "Mislabelled.", "base": 4})
    assert r.json() == {"ok": True, "run": {"id": RUN, "untriaged": 0}}
    assert stubs["triage"] == {"triage": "label_wrong", "note": "Mislabelled.", "base": 4}
    assert stubs["calls"][-1][1][1:] == (RUN, "turn-1")

    r = me.post(f"{B}/judges/{JUDGE}/freeze", json={"stop_reason": "Stable on dev twice.", "base": 2})
    assert r.json() == {"ok": True, "judge": {"id": JUDGE, "status": "frozen"}}
    assert stubs["freeze"] == {"stop_reason": "Stable on dev twice.", "base": 2}

    r = me.patch(f"{B}/judges/{JUDGE}/decision", json={"decision": "do_not_use", "note": "Too lenient.", "base": 3})
    assert r.json() == {"ok": True, "judge": {"id": JUDGE, "decision": "use"}}              # the store's answer, as it is
    assert stubs["set_decision"] == {"decision": "do_not_use", "note": "Too lenient.", "base": 3}

    me.patch(f"{B}/judges/{JUDGE}/threshold", json={"threshold": 0.35, "base": 5})
    assert stubs["set_threshold"] == {"threshold": 0.35, "base": 5}
    me.patch(f"{B}/judges/{JUDGE}/threshold", json={"base": 5})                            # clearing it: no number
    assert stubs["set_threshold"] == {"threshold": None, "base": 5}


def test_a_judge_is_registered_with_its_parts_and_examples(stubs, monkeypatch):
    async def ok(principal, **kw):
        stubs["calls"].append(("register", (principal,), kw))
        return {"id": JUDGE, "version": 0}, [{"code": "built_below_minimum", "message": "Built below the minimum."}]

    monkeypatch.setattr(judges, "register", ok)
    parts = {"task": "Judge it.", "fail_rule": "Fails when a code is invented.", "pass_rule": "Passes when every code is real.",
             "edge_cases": None}
    examples = [{"turn_id": f"t{i}", "role": role, "critique": "A long enough critique.", "verdict": verdict}
                for i, (role, verdict) in enumerate((("clear_fail", "Fail"), ("clear_pass", "Pass"), ("borderline", "Fail")))]
    r = _client(_p("superadmin", None)).post(f"{B}/judges", json={"mode_id": MODE, "kind": "llm", "change_note": "First.",
                                                                  "parts": parts, "examples": examples})
    assert r.status_code == 201
    assert r.json() == {"ok": True, "judge": {"id": JUDGE, "version": 0},
                        "warnings": [{"code": "built_below_minimum", "message": "Built below the minimum."}]}
    kw = stubs["calls"][-1][2]
    assert kw["mode_id"] == MODE and kw["kind"] == "llm" and kw["change_note"] == "First." and kw["check_key"] is None
    assert kw["parts"] == parts and kw["examples"] == examples
    r = _client(_p("superadmin", None)).post(f"{B}/judges", json={"mode_id": MODE, "kind": "code", "check_key": "refusal"})
    kw = stubs["calls"][-1][2]
    assert kw["kind"] == "code" and kw["check_key"] == "refusal" and kw["parts"] is None and kw["examples"] is None


# -- request bodies are checked before anything is read or spent -----------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("POST", f"/modes/{MODE}/splits", {"fractions": [0.5, 0.5]}),
    ("POST", f"/modes/{MODE}/splits", {"fractions": [0.2, 0.2, 0.2, 0.4]}),
    ("POST", f"/modes/{MODE}/splits", {"seed": -1}),
    ("POST", f"/modes/{MODE}/splits", {"override_reason": "x" * 1001}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "prompt"}),
    ("POST", "/judges", {"kind": "code"}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "llm", "parts": {"fail_rule": "short", "pass_rule": "Passes when real."}}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "llm", "examples": [
        {"turn_id": "t", "role": "clear_fail", "critique": "A long enough critique.", "verdict": "Fail"}] * 4}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "llm", "examples": [
        {"turn_id": "t", "role": "odd_one", "critique": "A long enough critique.", "verdict": "Fail"}]}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "llm", "examples": [
        {"turn_id": "t", "role": "clear_fail", "critique": "A long enough critique.", "verdict": "Maybe"}]}),
    ("POST", "/judges", {"mode_id": MODE, "kind": "llm", "examples": [
        {"turn_id": "t", "role": "clear_fail", "critique": "short", "verdict": "Fail"}]}),
    ("POST", f"/judges/{JUDGE}/preview", {}),
    ("POST", f"/judges/{JUDGE}/preview", {"turn_id": ""}),
    ("POST", f"/judges/{JUDGE}/preview", {"turn_id": "t" * (evidence.MAX_ID_LENGTH + 1)}),
    ("POST", f"/judges/{JUDGE}/runs", {}),
    ("POST", f"/judges/{JUDGE}/runs", {"split": "train"}),
    ("POST", f"/judges/{JUDGE}/runs", {"split": "dev", "usd_cap": 0}),
    ("POST", f"/judges/{JUDGE}/runs", {"split": "dev", "usd_cap": -1}),
    ("POST", f"/judges/{JUDGE}/runs", {"split": "dev", "usd_cap": 100.01}),
    ("PATCH", f"/judge-runs/{RUN}/disagreements/turn-1", {"triage": "judge_wrong"}),
    ("PATCH", f"/judge-runs/{RUN}/disagreements/turn-1", {"triage": "meh", "base": 1}),
    ("PATCH", f"/judge-runs/{RUN}/disagreements/turn-1", {"triage": "judge_wrong", "base": 0}),
    ("PATCH", f"/judge-runs/{RUN}/disagreements/{'t' * (evidence.MAX_ID_LENGTH + 1)}", {"triage": "judge_wrong", "base": 1}),
    ("POST", f"/judges/{JUDGE}/freeze", {"base": 1}),
    ("POST", f"/judges/{JUDGE}/freeze", {"stop_reason": "x" * 2001, "base": 1}),
    ("POST", f"/judges/{JUDGE}/freeze", {"stop_reason": "Stable on dev twice.", "base": 0}),
    ("PATCH", f"/judges/{JUDGE}/decision", {"decision": "maybe", "base": 1}),
    ("PATCH", f"/judges/{JUDGE}/decision", {"decision": "use"}),
    ("PATCH", f"/judges/{JUDGE}/threshold", {"threshold": 0.4}),
    ("PATCH", f"/judges/{JUDGE}/threshold", {"threshold": "high", "base": 1}),
    ("GET", "/failure-report?format=pdf", None),
    ("GET", f"/judges/{'j' * 65}", None),
    ("GET", f"/modes/{'m' * 65}/judge-readiness", None),
])
def test_a_bad_request_is_a_422_and_reaches_nothing(stubs, method, path, body):
    r = _client(_p("superadmin", None)).request(method, B + path, json=body)
    assert r.status_code == 422, (path, body, r.text)
    assert stubs["calls"] == []


# -- the failure report ----------------------------------------------------------------------------------------------

def test_failure_report_markdown_is_an_attachment(stubs):
    r = _client(_p("superadmin", None)).get(f"{B}/failure-report", params={"format": "md"})
    assert r.status_code == 200 and r.text == "# Failure report\n"
    assert r.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="failure-report.md"' in r.headers["content-disposition"]
    assert stubs["failure_report"] == {"fmt": "md"}
    r = _client(_p("superadmin", None)).get(f"{B}/failure-report")
    assert r.status_code == 200 and r.json() == {"ok": True, "generated_at": "2026-10-15T12:00:00+00:00", "modes": []}
    assert stubs["failure_report"] == {"fmt": "json"}
    assert json.loads(r.text)["modes"] == []


def test_the_failure_report_is_the_platform_teams_alone(stubs):
    for who, q in ((_p("admin"), {}), (_p("superadmin", None), {"organization_id": ORG})):   # a company, or acting for one
        r = _client(who).get(f"{B}/failure-report", params={"format": "md", **q})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    assert stubs["calls"] == []
    # a superadmin's own company is not "acting for" it: only naming a company is
    assert _client(_p("superadmin", ORG)).get(f"{B}/failure-report").status_code == 200
