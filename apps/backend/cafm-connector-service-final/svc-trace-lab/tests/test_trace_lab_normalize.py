"""Trace Lab normalizer: tree, order, roles, model-call cards, flags, purity."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trace_lab import normalize as n

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
ORG = "11111111-1111-4111-8111-111111111111"
USER = "33333333-3333-4333-8333-333333333333"


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def span(id, parent, seq, kind, name, *, agent="orchestrator", start=0.0, end=None, input=None, output=None,
         model=None, ok=True, error=None, tin=None, tout=None, tcache=None, cost=None, latency=None):
    return {"id": id, "turn_id": "turn-1", "parent_id": parent, "seq": seq, "kind": kind, "name": name, "agent": agent,
            "model": model, "started_at": at(start), "ended_at": at(start + 1 if end is None else end),
            "latency_ms": latency, "input": input, "output": output, "input_tokens": tin, "output_tokens": tout,
            "cache_read_tokens": tcache, "cost_usd": cost, "ok": ok, "error": error}


def turn(**kw):
    row = {"turn_id": "turn-1", "session_id": "s1", "organization_id": ORG, "user_id": USER, "email": "fm@example.com",
           "question": "How many work orders at B-301 in August?", "answer": "758 work orders.", "route": "single:wo",
           "started_at": at(0), "ended_at": at(10), "latency_ms": 10000, "llm_calls": 2, "tool_calls": 1,
           "cost_usd": Decimal("0.050000"), "cost_complete": True, "input_tokens": 2000, "output_tokens": 100,
           "cache_read_tokens": 0, "models": ["claude-sonnet-5"], "tools": ["answer_from_records"], "ok": True,
           "error": None, "feedback_rating": None, "feedback_comment": None, "feedback_by": None,
           "feedback_at": None, "created_at": at(11)}
    row.update(kw)
    return row


LOOP_IN_1 = {"messages": 3, "last_human_message": "How many work orders at B-301 in August?", "tool_results_since_last_human": 0}
LOOP_IN_2 = {"messages": 5, "last_human_message": "How many work orders at B-301 in August?", "tool_results_since_last_human": 1}


def loop_tree():
    """The shape svc-deepagents' trace writes for a stream-loop turn: model calls under an 'orchestrator loop'
    agent span (trace.on_model_start), the tool on the root (trace.on_activity), its query under the tool
    (trace.on_sql)."""
    return [
        span("root", None, 0, "turn", "turn", start=0, end=10, input={"message": "How many…", "mode": "stream"},
             output={"answer": "758 work orders."}, latency=10000),
        span("prompt", "root", 1, "stage", "prompt assembled", start=0.1,
             input={"user_message": "How many…", "system_prompt": "SYSTEM", "page_context": None},
             output={"system_prompt_chars": 6}),
        span("loop", "root", 2, "agent", "orchestrator loop", agent="orchestrator loop", start=0.2, end=9, cost=0.9),
        span("m1", "loop", 3, "llm", "model", agent="orchestrator loop", start=0.2, end=2, model="claude-sonnet-5",
             input=LOOP_IN_1, output={"content": "", "tool_calls": [{"name": "answer_from_records", "args": {"q": "wo"}}]},
             tin=900, tout=40, tcache=0, cost=0.004, latency=1800),
        span("t1", "root", 4, "tool", "answer_from_records", agent="tool:wo_engine", start=2, end=5,
             input={"question": "work orders B-301 August"}, output={"total": 758}),
        span("q1", "t1", 5, "db", "records: work_orders", agent="tool:wo_engine", start=3, end=3.1,
             input={"sql": "SELECT count(*) FROM work_orders", "params": {}, "label": "records: work_orders"},
             output={"row_count": 1, "rows": [{"count": 758}]}),
        span("m2", "loop", 6, "llm", "model", agent="orchestrator loop", start=5, end=9, model="claude-sonnet-5",
             input=LOOP_IN_2, output={"content": "758 work orders.", "tool_calls": []},
             tin=1100, tout=60, tcache=0, cost=0.005, latency=4000),
    ]


def ids(walked):
    return [s["id"] for s, _, _ in walked]


def roles(record):
    return [m["role"] for m in record["messages"]]


def test_agent_groups_are_transparent_for_order():
    walked = n.walk(loop_tree(), "root")
    assert ids(walked) == ["root", "prompt", "loop", "m1", "t1", "q1", "m2"]
    depth = {s["id"]: d for s, d, _ in walked}
    group = {s["id"]: g for s, _, g in walked}
    assert depth["m1"] == depth["t1"] == depth["m2"] == 1 and depth["q1"] == 2
    assert group["m1"] == group["m2"] == "orchestrator loop" and group["t1"] is None


def test_order_is_depth_first_by_seq_not_by_timestamp():
    spans = loop_tree()
    for s in spans:
        if s["id"] == "m2":
            s["started_at"] = at(0.05)       # back-computed from latency, earlier than everything (Span.close)
    assert ids(n.walk(spans, "root")) == ["root", "prompt", "loop", "m1", "t1", "q1", "m2"]


def test_repeated_seq_breaks_ties_by_start_then_id():
    spans = [span("root", None, 0, "turn", "turn"), span("b", "root", 4, "tool", "x", start=2),
             span("a", "root", 4, "tool", "x", start=3), span("c", "root", 4, "tool", "x", start=2)]
    assert ids(n.walk(spans, "root")) == ["root", "b", "c", "a"]


def test_an_orphan_and_a_cycle_hang_from_the_root():
    spans = [span("root", None, 0, "turn", "turn"), span("o", "gone", 3, "tool", "x"),
             span("c1", "c2", 4, "tool", "y"), span("c2", "c1", 5, "tool", "z")]
    walked = n.walk(spans, "root")
    assert ids(walked)[:2] == ["root", "o"] and set(ids(walked)) == {"root", "o", "c1", "c2"}
    assert all(d == 1 for s, d, _ in walked if s["id"] != "root")


def test_two_trees_keep_the_one_matching_the_turn_row():
    spans = [span("ra", None, 0, "turn", "turn", start=0, end=10), span("ka", "ra", 1, "tool", "a"),
             span("rb", None, 0, "turn", "turn", start=0, end=20), span("kb", "rb", 1, "tool", "b"),
             span("orphan", "gone", 2, "tool", "c")]
    kept, flags = n.pick_tree(turn(ended_at=at(10)), spans)
    assert {s["id"] for s in kept} == {"ra", "ka", "orphan"} and flags == ["duplicate_trees"]
    kept, _ = n.pick_tree(turn(ended_at=at(99)), spans)
    assert {s["id"] for s in kept} == {"rb", "kb", "orphan"}   # no match: the latest root


def test_reasoning_is_stripped_from_structures_json_and_python_reprs():
    payload = {"content": [{"type": "thinking", "thinking": "secret chain", "signature": "sigval123"},
                           {"type": "text", "text": "Two expire."}],
               "encrypted_content": "enc-xyz",
               "summary": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "Looked up certs"}]}],
               "repr": "[{'type': 'thinking', 'thinking': 'hidden plan', 'signature': 'abc'}, {'type': 'text', 'text': 'ok'}]",
               "json": json.dumps([{"type": "redacted_thinking", "data": "zzz"}, {"type": "text", "text": "fine"}])}
    out = n.strip_reasoning(payload)
    text = json.dumps(out)
    for leaked in ("secret chain", "sigval123", "enc-xyz", "hidden plan", "zzz"):
        assert leaked not in text, leaked
    assert out["content"][0] == n.REASONING_HIDDEN and out["content"][1]["text"] == "Two expire."
    assert out["summary"][0]["label"] == n.PUBLISHED_SUMMARY_LABEL
    assert out["summary"][0]["summary"][0]["text"] == "Looked up certs"
    assert n.strip_reasoning("plain text") == "plain text"
    assert payload["encrypted_content"] == "enc-xyz"


def test_model_card_lists_unavailable_fields_and_never_invents_them():
    bare = span("x", "root", 1, "llm", "analyst", model=None, input={"system_prompt": "S", "user_message": "U"},
                output={"model_output": "ok"})
    card = n.model_call_card(bare, turn_id="turn-1", group=None, prompt_ref=None)
    assert card["provider"] is None and card["model"] is None and card["total_cost"] is None
    for field in n.ALWAYS_UNAVAILABLE + ("model", "model_parameters", "input_tokens", "output_tokens",
                                         "cache_read_tokens", "latency", "total_cost"):
        assert field in card["unavailable"], field
    assert card["cost_status"] == "unavailable" and card["usage"]["total_tokens"] is None
    unpriced = dict(bare, model="mystery-model", input_tokens=10, output_tokens=2)
    assert n.model_call_card(unpriced, turn_id="turn-1", group=None, prompt_ref=None)["cost_status"] == "unpriced"
    priced = dict(unpriced, cost_usd=Decimal("0.001"), latency_ms=900, cache_read_tokens=0,
                  input={"system_prompt": "S", "user_message": "U", "params": {"max_tokens": 900}})
    p = n.model_call_card(priced, turn_id="turn-1", group=None, prompt_ref=None)
    assert p["cost_status"] == "priced" and p["total_cost"] == 0.001 and p["latency_s"] == 0.9
    assert p["usage"] == {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 0, "total_tokens": 12}
    assert p["model_parameters"] == {"max_tokens": 900} and "model_parameters" not in p["unavailable"]
    assert p["request_kind"] == "exchange" and p["request_complete"] is True and p["label"] == "analyst"
    assert p["cot_note"] == n.COT_CARD_NOTE and p["status"] == "ok" and p["truncated"] is False


def test_loop_calls_are_marked_summary_only_and_point_at_the_prompt_note():
    m1 = next(s for s in loop_tree() if s["id"] == "m1")
    card = n.model_call_card(m1, turn_id="turn-1", group="orchestrator loop", prompt_ref="prompt")
    assert card["request_kind"] == "loop_summary" and card["request_complete"] is False
    assert card["prompt_ref"] == "prompt" and card["label"] == "tool decision"
    assert card["operation"] == "orchestrator loop › model"
    m2 = next(s for s in loop_tree() if s["id"] == "m2")
    assert n.model_call_card(m2, turn_id="turn-1", group="orchestrator loop", prompt_ref="prompt")["label"] == "answer synthesis"


def test_ledger_only_calls_have_no_request_or_response():
    ledger = span("l", "t1", 7, "llm", "planner", agent="tool:wo_engine", model="claude-sonnet-5", tin=13000, tout=200,
                  cost=0.029, latency=2500)
    card = n.model_call_card(ledger, turn_id="turn-1", group=None, prompt_ref="prompt")
    assert card["request_kind"] == "none" and card["request"] is None and card["response"] is None
    assert card["label"] == "planner" and card["prompt_ref"] is None
    assert {"request", "response"} <= set(card["unavailable"])


def test_a_router_is_a_routing_decision_model_call():
    router = span("r", "root", 1, "router", "router", agent="agent_router", model="claude-haiku-4-5",
                  input={"user_message": "x"}, output={"agent": "compliance", "also": [], "reason": "certs"})
    card = n.model_call_card(router, turn_id="turn-1", group=None, prompt_ref=None)
    assert card["call_kind"] == "router" and card["label"] == "routing decision"
    assert card["response"] == {"agent": "compliance", "also": [], "reason": "certs"}


def test_cards_are_redacted_reasoning_free_and_flag_cut_payloads():
    s = span("a", "root", 1, "llm", "analyst", model="claude-sonnet-5", ok=False, error="401 Bearer abcdefghijklmnop",
             input={"system_prompt": "S", "user_message": "my api_key=k-123", "params": {"api_key": "k-9"}},
             output=[{"type": "thinking", "thinking": "secret chain", "signature": "sig"},
                     {"type": "text", "text": "x… [900 more chars]"}])
    card = n.model_call_card(s, turn_id="turn-1", group=None, prompt_ref=None)
    blob = json.dumps(card)
    for leaked in ("k-123", "k-9", "secret chain", "abcdefghijklmnop"):
        assert leaked not in blob, leaked
    assert card["status"] == "error" and card["status_message"] == "401 Bearer [redacted]" and card["truncated"] is True


def test_trace_id_is_the_turn_id():
    rec = n.normalize_turn(turn(), loop_tree())
    assert rec["id"] == rec["trace_id"] == "turn-1" and rec["normalizer_version"] == n.NORMALIZER_VERSION


def test_a_simple_turn_reads_user_tool_call_tool_result_assistant():
    spans = [span("root", None, 0, "turn", "turn"), span("t", "root", 1, "tool", "list_certificates", output={"rows": 2})]
    assert roles(n.normalize_turn(turn(), spans)) == ["user", "tool_call", "tool_result", "assistant"]


def test_a_loop_turn_reads_in_the_order_it_ran():
    rec = n.normalize_turn(turn(), loop_tree())
    assert roles(rec) == ["user", "system_note", "model_call", "tool_call", "tool_result", "db_query", "model_call", "assistant"]
    assert [m["label"] for m in rec["messages"] if m["role"] == "model_call"] == ["tool decision", "answer synthesis"]
    assert rec["messages"][0]["text"] == "How many work orders at B-301 in August?"
    assert rec["messages"][-1]["text"] == "758 work orders." and rec["messages"][-1]["subtype"] == "answer"
    assert rec["groups"][0]["name"] == "orchestrator loop" and rec["groups"][0]["cost_is_whole_run"] is True
    assert "inferred_rewrite" not in rec["flags"] and rec["messages"][-1]["rewrite"] is None


def test_every_span_kind_maps_to_its_role():
    spans = [span("root", None, 0, "turn", "turn"),
             span("r", "root", 1, "router", "router", agent="agent_router", model="claude-haiku-4-5", output={"agent": "wo"}),
             span("p", "root", 2, "plan", "plan: 1 step", input={"source": "router", "question": "q"},
                  output={"plan": {"steps": [{"target": "wo"}]}, "rejected": None}),
             span("s", "p", 3, "step", "verify claims", output={"ok": True}),
             span("s2", "p", 4, "step", "wo", output={"ok": True}),
             span("st", "root", 5, "stage", "fetch", agent="compliance", output={"rows": 12}),
             span("odd", "root", 6, "mystery", "thing")]
    rec = n.normalize_turn(turn(), spans)
    got = [(m["role"], m.get("subtype"), m.get("call_kind")) for m in rec["messages"]]
    assert got == [("user", None, None), ("model_call", None, "router"), ("plan", None, None), ("step", "check", None),
                   ("step", "step", None), ("system_note", "stage", None), ("system_note", "unknown_kind", None),
                   ("assistant", "answer", None)]
    assert rec["messages"][6]["label"] == "mystery: thing"
    assert set(roles(rec)) <= set(n.ROLES)


def test_a_tool_call_and_its_result_share_one_span():
    rec = n.normalize_turn(turn(), loop_tree())
    i = next(k for k, m in enumerate(rec["messages"]) if m["role"] == "tool_call")
    call, result = rec["messages"][i], rec["messages"][i + 1]
    assert result["role"] == "tool_result" and call["span_id"] == result["span_id"] == "t1"
    assert call["anchor"] is None and result["anchor"] == {"turn_id": "turn-1", "span_id": "t1", "field": "output"}
    assert call["arguments"] == {"question": "work orders B-301 August"} and result["content"] == {"total": 758}


def test_tool_requests_link_to_tool_spans_by_name_and_order():
    spans = loop_tree()
    m1 = next(s for s in spans if s["id"] == "m1")
    m1["output"] = {"content": "", "tool_calls": [{"name": "answer_from_records", "args": {}},
                                                  {"name": "list_vendor_accreditations", "args": {}}]}
    rec = n.normalize_turn(turn(), spans)
    call = next(m for m in rec["messages"] if m["role"] == "tool_call")
    assert call["requested_by"] == "m1" and "unexecuted_tool_requests" in rec["flags"]
    assert "unexecuted_tool_requests" not in n.normalize_turn(turn(), loop_tree())["flags"]


def test_concurrent_same_name_tools_are_flagged():
    spans = [span("root", None, 0, "turn", "turn"), span("a", "root", 1, "tool", "search", start=1, end=3),
             span("b", "root", 2, "tool", "search", start=2, end=4)]
    assert "concurrent_same_name_tools" in n.normalize_turn(turn(), spans)["flags"]


def test_loop_model_messages_carry_cards_pointing_at_the_prompt_note():
    rec = n.normalize_turn(turn(), loop_tree())
    first = next(m for m in rec["messages"] if m["role"] == "model_call")
    assert first["model_call"]["prompt_ref"] == "prompt" and first["model_call"]["request_complete"] is False
    assert [c["span_id"] for c in rec["model_calls"]] == ["m1", "m2"]
    note = rec["messages"][1]
    assert note["subtype"] == "prompt" and note["label"] == "What the model was given" and note["collapse"] is True


def test_agent_group_cost_is_never_added_to_its_children():
    rec = n.normalize_turn(turn(cost_usd=Decimal("0.050000")), loop_tree())
    assert rec["usage"]["cost_usd"] == 0.05 and rec["groups"][0]["cost_usd"] == 0.9
    assert [c["total_cost"] for c in rec["model_calls"]] == [0.004, 0.005]
    assert rec["usage"]["total_tokens"] == 2100 and rec["usage"]["llm_calls"] == 2


def test_the_reasoning_boundary_is_stated_on_every_record():
    rec = n.normalize_turn(turn(), loop_tree())
    assert rec["cot_boundary"] == n.COT_NOTICE
    assert all(m["cot_note"] == n.COT_CARD_NOTE for m in rec["messages"] if m["role"] == "model_call")
    assert n.normalize_turn(turn(), [])["cot_boundary"] == n.COT_NOTICE


def test_thinking_blocks_never_reach_the_timeline():
    spans = [span("root", None, 0, "turn", "turn"),
             span("a", "root", 1, "llm", "analyst", agent="compliance", model="claude-sonnet-5",
                  input={"system_prompt": "S", "user_message": "U"},
                  output=[{"type": "thinking", "thinking": "secret chain", "signature": "sigval123"},
                          {"type": "text", "text": "Two expire."}]),
             span("m", "root", 2, "llm", "model", model="claude-sonnet-5", input=LOOP_IN_1,
                  output={"content": "[{'type': 'thinking', 'thinking': 'hidden plan', 'signature': 'abc'}, "
                                     "{'type': 'text', 'text': 'ok'}]", "tool_calls": [], "encrypted_content": "enc-xyz"})]
    text = json.dumps(n.normalize_turn(turn(), spans), default=str)
    for leaked in ("secret chain", "sigval123", "hidden plan", "enc-xyz"):
        assert leaked not in text, leaked
    assert n.REASONING_HIDDEN in text


def test_results_over_240_chars_start_collapsed():
    spans = [span("root", None, 0, "turn", "turn"), span("long", "root", 1, "tool", "a", output="x" * 241),
             span("short", "root", 2, "tool", "b", output="y" * 240)]
    results = [m for m in n.normalize_turn(turn(), spans)["messages"] if m["role"] == "tool_result"]
    assert [m["collapse"] for m in results] == [True, False] and results[0]["chars"] == 241


def test_a_purged_turn_still_reads_question_and_answer():
    rec = n.normalize_turn(turn(), [])
    assert roles(rec) == ["user", "assistant"] and "spans_purged" in rec["flags"]
    assert rec["messages"][0]["anchor"]["field"] == "question" and rec["model_calls"] == []


def test_a_post_loop_rewrite_is_flagged_inferred_by_comparison():
    rewritten = n.normalize_turn(turn(answer="758 work orders.\n\n_Automated accuracy check: flagged_"), loop_tree())
    assert "inferred_rewrite" in rewritten["flags"]
    assert rewritten["messages"][-1]["rewrite"] == {"source": "inferred", "loop_answer": "758 work orders."}
    spaced = n.normalize_turn(turn(answer="  758   work\norders. "), loop_tree())
    assert "inferred_rewrite" not in spaced["flags"]                        # whitespace is not a rewrite


def test_no_rewrite_is_inferred_without_a_fair_comparison():
    def last_loop(output):
        spans = loop_tree()
        next(s for s in spans if s["id"] == "m2")["output"] = output
        return spans

    other = "Something else entirely."
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer=other), last_loop(
        {"content": "", "tool_calls": [{"name": "answer_from_records", "args": {}}]}))["flags"]   # ended on a tool call
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer=other), last_loop(
        {"content": "758… [9000 more chars]", "tool_calls": []}))["flags"]                       # content was cut
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer=other), last_loop(
        {"content": "   ", "tool_calls": []}))["flags"]                                          # nothing said
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer=other, ok=False), loop_tree())["flags"]
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer="gate_interrupt"), loop_tree())["flags"]
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer="y" * n.ANSWER_CHARS), loop_tree())["flags"]
    no_loop = [s for s in loop_tree() if s["id"] not in ("loop", "m1", "m2")]
    assert "inferred_rewrite" not in n.normalize_turn(turn(answer=other), no_loop)["flags"]
    blocks = last_loop({"content": "[{'type': 'thinking', 'thinking': 'x', 'signature': 's'}, "
                                   "{'type': 'text', 'text': '758 work orders.'}]", "tool_calls": []})
    assert "inferred_rewrite" not in n.normalize_turn(turn(), blocks)["flags"]                    # text blocks compared


def test_provenance_is_inferred_at_read_time_and_labelled():
    rec = n.normalize_turn(turn(), loop_tree())
    m = rec["meta"]
    assert (m["channel"], m["channel_source"], m["classifier_version"]) == ("chat", "inferred", "inferred-1")
    cell = "What is the status of the Gas Safety Certificate at Kingsway House, and who services it?"
    rec = n.normalize_turn(turn(question=cell), [])
    assert (rec["meta"]["channel"], rec["meta"]["origin"]) == ("ui_action", "ui.matrix_cell:ok")
    assert rec["messages"][0]["channel"] == "ui_action" and rec["messages"][0]["origin"] == "ui.matrix_cell:ok"
    support = n.normalize_turn(turn(question="how?"), [], activity_input={
        "payload": {"message": "how?", "extra_context": "This is a Hoistra support session. Guide."}})
    assert support["meta"]["channel"] == "support" and support["meta"]["channel_source"] == "inferred"


def test_legacy_wrapped_question_is_repaired_and_flagged():
    wrapped = "## Page\nBuildings list" + n.QUESTION_MARKER + "Which buildings are at risk?"
    rec = n.normalize_turn(turn(question=wrapped), [])
    assert rec["input"] == "Which buildings are at risk?" and "question_repaired" in rec["flags"]
    long = n.normalize_turn(turn(question="This is a Hoistra support session. " + "g" * 4000), [])
    assert "question_wrapped" in long["flags"] and long["meta"]["channel"] == "support"
    by_activity = n.normalize_turn(turn(question=wrapped), [], activity_input={"payload": {"message": "Typed words"}})
    assert by_activity["input"] == "Typed words"


def test_truncation_markers_are_flagged():
    spans = [span("root", None, 0, "turn", "turn"), span("a", "root", 1, "tool", "a", output="rows… [1200 more chars]"),
             span("b", "root", 2, "tool", "b", input={"_truncated": '{"q": "x'})]
    rec = n.normalize_turn(turn(), spans)
    assert "truncated" in rec["flags"]
    assert [m["truncated"] for m in rec["messages"] if m["role"] == "tool_result"] == [True, True]


def test_normalize_never_mutates_its_inputs():
    t, spans = turn(), loop_tree()
    meta = {"channel": "chat", "channel_source": "recorded", "origin_params": {"api_key": "x"}}
    act = {"payload": {"message": "How many?", "extra_context": "ctx"}}
    before = copy.deepcopy((t, spans, meta, act))
    n.normalize_turn(t, spans, meta=meta, activity_input=act)
    n.normalize_turn(t, spans, activity_input=act)
    assert (t, spans, meta, act) == before


def test_recorded_meta_feedback_flags_and_permalink():
    meta = {"channel": "golden", "channel_source": "recorded", "classifier_version": None, "origin": "golden.case",
            "origin_params": {"building": "B-301"}, "page": "cc", "context_text": "api_key=abc123 page",
            "asker_role": "superadmin", "asker_scope": {"view_as": True}, "golden_run_id": "6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e",
            "golden_case_key": "B-07", "rep": 2}
    conv = {"key": "s:golden-x", "session_kind": "synthetic", "session_label": "Golden run · B-07 · rep 2",
            "turn_index": 2, "turn_count": 3, "attempt": 1, "attempt_source": None, "retry_of": None,
            "thread_deleted": True}
    rec = n.normalize_turn(turn(session_id="golden-x", feedback_rating="down", feedback_comment="Wrong period",
                                feedback_by=USER, cost_complete=False, ok=False, error="boom"), [], meta=meta,
                           conversation=conv)
    m = rec["meta"]
    assert (m["channel"], m["channel_source"], m["origin"], m["page"]) == ("golden", "recorded", "golden.case", "cc")
    assert (m["turn_index"], m["turn_count"], m["case_key"], m["rep"]) == (2, 3, "B-07", 2)
    assert m["context_text"] == "api_key=[redacted] page" and m["asker"]["role"] == "superadmin"
    assert m["run_id"] == "6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e" and m["conversation_key"] == "s:golden-x"
    assert rec["feedback"] == {"rating": "down", "comment": "Wrong period", "by_asker": True, "at": None}
    for f in ("view_as", "thread_deleted", "cost_incomplete", "failed"):
        assert f in rec["flags"], f
    assert rec["permalink"] == "?tl=conversations&run=6f0c1d5e-4a7b-4c1e-9d0e-0f1a2b3c4d5e&conv=s%3Agolden-x&turn=turn-1"
    assert rec["messages"][-1]["subtype"] == "error"
    live = n.normalize_turn(turn(), [], conversation={"key": "s:s1", "run_id": "live"})
    assert live["permalink"] == "?tl=conversations&run=live&conv=s%3As1&turn=turn-1"
    assert live["meta"]["attempt"] == 1 and live["meta"]["attempt_source"] is None


def test_features_come_from_the_turn_row():
    f = n.features(turn(tools=["retrieve_docs", "answer_from_records"], input_tokens=10, output_tokens=5, cache_read_tokens=1))
    assert f == {"turn_count": 2, "tool_call_count": 1, "distinct_tools": 2, "has_retrieval": 1, "tokens": 16,
                 "llm_calls": 2, "cost_usd": 0.05, "latency_ms": 10000}
    assert n.features(turn(tools=["answer_from_records"]))["has_retrieval"] == 0
    rec = n.normalize_turn(turn(), loop_tree())
    assert rec["features"]["has_retrieval"] == 1            # a db span counts
    assert rec["text"].startswith("user: How many work orders") and "\nassistant: 758 work orders." in rec["text"]


def test_span_messages_carry_the_block_text_the_timeline_shows():
    import pytest

    spans = loop_tree() + [
        span("p", "root", 7, "plan", "plan: 1 step", input={"source": "router", "question": "q"},
             output={"plan": {"steps": [{"target": "wo", "api_key": "k-1"}]}, "rejected": None}),
        span("s", "p", 8, "step", "wo", output={"ok": True, "note": "Bearer abcdefghijklmnop"})]
    by_id = {s["id"]: s for s in spans}
    rec = n.normalize_turn(turn(), spans)
    block = {(m["role"], m["span_id"]): m["block"] for m in rec["messages"]}
    for role, sid in (("tool_result", "t1"), ("db_query", "q1"), ("plan", "p"), ("step", "s"),
                      ("model_call", "m1"), ("model_call", "m2")):
        assert block[(role, sid)] == n.block_text(by_id[sid], "output"), sid
    assert block[("tool_result", "t1")] == '{\n  "total": 758\n}'
    assert block[("db_query", "q1")] == '[\n  {\n    "count": 758\n  }\n]'
    assert "k-1" not in block[("plan", "p")] and "abcdefghijklmnop" not in block[("step", "s")]
    assert block[("tool_call", "t1")] is None
    assert block[("user", "root")] is None and block[("assistant", "root")] is None
    assert n.block_text(span("e", "root", 11, "tool", "x"), "output") == ""
    with pytest.raises(ValueError):
        n.block_text(by_id["t1"], "question")
