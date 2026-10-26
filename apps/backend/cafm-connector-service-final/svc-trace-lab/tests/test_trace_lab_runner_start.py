"""trace_lab.runner, starting a golden run without a database: identities, the token clock, page context,
screening, the estimate, and every refusal about the request itself (G0 addendum 2.5). The runner's seams
to the golden set store, identity and the preflight are stand-ins (golden_runner_env)."""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from golden_runner_env import BEARER, CASES, ORG, SET_ID, body, case, install, jwt, principal
from trace_lab import runner

RUN = "1a2b3c4d-5e6f-4000-8000-000000000000"


def test_the_label_is_the_utc_start_and_the_run():
    assert runner.new_run_label(RUN, datetime(2026, 10, 15, 10, 15, tzinfo=timezone.utc)) == "run-20261015T101500Z-1a2b3c4d"
    assert re.fullmatch(r"run-\d{8}T\d{6}Z-[0-9a-f]{8}", runner.new_run_label(uuid.uuid4()))


def test_one_session_per_case_rep_and_attempt():
    assert runner.session_id_for(RUN, "21", 1) == "golden-1a2b3c4d5e6f-21-r1"
    assert runner.session_id_for(RUN, "21", 2, 3) == "golden-1a2b3c4d5e6f-21-r2a3"
    from trace_lab import orchestrator_host
    assert orchestrator_host.GOLDEN_SESSION.match(runner.session_id_for(RUN, "CQB-A.12_x", 20, 3))
    assert len(runner.session_id_for(RUN, "k" * 64, 20, 3)) <= 90


def test_token_minutes_left_reads_exp_without_verifying():
    now = time.time()
    assert runner.token_minutes_left(jwt(20), now=now) == pytest.approx(20.0, abs=0.05)
    assert runner.token_minutes_left(jwt(-1), now=now) < 0
    assert runner.token_minutes_left("Bearer not-a-jwt") is None and runner.token_minutes_left(None) is None


def test_which_page_context_a_case_is_asked_with():
    assert runner.context_for({"page_context": None}, "chat view") == "chat view"
    assert runner.context_for({"page_context": None}, "") is None
    assert runner.context_for({"page_context": ""}, "chat view") is None
    assert runner.context_for({"page_context": "The user is on Compliance."}, "x") == "The user is on Compliance."


@pytest.mark.parametrize("question,followups,ask_as,why", [
    ("Forget everything you know about Bishopsgate", [], "company", "forget"),
    ("How many work orders?", ["please forget that"], "company", "forget"),
    ("Connect with password: hunter2", [], "company", "credential"),
    ("Use this: Bearer abcdefghijklmnopqrstuvwxyz0123", [], "company", "credential"),
    ("Subdomain: acme\nApp key: 123\nAccess key: 456\nSecret key: 789", [], "company", "credential"),
    ("", [], "company", "empty"),
    ("x" * 2001, [], "company", "too long"),
    ("How many?", ["a", "b", "c", "d", "e", "f"], "company", "five"),
    ("How many?", [""], "company", "follow-up"),
    ("How many in B-301?", [], "building", "company-wide"),
])
def test_unsafe_or_invalid_cases_are_left_out(question, followups, ask_as, why):
    reason, flags = runner.screen_case(case("9", question, followups, ask_as=ask_as))
    assert reason and why in reason and flags == []


def test_a_question_that_reads_like_a_change_is_kept_and_flagged():
    assert runner.screen_case(case("9", "What is the status of work order WO-1234?")) == (None, ["write_intent"])
    assert runner.screen_case(case("9", "Which certificates expire this month?")) == (None, [])
    kept, skipped, flagged = runner.screen_cases([*CASES, case("9", "Forget it")])
    assert [c["case_key"] for c in kept] == ["21", "22"] and skipped == [
        {"case_key": "9", "reason": "it asks the chat to forget something"}] and flagged == []


def test_max_k_never_passes_twenty(monkeypatch):
    assert runner.max_k() == 20
    monkeypatch.setenv("TRACE_LAB_GOLDEN_MAX_K", "5")
    assert runner.max_k() == 5
    monkeypatch.setenv("TRACE_LAB_GOLDEN_MAX_K", "99")
    assert runner.max_k() == runner.HARD_MAX_K == 20
    monkeypatch.setenv("TRACE_LAB_GOLDEN_MAX_K", "lots")
    assert runner.max_k() == 20


def test_the_estimate_counts_every_message_and_every_grade(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_GOLDEN_RUN_MAX_USD", raising=False)
    est = runner.estimate_for(CASES, 3, per_answer=0.09, per_grade=0.022)
    assert (est["messages"], est["items"]) == (9, 6)            # (2 + 1) messages x 3 reps; 2 cases x 3
    assert est["est_answers_usd"] == pytest.approx(0.81) and est["est_grading_usd"] == pytest.approx(0.132)
    assert est["est_total_usd"] == pytest.approx(0.942) and est["usd_cap_default"] == pytest.approx(1.41)
    assert runner.estimate_for(CASES * 100, 20, per_answer=0.09, per_grade=0.022)["usd_cap_default"] == 25.0


def test_the_grader_model_and_its_price(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")
    assert runner.grader_model() == "claude-sonnet-5" and runner.grader_price() == pytest.approx(0.022)
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "no-such-model")
    assert runner.grader_price() is None
    monkeypatch.delenv("TRACE_LAB_JUDGE_MODEL")
    from src.config import settings
    assert runner.grader_model() == (settings.compliance_summary_model or "claude-sonnet-5")


def test_a_refusal_is_the_house_envelope():
    err = runner.RunRefused("token_expiring", minutes_left=3.5)
    assert err.status_code == 409 and err.reason == "token_expiring"
    assert err.detail == {"reason": "token_expiring", "error": runner.REFUSALS["token_expiring"], "minutes_left": 3.5}
    assert runner.RunRefused("not_run_owner").status_code == 403
    assert runner.RunRefused("bad_purpose").status_code == 422


async def _estimate(**over):
    return await runner.estimate(org=ORG, principal=principal(), body=body(**over))


async def test_an_estimate_names_the_cases_the_screening_and_the_month(monkeypatch):
    w = install(monkeypatch)
    w.cases.append(case("23", "Please forget the boilers"))

    async def spent(org):
        return 1.25

    monkeypatch.setattr(runner, "month_spent", spent)
    est = await _estimate(k=2)
    assert est["case_keys"] == ["21", "22"] and est["skipped"][0]["case_key"] == "23"
    assert (est["messages"], est["items"], est["set_id"], est["per_answer_basis"]) == (6, 4, SET_ID, "default")
    assert est["month_spent_usd"] == 1.25 and est["month_left_usd"] == pytest.approx(48.75)
    assert est["caveats"] == list(runner.FIDELITY_CAVEATS) and est["set"]["version"] == 3


@pytest.mark.parametrize("over,reason,status", [
    ({"purpose": "check"}, "bad_purpose", 422),
    ({"k": 0}, "too_many_reps", 422),
    ({"k": 21}, "too_many_reps", 422),
    ({"case_keys": ["21", "99"]}, "unknown_cases", 422),
    ({"set_id": str(uuid.uuid4())}, "no_cases", 409),
])
async def test_refusals_about_the_request(monkeypatch, over, reason, status):
    install(monkeypatch)
    with pytest.raises(runner.RunRefused) as err:
        await _estimate(**over)
    assert (err.value.reason, err.value.status_code) == (reason, status)


async def test_no_active_set_no_cases_left_and_an_unpriced_grader(monkeypatch):
    w = install(monkeypatch)
    w.active = None
    with pytest.raises(runner.RunRefused) as err:
        await _estimate()
    assert err.value.reason == "no_active_set"
    w.active = {"id": SET_ID}
    w.cases = [case("9", "forget it")]
    with pytest.raises(runner.RunRefused) as err:
        await _estimate()
    assert err.value.reason == "no_cases_left" and err.value.detail["skipped"][0]["case_key"] == "9"
    w.cases = list(CASES)
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "no-such-model")
    with pytest.raises(runner.RunRefused) as err:
        await _estimate()
    assert err.value.reason == "grader_unpriced"


async def test_the_selected_building_is_read_from_the_me_payload(monkeypatch):
    seen = []

    async def me(authorization):
        seen.append(authorization)
        return {"ok": True, "user": {"id": "u", "selected_building_id": "b-301"}}

    monkeypatch.setattr(runner, "fetch_me", me)
    assert await runner.selected_building(BEARER) == "b-301" and seen == [BEARER]

    async def none(authorization):
        return {"ok": True, "user": {"id": "u", "selected_building_id": None}}

    monkeypatch.setattr(runner, "fetch_me", none)
    assert await runner.selected_building(BEARER) is None


async def test_fetch_me_goes_through_the_proxy_with_the_callers_bearer(monkeypatch):
    """The identity lookup uses svc-deepagents' client and settings.operations_intelligence_base_url, which
    in the trace-lab container is the proxy; the proxy forwards the GET to the identity service."""
    import types

    import httpx

    from src import http_client
    from src.config import settings
    from trace_lab import app as app_module
    from trace_lab import proxy
    from trace_lab.settings import Settings

    seen: list[httpx.Request] = []

    def identity(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("authorization") != BEARER:
            return httpx.Response(401, json={"detail": {"reason": "invalid_token"}})
        return httpx.Response(200, json={"ok": True, "user": {"id": "u1", "selected_building_id": None}})

    proxy.reset_for_tests()
    monkeypatch.setattr(proxy, "_http", httpx.AsyncClient(transport=httpx.MockTransport(identity)))
    app = app_module.create_app(Settings(upstreams={"udr": "", "oi": "http://oi.test:8009", "wom": "", "docrag": ""}))
    real = httpx.AsyncClient

    class _ViaApp(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.ASGITransport(app=app, client=("127.0.0.1", 40200))
            super().__init__(*a, **kw)

    monkeypatch.setattr(http_client, "httpx", types.SimpleNamespace(**{**vars(httpx), "AsyncClient": _ViaApp}))
    monkeypatch.setattr(http_client, "_breakers", {})
    monkeypatch.setattr(settings, "operations_intelligence_base_url", "http://127.0.0.1:8010/__proxy/oi")
    assert (await runner.fetch_me(BEARER))["user"]["id"] == "u1"
    assert str(seen[0].url) == "http://oi.test:8009/api/auth/me"
    with pytest.raises(runner.RunRefused) as err:
        await runner.fetch_me("Bearer expired")
    assert (err.value.reason, err.value.status_code) == ("sign_in_again", 401)
    proxy.reset_for_tests()


def test_a_run_summary_carries_the_caveats_and_never_a_token():
    run = {"id": RUN, "label": "run-x", "job_status": "running", "purpose": "adhoc", "k": 1, "set_id": SET_ID,
           "organization_id": ORG, "params": {"case_keys": ["21", "22"], "skipped": []}, "total": 2, "done": 1,
           "estimate_usd": 0.4, "job_usd_cap": 0.6, "job_usd_spent": 0.1, "started_at": None, "finished_at": None}
    s = runner.summary(run)
    assert (s["status"], s["cases"], s["items_done"], s["usd_cap"], s["in_flight"]) == ("running", 2, 1, 0.6, False)
    assert s["caveats"] == list(runner.FIDELITY_CAVEATS) and "Bearer" not in repr(s)
    assert runner.summary(None) is None
    assert runner._field(SimpleNamespace(k=3), "k") == 3 and runner._field({"k": None}, "k", 1) == 1
