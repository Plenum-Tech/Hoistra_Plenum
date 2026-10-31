"""A wrong verdict needs a note on the first failure and a verbatim quote at a stated occurrence; a right
verdict is stored as "no failure observed" with nothing highlighted; the anchor copies the step's name,
kind and agent so the note survives the span purge (spec "Open coding ..." 2.1, 2.2, 2.7). Pure rules and
the quote check against a normalized turn; the database paths are in the _sql twins of these files."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from trace_lab import annotations, review_errors
from trace_lab.annotations import Anchor

SPAN = str(uuid.uuid4())
# Messages as normalize_turn emits them: span messages anchored on output carry `block` (normalize.block_text);
# question, answer and tool-call messages carry None. A message anchored on a field that is not annotatable
# (here a made-up one) never becomes a block.
RECORD = {"messages": [
    {"role": "user", "anchor": {"turn_id": "turn-1", "span_id": None, "field": "question"}, "text": "How many WOs in Aug?",
     "block": None},
    {"role": "tool_call", "span_id": SPAN, "anchor": None, "arguments": {"month": "2026-08"}, "block": None},
    {"role": "tool_result", "span_id": SPAN, "anchor": {"turn_id": "turn-1", "span_id": SPAN, "field": "output"},
     "content": {"count": 758, "period": "2026-07"}, "block": '{\n  "count": 758,\n  "period": "2026-07"\n}'},
    {"role": "model_call", "span_id": "m1", "anchor": {"turn_id": "turn-1", "span_id": "m1", "field": "output"},
     "model_call": {"response": "There were 758 work orders."}, "block": "There were 758 work orders."},
    {"role": "model_call", "span_id": "m2", "anchor": {"turn_id": "turn-1", "span_id": "m2", "field": "output"},
     "model_call": {"response": None}, "block": ""},
    {"role": "system_note", "span_id": "n1", "anchor": {"turn_id": "turn-1", "span_id": "n1", "field": "answer_after"},
     "block": None},
    {"role": "assistant", "anchor": {"turn_id": "turn-1", "span_id": "root", "field": "answer"},
     "text": "There were 758 work orders in August, 758 in total.", "block": None},
]}


def test_locate_quote_counts_overlapping_occurrences():
    assert annotations.locate_quote("aaaa", "aa", 1) == 0
    assert annotations.locate_quote("aaaa", "aa", 2) == 1          # overlapping, as the browser counts
    assert annotations.locate_quote("aaaa", "aa", 3) == 2
    assert annotations.locate_quote("aaaa", "aa", 4) is None
    assert annotations.locate_quote("758 in August, 758 in total", "758", 2) == 15
    assert annotations.locate_quote("x", "", 1) is None
    assert annotations.locate_quote("x", "x", 0) is None
    assert annotations.locate_quote("x", "x", -1) is None


def test_block_texts_follow_the_normalized_messages():
    blocks = annotations.block_texts(RECORD)
    assert set(blocks) == {":question", f"{SPAN}:output", "m1:output", "m2:output", ":answer"}   # no tool args, no notes
    assert blocks[":answer"]["text"].startswith("There were 758")
    assert blocks[":question"]["text"] == "How many WOs in Aug?"
    assert blocks[f"{SPAN}:output"]["text"] == '{\n  "count": 758,\n  "period": "2026-07"\n}'   # the normalizer's block, as is
    assert blocks["m1:output"]["text"] == "There were 758 work orders."
    assert blocks["m2:output"]["text"] == ""                                          # an empty block: nothing to quote
    assert blocks[f"{SPAN}:output"]["span_id"] == SPAN and blocks[":answer"]["span_id"] is None
    assert annotations.block_key(None, "answer") == ":answer" and annotations.block_key("s", "answer") == ":answer"
    assert annotations.block_key("s", "output") == "s:output" and annotations.block_key(None, "question") == ":question"
    assert not hasattr(annotations, "BLOCK_SOURCE") and not hasattr(annotations, "as_text")  # the rule is the normalizer's


def test_the_first_message_of_a_block_wins_and_the_wrong_role_never_fills_a_turn_field():
    record = {"messages": [
        {"role": "assistant", "anchor": {"turn_id": "t", "span_id": None, "field": "question"}, "text": "not the question"},
        {"role": "user", "anchor": {"turn_id": "t", "span_id": None, "field": "question"}, "text": "the question"},
        {"role": "tool_result", "anchor": {"turn_id": "t", "span_id": "s1", "field": "output"}, "block": "first"},
        {"role": "step", "anchor": {"turn_id": "t", "span_id": "s1", "field": "output"}, "block": "second"},
        {"role": "tool_call", "anchor": {"turn_id": "t", "span_id": "s2", "field": "input"}, "block": None},
    ]}
    blocks = annotations.block_texts(record)
    assert blocks[":question"]["text"] == "the question" and blocks["s1:output"]["text"] == "first"
    assert set(blocks) == {":question", "s1:output"}
    assert annotations.block_texts({}) == {} and annotations.block_texts({"messages": None}) == {}


def test_a_wrong_verdict_without_a_note_or_an_anchor_is_refused():
    a = Anchor(None, "answer", "758 in August")
    for note in (None, "", "   "):
        with pytest.raises(review_errors.Invalid) as e:
            annotations.validate_verdict("wrong", note, a)
        assert e.value.reason == "note_required"
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", None)
    assert e.value.reason == "anchor_required"
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", Anchor(None, "answer", "   "))
    assert e.value.reason == "anchor_required"
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "counted July", Anchor(None, "output", "758"))
    assert e.value.reason == "span_not_in_turn"


def test_limits_and_malformed_anchors_are_refused_before_any_database_work():
    ok = Anchor(None, "answer", "758")
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("maybe", "x", ok)
    assert e.value.reason == "bad_verdict"
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "n" * (annotations.NOTE_MAX + 1), ok)
    assert e.value.reason == "note_required"
    assert annotations.validate_verdict("wrong", "n" * annotations.NOTE_MAX, ok)[0] == "n" * annotations.NOTE_MAX
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "x", Anchor(None, "answer", "q" * (annotations.QUOTE_MAX + 1)))
    assert e.value.reason == "anchor_required"
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("wrong", "x", Anchor(None, "feedback", "758"))
    assert e.value.reason == "anchor_required"
    for occurrence in (0, -1, 1.5, "2", None, True):
        with pytest.raises(review_errors.Invalid) as e:
            annotations.validate_verdict("wrong", "x", Anchor(None, "answer", "758", occurrence))
        assert e.value.reason == "anchor_required", occurrence


def test_a_right_verdict_is_no_failure_observed_with_nothing_highlighted():
    assert annotations.validate_verdict("right", "looks fine", None) == ("no failure observed", None)
    assert annotations.validate_verdict("right", None, None) == ("no failure observed", None)
    with pytest.raises(review_errors.Invalid) as e:
        annotations.validate_verdict("right", None, Anchor(None, "answer", "758"))
    assert e.value.reason == "anchor_on_right"


def test_a_turn_field_anchor_never_keeps_a_span_id():
    note, a = annotations.validate_verdict("wrong", "counted July", Anchor("root", "answer", "758", 2))
    assert a == Anchor(None, "answer", "758", 2) and note == "counted July"
    _, q = annotations.validate_verdict("wrong", "misread", Anchor("root", "question", "WOs"))
    assert q == Anchor(None, "question", "WOs", 1)
    _, s = annotations.validate_verdict("wrong", "bad step", Anchor("span-1", "input", "x"))
    assert s.span_id == "span-1" and s.field == "input"


def test_a_comment_needs_text_within_the_limit():
    assert annotations.validate_comment("Same failure as last week.") == "Same failure as last week."
    for bad in (None, "", "  \n "):
        with pytest.raises(review_errors.Invalid) as e:
            annotations.validate_comment(bad)
        assert e.value.reason == "note_required"
    with pytest.raises(review_errors.Invalid):
        annotations.validate_comment("c" * (annotations.NOTE_MAX + 1))


@pytest.fixture
def pinned(monkeypatch):
    spans = [{"id": SPAN, "turn_id": "turn-1", "name": "answer_from_records", "kind": "tool", "agent": "wo_engine"},
             {"id": "m2", "turn_id": "turn-1", "name": "model call", "kind": "llm", "agent": "orchestrator"}]

    async def fake_record(turn):
        return RECORD, spans

    monkeypatch.setattr(annotations, "_turn_record", fake_record)
    return lambda anchor: asyncio.run(annotations._pin({"turn_id": "turn-1"}, anchor))


def test_the_anchor_copies_the_step_name_kind_and_agent(pinned):
    pin = pinned(Anchor(SPAN, "output", '"period": "2026-07"'))
    assert (pin["sname"], pin["skind"], pin["sagent"], pin["start"]) == ("answer_from_records", "tool", "wo_engine", 20)
    assert (pin["span_id"], pin["field"], pin["quote"], pin["occ"]) == (SPAN, "output", '"period": "2026-07"', 1)
    turn = pinned(Anchor(None, "answer", "758", 2))
    assert (turn["span_id"], turn["sname"], turn["skind"], turn["sagent"], turn["start"]) == (None, "turn", "answer", None, 38)
    assert (turn["field"], turn["quote"], turn["occ"]) == ("answer", "758", 2)


def test_a_span_id_is_matched_whatever_its_case_and_stored_as_the_stored_id(pinned):
    pin = pinned(Anchor(SPAN.upper(), "output", "758"))
    assert pin["span_id"] == SPAN


def test_a_question_or_answer_anchor_is_the_turns_own_even_if_a_span_id_comes_with_it(pinned):
    pin = pinned(Anchor(SPAN, "answer", "758", 2))
    assert (pin["span_id"], pin["sname"], pin["skind"], pin["start"]) == (None, "turn", "answer", 38)
    assert pinned(Anchor("not-a-span", "question", "WOs"))["span_id"] is None


def test_a_quote_missing_at_its_occurrence_is_refused(pinned):
    with pytest.raises(review_errors.Invalid) as e:
        pinned(Anchor(None, "answer", "758", 3))
    assert e.value.reason == "quote_not_found"
    with pytest.raises(review_errors.Invalid) as e:
        pinned(Anchor(None, "answer", "seven hundred"))
    assert e.value.reason == "quote_not_found"
    with pytest.raises(review_errors.Invalid) as e:
        pinned(Anchor("m2", "output", "anything"))          # an empty block has nothing to quote
    assert e.value.reason == "quote_not_found"


def test_a_span_from_another_turn_is_refused(pinned):
    with pytest.raises(review_errors.Invalid) as e:
        pinned(Anchor(str(uuid.uuid4()), "output", "758"))
    assert e.value.reason == "span_not_in_turn"


def test_conversation_keys_select_their_turns_and_nothing_else():
    assert annotations.key_where("s:abc") == ("tt.session_id = :tl_key_v AND tt.session_id NOT LIKE 'rerun%'", {"tl_key_v": "abc"})
    assert annotations.key_where("r:turn-1") == ("tt.turn_id = :tl_key_v", {"tl_key_v": "turn-1"})
    assert annotations.key_where("t:turn-2")[1] == {"tl_key_v": "turn-2"}
    assert annotations.key_where("s:abc", "x")[0].startswith("x.session_id = ")
    for bad in ("x:1", "s:", "abc", "", ":", "S:abc"):
        with pytest.raises(review_errors.NotFound) as e:
            annotations.key_where(bad)
        assert e.value.reason == "turn_not_found"


def test_the_reviewed_filter_is_ids_for_the_conversation_list_never_a_join():
    sql, p = annotations.reviewed_predicate("b", "reviewed", {"turn-2", "turn-1"})
    assert sql == "b.turn_id = ANY(CAST(:tl_rev_ids AS text[]))" and p == {"tl_rev_ids": ["turn-1", "turn-2"]}
    assert annotations.reviewed_predicate("b", "mine", ["turn-1"])[0] == sql
    sql, p = annotations.reviewed_predicate("tt", "unreviewed", {"turn-1"})
    assert sql == "NOT (tt.turn_id = ANY(CAST(:tl_rev_ids AS text[])))" and p == {"tl_rev_ids": ["turn-1"]}
    assert annotations.reviewed_predicate("tt", "reviewed", set())[1] == {"tl_rev_ids": []}
    for mode in ("any", "", "all"):
        with pytest.raises(ValueError):
            annotations.reviewed_predicate("b", mode, set())
    with pytest.raises(ValueError):
        annotations.reviewed_predicate("b; DROP TABLE x", "reviewed", set())
    assert "plenum_cafm" not in sql and "tracelab" not in sql


def test_company_clause_scopes_to_one_company_or_means_the_platform_view():
    org = str(uuid.uuid4())
    assert annotations.company_clause(org, "a") == ("a.organization_id = CAST(:tl_org AS uuid)", {"tl_org": org})
    assert annotations.company_clause(org.upper(), "a")[1] == {"tl_org": org}
    assert annotations.company_clause(None, "a") == ("TRUE", {})
    with pytest.raises(ValueError):
        annotations.company_clause("not-a-uuid", "a")
    with pytest.raises(ValueError):
        annotations.company_clause(org, "a; DROP TABLE x")


def test_annotation_out_shapes_a_stored_version_for_the_api():
    row = {"id": uuid.uuid4(), "lineage_id": uuid.uuid4(), "version": 2, "supersedes": uuid.uuid4(), "kind": "verdict",
           "turn_id": "turn-1", "conversation_key": "s:abc", "reviewer_id": uuid.uuid4(), "reviewer_email": "a@x.com",
           "tombstone": False, "verdict": "wrong", "note": "Counted July.", "span_id": None, "field": "answer",
           "quote": "758", "quote_occurrence": 2, "quote_start": 38, "span_name": "turn", "span_kind": "answer",
           "span_agent": None, "blind": True, "created_at": None, "organization_id": uuid.uuid4()}
    out = annotations.annotation_out(row)
    assert out["id"] == str(row["id"]) and out["supersedes"] == str(row["supersedes"]) and out["version"] == 2
    assert out["anchor"] == {"span_id": None, "field": "answer", "quote": "758", "occurrence": 2, "start": 38,
                             "span_name": "turn", "span_kind": "answer", "span_agent": None}
    assert (out["verdict"], out["note"], out["blind"], out["tombstone"]) == ("wrong", "Counted July.", True, False)
    assert "organization_id" not in out
    assert annotations.annotation_out(None) is None
    right = annotations.annotation_out({**row, "verdict": "right", "quote": None, "field": None, "quote_occurrence": None,
                                        "quote_start": None})
    assert right["anchor"] is None


def test_a_head_race_is_recognised_by_its_constraint_name():
    class Cause(Exception):
        constraint_name = "ux_tl_ann_verdict_head"

    class Wrapper(Exception):
        def __init__(self, orig):
            super().__init__("duplicate key value")
            self.orig = orig

    assert annotations._constraint(Wrapper(Cause())) == "ux_tl_ann_verdict_head"
    assert annotations._constraint(Exception("violates unique constraint ux_tl_ann_lineage_head")) == "ux_tl_ann_lineage_head"
    assert annotations._constraint(Exception("violates unique constraint ux_tl_ann_supersedes")) == "ux_tl_ann_supersedes"
    assert annotations._constraint(Exception("something else")) is None
    assert set(annotations._HEAD_RACES) == {"ux_tl_ann_verdict_head", "ux_tl_ann_supersedes", "ux_tl_ann_lineage_head"}
