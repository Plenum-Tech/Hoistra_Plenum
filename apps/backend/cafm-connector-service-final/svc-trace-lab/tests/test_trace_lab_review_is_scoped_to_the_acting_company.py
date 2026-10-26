"""The review routes: only admins review, a company admin reviews only their company, a superadmin writes only while
acting for a company, the error envelope carries the reason the page switches on, and every route is down (503) while
Trace Lab is. Who may ask, and the reasons, are auth.py's one rule: 403 reviewers_only, 403 bad_company (another
company), 400 bad_company (not an id), 400 name_a_company. The stores are faked: no database, no network, no model."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.services.principal import Principal, current_principal
from trace_lab import annotations, db, routes_review, sampling
from trace_lab.review_errors import Forbidden, Invalid, NotFound, Stale

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
B = "/api/trace-lab"
RIGHT = {"verdict": "right", "base": None}


def _principal(role="admin", org=ORG):
    return Principal(user_id=uuid.uuid4(), email="rev@example.com", organization_id=uuid.UUID(org) if org else None,
                     role=role, can_ingest=True, building_ids=None)


@pytest.fixture
def client(monkeypatch):
    calls = []
    state = {"ready": True}

    async def ready():
        if not state["ready"]:
            raise HTTPException(status_code=503, detail={"reason": "trace_lab_unavailable", "error": "Not ready."})

    app = FastAPI()
    app.include_router(routes_review.router, prefix=B)
    app.dependency_overrides[db.require_ready] = ready

    async def fake_save(principal, org, turn_id, **kw):
        calls.append(("save", org, turn_id, kw))
        return {"id": "a1", "verdict": kw["verdict"]}

    async def fake_batches(principal, org, **kw):
        calls.append(("batches", org, kw))
        return []

    monkeypatch.setattr(annotations, "save_verdict", fake_save)
    monkeypatch.setattr(sampling, "list_batches", fake_batches)

    def as_(p):
        app.dependency_overrides[current_principal] = lambda: p
        return TestClient(app)

    as_.calls, as_.state = calls, state
    return as_


def test_a_plain_user_is_refused(client):
    r = client(_principal("user")).put(f"{B}/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only" and not client.calls


def test_an_admin_with_no_company_is_refused(client):
    r = client(_principal("admin", org=None)).get(f"{B}/sampling/batches")
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "reviewers_only" and not client.calls


def test_an_admin_naming_another_company_is_refused(client):
    r = client(_principal()).get(f"{B}/sampling/batches", params={"organization_id": OTHER})
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "bad_company" and not client.calls


def test_an_admin_naming_their_own_company_is_the_same_as_naming_none(client):
    r = client(_principal()).get(f"{B}/sampling/batches", params={"organization_id": ORG})
    assert r.status_code == 200 and client.calls == [("batches", ORG, {"include_withdrawn": False})]


def test_a_company_id_that_is_not_an_id_is_refused(client):
    r = client(_principal("superadmin")).get(f"{B}/sampling/batches", params={"organization_id": "not-a-company"})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "bad_company" and not client.calls


def test_an_admin_writes_for_their_own_company(client):
    r = client(_principal()).put(f"{B}/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 200 and r.json() == {"ok": True, "annotation": {"id": "a1", "verdict": "right"}}
    assert client.calls == [("save", ORG, "turn-1", {"verdict": "right", "note": None, "anchor": None, "base": None})]


def test_a_wrong_verdict_hands_its_anchor_to_the_store(client):
    body = {"verdict": "wrong", "note": "Counted July.", "base": "head-1",
            "anchor": {"span_id": "s1", "field": "output", "quote": "758", "occurrence": 2}}
    assert client(_principal()).put(f"{B}/turns/turn-1/verdict", json=body).status_code == 200
    kw = client.calls[0][3]
    assert kw["anchor"] == annotations.Anchor("s1", "output", "758", 2) and kw["base"] == "head-1" and kw["note"] == "Counted July."


def test_a_superadmin_not_acting_reads_the_platform_but_cannot_write(client):
    c = client(_principal("superadmin", org=None))
    r = c.put(f"{B}/turns/turn-1/verdict", json=RIGHT)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"
    assert c.get(f"{B}/sampling/batches").status_code == 200
    assert client.calls == [("batches", None, {"include_withdrawn": False})]


def test_a_superadmin_acting_reviews_that_company(client):
    r = client(_principal("superadmin", org=None)).put(f"{B}/turns/turn-1/verdict", json=RIGHT,
                                                       params={"organization_id": OTHER})
    assert r.status_code == 200 and client.calls[0][1] == OTHER


@pytest.mark.parametrize("path", ["/queue", "/sampling/plan", "/map?since=2026-10-01T00:00:00Z&until=2026-10-02T00:00:00Z"])
def test_the_queue_the_plan_and_the_map_need_a_company(client, path):
    r = client(_principal("superadmin", org=None)).get(B + path)
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "name_a_company"


@pytest.mark.parametrize("exc,status,reason", [
    (Stale({"id": "head-2"}), 409, "stale"),
    (Invalid("quote_not_found"), 422, "quote_not_found"),
    (NotFound("turn_not_found"), 404, "turn_not_found"),
    (Forbidden("not_author", "Only the author can change a comment."), 403, "not_author"),
])
def test_review_errors_keep_the_house_envelope(client, monkeypatch, exc, status, reason):
    async def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(annotations, "save_verdict", boom)
    r = client(_principal()).put(f"{B}/turns/turn-1/verdict", json=RIGHT)
    body = r.json()["detail"]
    assert r.status_code == status and body["reason"] == reason and body["error"]
    if reason == "stale":
        assert body["current"] == {"id": "head-2"}


def test_the_error_sentence_names_no_table_service_or_id(client, monkeypatch):
    async def boom(*a, **kw):
        raise NotFound("batch_not_found")

    monkeypatch.setattr(sampling, "withdraw", boom)
    r = client(_principal()).post(f"{B}/sampling/batches/b-1/withdraw", json={"reason": "Wrong period."})
    sentence = r.json()["detail"]["error"].lower()
    assert r.status_code == 404 and not any(w in sentence for w in ("tracelab", "plenum", "svc-", "sql"))


def test_bodies_are_validated_before_anything_runs(client):
    c = client(_principal())
    assert c.put(f"{B}/turns/t/verdict", json={"verdict": "maybe"}).status_code == 422
    assert c.put(f"{B}/turns/t/verdict", json={"verdict": "wrong", "note": "x",
                                               "anchor": {"field": "html", "quote": "q"}}).status_code == 422
    assert c.post(f"{B}/sampling/batches/b/withdraw", json={"reason": ""}).status_code == 422
    assert c.get(f"{B}/progress", params={"block": 4}).status_code == 422
    assert c.put(f"{B}/comments/l-1", json={"note": "x"}).status_code == 422                 # base is required
    assert c.delete(f"{B}/turns/t/verdict").status_code == 422                                # base is required
    batch = {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15,
             "since": "2026-10-01T00:00:00Z", "until": "2026-10-02T00:00:00Z"}
    assert c.post(f"{B}/sampling/batches", json={**batch, "k": 0}).status_code == 422
    assert c.post(f"{B}/sampling/batches", json={**batch, "k": 201}).status_code == 422
    assert c.post(f"{B}/sampling/batches", json={**batch, "reviews_required": 4}).status_code == 422
    assert c.post(f"{B}/sampling/batches", json={**batch, "strategy": "monitor_flagged"}).status_code == 422   # not over HTTP
    assert c.post(f"{B}/sampling/batches", json={**batch, "source": "guess"}).status_code == 422
    r = c.get(f"{B}/annotations")
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "name_a_conversation"
    assert not client.calls


def test_a_batch_is_handed_to_sampling_with_the_company_and_every_field(client, monkeypatch):
    seen = {}

    async def fake_create(principal, org, req):
        seen.update(req, org=org)
        return {"ok": True, "batch": {"id": "b-1"}, "items": []}

    monkeypatch.setattr(sampling, "create_batch", fake_create)
    body = {"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": "route_target",
            "since": "2026-10-01T00:00:00Z", "until": "2026-10-15T00:00:00Z"}
    r = client(_principal()).post(f"{B}/sampling/batches", json=body)
    assert r.status_code == 201 and r.json()["batch"]["id"] == "b-1"
    assert seen["org"] == ORG and seen["k"] == 30 and seen["seed"] == 7 and seen["dimension"] == "route_target"
    assert seen["channel"] == "chat" and seen["reviews_required"] == 1 and seen["include_view_as"] is False


def test_the_routes_answer_503_while_trace_lab_is_down_but_only_to_reviewers(client):
    client.state["ready"] = False
    r = client(_principal()).get(f"{B}/sampling/batches")
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "trace_lab_unavailable"
    assert client(_principal()).put(f"{B}/turns/t/verdict", json=RIGHT).status_code == 503
    assert client(_principal("user")).get(f"{B}/sampling/batches").status_code == 403
    assert not client.calls


def test_preview_takes_the_batch_fields_as_query_parameters(client, monkeypatch):
    seen = {}

    async def fake_preview(principal, org, req):
        seen.update(req, org=org)
        return {"ok": True, "pool_size": 0, "excluded_sampled": 0, "truncated": False, "picks": []}

    monkeypatch.setattr(sampling, "preview", fake_preview)
    c = client(_principal())
    q = {"plan_step": "dimension", "strategy": "dimension", "k": 30, "dimension": "route_target",
         "since": "2026-10-01T00:00:00Z", "until": "2026-10-15T00:00:00Z"}
    r = c.get(f"{B}/sampling/preview", params=q)
    assert r.status_code == 200 and seen["org"] == ORG and seen["k"] == 30 and seen["seed"] == 7
    assert c.get(f"{B}/sampling/preview", params={**q, "strategy": "thumbs"}).status_code == 422
    assert c.get(f"{B}/sampling/preview", params={**q, "strategy": "monitor_flagged"}).status_code == 422


def test_the_progress_company_filter_is_the_platform_views_alone(client, monkeypatch):
    seen = []

    async def fake_progress(principal, org, **kw):
        seen.append((org, kw["filter_org"]))
        return {"ok": True}

    monkeypatch.setattr(sampling, "progress", fake_progress)
    client(_principal()).get(f"{B}/progress", params={"filter_org": OTHER})
    client(_principal("superadmin", org=None)).get(f"{B}/progress", params={"filter_org": OTHER})
    client(_principal("superadmin", org=None)).get(f"{B}/progress")
    assert seen == [(ORG, None), (None, OTHER), (None, None)]


def test_a_missing_batch_is_a_404_and_annotations_need_something_to_name(client, monkeypatch):
    async def none(*a, **kw):
        return None

    async def no_turns(*a, **kw):
        return {}

    monkeypatch.setattr(sampling, "batch_detail", none)
    monkeypatch.setattr(annotations, "for_turns", no_turns)
    c = client(_principal())
    r = c.get(f"{B}/sampling/batches/b-1")
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "batch_not_found"
    r = c.get(f"{B}/annotations", params={"turn_id": "turn-x"})
    assert r.status_code == 404 and r.json()["detail"]["reason"] == "turn_not_found"


def test_one_turn_lookup_has_the_conversation_shape_the_page_reads(client, monkeypatch):
    async def states(principal, org, ids):
        return {ids[0]: {"mine": None, "others": None, "others_count": 0}}

    monkeypatch.setattr(annotations, "for_turns", states)
    r = client(_principal()).get(f"{B}/annotations", params={"turn_id": "turn-x"})
    assert r.status_code == 200 and r.json() == {
        "ok": True, "conversation_key": None, "turns": {"turn-x": {"mine": None, "others": None, "others_count": 0}},
        "comments": None, "comments_hidden": 0, "blocks": None}


def test_comments_and_history_go_to_the_annotation_store_for_the_acting_company(client, monkeypatch):
    seen = []

    def recorder(name, value):
        async def fn(principal, org, *a, **kw):
            seen.append((name, org, a, kw))
            return value
        return fn

    for name, value in (("add_comment", {"id": "c1"}), ("edit_comment", {"id": "c2"}), ("delete_comment", {"id": "c3"}),
                        ("history", {"ok": True, "turn_id": "t"}), ("delete_verdict", {"id": "d1"})):
        monkeypatch.setattr(annotations, name, recorder(name, value))
    c = client(_principal())
    assert c.post(f"{B}/comments", json={"conversation_key": "s:x", "turn_id": "t", "note": "Hm."}).status_code == 201
    assert c.put(f"{B}/comments/l-1", json={"note": "Hmm.", "base": "c1"}).json()["annotation"] == {"id": "c2"}
    assert c.delete(f"{B}/comments/l-1", params={"base": "c2"}).json()["annotation"] == {"id": "c3"}
    assert c.delete(f"{B}/turns/t/verdict", params={"base": "v1"}).json()["annotation"] == {"id": "d1"}
    assert c.get(f"{B}/turns/t/annotation-history").json() == {"ok": True, "turn_id": "t"}
    assert [(n, o) for n, o, *_ in seen] == [("add_comment", ORG), ("edit_comment", ORG), ("delete_comment", ORG),
                                             ("delete_verdict", ORG), ("history", ORG)]
    assert seen[0][2] == ("s:x",) and seen[0][3] == {"note": "Hm.", "turn_id": "t"}
    assert seen[1][2] == ("l-1",) and seen[1][3] == {"note": "Hmm.", "base": "c1"}
