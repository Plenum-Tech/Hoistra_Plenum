"""The eval store's pure half (11 Nov 2026, task plan B1): a recorded turn and its spans become one trace row and one
observation row per step, and those rows give back exactly the tree the Trace tab draws.

"Exactly" is the point. The Sessions page reuses the Trace tab's tree view, so a field lost on the way into the table
would silently show less than Langfuse and the old tab do. node_of(rows_for(...)) is therefore compared with
trace_tree.build_tree node for node, key for key."""
from __future__ import annotations

import pytest

from eval_fixtures import ANSWER, sample_turn
from trace_lab import eval_store, trace_tree


def test_rows_for_keeps_the_tree():
    turn, spans = sample_turn()
    trace, obs = eval_store.rows_for(turn, spans)
    nodes = trace_tree.build_tree(turn, spans)["nodes"]
    assert [o["id"] for o in obs] == [n["id"] for n in nodes]
    assert obs[0]["depth"] == 0 and obs[0]["parent_observation_id"] is None
    assert [o["parent_observation_id"] for o in obs[1:]] == [n["parent_id"] for n in nodes[1:]]
    tool = next(o for o in obs if o["type"] == "tool")
    assert tool["level"] == "ERROR" and tool["status_message"] == "upstream timeout"
    gen = next(o for o in obs if o["type"] == "generation")
    assert gen["usage"] == {"input": 120, "output": 30, "cache_read": 0} and gen["model"] == "gpt-5.6-terra"
    assert gen["name"] == "analyst" and float(gen["cost_usd"]) == 0.0042
    assert trace["id"] == "turn-abc"


def test_node_of_gives_back_every_node_exactly():
    turn, spans = sample_turn()
    _, obs = eval_store.rows_for(turn, spans)
    assert [eval_store.node_of(o) for o in obs] == trace_tree.build_tree(turn, spans)["nodes"]


def test_the_trace_row_carries_question_answer_status_and_header():
    turn, spans = sample_turn()
    trace, _ = eval_store.rows_for(turn, spans)
    assert trace["input"] == turn["question"] and trace["output"] == ANSWER
    assert trace["status"] == "ok" and trace["name"] == "chat"
    assert trace["header"] == trace_tree.build_tree(turn, spans)["turn"]
    assert trace["metadata"]["answer_words"] == len(ANSWER.split())
    assert trace["metadata"]["tools"] == ["list_certificates"]
    assert trace["started_at"].isoformat() == "2026-11-11T09:00:00+00:00"


def test_a_failed_turn_is_an_error_trace():
    turn, spans = sample_turn(ok=False)
    trace, _ = eval_store.rows_for(turn, spans)
    assert trace["status"] == "error"


def test_the_callers_status_and_metadata_are_kept():
    turn, spans = sample_turn()
    trace, _ = eval_store.rows_for(turn, spans, status="timeout", extra_metadata={"asked_as": "pm@example.com"})
    assert trace["status"] == "timeout"
    assert trace["metadata"]["asked_as"] == "pm@example.com" and "answer_words" in trace["metadata"]


def test_a_turn_with_no_spans_is_still_a_trace_with_a_root():
    turn, spans = sample_turn(with_spans=False)
    trace, obs = eval_store.rows_for(turn, spans)
    assert len(obs) == 1 and obs[0]["depth"] == 0 and obs[0]["type"] == "agent"
    assert trace["input"] == turn["question"]


def test_session_ids_round_trip():
    sid = eval_store.session_id_for("baseline_v0", "MT-014", 1)
    assert sid == "scn__baseline_v0__MT-014__1" and len(sid) <= 120
    assert eval_store.parse_session_id(sid) == ("baseline_v0", "MT-014", 1)
    longest = eval_store.session_id_for("t" * 40, "s" * 60, 99)
    assert len(longest) <= 120 and eval_store.parse_session_id(longest) == ("t" * 40, "s" * 60, 99)


@pytest.mark.parametrize("sid", ["scenario-ops-0123456789ab", "golden-0123456789ab-K-r1", "regression-0123456789ab",
                                 "scn__baseline_v0__MT-014", "scn__base__line__MT-014__1", "scn____MT-014__1", "", None])
def test_other_session_ids_are_not_scenario_sessions(sid):
    assert eval_store.parse_session_id(sid) is None


@pytest.mark.parametrize("tag, scenario, attempt", [("base__line", "MT-1", 1), ("ok", "a__b", 1), ("t" * 41, "MT-1", 1),
                                                   ("ok", "s" * 61, 1), ("ok", "MT-1", 0), ("", "MT-1", 1),
                                                   ("ok", "MT 1", 1), ("ok_", "MT-1", 1), ("ok", "MT-1_", 1), ("ok", "MT-1", 100)])
def test_a_session_id_that_would_not_read_back_is_refused(tag, scenario, attempt):
    with pytest.raises(ValueError):
        eval_store.session_id_for(tag, scenario, attempt)
