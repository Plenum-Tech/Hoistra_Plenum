"""The Trace tab's tree (9 Nov 2026), no database: one stored turn and its spans become the same nested steps, types,
inputs and outputs the Langfuse export sends, redacted the way the rest of Trace Lab is."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.agents import langfuse_export as lf
from trace_lab import normalize, trace_tree

T0 = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
Q = "which maintenance work orders are breached the SLA ?"
A = "13 open maintenance work orders are past their SLA."

TURN = {"turn_id": "turn-tt", "session_id": "sess-9", "organization_id": "0b1c2d3e-0000-4000-8000-000000000001",
        "user_id": "u-1", "email": "aasim@plenum-tech.com", "question": Q, "answer": A, "route": "single:maintenance",
        "started_at": T0, "ended_at": T0 + timedelta(milliseconds=18790), "latency_ms": 18790, "llm_calls": 3,
        "tool_calls": 2, "cost_usd": 0.11312, "cost_complete": True, "input_tokens": 40000, "output_tokens": 2000,
        "cache_read_tokens": 8000, "models": ["claude-sonnet-5", "gpt-5.6-terra"], "tools": ["answer_from_records"],
        "ok": True, "error": None}


def _s(i, parent, seq, kind, name, start_ms=0, end_ms=None, **kw):
    return {"id": i, "turn_id": "turn-tt", "parent_id": parent, "seq": seq, "kind": kind, "name": name,
            "agent": kw.pop("agent", "maintenance"), "started_at": T0 + timedelta(milliseconds=start_ms),
            "ended_at": T0 + timedelta(milliseconds=end_ms if end_ms is not None else start_ms + 10),
            "latency_ms": (end_ms if end_ms is not None else start_ms + 10) - start_ms, **kw}


SPANS = [
    _s(1, None, 0, "turn", "turn", 0, 18790, agent="orchestrator", input={"message": Q, "mode": "ask"},
       output={"answer": A}),
    _s(2, 1, 1, "router", "router", 10, 2720, agent="router", model="claude-sonnet-5", input_tokens=900,
       output_tokens=20, cost_usd=0.00769, input={"q": Q}, output={"model_output": {"route": "maintenance"}}),
    _s(3, 1, 2, "plan", "plan: 1 step", 2730, 2740, agent="orchestrator", output={"plan": ["maintenance"]}),
    _s(4, 1, 3, "agent", "maintenance", 2750, 17610),
    _s(5, 4, 4, "llm", "model", 2760, 5150, model="gpt-5.6-terra", input_tokens=5200, output_tokens=140,
       cost_usd=0.034392, input={"messages": ["find breached work orders"]},
       output={"model_output": "call answer_from_records"}),
    _s(6, 4, 5, "tool", "answer_from_records", 5160, 10920, input={"question": Q}, output={"rows": 13}),
    _s(7, 6, 6, "db", "count", 5170, 5180, input={"sql": "SELECT count(*) FROM work_orders"}, output={"row_count": 13}),
    _s(8, 6, 7, "db", "focus WorkOrder", 5190, 5210, input={"sql": "SELECT * FROM work_orders"},
       output={"_truncated": "abc"}),
    _s(9, 4, 8, "llm", "plan", 2755, 17600),
    _s(10, 4, 9, "tool", "list_vendor_accreditations", 11000, 41000, input={"vendor": "Pennard"}, ok=False,
       error="timeout after 30 s"),
    _s(11, 999, 10, "db", "orphan step", 12000, 12010, input={"sql": "SELECT 1"}, output={"row_count": 1}),
]

ORDER = ["chat", "router", "plan: 1 step", "maintenance", "model", "answer_from_records", "count", "focus WorkOrder",
         "plan", "list_vendor_accreditations", "orphan step"]


def _by_name(tree):
    return {n["name"]: n for n in tree["nodes"]}


def _otel_attrs(span):
    return {a["key"]: next(iter(a["value"].values())) for a in span["attributes"]}


def test_nodes_are_depth_first_with_the_chat_root_first():
    tree = trace_tree.build_tree(TURN, SPANS)
    nodes = tree["nodes"]
    assert [n["name"] for n in nodes] == ORDER
    assert [n["depth"] for n in nodes] == [0, 1, 1, 1, 2, 2, 3, 3, 2, 2, 1]
    root = nodes[0]
    assert root["parent_id"] is None and root["id"] == "1"
    assert all(isinstance(n["id"], str) for n in nodes)
    seen = set()
    for n in nodes:
        assert n["parent_id"] is None or n["parent_id"] in seen, n["name"]
        seen.add(n["id"])
    assert _by_name(tree)["orphan step"]["parent_id"] == "1", "a step whose parent is unknown hangs from the root"


def test_types_follow_the_langfuse_export():
    by = _by_name(trace_tree.build_tree(TURN, SPANS))
    assert {k: by[k]["type"] for k in ORDER} == {
        "chat": "agent", "router": "generation", "plan: 1 step": "span", "maintenance": "agent",
        "model": "generation", "answer_from_records": "tool", "count": "span", "focus WorkOrder": "span",
        "plan": "span", "list_vendor_accreditations": "tool", "orphan step": "span"}


def test_cost_and_tokens_only_on_model_calls():
    by = _by_name(trace_tree.build_tree(TURN, SPANS))
    assert (by["router"]["cost_usd"], by["router"]["model"], by["router"]["input_tokens"],
            by["router"]["output_tokens"]) == (0.00769, "claude-sonnet-5", 900, 20)
    assert by["model"]["cost_usd"] == 0.034392 and by["model"]["model"] == "gpt-5.6-terra"
    for name in ("maintenance", "answer_from_records", "count", "plan"):
        assert by[name]["cost_usd"] is None and by[name]["model"] is None and by[name]["input_tokens"] is None, name


def test_inputs_and_outputs_match_what_langfuse_receives():
    tree = trace_tree.build_tree(TURN, SPANS)
    otel = {s["spanId"]: s for s in lf.otel_spans_for(TURN, SPANS)}
    io = lf.filled_io(TURN, lf._ordered(SPANS))
    for n in tree["nodes"][1:]:
        span = otel[lf._span_id("turn-tt", n["id"])]
        a = _otel_attrs(span)
        assert span["name"] == n["name"]
        assert a["langfuse.observation.type"] == n["type"], n["name"]
        assert lf._io(n["input"]) == a.get("langfuse.observation.input"), n["name"]
        assert lf._io(n["output"]) == a.get("langfuse.observation.output"), n["name"]
        assert n["filled"] == io[n["id"]][2], n["name"]
        assert span["parentSpanId"] == lf._span_id("turn-tt", n["parent_id"]), n["name"]
    root = tree["nodes"][0]
    assert (root["input"], root["output"]) == (Q, A)


def test_a_failed_step_is_an_error_with_its_message():
    n = _by_name(trace_tree.build_tree(TURN, SPANS))["list_vendor_accreditations"]
    assert n["ok"] is False and n["level"] == "ERROR" and n["error"] == "timeout after 30 s"
    assert n["output"] == {"error": "timeout after 30 s"} and n["filled"]["output_from"] == "its error"
    assert _by_name(trace_tree.build_tree(TURN, SPANS))["count"]["level"] == "DEFAULT"


def test_values_are_redacted():
    secret = {"password": "hunter2-pass", "url": "https://x.test/file?sig=abc123sig&id=7"}
    spans = [SPANS[0], _s(2, 1, 1, "tool", "lookup", 10, 20, input=secret, output={"ok": True})]
    n = _by_name(trace_tree.build_tree(TURN, spans))["lookup"]
    assert n["input"] == normalize.redact(secret)
    assert "hunter2-pass" not in json.dumps(n["input"]) and "abc123sig" not in json.dumps(n["input"])


def test_a_cut_value_says_so():
    n = _by_name(trace_tree.build_tree(TURN, SPANS))["focus WorkOrder"]
    assert n["output"] == "abc … [cut: Hoist Traces keeps 16,000 characters]" and n["truncated"] is True
    assert _by_name(trace_tree.build_tree(TURN, SPANS))["count"]["truncated"] is False


def test_the_turn_header():
    t = trace_tree.build_tree(TURN, SPANS)["turn"]
    assert t["turn_id"] == "turn-tt" and t["langfuse_trace_id"] == lf.otel_trace_id("turn-tt")
    assert (t["session_id"], t["email"], t["route"], t["models"]) == (
        "sess-9", "aasim@plenum-tech.com", "single:maintenance", ["claude-sonnet-5", "gpt-5.6-terra"])
    assert (t["latency_ms"], t["cost_usd"], t["cost_complete"]) == (18790, 0.11312, True)
    assert (t["input_tokens"], t["output_tokens"], t["cache_read_tokens"], t["total_tokens"]) == (40000, 2000, 8000, 50000)
    assert t["ok"] is True and t["error"] is None
    assert t["started_at"].startswith("2026-11-09T12:00:00") and t["ended_at"].startswith("2026-11-09T12:00:18")


def test_a_purged_turn_is_a_root_alone():
    tree = trace_tree.build_tree(TURN, [])
    assert tree["purged"] is True and len(tree["nodes"]) == 1
    root = tree["nodes"][0]
    assert (root["id"], root["name"], root["type"], root["parent_id"], root["depth"]) == ("root", "chat", "agent", None, 0)
    assert (root["input"], root["output"], root["latency_ms"]) == (Q, A, 18790)
    assert trace_tree.build_tree(TURN, SPANS)["purged"] is False
