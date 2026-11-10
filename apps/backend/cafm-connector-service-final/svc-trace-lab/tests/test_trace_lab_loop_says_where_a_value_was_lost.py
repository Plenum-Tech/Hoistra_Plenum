"""A test that still fails says where each missing value was lost, and the next draft starts from that (10 Nov 2026).

R42, R44, R45 and R46 each came back Still failing for a different reason, and each took a person reading the rerun's
steps to find it: the data never had the work orders (R42: a code change), the answer left out what the read returned
(R44: a weak rule), the review step removed what the answer had added (R45: the reviewer's document), and the answer
had them in a shortened list (R46: Compare). Compare now follows each missing value through the rerun's own steps -
the reads, the step that wrote the answer, the review and its revision, the final answer - and names the first step
it is missing from. Agent: draft fix reads that: a value the data never had is a code change and no draft is made;
anything else is drafted again against the last answer and where it lost the value.
"""
from __future__ import annotations

import json

import pytest

from trace_lab import agent, golden, loop
from trace_lab.review_errors import Invalid

ITEMS = golden.parse_expected_values("WO-B-301-4560\nWO-B-301-4562\n!confirm no live")
DATA = json.dumps({"blocked": [{"wo_code": "WO-B-301-4562"}], "at_risk": [{"wo_code": "WO-B-301-4560"}]})


def span(seq, name, kind, output):
    return {"seq": seq, "name": name, "kind": kind, "output": output}


def lost(spans, final):
    return {x["raw"]: x["stage"] for x in loop.where_lost(ITEMS, spans, final, [])}


def test_a_value_the_data_never_had_is_a_code_change():
    spans = [span(1, "work_order_blockers", "tool", json.dumps({"blocked": [{"wo_code": "WO-B-301-4562"}]})),
             span(2, "analyst", "llm", "WO-B-301-4562 is held.")]
    assert lost(spans, "WO-B-301-4562 is held.") == {"WO-B-301-4560": "not_in_data"}


def test_a_value_the_read_returned_and_the_answer_left_out_was_dropped_by_the_writer():
    spans = [span(1, "work_order_blockers", "tool", DATA), span(2, "analyst", "llm", "WO-B-301-4562 is held; 1 more at risk.")]
    assert lost(spans, "WO-B-301-4562 is held; 1 more at risk.") == {"WO-B-301-4560": "dropped_by_writer"}


def test_a_value_the_review_had_removed_was_removed_in_review():
    spans = [span(1, "work_order_blockers", "tool", DATA),
             span(2, "analyst", "llm", "Held WO-B-301-4562. At risk WO-B-301-4560."),
             span(3, "review", "llm", '{"verdict": "revise", "findings": ["remove the at-risk group"]}'),
             span(4, "analyst_revision", "llm", "Held WO-B-301-4562.")]
    assert lost(spans, "Held WO-B-301-4562.") == {"WO-B-301-4560": "removed_in_review"}


def test_a_value_written_but_missing_from_the_final_answer_was_lost_at_the_end():
    spans = [span(1, "work_order_blockers", "tool", DATA), span(2, "analyst", "llm", "Held WO-B-301-4562. At risk WO-B-301-4560.")]
    assert lost(spans, "Held WO-B-301-4562.") == {"WO-B-301-4560": "lost_at_the_end"}


def test_a_must_not_value_that_is_still_said_is_named():
    spans = [span(1, "work_order_blockers", "tool", DATA), span(2, "model", "llm", "x")]
    out = lost(spans, "WO-B-301-4560 and WO-B-301-4562; confirm no live work.")
    assert out == {"!confirm no live": "said_anyway"}


def test_without_a_writing_step_the_last_model_call_writes():
    spans = [span(1, "list_blocked_vendors", "tool", DATA), span(2, "model", "llm", "decided: list_blocked_vendors"),
             span(3, "model", "llm", "WO-B-301-4562 held.")]
    assert lost(spans, "WO-B-301-4562 held.") == {"WO-B-301-4560": "dropped_by_writer"}


def test_found_values_are_not_listed():
    spans = [span(1, "work_order_blockers", "tool", DATA), span(2, "analyst", "llm", "WO-B-301-4560, WO-B-301-4562")]
    assert lost(spans, "WO-B-301-4560 and WO-B-301-4562.") == {}


# -- what the next draft is given ------------------------------------------------------------------------------------

COMPARE = {"run": "R46", "questions": [
    {"question": "R46-1", "verdict": "still_failing", "after": {"answer": "Held WO-B-301-4562. 1 more at risk."},
     "lost": [{"raw": "WO-B-301-4560", "stage": "dropped_by_writer"}]},
    {"question": "R46-2", "verdict": "fixed", "after": {"answer": "ok"}, "lost": []}]}


def test_the_last_test_is_handed_to_the_next_draft():
    block, gaps = agent._last_test(COMPARE)
    assert gaps == []
    assert "R46-1" in block and "WO-B-301-4560: in the data, left out by the answer" in block
    assert "Held WO-B-301-4562. 1 more at risk." in block and "R46-2" not in block, "only what still fails"


def test_when_every_missing_value_was_never_in_the_data_no_draft_can_fix_it():
    only_gaps = {"run": "R42", "questions": [{"question": "R42-1", "verdict": "still_failing", "after": {"answer": "x"},
                                              "lost": [{"raw": "WO-B-301-4551", "stage": "not_in_data"}]}]}
    block, gaps = agent._last_test(only_gaps)
    assert gaps == ["WO-B-301-4551"]
    with pytest.raises(Invalid) as e:
        agent._refuse_data_gap(gaps)
    assert e.value.reason == "data_gap" and "WO-B-301-4551" in e.value.error and "code" in e.value.error.lower()
    agent._refuse_data_gap([])   # nothing missing from the data: drafting goes on


def test_a_redraft_replaces_the_rule_s_earlier_section_rather_than_stacking_a_second():
    doc = ("# Answering\n\nKeep this.\n\n## Old wording (S-13)\n\nThis rule takes precedence...\n\nOld text.\n\n"
           "## Another rule (S-12)\n\nKeep me too.\n\n## Keep what rule S-13 requires (S-13)\n\nOld keep.\n")
    out = agent._without_rule(doc, "S-13")
    assert "Old text." not in out and "Old keep." not in out and "(S-13)" not in out
    assert "Keep this." in out and "## Another rule (S-12)" in out and "Keep me too." in out
    assert agent._without_rule(doc, "S-99") == doc


def test_a_count_the_answer_must_work_out_is_never_called_a_data_gap():
    """R47 (10 Nov 2026): the expected value "seven" (a tally of the rows) was missing and the loop said "never in the
    data: a code change". A figure is worked out from the rows, so no read ever carries it: missing, it is the
    answer's to state - an instruction matter, never a data gap."""
    items = golden.parse_expected_values("seven\nOstley Power Services")
    spans = [span(1, "list_vendor_accreditations", "tool", json.dumps({"rows": [{"vendor": "Ostley Power Services"}]})),
             span(2, "analyst", "llm", "Ostley Power Services lapsed.")]
    out = {x["raw"]: x["stage"] for x in loop.where_lost(items, spans, "Ostley Power Services lapsed.", [])}
    assert out == {"seven": "not_stated"}
    block, gaps = agent._last_test({"run": "R47", "questions": [{"question": "R47-1", "verdict": "still_failing",
                                                                 "after": {"answer": "x"},
                                                                 "lost": [{"raw": "seven", "stage": "not_stated"}]}]})
    assert gaps == [] and "seven: a figure worked out from the data, not stated" in block


def test_the_loop_page_gets_each_expected_answer_to_edit():
    """10 Nov 2026: a saved expected answer could not be opened from the Loop page; its form is now prefilled."""
    row = {"expected": "Eleven lapsed.", "evidence": json.dumps({"spec_rule": "S-15", "data": "vendors", "values": ["seven", "!nine of them"]})}
    assert loop._expected_out(row) == {"text": "Eleven lapsed.", "records": "vendors", "rule": "S-15",
                                       "values": ["seven", "!nine of them"]}
    assert loop._expected_out(None) is None
    assert loop._expected_out({"expected": "x", "evidence": None}) == {"text": "x", "records": "", "rule": None, "values": []}
