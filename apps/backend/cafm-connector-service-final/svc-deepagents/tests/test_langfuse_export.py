"""Hoist Traces in Langfuse (9 Nov 2026): every chat turn is sent to the self-hosted Langfuse in its own data model, so
Aasim can read a conversation there - session = conversation, trace = one question, generations = model calls (model,
tokens, cost), spans = the steps between (agents, stages, tools, SQL). Off unless LANGFUSE_HOST and both keys are set;
sent after the turn, in the background; a Langfuse that is down or refuses never slows or breaks an answer."""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from src.agents import langfuse_export as lf
from src.agents import trace

T0 = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 11, 9, 10, 0, 42, tzinfo=timezone.utc)

TURN = {"turn_id": "turn-abc", "session_id": "sess-1", "organization_id": "org-1", "user_id": "user-1",
        "email": "aasim.shaik@plenum-tech.com", "question": "Which certificates expire in January?",
        "answer": "Three certificates expire in January 2027.", "route": "single:compliance", "instance": "comp",
        "started_at": T0, "ended_at": T1, "latency_ms": 42000, "llm_calls": 2, "tool_calls": 1, "cost_usd": 0.094,
        "input_tokens": 1200, "output_tokens": 300, "models": ["gpt-5.6-terra", "claude-sonnet-5"], "tools": ["list_building_certificates"],
        "ok": True, "error": None}
SPANS = [
    {"id": "s0", "parent_id": None, "seq": 0, "kind": "turn", "name": "turn", "agent": "orchestrator", "started_at": T0, "ended_at": T1},
    {"id": "s1", "parent_id": "s0", "seq": 1, "kind": "agent", "name": "compliance", "agent": "compliance", "started_at": T0, "ended_at": T1},
    {"id": "s2", "parent_id": "s1", "seq": 2, "kind": "llm", "name": "plan", "agent": "compliance", "model": "gpt-5.6-terra",
     "started_at": T0, "ended_at": T0, "input": {"question": "Which certificates expire in January?"}, "output": {"filters": []},
     "input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.0014, "ok": True},
    {"id": "s3", "parent_id": "s1", "seq": 3, "kind": "db", "name": "focus ComplianceCertificate", "agent": "compliance",
     "started_at": T0, "ended_at": T0, "input": {"sql": "SELECT ..."}, "output": {"rows": 3}, "ok": True},
    {"id": "s4", "parent_id": "s0", "seq": 4, "kind": "tool", "name": "list_building_certificates", "agent": "orchestrator",
     "started_at": T0, "ended_at": T1, "ok": False, "error": "timeout after 30 s"},
]


def test_a_turn_becomes_a_langfuse_trace_with_its_generations_and_spans():
    events = lf.batch_for(TURN, SPANS, environment="local", release="375b0f5")
    types = [e["type"] for e in events]
    assert types == ["trace-create", "span-create", "generation-create", "span-create", "span-create"], "root turn is the trace"
    assert all(e["id"] and e["timestamp"] for e in events)

    tr = events[0]["body"]
    assert (tr["id"], tr["sessionId"], tr["userId"]) == ("turn-abc", "sess-1", "aasim.shaik@plenum-tech.com")
    assert tr["input"] == TURN["question"] and tr["output"] == TURN["answer"]
    assert tr["timestamp"] == "2026-11-09T10:00:00.000Z" and tr["environment"] == "local" and tr["release"] == "375b0f5"
    assert {"route:single:compliance", "instance:comp"} <= set(tr["tags"])
    assert tr["metadata"]["organization_id"] == "org-1" and tr["metadata"]["cost_usd"] == 0.094

    by_id = {e["body"]["id"]: e["body"] for e in events[1:]}
    assert "parentObservationId" not in by_id["s1"] and by_id["s1"]["traceId"] == "turn-abc", "children of the turn hang on the trace"
    gen = by_id["s2"]
    assert gen["parentObservationId"] == "s1" and gen["model"] == "gpt-5.6-terra"
    assert gen["usage"] == {"input": 1000, "output": 100, "total": 1100, "unit": "TOKENS", "totalCost": 0.0014}
    assert gen["usageDetails"] == {"input": 1000, "output": 100} and gen["costDetails"] == {"total": 0.0014}
    assert gen["startTime"].endswith("Z") and gen["input"] == {"question": "Which certificates expire in January?"}
    assert by_id["s3"]["metadata"]["kind"] == "db" and by_id["s3"]["input"] == {"sql": "SELECT ..."}
    assert by_id["s4"]["level"] == "ERROR" and by_id["s4"]["statusMessage"] == "timeout after 30 s"
    assert by_id["s1"]["level"] == "DEFAULT"


def _attrs(span):
    out = {}
    for a in span["attributes"]:
        v = a["value"]
        out[a["key"]] = (v.get("stringValue") if "stringValue" in v else int(v["intValue"]) if "intValue" in v
                         else v.get("doubleValue") if "doubleValue" in v else v.get("boolValue") if "boolValue" in v
                         else [x["stringValue"] for x in v["arrayValue"]["values"]])
    return out


def test_over_opentelemetry_a_turn_is_a_trace_of_nested_observations():
    """Langfuse v4 takes traces only over OpenTelemetry (v4.76 refuses trace-create on the ingestion API)."""
    spans = lf.otel_spans_for(TURN, SPANS, environment="local", release="375b0f5")
    assert len(spans) == 5 and "parentSpanId" not in spans[0], "the turn span is the root"
    tid = lf.otel_trace_id("turn-abc")
    assert len(tid) == 32 and all(s["traceId"] == tid for s in spans) and lf.otel_trace_id("turn-abc") == tid
    assert all(len(s["spanId"]) == 16 for s in spans) and len({s["spanId"] for s in spans}) == 5
    assert spans[0]["startTimeUnixNano"] == str(int(T0.timestamp()) * 10**9)

    root = _attrs(spans[0])
    assert root["langfuse.trace.name"] == "chat" and root["langfuse.session.id"] == "sess-1"
    assert root["langfuse.user.id"] == "aasim.shaik@plenum-tech.com"
    assert root["langfuse.trace.input"] == TURN["question"] and root["langfuse.trace.output"] == TURN["answer"]
    assert set(root["langfuse.trace.tags"]) == {"route:single:compliance", "instance:comp"}
    assert root["langfuse.trace.metadata.hoistra_turn_id"] == "turn-abc" and root["langfuse.trace.metadata.organization_id"] == "org-1"
    assert root["langfuse.trace.metadata.cost_usd"] == 0.094 and root["langfuse.environment"] == "local"
    assert root["langfuse.observation.type"] == "agent"

    by_name = {s["name"]: s for s in spans[1:]}
    agent, gen, db, tool = by_name["compliance"], by_name["plan"], by_name["focus ComplianceCertificate"], by_name["list_building_certificates"]
    assert agent["parentSpanId"] == spans[0]["spanId"] and tool["parentSpanId"] == spans[0]["spanId"]
    assert gen["parentSpanId"] == agent["spanId"] and db["parentSpanId"] == agent["spanId"]
    g = _attrs(gen)
    assert g["langfuse.observation.type"] == "generation" and g["langfuse.observation.model.name"] == "gpt-5.6-terra"
    assert json.loads(g["langfuse.observation.usage_details"]) == {"input": 1000, "output": 100}
    assert json.loads(g["langfuse.observation.cost_details"]) == {"total": 0.0014}
    assert json.loads(g["langfuse.observation.input"]) == {"question": "Which certificates expire in January?"}
    assert g["langfuse.session.id"] == "sess-1", "every observation carries the session, so a filter finds it"
    assert _attrs(agent)["langfuse.observation.type"] == "agent" and _attrs(db)["langfuse.observation.type"] == "span"
    t = _attrs(tool)
    assert t["langfuse.observation.type"] == "tool" and t["langfuse.observation.level"] == "ERROR"
    assert t["langfuse.observation.status_message"] == "timeout after 30 s" and tool["status"]["code"] == 2
    assert _attrs(agent)["langfuse.observation.level"] == "DEFAULT" and agent["status"]["code"] == 1
    json.dumps(lf.otel_payload(spans))


def test_a_model_stage_with_no_model_call_of_its_own_is_a_span_not_a_generation():
    """The compliance "plan" stage stays open for the whole turn (97 s on 9 Nov) with no model and no
    tokens; its model call is a separate span. In Langfuse it read as a 97 s generation."""
    stage = {"id": "s5", "parent_id": "s1", "seq": 5, "kind": "llm", "name": "plan", "agent": "compliance",
             "started_at": T0, "ended_at": T1, "input": {"question": "q"}}
    priced = dict(stage, id="s6", seq=6, model=None, cost_usd=0.002)
    spans = lf.otel_spans_for(TURN, SPANS + [stage, priced])
    types = {s["spanId"]: _attrs(s)["langfuse.observation.type"] for s in spans}
    assert types[lf._span_id("turn-abc", "s5")] == "span"
    assert types[lf._span_id("turn-abc", "s6")] == "generation", "a cost is a model call"
    assert types[lf._span_id("turn-abc", "s2")] == "generation"
    events = {e["body"]["id"]: e["type"] for e in lf.batch_for(TURN, SPANS + [stage, priced])}
    assert events["s5"] == "span-create" and events["s6"] == "generation-create" and events["s2"] == "generation-create"


def test_an_agent_step_shows_the_question_it_was_given_and_its_last_steps_output():
    """Hoist Traces makes an agent span only to group its steps, with no input or output of its own -
    Langfuse showed both as "undefined" (9 Nov). In Langfuse it carries the question it was asked and
    the output of its last step that has one."""
    spans = SPANS + [{"id": "s5", "parent_id": "s1", "seq": 5, "kind": "llm", "name": "review", "agent": "compliance",
                      "model": "gpt-5.6-terra", "started_at": T1, "ended_at": T1, "output": {"verdict": "ok"}},
                     {"id": "s6", "parent_id": "s1", "seq": 6, "kind": "db", "name": "audit", "agent": "compliance",
                      "started_at": T1, "ended_at": T1}]
    agent = next(s for s in lf.otel_spans_for(TURN, spans) if s["name"] == "compliance")
    a = _attrs(agent)
    assert a["langfuse.observation.input"] == TURN["question"]
    assert json.loads(a["langfuse.observation.output"]) == {"verdict": "ok"}, "the last step with an output"
    body = next(e["body"] for e in lf.batch_for(TURN, spans) if e["body"].get("id") == "s1")
    assert body["input"] == TURN["question"] and body["output"] == {"verdict": "ok"}
    assert a["langfuse.observation.metadata.input_from"] == "the question"
    assert a["langfuse.observation.metadata.output_from"] == "its last step (review)"
    lone = [dict(SPANS[0]), dict(SPANS[1])]
    a2 = _attrs(next(s for s in lf.otel_spans_for(TURN, lone) if s["name"] == "compliance"))
    assert a2["langfuse.observation.input"] == TURN["question"]
    assert json.loads(a2["langfuse.observation.output"]) == {"status": "finished"}
    assert a2["langfuse.observation.metadata.output_from"] == "no output was recorded"


def test_every_step_shows_an_input_and_an_output():
    """All the steps must show properly (Hussain, 9 Nov): the shapes seen in a real compliance turn."""
    agent = {"id": "a", "parent_id": "s0", "seq": 1, "kind": "agent", "name": "compliance", "agent": "compliance"}
    plan_stage = {"id": "p1", "parent_id": "a", "seq": 2, "kind": "llm", "name": "plan", "input": {"question": "q"}}
    plan_call = {"id": "p2", "parent_id": "a", "seq": 3, "kind": "llm", "name": "plan", "model": "gpt-5.6-terra",
                 "input": {"system_prompt": "You plan"}, "output": {"plan": {"steps": 2}}}
    fetch = {"id": "f", "parent_id": "a", "seq": 4, "kind": "stage", "name": "fetch",
             "output": {"_truncated": '{"spec": {"scope": "building", "rows": [1, 2'}}
    tool = {"id": "t", "parent_id": "a", "seq": 5, "kind": "tool", "name": "list_building_certificates",
            "input": {"month": "2027-01"}, "ok": False, "error": "timeout after 30 s"}
    spans = [SPANS[0], agent, plan_stage, plan_call, fetch, tool]
    io = lf.filled_io(TURN, spans)
    assert all(io[s["id"]][0] is not None and io[s["id"]][1] is not None for s in spans[1:]), io
    assert io["p1"][1] == {"plan": {"steps": 2}} and io["p1"][2]["output_from"] == "its model call (plan)"
    assert io["f"][0] == {"plan": {"steps": 2}} and io["f"][2]["input_from"] == "the step before (plan)"
    assert io["f"][1].startswith('{"spec": {"scope": "building"') and io["f"][1].endswith("16,000 characters]"), \
        "a cut payload reads as text, not as one escaped string"
    assert io["t"][1] == {"error": "timeout after 30 s"} and io["t"][2]["output_from"] == "its error"
    assert io["p2"][2] == {}, "what a step recorded itself is shown as is, with no note"
    for s in lf.otel_spans_for(TURN, spans):
        a = _attrs(s)
        assert a.get("langfuse.observation.input") and a.get("langfuse.observation.output"), s["name"]
    for e in lf.batch_for(TURN, spans)[1:]:
        assert e["body"].get("input") is not None and e["body"].get("output") is not None, e["body"]["name"]


def test_a_turn_whose_steps_were_purged_is_still_a_trace():
    spans = lf.otel_spans_for(TURN, [])
    assert len(spans) == 1 and _attrs(spans[0])["langfuse.trace.input"] == TURN["question"]


def test_it_sends_over_opentelemetry_and_falls_back_to_ingestion_on_a_langfuse_without_it(monkeypatch):
    seen = []

    def v3(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    monkeypatch.setattr(lf, "_transport", lambda: httpx.MockTransport(v3))
    cfg = {"host": "https://langfuse.plenum.test", "public": "pk", "secret": "sk", "environment": None, "release": None}
    assert asyncio.run(lf.send_turn(TURN, SPANS, cfg)) is True
    assert seen == ["/api/public/otel/v1/traces"]

    seen.clear()

    def v2(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/otel/v1/traces"):
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(207, json={"successes": [], "errors": []})

    monkeypatch.setattr(lf, "_transport", lambda: httpx.MockTransport(v2))
    assert asyncio.run(lf.send_turn(TURN, SPANS, cfg)) is True
    assert seen == ["/api/public/otel/v1/traces", "/api/public/ingestion"]

    monkeypatch.setattr(lf, "_transport", lambda: httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    assert asyncio.run(lf.send_turn(TURN, SPANS, cfg)) is False


def test_off_unless_the_host_and_both_keys_are_set(monkeypatch):
    for k in ("LANGFUSE_HOST", "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert lf.settings() is None and not lf.enabled()
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.plenum.test/")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1")
    assert not lf.enabled(), "a host and one key is not enough"
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-1")
    assert lf.settings()["host"] == "https://langfuse.plenum.test" and lf.enabled()


def test_it_posts_to_the_ingestion_api_with_basic_auth_in_batches(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(207, json={"successes": [{"id": "x", "status": 201}], "errors": []})

    monkeypatch.setattr(lf, "_transport", lambda: httpx.MockTransport(handler))
    monkeypatch.setattr(lf, "BATCH_EVENTS", 2)
    cfg = {"host": "https://langfuse.plenum.test", "public": "pk-lf-1", "secret": "sk-lf-1", "environment": None, "release": None}
    events = lf.batch_for(TURN, SPANS)
    assert asyncio.run(lf.send(events, cfg)) is True
    assert len(seen) == 3, "5 events in batches of 2"
    r = seen[0]
    assert str(r.url) == "https://langfuse.plenum.test/api/public/ingestion" and r.method == "POST"
    assert r.headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()
    assert [e["type"] for e in json.loads(r.content)["batch"]] == ["trace-create", "span-create"]


@pytest.mark.parametrize("handler", [
    lambda req: httpx.Response(401, json={"message": "Invalid credentials"}),
    lambda req: httpx.Response(500, text="down"),
    lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused")),
])
def test_a_langfuse_that_refuses_or_is_down_never_raises(monkeypatch, handler):
    monkeypatch.setattr(lf, "_transport", lambda: httpx.MockTransport(handler))
    cfg = {"host": "https://langfuse.plenum.test", "public": "pk", "secret": "sk", "environment": None, "release": None}
    assert asyncio.run(lf.send(lf.batch_for(TURN, SPANS), cfg)) is False


def test_rows_read_back_from_the_store_map_the_same_way():
    """The backfill reads JSONB as dicts or text, NUMERIC as Decimal and UUIDs as objects."""
    import uuid
    from decimal import Decimal

    turn = dict(TURN, cost_usd=Decimal("0.094000"), organization_id=uuid.UUID(int=1), models='["gpt-5.6-terra"]')
    turn.pop("instance")
    spans = [dict(s) for s in SPANS]
    spans[2] = dict(spans[2], cost_usd=Decimal("0.001400"), input='{"question": "q"}', id=uuid.UUID(int=2),
                    parent_id="s1")
    events = lf.batch_for(turn, spans)
    tr = events[0]["body"]
    assert tr["metadata"]["cost_usd"] == 0.094 and tr["metadata"]["models"] == ["gpt-5.6-terra"]
    assert tr["metadata"]["organization_id"] == str(uuid.UUID(int=1)) and not any(t.startswith("instance:") for t in tr["tags"])
    gen = next(e["body"] for e in events if e["type"] == "generation-create")
    assert gen["id"] == str(uuid.UUID(int=2)) and gen["costDetails"] == {"total": 0.0014} and gen["input"] == {"question": "q"}
    json.dumps(events)  # all of it is plain JSON


def test_the_backfill_only_reads():
    from pathlib import Path

    import ast

    src = (Path(__file__).resolve().parents[1] / "scripts" / "langfuse_backfill.py").read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in src
    doc = ast.get_docstring(ast.parse(src), clean=False) or ""
    code = src.replace(doc, "").upper()
    for verb in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ", "TRUNCATE ", "CREATE "):
        assert verb not in code, verb


def test_a_live_turn_is_exported_after_it_ends_and_only_by_the_chat_service(monkeypatch):
    sent = []

    async def fake_send(turn, spans, cfg=None):
        sent.append(lf.batch_for(turn, spans))
        return True

    async def no_write(*a, **k):
        return None

    monkeypatch.setattr(trace, "_write", no_write)
    monkeypatch.setattr(lf, "send_turn", fake_send)
    for k in ("LANGFUSE_HOST", "LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(k, raising=False)

    async def one_turn(ready: bool):
        monkeypatch.setattr(trace, "_ready", ready)
        t = trace.Turn("turn-live", "sess-live")       # as Trace Lab's capture builds it
        t.question = "Which certificates expire in January?"
        s = t.new(kind="llm", name="plan", agent="compliance", parent_id=t.root.id, model="gpt-5.6-terra")
        s.close(output={"filters": []}, input_tokens=10, output_tokens=5)
        trace._flush(t, answer="Three.", tool_calls=[])
        await asyncio.sleep(0.05)

    asyncio.run(one_turn(ready=True))
    assert sent == [], "no Langfuse configured, nothing sent"
    monkeypatch.setenv("LANGFUSE_HOST", "https://langfuse.plenum.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    asyncio.run(one_turn(ready=False))
    assert sent == [], "Trace Lab's reruns and golden runs (no trace tables) are not real chats"
    asyncio.run(one_turn(ready=True))
    assert len(sent) == 1
    tr = sent[0][0]["body"]
    assert tr["id"] == "turn-live" and tr["sessionId"] == "sess-live" and tr["output"] == "Three."
    assert [e["type"] for e in sent[0]] == ["trace-create", "generation-create"]
