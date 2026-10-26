"""The taxonomy read and decision surfaces: GET /modes carries the agent and level labels from the server; readers are a
company's admins and the platform; label writes, note filing, suggestion decisions and bulk approval go to the company
or the platform owner; GET /proposals by turn reads both suggestion kinds; grouping queues are the owner's; bulk refusals
are 422 with their findings; requirements carry area labels. The store is faked: no database, no network."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import trace_lab_m4_fixtures as fx
from src.services.principal import Principal, current_principal
from trace_lab import annotations, db, labels, proposals, requirements, routes_taxonomy, taxonomy

B = "/api/trace-lab"
MODE = "00000000-0000-4000-8000-000000000009"
NOTE = "00000000-0000-4000-8000-000000000007"
PROP = "00000000-0000-4000-8000-000000000006"
PID = "00000000-0000-4000-8000-000000000001"
SERVED = "2026-10-15T10:00:00+00:00"


def _principal(role="admin", org=fx.ORG_A):
    return Principal(user_id=uuid.uuid4(), email="who@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


@pytest.fixture
def client(monkeypatch):
    calls: list[tuple[str, tuple, dict]] = []
    state = {"ready": True}

    async def ready():
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes_taxonomy.router, prefix=B)
    app.dependency_overrides[db.require_ready] = ready

    def stub(module, name, result):
        async def fake(*a, **kw):
            calls.append((name, a, kw))
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(module, name, fake)

    def as_(p):
        app.dependency_overrides[current_principal] = lambda: p
        return TestClient(app)

    as_.calls, as_.state, as_.stub = calls, state, stub
    return as_


# ── reads ─────────────────────────────────────────────────────────────────────────────────

def test_modes_carry_agent_and_level_labels_from_the_server(client):
    client.stub(taxonomy, "list_modes", [])
    body = client(_principal()).get(f"{B}/modes").json()
    assert {"key": "udr", "label": "Records lookup"} in body["agents"]
    assert {"key": "data", "label": "Data fault: the data owner fixes the records"} in body["levels"]
    assert body["fix_test"] == "Does fixing one fix the other?" and body["is_owner"] is False and body["ok"] is True
    assert body["grouping_note"] == taxonomy.GROUPING_CARD_NOTE and body["statuses"] == list(taxonomy.STATUSES)
    owner = client(_principal("superadmin", org=None)).get(f"{B}/modes").json()
    assert owner["is_owner"] is True
    acting = client(_principal("superadmin", org=None)).get(f"{B}/modes", params={"organization_id": fx.ORG_B}).json()
    assert acting["is_owner"] is False                                  # acting for a company is not the platform's view


def test_modes_are_listed_for_the_callers_company_and_retired_ones_only_when_asked(client):
    client.stub(taxonomy, "list_modes", [{"id": MODE}])
    client(_principal()).get(f"{B}/modes")
    client(_principal()).get(f"{B}/modes", params={"include_retired": "true"})
    client(_principal("superadmin", org=None)).get(f"{B}/modes")
    assert [(c[1][1], c[2]) for c in client.calls] == [(fx.ORG_A, {"include_retired": False}),
                                                       (fx.ORG_A, {"include_retired": True}), (None, {"include_retired": False})]


def test_one_mode_and_a_mode_that_is_not_there(client):
    client.stub(taxonomy, "get_mode", {"mode": {"id": MODE}, "contract": None})
    assert client(_principal()).get(f"{B}/modes/{MODE}").json() == {"ok": True, "mode": {"id": MODE}, "contract": None}
    client.stub(taxonomy, "get_mode", None)
    r = client(_principal()).get(f"{B}/modes/{MODE}")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "mode_not_found" and r.json()["detail"]["error"]


@pytest.mark.parametrize("path,target", [("/modes", (taxonomy, "list_modes")), (f"/modes/{MODE}", (taxonomy, "get_mode")),
                                         ("/notes/unfiled", (annotations, "unfiled_notes")),
                                         ("/labels/matrix", (labels, "matrix")), ("/turns/turn-1/labels", (labels, "cells_for_turn")),
                                         (f"/labels/turn-1/{MODE}/history", (labels, "history")),
                                         ("/proposals?kind=cell", (proposals, "queue")), ("/requirements", (requirements, "list_requirements"))])
def test_a_plain_user_and_an_admin_with_no_company_read_nothing_and_an_admin_cannot_name_another_company(client, path, target):
    client.stub(*target, {} if "matrix" in path else [])
    for who in (_principal("user"), _principal("admin", org=None)):
        r = client(who).get(B + path)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only"
    sep = "&" if "?" in path else "?"
    r = client(_principal()).get(f"{B}{path}{sep}organization_id={fx.ORG_B}")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"
    assert client.calls == []


def test_the_reads_pass_the_company_a_superadmin_names_and_none_for_the_platform_view(client):
    client.stub(annotations, "unfiled_notes", {"notes": [], "total": 0, "hidden": 0})
    client(_principal()).get(f"{B}/notes/unfiled", params={"limit": 20, "offset": 40})
    client(_principal("superadmin", org=None)).get(f"{B}/notes/unfiled")
    client(_principal("superadmin", org=None)).get(f"{B}/notes/unfiled", params={"organization_id": fx.ORG_B})
    assert [(c[1][1], c[2]) for c in client.calls] == [(fx.ORG_A, {"limit": 20, "offset": 40}), (None, {"limit": 50, "offset": 0}),
                                                       (fx.ORG_B, {"limit": 50, "offset": 0})]
    assert client(_principal()).get(f"{B}/notes/unfiled", params={"limit": 201}).status_code == 422


def test_the_matrix_reads_with_its_filters_and_only_the_live_statuses(client):
    client.stub(labels, "matrix", {"ok": True, "rows": []})
    r = client(_principal()).get(f"{B}/labels/matrix", params={"state": "missing", "statuses": "frozen,bogus,candidate",
                                                               "mode_ids": f"{MODE}, ,x", "limit": 25, "offset": 5})
    assert r.status_code == 200 and r.json() == {"ok": True, "rows": []}
    (_n, args, kw), = client.calls
    assert args[1] == fx.ORG_A and kw["state"] == "missing" and kw["statuses"] == ("frozen", "candidate")
    assert kw["mode_ids"] == [MODE, "x"] and kw["limit"] == 25 and kw["offset"] == 5 and kw["filter_org"] is None
    assert client(_principal()).get(f"{B}/labels/matrix", params={"state": "bogus"}).status_code == 422
    client(_principal()).get(f"{B}/labels/matrix", params={"statuses": "bogus"})
    assert client.calls[-1][2]["statuses"] == taxonomy.COMPLETENESS_STATUSES      # nothing usable: the default pair


def test_the_turn_chips_and_a_cells_history(client):
    client.stub(labels, "cells_for_turn", {"cells": [], "hidden": False})
    client.stub(labels, "history", {"chain": [{"id": "l1"}], "hidden": False})
    assert client(_principal()).get(f"{B}/turns/turn-1/labels").json() == {"ok": True, "cells": [], "hidden": False}
    assert client(_principal()).get(f"{B}/labels/turn-1/{MODE}/history").json() == {
        "ok": True, "chain": [{"id": "l1"}], "hidden": False}


def test_a_history_the_blind_rule_hides_reaches_the_client_as_empty_and_hidden(client):
    client.stub(labels, "history", {"chain": [], "hidden": True})
    assert client(_principal()).get(f"{B}/labels/turn-1/{MODE}/history").json() == {"ok": True, "chain": [], "hidden": True}


# ── proposals ─────────────────────────────────────────────────────────────────────────────

def test_proposals_by_turn_read_both_suggestion_kinds(client):
    client.stub(proposals, "queue", {"served_at": SERVED, "proposals": [], "total": 0, "hidden": 0})
    r = client(_principal()).get(f"{B}/proposals", params={"turn_id": "turn-1"})
    assert r.status_code == 200 and r.json()["ok"] is True
    (_n, args, kw), = client.calls
    assert kw["kind"] == "suggestion" and kw["turn_id"] == "turn-1" and kw["status"] == "pending" and args[1] == fx.ORG_A
    r = client(_principal()).get(f"{B}/proposals")
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "kind_required" and len(client.calls) == 1


def test_a_queue_passes_its_filters_and_paging(client):
    client.stub(proposals, "queue", {"served_at": SERVED, "proposals": [], "total": 0, "hidden": 0})
    client(_principal()).get(f"{B}/proposals", params={"kind": "depth_scan", "status": "accepted", "mode_id": MODE,
                                                       "run_id": PID, "limit": 20, "offset": 40})
    kw = client.calls[0][2]
    assert (kw["kind"], kw["status"], kw["mode_id"], kw["run_id"], kw["limit"], kw["offset"]) == (
        "depth_scan", "accepted", MODE, PID, 20, 40)
    for bad in ({"kind": "nope"}, {"kind": "cell", "status": "nope"}, {"kind": "cell", "limit": 0}, {"kind": "cell", "limit": 201},
                {"kind": "cell", "offset": -1}):
        assert client(_principal()).get(f"{B}/proposals", params=bad).status_code == 422


def test_a_grouping_queue_is_refused_to_a_company_admin_and_read_by_the_owner(client):
    client.stub(proposals, "queue", taxonomy.forbidden("platform_only"))
    r = client(_principal()).get(f"{B}/proposals", params={"kind": "group"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "platform_only"
    client.stub(proposals, "queue", {"served_at": SERVED, "proposals": [{"id": PID}], "total": 1, "hidden": 0})
    r = client(_principal("superadmin", org=None)).get(f"{B}/proposals", params={"kind": "group"})
    assert r.status_code == 200 and r.json()["proposals"] == [{"id": PID}] and client.calls[-1][1][1] is None


def test_deciding_a_suggestion_goes_to_the_company_or_the_platform_owner(client):
    client.stub(proposals, "decide", {"proposal": {"id": PID}, "label": {"id": "l1"}})
    body = {"decision": "override", "present": False, "base": "l0", "edits": {"name": "counts_closed_jobs", "level": "data"},
            "override_labels": True}
    assert client(_principal()).post(f"{B}/proposals/{PID}/decide", json=body).status_code == 200
    assert client(_principal("superadmin", org=None)).post(f"{B}/proposals/{PID}/decide", json={"decision": "accept"}).status_code == 200
    assert client(_principal("superadmin", org=None)).post(f"{B}/proposals/{PID}/decide?organization_id={fx.ORG_B}",
                                                           json={"decision": "dismiss"}).status_code == 200
    mine, platform, acting = client.calls
    assert mine[1][1:3] == (fx.ORG_A, PID) and mine[2] == {"decision": "override", "present": False,
                                                           "edits": {"name": "counts_closed_jobs", "level": "data"},
                                                           "base": "l0", "override_labels": True}
    assert platform[1][1] is None and platform[2]["edits"] is None and platform[2]["present"] is None
    assert acting[1][1] == fx.ORG_B
    r = client(_principal()).post(f"{B}/proposals/{PID}/decide?organization_id={fx.ORG_B}", json={"decision": "accept"})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company"
    for bad in ({"decision": "approve"}, {}, {"decision": "accept", "edits": {"name": "Bad Name"}}):
        assert client(_principal()).post(f"{B}/proposals/{PID}/decide", json=bad).status_code == 422
    assert len(client.calls) == 3


def test_a_decision_refused_by_the_store_is_the_house_envelope_and_a_stale_one_carries_the_proposal(client):
    from trace_lab.review_errors import Stale

    client.stub(proposals, "decide", Stale({"id": PID, "status": "accepted"}))
    r = client(_principal()).post(f"{B}/proposals/{PID}/decide", json={"decision": "accept"})
    assert r.status_code == 409 and r.json()["detail"]["current"] == {"id": PID, "status": "accepted"}
    client.stub(proposals, "decide", proposals._invalid("decision_not_allowed", kind="cell", decision="dismiss"))
    r = client(_principal()).post(f"{B}/proposals/{PID}/decide", json={"decision": "dismiss"})
    d = r.json()["detail"]
    assert r.status_code == 422 and d["reason"] == "decision_not_allowed" and d["kind"] == "cell" and d["error"].endswith(".")
    client.stub(proposals, "decide", proposals._not_found("proposal_not_found"))
    assert client(_principal()).post(f"{B}/proposals/{PID}/decide", json={"decision": "accept"}).status_code == 404


def test_bulk_check_returns_findings_and_approve_refusals_are_422_with_theirs(client):
    client.stub(proposals, "bulk_check", {"allowed": False, "refused": [{"rule": 2, "detail": "x"}], "flagged": [],
                                          "warnings": [], "spot_check": {"seed": "s", "items": []}, "stats": {}})
    body = {"action": "accept", "proposal_ids": [PID], "served_at": SERVED}
    r = client(_principal()).post(f"{B}/proposals/bulk/check", json=body)
    assert r.status_code == 200 and r.json()["allowed"] is False and r.json()["refused"][0]["rule"] == 2 and r.json()["ok"] is True
    kw = client.calls[0][2]
    assert kw["action"] == "accept" and kw["proposal_ids"] == [PID] and kw["served_at"].year == 2026
    client.stub(proposals, "bulk_approve", proposals._invalid("bulk_refused", refused=[{"rule": 7, "detail": "wait"}], flagged=[],
                                                              warnings=[]))
    r = client(_principal()).post(f"{B}/proposals/bulk/approve", json=dict(body, bases={PID: None}))
    assert r.status_code == 422 and r.json()["detail"]["refused"][0]["rule"] == 7 and r.json()["detail"]["reason"] == "bulk_refused"
    client.stub(proposals, "bulk_approve", proposals._conflict("spot_check_incomplete"))
    assert client(_principal()).post(f"{B}/proposals/bulk/approve", json=body).status_code == 409


@pytest.mark.parametrize("bad", [{"action": "delete"}, {"proposal_ids": []}, {"proposal_ids": ["x"] * 501}, {"served_at": "soon"}])
def test_a_malformed_bulk_request_is_a_422(client, bad):
    client.stub(proposals, "bulk_check", {})
    r = client(_principal()).post(f"{B}/proposals/bulk/check", json=dict({"action": "accept", "proposal_ids": [PID], "served_at": SERVED}, **bad))
    assert r.status_code == 422 and client.calls == []


def test_bulk_goes_to_the_callers_company_and_a_plain_user_cannot(client):
    client.stub(proposals, "bulk_approve", {"approved": [], "skipped": [], "labels": []})
    body = {"action": "accept", "proposal_ids": [PID], "served_at": SERVED, "bases": {PID: "l0"}}
    client(_principal()).post(f"{B}/proposals/bulk/approve", json=body)
    client(_principal("superadmin", org=None)).post(f"{B}/proposals/bulk/approve", json=body)
    assert [c[1][1] for c in client.calls] == [fx.ORG_A, None] and client.calls[0][2]["bases"] == {PID: "l0"}
    assert client(_principal("user")).post(f"{B}/proposals/bulk/approve", json=body).status_code == 403


# ── labels and notes ──────────────────────────────────────────────────────────────────────

def test_label_writes_go_to_the_company_or_the_owner(client):
    client.stub(labels, "set_label", {"cell": {"state": "present"}, "label": {"id": "l1"}})
    url = f"{B}/labels/turn-1/{MODE}"
    body = {"present": True, "base": None}
    assert client(_principal()).put(url, json=body).status_code == 200
    assert client(_principal("superadmin", org=None)).put(f"{url}?organization_id={fx.ORG_B}", json=body).status_code == 200
    assert client(_principal("superadmin", org=None)).put(url, json=body).status_code == 200
    assert [c[1][1] for c in client.calls] == [fx.ORG_A, fx.ORG_B, None]
    r = client(_principal()).put(f"{url}?organization_id={fx.ORG_B}", json=body)
    assert r.status_code == 403 and len(client.calls) == 3
    assert client(_principal("user")).put(url, json=body).status_code == 403


def test_a_label_carries_its_note_its_quote_and_its_spot_check_seed_and_a_clear_has_no_value(client):
    client.stub(labels, "set_label", {"cell": {}, "label": {}})
    client(_principal()).put(f"{B}/labels/turn-1/{MODE}", json={
        "present": True, "base": "l0", "note": "Counts jobs on hold.", "spot_check_seed": "abcd",
        "anchor": {"field": "answer", "quote": "12 open", "occurrence": 2}})
    client(_principal()).put(f"{B}/labels/turn-1/{MODE}", json={"present": None, "base": "l0"})
    first, clear = client.calls
    assert first[1][2:4] == ("turn-1", MODE) and first[2]["spot_check_seed"] == "abcd" and first[2]["note"] == "Counts jobs on hold."
    assert (first[2]["anchor"].field, first[2]["anchor"].quote, first[2]["anchor"].occurrence) == ("answer", "12 open", 2)
    assert clear[2]["present"] is None and clear[2]["anchor"] is None
    assert client(_principal()).put(f"{B}/labels/turn-1/{MODE}", json={"base": "l0"}).status_code == 422       # present is required
    assert client(_principal()).put(f"{B}/labels/turn-1/{MODE}", json={"present": True, "anchor": {"field": "nope", "quote": "x"}}).status_code == 422


def test_filing_a_note_goes_to_the_company_or_the_owner_and_setting_it_aside_is_the_owners(client):
    client.stub(annotations, "file_note", {"cell": {}, "label": {}})
    c = client(_principal())
    assert c.post(f"{B}/notes/{NOTE}/file", json={"mode_id": MODE, "base": "l0", "override_label": True}).status_code == 200
    assert client(_principal("superadmin", org=None)).post(f"{B}/notes/{NOTE}/file", json={"mode_id": MODE}).status_code == 200
    mine, platform = client.calls
    assert mine[1][1:4] == (fx.ORG_A, NOTE, MODE) and mine[2] == {"base": "l0", "override_label": True}
    assert platform[1][1] is None and platform[2] == {"base": None, "override_label": False}
    assert client(_principal()).post(f"{B}/notes/{NOTE}/dispose", json={"outcome": "no_mode_fits", "note": "x"}).status_code == 403
    assert client(_principal()).post(f"{B}/notes/{NOTE}/file", json={}).status_code == 422


# ── requirements ──────────────────────────────────────────────────────────────────────────

def test_requirements_carry_area_labels_and_seeding_is_the_owners(client):
    client.stub(requirements, "list_requirements", [])
    client.stub(requirements, "seed", {"inserted": ["GEN-1"], "skipped": [], "drift": []})
    body = client(_principal()).get(f"{B}/requirements").json()
    assert {"key": "UDR", "label": "Records lookup"} in body["areas"] and body["requirements"] == [] and body["ok"] is True
    assert {"key": "data", "label": taxonomy.LEVEL_LABELS["data"]} in body["levels"]
    assert client(_principal()).post(f"{B}/requirements/seed").status_code == 403
    assert client(_principal("superadmin", org=None)).post(f"{B}/requirements/seed").json()["inserted"] == ["GEN-1"]


def test_requirements_are_read_for_the_callers_company_and_filtered_by_status(client):
    client.stub(requirements, "list_requirements", [])
    client(_principal()).get(f"{B}/requirements", params={"status": "active"})
    client(_principal("superadmin", org=None)).get(f"{B}/requirements")
    assert [c[2] for c in client.calls] == [{"status": "active", "org": fx.ORG_A}, {"status": None, "org": None}]
    assert client(_principal()).get(f"{B}/requirements", params={"status": "bogus"}).status_code == 422


def test_a_refused_requirement_write_is_the_house_envelope(client):
    client.stub(requirements, "create", requirements._invalid("statement_length"))
    r = client(_principal("superadmin", org=None)).post(f"{B}/requirements", json={
        "area": "GEN", "statement": "Every figure traces to a record.", "level": "technical"})
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "statement_length" and r.json()["detail"]["error"]


# ── readiness ─────────────────────────────────────────────────────────────────────────────

def test_routes_answer_503_while_trace_lab_is_unverified_to_those_who_may_ask(client):
    client.state["ready"] = False
    assert client(_principal("superadmin", org=None)).get(f"{B}/modes").status_code == 503
    assert client(_principal()).get(f"{B}/labels/matrix").status_code == 503
    assert client(_principal()).put(f"{B}/labels/turn-1/{MODE}", json={"present": True}).status_code == 503
    assert client(_principal("user")).get(f"{B}/modes").status_code == 403           # a stranger learns nothing
