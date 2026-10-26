"""Proposer input (spec §7.5): question, answer, router/plan/step/tool/db spans in seq order up to 24,000 characters;
never llm spans, the assembled prompt, labels, notes, verdicts or the asker's email. The question is the repaired one,
exactly as the Conversations screen shows it, and every location holds exactly the text shown. Pure: no database."""
from __future__ import annotations

import copy

from trace_lab import annotations, normalize

TURN = {"question": "How many open work orders?", "answer": "There are 12 open work orders.", "ok": True,
        "email": "asker@x.test", "feedback_comment": "LABEL-LEAK", "note": "NOTE-LEAK", "verdict": "wrong"}


def _span(seq, kind, name, *, agent=None, input=None, output=None, error=None, parent_id=None):
    return {"id": f"00000000-0000-4000-8000-{seq:012d}", "seq": seq, "kind": kind, "name": name, "agent": agent,
            "input": input, "output": output, "error": error, "started_at": None, "parent_id": parent_id}


def test_model_spans_and_the_assembled_prompt_are_left_out():
    spans = [_span(1, "llm", "model", output={"content": "SECRET-MODEL-MESSAGE"}),
             _span(2, "stage", "prompt assembled", input={"system_prompt": "SYSTEM-PROMPT"}),
             _span(3, "tool", "query_table", agent="udr", input={"table": "work_orders"}, output=[{"open": 12}]),
             _span(4, "agent", "compliance")]
    text, loc = normalize.evidence_text(TURN, spans, [])
    assert "SECRET-MODEL-MESSAGE" not in text and "SYSTEM-PROMPT" not in text
    assert '[S1] tool "query_table" (udr)' in text
    assert set(loc) == {"question", "answer", "S1.in", "S1"}
    assert loc["S1"]["text"] in text and loc["S1"]["span_id"].endswith("000000000003") and loc["S1"]["field"] == "output"
    assert loc["S1.in"]["field"] == "input"


def test_the_turn_carries_no_labels_notes_verdicts_or_email():
    text, _ = normalize.evidence_text(TURN, [], [])
    for leak in ("LABEL-LEAK", "NOTE-LEAK", "asker@x.test"):
        assert leak not in text
    assert text.startswith("QUESTION\nHow many open work orders?\n\nANSWER\nThere are 12 open work orders.")


def test_spans_stop_at_the_cap_and_every_handle_is_on_screen():
    spans = [_span(i, "tool", f"t{i}", output={"rows": "x" * 2500}) for i in range(1, 30)]
    text, loc = normalize.evidence_text(TURN, spans, [], cap=8000)
    assert len(text) <= 8000
    shown = [k for k in loc if k.startswith("S") and "." not in k]
    assert shown and all(f"[{k}]" in text for k in shown)
    assert len(shown) < 29


def test_earlier_turns_and_a_failure_are_shown():
    earlier = [{"question": "q0", "answer": "a0"}, {"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}]
    text, _ = normalize.evidence_text(dict(TURN, ok=False, error="timeout"), [], earlier)
    assert text.startswith("EARLIER IN THIS CONVERSATION\nQ: q1\nA: a1\nQ: q2\nA: a2")
    assert "THE TURN FAILED: timeout" in text


def test_the_answer_is_cut_at_8000_and_its_location_holds_exactly_what_is_shown():
    text, loc = normalize.evidence_text(dict(TURN, answer="a" * 9000), [], [])
    assert loc["answer"]["text"] == "a" * 8000 and ("a" * 8001) not in text


# ── what the real code adds to the source ─────────────────────────────────────────────────

def test_the_kinds_are_the_ones_the_chat_records_and_there_is_no_gate_span():
    assert normalize.EVIDENCE_KINDS == ("router", "plan", "step", "tool", "db")
    spans = [_span(1, "gate", "step gates", output={"verdict": "GATE-SPAN"}), _span(2, "router", "route", output={"route": "wo"})]
    text, loc = normalize.evidence_text(TURN, spans, [])
    assert "GATE-SPAN" not in text and '[S1] router "route"' in text and set(loc) == {"question", "answer", "S1"}


def test_a_wrapped_question_is_read_as_the_asker_wrote_it_exactly_as_the_screen_shows_it():
    wrapped = "Page context: Assets > Boilers\nSelected building: B-301" + normalize.QUESTION_MARKER + "How many open work orders?"
    text, loc = normalize.evidence_text(dict(TURN, question=wrapped), [], [])
    assert loc["question"]["text"] == "How many open work orders?"
    assert text.startswith("QUESTION\nHow many open work orders?\n\nANSWER") and "Page context" not in text


def test_the_recorded_message_of_the_activity_row_wins_when_the_caller_passes_it():
    turn = dict(TURN, question="Page context: x")
    activity = {"payload": {"message": "How many open work orders?"}, "created_at": None}
    text, loc = normalize.evidence_text(turn, [], [], activity_input=activity)
    assert loc["question"]["text"] == "How many open work orders?"
    meta = {"asker_question": "What the asker typed"}
    _, loc = normalize.evidence_text(turn, [], [], meta=meta, activity_input=activity)
    assert loc["question"]["text"] == "What the asker typed"


def test_earlier_questions_are_repaired_the_same_way():
    earlier = [{"question": "ctx" + normalize.QUESTION_MARKER + "q1", "answer": "a1"}]
    text, _ = normalize.evidence_text(TURN, [], earlier)
    assert text.startswith("EARLIER IN THIS CONVERSATION\nQ: q1\nA: a1")


def test_secrets_never_reach_the_proposer_and_the_location_text_is_the_redacted_text():
    token = "Bearer abcdefghijklmnop123456"
    turn = dict(TURN, question="Use " + token + " to fetch it", answer="Done with " + token + ".")
    spans = [_span(1, "tool", "fetch", input={"authorization": token, "table": "t"}, output="rows for " + token)]
    earlier = [{"question": "q " + token, "answer": "a " + token}]
    text, loc = normalize.evidence_text(turn, spans, earlier)
    assert "abcdefghijklmnop123456" not in text
    for handle in loc.values():
        assert "abcdefghijklmnop123456" not in handle["text"] and handle["text"] in text


def test_every_location_is_text_the_screen_shows_for_that_block():
    wrapped = "Page context: Assets" + normalize.QUESTION_MARKER + "How many open work orders?"
    turn = {"turn_id": "turn-1", "session_id": "s1", "organization_id": "o", "question": wrapped,
            "answer": "There are 12 open work orders.", "ok": True, "started_at": None}
    spans = [_span(0, "turn", "turn", agent="orchestrator"),
             _span(1, "plan", "plan", agent="orchestrator", output={"plan": {"steps": ["count work orders"]}},
                   parent_id="00000000-0000-4000-8000-000000000000"),
             _span(2, "tool", "query_table", agent="udr", input={"table": "work_orders"}, output=[{"open": 12}],
                   parent_id="00000000-0000-4000-8000-000000000000"),
             _span(3, "db", "records: work_orders", agent="udr", input={"sql": "select 1"},
                   output={"rows": [{"open": 12}], "row_count": 1}, parent_id="00000000-0000-4000-8000-000000000002")]
    record = normalize.normalize_turn(turn, spans)
    blocks = annotations.block_texts(record)
    _, loc = normalize.evidence_text(turn, spans, [])
    outputs = [k for k, v in loc.items() if v["field"] != "input"]
    assert {"question", "answer"} <= set(outputs) and len(outputs) >= 4
    for key in outputs:
        shown = blocks[annotations.block_key(loc[key]["span_id"], loc[key]["field"])]["text"]
        assert shown.startswith(loc[key]["text"]), (key, shown[:80], loc[key]["text"][:80])


def test_long_fields_are_cut_and_the_location_keeps_the_cut_text():
    spans = [_span(1, "tool", "t", input={"q": "i" * 3000}, output="o" * 5000)]
    _, loc = normalize.evidence_text(dict(TURN, question="q" * 6000), spans, [])
    assert len(loc["question"]["text"]) == 4000
    assert len(loc["S1.in"]["text"]) == 1500 and len(loc["S1"]["text"]) == 3000


def test_a_step_with_an_error_and_no_output_is_shown_without_a_location_for_the_missing_side():
    spans = [_span(1, "step", "check s1", error="boom password=hunter2secret", input=None, output=None)]
    text, loc = normalize.evidence_text(TURN, spans, [])
    assert '[S1] step "check s1"' in text and "error:" in text and "hunter2secret" not in text
    assert set(loc) == {"question", "answer"}


def test_the_inputs_are_not_changed():
    spans = [_span(2, "tool", "b", output={"x": 1}), _span(1, "tool", "a", output={"y": 2})]
    earlier = [{"question": "q", "answer": "a"}]
    before = copy.deepcopy((TURN, spans, earlier))
    normalize.evidence_text(TURN, spans, earlier)
    assert (TURN, spans, earlier) == before


def test_steps_are_read_in_sequence_order_whatever_order_they_arrive_in():
    spans = [_span(2, "tool", "second", output="two"), _span(1, "tool", "first", output="one")]
    text, loc = normalize.evidence_text(TURN, spans, [])
    assert text.index('"first"') < text.index('"second"')
    assert loc["S1"]["text"] == "one" and loc["S2"]["text"] == "two"
