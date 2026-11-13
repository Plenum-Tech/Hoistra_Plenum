"""The eval store's routes (11 Nov 2026, task plan Workstream B): mounted with the service, and each one hands the
caller's request to the module that does the work, with the caller as that module needs them.

What the modules do is tested where they live (eval_store, scenario_file, and the modules after them). Here: the
paths exist, a request reaches the right function with the right arguments, a refused request comes back in the
house envelope, and a bad scenario file is answered with its lines."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from eval_fixtures import ORG, member
from trace_lab import app as app_module
from trace_lab import (db, eval_judges, eval_store, expected_flags, routes_eval, routes_runs, scenario_file, scores, spec_doc,
                       sync_push, trace_labels)
from trace_lab.review_errors import Forbidden, NotFound

B = "/api/trace-lab"
SAMPLE = Path(__file__).parent / "fixtures" / "eval_scenarios_sample.jsonl"
PATHS = {
    ("GET", "/eval/run-tags"), ("POST", "/eval/run-tags"), ("POST", "/eval/ingest"),
    ("GET", "/eval/sessions"), ("GET", "/eval/sessions/{session_id}"), ("GET", "/eval/traces/{trace_id}/tree"),
    ("POST", "/eval/scenarios/import"), ("GET", "/eval/scenarios"), ("GET", "/eval/scenarios/export"),
    ("POST", "/eval/scenarios/approve"),
    ("GET", "/eval/sync"), ("POST", "/eval/sync"),
    ("GET", "/eval/spec"), ("POST", "/eval/spec/reload"), ("POST", "/eval/scenarios/generate"),
    ("POST", "/eval/evaluate"), ("PUT", "/eval/traces/{trace_id}/scores/{level}"), ("GET", "/eval/compare"),
    ("GET", "/eval/traces/{trace_id}/notes"), ("GET", "/eval/traces/{trace_id}/labels"),
    ("PUT", "/eval/traces/{trace_id}/labels/{mode}"),
    ("GET", "/eval/traces/{trace_id}/expected-flag"), ("PUT", "/eval/traces/{trace_id}/expected-flag"),
    ("GET", "/eval/labels"),
}


@pytest.fixture(autouse=True)
def _planned_sections_only(monkeypatch):
    """No database here: the sections a scenario may cite are the planned ones unless a test says otherwise."""
    async def planned():
        return set(scenario_file.SPEC_SECTIONS)

    monkeypatch.setattr(scenario_file, "allowed_sections", planned)


def test_every_eval_route_is_in_the_service_routers():
    assert routes_eval.router in app_module.ROUTERS
    have = {(m, r.path) for r in routes_eval.router.routes for m in r.methods}
    assert PATHS <= have, PATHS - have


@pytest.fixture
def who():
    return member(org=ORG, all_companies=False, role="admin")


@pytest.fixture
def client(who):
    app = FastAPI()
    app.include_router(routes_eval.router, prefix=B)

    async def _member():
        return who

    async def _ready():
        return None

    app.dependency_overrides[routes_runs._wide] = _member
    app.dependency_overrides[db.require_ready] = _ready
    return TestClient(app)


def _record(monkeypatch, module, name, result):
    calls = []

    async def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, name, fake)
    return calls


def test_the_session_list_passes_every_filter_through(client, who, monkeypatch):
    calls = _record(monkeypatch, eval_store, "list_sessions", {"sessions": [], "next": None})
    r = client.get(f"{B}/eval/sessions", params={
        "run_tag": "baseline_v0", "type": "multi_turn", "status": "failed", "level": "ux", "verdict": "fail",
        "needs_sme": "true", "stale": "true", "all_attempts": "true", "limit": 20,
        "before": "2026-11-11T09:00:00+00:00"})
    assert r.status_code == 200 and r.json() == {"ok": True, "sessions": [], "next": None}
    args, kw = calls[0]
    assert args == (who,)
    assert kw == {"run_tag": "baseline_v0", "type": "multi_turn", "status": "failed", "level": "ux", "verdict": "fail",
                  "needs_sme": True, "stale": True, "origin": None, "all_attempts": True, "limit": 20,
                  "before": datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)}


def test_a_session_and_a_trace_tree_are_read_by_id(client, who, monkeypatch):
    got = _record(monkeypatch, eval_store, "get_session", {"session": {"id": "s"}, "scenario": None, "traces": []})
    tree = _record(monkeypatch, eval_store, "trace_tree_of", {"turn": {"turn_id": "t"}, "nodes": [], "purged": False})
    r = client.get(f"{B}/eval/sessions/scn__baseline_v0__MT-001__1")
    assert r.status_code == 200 and r.json()["session"] == {"id": "s"}
    assert got[0][0] == (who, "scn__baseline_v0__MT-001__1")
    r = client.get(f"{B}/eval/traces/turn-abc/tree")
    assert r.status_code == 200 and r.json() == {"ok": True, "tree": {"turn": {"turn_id": "t"}, "nodes": [], "purged": False}}
    assert tree[0][0] == (who, "turn-abc")


def test_a_session_that_is_not_there_is_a_404_in_the_house_envelope(client, monkeypatch):
    _record(monkeypatch, eval_store, "get_session", NotFound("session_not_found", "That session is not in Trace Lab."))
    r = client.get(f"{B}/eval/sessions/nope")
    assert r.status_code == 404
    assert r.json()["detail"] == {"reason": "session_not_found", "error": "That session is not in Trace Lab."}


def test_run_tags_are_listed_and_made(client, who, monkeypatch):
    listed = _record(monkeypatch, eval_store, "list_run_tags", [{"tag": "baseline_v0"}])
    made = _record(monkeypatch, eval_store, "create_run_tag", {"tag": "postfix_v1", "kind": "post_fix"})
    assert client.get(f"{B}/eval/run-tags").json() == {"ok": True, "run_tags": [{"tag": "baseline_v0"}]}
    assert listed[0][0] == (who,)
    r = client.post(f"{B}/eval/run-tags", json={"tag": "postfix_v1", "kind": "post_fix", "note": "after the fix"})
    assert r.status_code == 201 and r.json()["run_tag"]["tag"] == "postfix_v1"
    assert made[0] == ((who, "postfix_v1"), {"kind": "post_fix", "note": "after the fix"})


def test_reading_recorded_conversations_is_for_curators_and_stays_in_the_callers_company(client, who, monkeypatch):
    calls = _record(monkeypatch, eval_store, "ingest_from_main",
                    {"sessions": 2, "traces": 3, "observations": 40, "skipped": 0})
    r = client.post(f"{B}/eval/ingest", json={"since": "2026-11-10T00:00:00Z", "run_tag": "baseline_v0",
                                              "sessions": {"chat-1": "SD-7-001", "chat-2": None}})
    assert r.status_code == 200 and r.json()["sessions"] == 2
    kw = calls[0][1]
    assert kw["org"] == ORG and kw["by"] is who and kw["run_tag"] == "baseline_v0"
    assert kw["sessions"] == {"chat-1": "SD-7-001", "chat-2": None}
    assert kw["since"] == datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_reading_conversations_without_a_date_goes_back_ninety_days(client, monkeypatch):
    calls = _record(monkeypatch, eval_store, "ingest_from_main", {"sessions": 0})
    assert client.post(f"{B}/eval/ingest", json={}).status_code == 200
    age = datetime.now(timezone.utc) - calls[0][1]["since"]
    assert timedelta(days=89, hours=23) < age < timedelta(days=90, hours=1)


def test_a_tester_may_not_read_conversations_into_the_store(monkeypatch):
    app = FastAPI()
    app.include_router(routes_eval.router, prefix=B)
    tester = member(org=ORG, all_companies=False, role="tester", manages=False)

    async def _member():
        return tester

    async def _ready():
        return None

    app.dependency_overrides[routes_runs._wide] = _member
    app.dependency_overrides[db.require_ready] = _ready
    calls = _record(monkeypatch, eval_store, "ingest_from_main", {})
    r = TestClient(app).post(f"{B}/eval/ingest", json={})
    assert r.status_code == 403 and calls == []


def test_an_imported_file_may_cite_the_rules_of_the_loaded_specification(client, monkeypatch):
    async def allowed():
        return {"s_1_overdue_reported_as_none"} | set(scenario_file.SPEC_SECTIONS)

    monkeypatch.setattr(scenario_file, "allowed_sections", allowed)
    _record(monkeypatch, scenario_file, "save_scenarios", {"added": 1, "new_versions": 0, "unchanged": 0})
    row = dict(json.loads(SAMPLE.read_text().splitlines()[0]), spec_sections=["s_1_overdue_reported_as_none"])
    files = {"file": ("one.jsonl", (json.dumps(row) + "\n").encode(), "application/x-ndjson")}
    r = client.post(f"{B}/eval/scenarios/import", files=files)
    assert r.status_code == 200 and r.json()["added"] == 1, r.json()


def test_a_good_scenario_file_is_stored_and_a_dry_run_stores_nothing(client, who, monkeypatch):
    saved = _record(monkeypatch, scenario_file, "save_scenarios", {"added": 4, "new_versions": 0, "unchanged": 0})
    files = {"file": ("scenarios.jsonl", SAMPLE.read_bytes(), "application/x-ndjson")}
    r = client.post(f"{B}/eval/scenarios/import", files=files, params={"dry_run": "true"})
    assert r.status_code == 200 and r.json() == {"ok": True, "dry_run": True, "scenarios": 4}
    assert saved == []
    r = client.post(f"{B}/eval/scenarios/import", files=files)
    assert r.status_code == 200 and r.json() == {"ok": True, "dry_run": False, "scenarios": 4, "added": 4,
                                                 "new_versions": 0, "unchanged": 0}
    args, kw = saved[0]
    assert args[0] is who and [s["scenario_id"] for s in args[1]] == ["SD-001", "MD-001", "MT-001", "FL-001"]
    assert kw == {"source": "import"}


def test_a_bad_scenario_file_is_answered_with_its_lines_and_nothing_is_stored(client, monkeypatch):
    saved = _record(monkeypatch, scenario_file, "save_scenarios", {})
    rows = [json.loads(x) for x in SAMPLE.read_text(encoding="utf-8").splitlines()]
    rows[1]["type"] = "other"
    body = "".join(json.dumps(x) + "\n" for x in rows).encode("utf-8")
    r = client.post(f"{B}/eval/scenarios/import", files={"file": ("s.jsonl", body, "application/x-ndjson")})
    assert r.status_code == 422 and saved == []
    detail = r.json()["detail"]
    assert detail["reason"] == "bad_rows" and [e["line"] for e in detail["errors"]] == [2]


def test_scenarios_are_listed_exported_and_approved(client, who, monkeypatch):
    rows, _ = scenario_file.parse_jsonl(SAMPLE.read_bytes())
    listed = _record(monkeypatch, scenario_file, "list_scenarios", rows)
    approved = _record(monkeypatch, scenario_file, "approve", {"approved": 1, "already": 0, "missing": []})
    r = client.get(f"{B}/eval/scenarios", params={"type": "failure", "section": "partial_failure", "stale": "true"})
    assert r.status_code == 200 and len(r.json()["scenarios"]) == 4
    assert listed[0] == ((who,), {"type": "failure", "section": "partial_failure", "status": None, "stale": True})
    r = client.get(f"{B}/eval/scenarios/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert scenario_file.parse_jsonl(r.content)[0] == rows
    r = client.post(f"{B}/eval/scenarios/approve", json={"scenario_ids": ["MT-001"]})
    assert r.status_code == 200 and r.json()["approved"] == 1
    assert approved[0][0] == (who, ["MT-001"])


def test_a_refusal_from_a_module_keeps_its_status(client, monkeypatch):
    _record(monkeypatch, scenario_file, "approve", Forbidden("curators_only", "Only curators can do this."))
    r = client.post(f"{B}/eval/scenarios/approve", json={"scenario_ids": ["MT-001"]})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "curators_only"


# ── pushing to another Trace Lab database (B5.3: the Sync button) ──────────────────────────────

def _client_as(m):
    app = FastAPI()
    app.include_router(routes_eval.router, prefix=B)

    async def _member():
        return m

    async def _ready():
        return None

    app.dependency_overrides[routes_runs._wide] = _member
    app.dependency_overrides[db.require_ready] = _ready
    return TestClient(app)


DONE = {"status": "done", "peer": "db.example.com/hoist_tracelab", "dry_run": False, "error": None, "watermark": None,
        "pushed": {"run_tags": 1, "spec_sections": 0, "scenarios": 250, "sessions": 250, "traces": 312,
                   "observations": 9804, "scores": 0}}


def test_only_those_who_may_push_are_told_where_the_push_goes_and_how_the_last_one_ended(client, monkeypatch):
    seen = _record(monkeypatch, sync_push, "status", {"enabled": True, "peer": "db.example.com/hoist_tracelab", "last": None})
    r = client.get(f"{B}/eval/sync")                         # the fixture's caller is one company's admin
    assert r.status_code == 200 and r.json() == {"ok": True, "enabled": False, "peer": None, "last": None}
    assert seen == [], "every company's push is not one company's to read"
    r = _client_as(member()).get(f"{B}/eval/sync")
    assert r.json() == {"ok": True, "enabled": True, "peer": "db.example.com/hoist_tracelab", "last": None}


def test_a_push_is_for_the_platform_team_and_the_consultant_only(client, monkeypatch):
    calls = _record(monkeypatch, sync_push, "push", DONE)
    r = client.post(f"{B}/eval/sync", json={})               # the fixture's caller is one company's admin
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only" and calls == []


def test_a_push_says_how_many_records_went(monkeypatch):
    platform = member()
    calls = _record(monkeypatch, sync_push, "push", DONE)
    r = _client_as(platform).post(f"{B}/eval/sync", json={"dry_run": False, "full": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["pushed"]["sessions"] == 250
    assert body["line"] == ("Pushed to db.example.com/hoist_tracelab: 250 sessions, 312 traces, 9,804 spans, "
                            "0 scores, 0 expected-outcome flags, 250 scenarios, 1 run tag, 0 spec sections.")
    assert calls[0] == ((), {"dry_run": False, "full": True, "by": platform})


def test_a_refused_push_says_why_and_a_broken_one_says_to_run_it_again(monkeypatch):
    _record(monkeypatch, sync_push, "push", sync_push.Refused("schema_version", "The dev database is on schema 13 "
                                                              "and this one is on 14."))
    r = _client_as(member()).post(f"{B}/eval/sync", json={})
    assert r.status_code == 409
    assert r.json()["detail"] == {"reason": "schema_version",
                                  "error": "The dev database is on schema 13 and this one is on 14."}
    _record(monkeypatch, sync_push, "push", {**DONE, "status": "failed", "error": "traces: connection lost"})
    r = _client_as(member()).post(f"{B}/eval/sync", json={})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["reason"] == "sync_failed" and detail["error"].endswith("Run it again to finish.")
    assert detail["pushed"]["sessions"] == 250


# ── the specification (B2.3) ───────────────────────────────────────────────────────────────

def test_the_current_specification_is_read_and_reloaded(client, who, monkeypatch):
    seen = _record(monkeypatch, spec_doc, "overview", {"spec_version": "abc123def456", "sections": [], "stale_scenarios": 3})
    loaded = _record(monkeypatch, spec_doc, "load", {"spec_version": "fff000fff000", "sections": 15, "new": True,
                                                     "changed": ["retry_rules"]})
    r = client.get(f"{B}/eval/spec")
    assert r.status_code == 200 and r.json() == {"ok": True, "spec_version": "abc123def456", "sections": [],
                                                 "stale_scenarios": 3}
    assert seen[0][0] == (who,)
    r = client.post(f"{B}/eval/spec/reload")
    assert r.status_code == 200 and r.json()["changed"] == ["retry_rules"] and r.json()["new"] is True
    assert loaded[0] == ((who,), {})


# ── generating scenarios (B2.1) ────────────────────────────────────────────────────────────

def test_generating_stores_the_batch_as_drafts_and_says_what_was_left_out(client, who, monkeypatch):
    saved = _record(monkeypatch, scenario_file, "save_scenarios", {"added": 25, "new_versions": 0, "unchanged": 0})
    r = client.post(f"{B}/eval/scenarios/generate", json={"count": 25, "seed": 3, "space": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["generated"] == 25 and body["added"] == 25 and body["seed"] == 3
    assert body["by_type"] == {"single_domain": 10, "multi_domain": 6, "multi_turn": 5, "failure": 4}
    assert isinstance(body["dropped"], int) and isinstance(body["dropped_examples"], list)
    args, kw = saved[0]
    assert args[0] is who and len(args[1]) == 25 and kw == {"source": "generator"}
    assert all(s["scenario_id"].split("-")[1] == "3" for s in args[1])


def test_a_generate_request_outside_its_limits_is_refused(client, monkeypatch):
    saved = _record(monkeypatch, scenario_file, "save_scenarios", {})
    assert client.post(f"{B}/eval/scenarios/generate", json={"count": 0}).status_code == 422
    assert client.post(f"{B}/eval/scenarios/generate", json={"count": 10, "space": "moon"}).status_code == 422
    assert saved == []


# ── evaluating, scoring and comparing (B3, B4) ─────────────────────────────────────────────

def test_a_pass_of_the_evaluators_is_started_with_its_levels_and_limit(client, who, monkeypatch):
    result = {"traces": 25, "scored": {"logic": 25}, "not_evaluated": {"logic": 0}, "errors": 0, "usd": 0.0,
              "capped": False, "remaining": 225}
    calls = _record(monkeypatch, eval_judges, "evaluate_run", result)
    r = client.post(f"{B}/eval/evaluate", json={"run_tag": "baseline_v0", "levels": ["logic"], "limit": 25})
    assert r.status_code == 200 and r.json() == {"ok": True, **result}
    assert calls[0] == ((who, "baseline_v0"), {"levels": ("logic",), "limit": 25, "skip": ()})
    r = client.post(f"{B}/eval/evaluate", json={"run_tag": "baseline_v0"})
    assert calls[1][1] == {"levels": ("logic", "ux", "domain"), "limit": 25, "skip": ()}
    client.post(f"{B}/eval/evaluate", json={"run_tag": "baseline_v0", "skip": ["turn-1", "turn-2"]})
    assert calls[2][1]["skip"] == ("turn-1", "turn-2"), "traces whose judge failed in this run are passed over"
    assert client.post(f"{B}/eval/evaluate", json={"run_tag": "baseline_v0", "limit": 500}).status_code == 422


def test_the_session_page_reads_a_traces_notes_and_labels_and_sets_a_label(client, who, monkeypatch):
    notes = _record(monkeypatch, eval_store, "trace_notes", {"trace_id": "turn-1", "organization_id": ORG,
                                                            "record": None, "labels": [], "me": "u"})
    r = client.get(f"{B}/eval/traces/turn-1/notes")
    assert r.status_code == 200 and r.json()["trace_id"] == "turn-1" and notes[0][0] == (who, "turn-1")
    listed = _record(monkeypatch, trace_labels, "labels_of", {"trace_id": "turn-1", "modes": [], "cells": {}, "me": "u"})
    assert client.get(f"{B}/eval/traces/turn-1/labels").json() == {"ok": True, "trace_id": "turn-1", "modes": [],
                                                                    "cells": {}, "me": "u"}
    assert listed[0][0] == (who, "turn-1")
    put = _record(monkeypatch, trace_labels, "set_label", {"trace_id": "turn-1", "mode": "m-1", "value": None, "at": None})
    r = client.put(f"{B}/eval/traces/turn-1/labels/m-1", json={"value": None})
    assert r.status_code == 200 and r.json()["label"]["mode"] == "m-1"
    assert put[0] == ((who, "turn-1"), {"mode": "m-1", "value": None})
    assert client.put(f"{B}/eval/traces/turn-1/labels/m-1", json={"value": "x" * 30}).status_code == 422


def test_a_person_scores_a_trace_at_a_level(client, who, monkeypatch):
    calls = _record(monkeypatch, scores, "annotate", {"verdict": "fail", "value": 0.25, "comment": "Too long."})
    r = client.put(f"{B}/eval/traces/turn-abc/scores/ux", json={"verdict": "fail", "value": 0.25, "comment": "Too long."})
    assert r.status_code == 200 and r.json() == {"ok": True, "score": {"verdict": "fail", "value": 0.25,
                                                                         "comment": "Too long."}}
    assert calls[0] == ((who, "turn-abc", "ux"), {"verdict": "fail", "value": 0.25, "comment": "Too long."})
    r = client.put(f"{B}/eval/traces/turn-abc/scores/ux", json={"verdict": "pass"})
    assert r.status_code == 200 and calls[1][1] == {"verdict": "pass", "value": None, "comment": None}


def test_two_run_tags_are_compared(client, who, monkeypatch):
    calls = _record(monkeypatch, scores, "compare", {"base": "baseline_v0", "head": "postfix_v1", "summary": {}, "rows": []})
    r = client.get(f"{B}/eval/compare", params={"base": "baseline_v0", "head": "postfix_v1"})
    assert r.status_code == 200 and r.json()["head"] == "postfix_v1"
    assert calls[0][0] == (who, "baseline_v0", "postfix_v1")
    assert client.get(f"{B}/eval/compare", params={"base": "baseline_v0"}).status_code == 422


def test_reading_chat_questions_is_asked_for_and_goes_back_a_week(client, who, monkeypatch):
    """13 Nov 2026: a question typed in the chat never reached Sessions, which reads scenario runs only."""
    calls = _record(monkeypatch, eval_store, "ingest_from_main", {"sessions": 2, "traces": 2})
    r = client.post(f"{B}/eval/ingest", json={"chat": True})
    assert r.status_code == 200 and r.json()["sessions"] == 2
    kw = calls[0][1]
    assert kw["chat"] is True and kw["org"] == ORG and kw["by"] is who
    age = datetime.now(timezone.utc) - kw["since"]
    assert timedelta(days=6, hours=23) < age < timedelta(days=7, hours=1)


def test_the_ordinary_read_does_not_ask_for_chat_questions(client, monkeypatch):
    calls = _record(monkeypatch, eval_store, "ingest_from_main", {"sessions": 0})
    assert client.post(f"{B}/eval/ingest", json={}).status_code == 200
    assert "chat" not in calls[0][1]


def test_a_reviewer_reads_and_sets_a_turns_expected_outcome_flag(client, who, monkeypatch):
    got = _record(monkeypatch, expected_flags, "flags_of", {"trace_id": "turn-1", "flagged": False, "mine": False, "flags": []})
    r = client.get(f"{B}/eval/traces/turn-1/expected-flag")
    assert r.status_code == 200 and r.json()["flagged"] is False and got[0][0] == (who, "turn-1")
    put = _record(monkeypatch, expected_flags, "set_flag", {"trace_id": "turn-1", "flagged": True, "at": None})
    r = client.put(f"{B}/eval/traces/turn-1/expected-flag", json={"flagged": True, "comment": "Wrong band."})
    assert r.status_code == 200 and r.json()["flag"]["flagged"] is True
    assert put[0] == ((who, "turn-1"), {"flagged": True, "comment": "Wrong band."})
    assert client.put(f"{B}/eval/traces/turn-1/expected-flag", json={"flagged": True, "comment": "x" * 2001}).status_code == 422


def test_the_labels_page_reads_a_run_tags_sessions_as_one_grid(client, who, monkeypatch):
    grid = {"run": {"code": "baseline_v0", "title": "Sessions", "kind": "sessions"}, "questions": [], "modes": [],
            "cells": {}, "last_updated": None, "me": "u"}
    got = _record(monkeypatch, trace_labels, "tag_matrix", grid)
    r = client.get(f"{B}/eval/labels", params={"run_tag": "baseline_v0"})
    assert r.status_code == 200 and r.json() == {"ok": True, **grid} and got[0][0] == (who, "baseline_v0")
    assert client.get(f"{B}/eval/labels").status_code == 422, "a run tag is needed"
    assert client.get(f"{B}/eval/labels", params={"run_tag": "x" * 41}).status_code == 422
