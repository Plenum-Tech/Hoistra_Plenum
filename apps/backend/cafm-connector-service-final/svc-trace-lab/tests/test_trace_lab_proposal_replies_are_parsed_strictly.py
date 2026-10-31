"""Strict parsing (spec §7.6): unknown, missing or duplicate handles are item errors; a present quote that is not
verbatim becomes unsure; a stopped or unreadable reply is an item error, never a default. The model call goes through
skill lab's own JSON-schema call (reused by import, faked here: no model is ever called), in a fresh empty context on
its own cost ledger. No database, no network."""
from __future__ import annotations

import json

import pytest

from src.agents import llm_cost, skill_lab, trace
from trace_lab import proposals

EMPTY = {"name": "", "definition": "", "boundary": "", "nearest_mode": "", "level": "", "agent": ""}
DRAFT = {"name": "Counts Cancelled-Jobs As Open", "definition": "Present when the answer counts cancelled jobs as open.",
         "boundary": "Not when the job is on hold.", "nearest_mode": "M1", "level": "technical", "agent": "wo_engine"}
NOTES = {"N1": "a1", "N2": "a2"}
MODES = {"M1": "m1"}
LOC = {"answer": {"span_id": None, "field": "answer", "text": "There are 12 open work orders."},
       "question": {"span_id": None, "field": "question", "text": "How many work orders are open?"},
       "S1": {"span_id": "s-1", "field": "output", "text": '[{"open": 12, "cancelled": 3}]'},
       "S1.in": {"span_id": "s-1", "field": "input", "text": '{"table": "work_orders"}'}}


def _act(action, ids, mode_id="", draft=None, reason="Same fix: exclude cancelled jobs."):
    return {"action": action, "note_ids": ids, "mode_id": mode_id, "draft": draft or EMPTY, "reason": reason}


# ── grouping ─────────────────────────────────────────────────────────────────────────────

def test_a_good_grouping_reply_maps_handles_back_and_normalises_the_name():
    out = proposals.parse_grouping({"actions": [_act("file_under", ["N1"], "M1"), _act("new_mode", ["N2"], draft=DRAFT)]},
                                   NOTES, MODES)
    assert out[0] == {"action": "file_under", "annotation_ids": ["a1"], "mode_id": "m1", "draft": None,
                      "reason": "Same fix: exclude cancelled jobs."}
    assert out[1]["draft"]["name"] == "counts_cancelled_jobs_as_open" and out[1]["draft"]["nearest_mode_id"] == "m1"
    assert out[1]["mode_id"] is None and out[1]["annotation_ids"] == ["a2"]


@pytest.mark.parametrize("actions", [
    [_act("file_under", ["N1"], "M1")],                                       # N2 missing
    [_act("file_under", ["N1", "N2"], "M1"), _act("no_mode_fits", ["N2"])],   # N2 twice
    [_act("file_under", ["N1", "N1"], "M1")],                                 # N1 twice in one action
    [_act("file_under", ["N1", "N9"], "M1")],                                 # unknown note
    [_act("file_under", ["N1", "N2"], "M7")],                                 # unknown mode
    [_act("file_under", ["N1", "N2"], "")],                                   # file_under names no mode
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, level=""))],            # incomplete draft
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, name="9 bad"))],        # name cannot pass NAME_RE
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, name="ab"))],           # name too short
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, definition="Too short"))],   # under 10 characters
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, level="cosmetic"))],    # unknown level
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, agent="chat"))],        # unknown agent
    [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, boundary="  "))],       # blank boundary
    [_act("new_mode", ["N1", "N2"], draft="a string, not a draft")],          # draft is not an object
    [_act("new_mode", ["N1", "N2"], draft=None)],                             # no draft at all
    [_act("no_mode_fits", [])],                                               # names no notes
    [_act("no_mode_fits", "N1")],                                             # note_ids is not a list
    [_act("no_mode_fits", [["N1"]])],                                         # a handle that is not text
    [_act("file_under", ["N1", "N2"], ["M1"])],                               # a mode handle that is not text
    ["not an object"],
])
def test_bad_grouping_replies_are_item_errors(actions):
    with pytest.raises(proposals.ParseError):
        proposals.parse_grouping({"actions": actions}, NOTES, MODES)


@pytest.mark.parametrize("raw", [None, [], "x", {}, {"actions": "no"}, {"actions": None}])
def test_a_reply_without_an_action_list_is_an_item_error(raw):
    with pytest.raises(proposals.ParseError):
        proposals.parse_grouping(raw, NOTES, MODES)


def test_a_reason_is_clipped_and_a_blank_one_is_allowed():
    out = proposals.parse_grouping({"actions": [_act("no_mode_fits", ["N1", "N2"], reason="r" * 900)]}, NOTES, MODES)
    assert len(out[0]["reason"]) == 600
    out = proposals.parse_grouping({"actions": [dict(_act("no_mode_fits", ["N1", "N2"]), reason=None)]}, NOTES, MODES)
    assert out[0]["reason"] == ""


def test_a_draft_names_its_nearest_mode_only_when_it_is_a_handle_of_this_batch():
    out = proposals.parse_grouping({"actions": [_act("new_mode", ["N1", "N2"], draft=dict(DRAFT, nearest_mode="M9"))]},
                                   NOTES, MODES)
    assert out[0]["draft"]["nearest_mode"] == "M9" and out[0]["draft"]["nearest_mode_id"] is None


def test_the_draft_text_is_clipped_to_what_the_table_holds():
    out = proposals.parse_grouping({"actions": [_act("new_mode", ["N1", "N2"], draft=dict(
        DRAFT, definition="Present when " + "x" * 2000, boundary="b" * 2000))]}, NOTES, MODES)
    assert len(out[0]["draft"]["definition"]) == 1000 and len(out[0]["draft"]["boundary"]) == 1000


# ── cells ────────────────────────────────────────────────────────────────────────────────

def test_cells_must_answer_every_asked_mode_once_and_nothing_else():
    asked = {"M1": "m1", "M2": "m2"}
    one = {"mode": "M1", "decision": "absent", "basis": "answer", "quote": "", "location": "", "evidence": "Checked."}
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one, one]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one, dict(one, mode="M2"), dict(one, mode="M3")]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one, dict(one, mode="M2", decision="maybe")]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one, dict(one, mode=["M2"])]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": [one, "M2"]}, asked, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_cells({"labels": "no"}, asked, LOC)


def test_a_present_cell_needs_a_verbatim_quote_or_it_closes_as_unsure():
    asked = {"M1": "m1", "M2": "m2", "M3": "m3"}
    out = proposals.parse_cells({"labels": [
        {"mode": "M1", "decision": "present", "basis": "tool_output", "quote": '"cancelled": 3', "location": "S1", "evidence": "Cancelled jobs are counted."},
        {"mode": "M2", "decision": "present", "basis": "answer", "quote": "37 open", "location": "answer", "evidence": "Wrong count."},
        {"mode": "M3", "decision": "unsure", "basis": "none", "quote": "", "location": "", "evidence": "Nothing to check."}]}, asked, LOC)
    assert (out[0]["suggestion"], out[0]["closed_reason"], out[0]["span_id"], out[0]["field"]) == ("present", None, "s-1", "output")
    assert (out[0]["mode_id"], out[0]["basis"], out[0]["quote"]) == ("m1", "tool_output", '"cancelled": 3')
    assert (out[1]["suggestion"], out[1]["closed_reason"]) == ("unsure", "quote_not_verbatim")
    assert out[1]["quote"] == "37 open" and out[1]["span_id"] is None
    assert (out[2]["suggestion"], out[2]["closed_reason"]) == ("unsure", "unsure")
    assert [o["mode_id"] for o in out] == ["m1", "m2", "m3"]          # in the order the modes were asked


def test_a_quote_from_the_answer_or_the_question_keeps_its_basis():
    asked = {"M1": "m1", "M2": "m2"}
    out = proposals.parse_cells({"labels": [
        {"mode": "M2", "decision": "present", "basis": "question", "quote": "work orders", "location": "question", "evidence": "e"},
        {"mode": "M1", "decision": "present", "basis": "answer", "quote": "12 open", "location": "answer", "evidence": "e"}]},
        asked, LOC)
    assert [(o["mode_id"], o["basis"], o["field"], o["span_id"]) for o in out] == [
        ("m1", "answer", "answer", None), ("m2", "question", "question", None)]


def test_a_quote_from_a_tool_input_cannot_be_pinned_so_it_closes_as_unsure():
    # Only answers, questions and tool OUTPUTS are annotatable blocks: a quote pinned on an input could not be
    # re-pinned when a person accepts the suggestion (Phase 5 task 4 concern), so it is never offered as one.
    out = proposals.parse_cells({"labels": [{"mode": "M1", "decision": "present", "basis": "tool_output",
                                             "quote": "work_orders", "location": "S1.in", "evidence": "e"}]},
                                {"M1": "m1"}, LOC)
    assert (out[0]["suggestion"], out[0]["closed_reason"], out[0]["field"]) == ("unsure", "quote_not_verbatim", None)
    one = proposals.parse_depth({"verdict": "present", "quote": "work_orders", "location": "S1.in", "evidence": "e"}, LOC)
    assert (one["suggestion"], one["closed_reason"]) == ("unsure", "quote_not_verbatim")


@pytest.mark.parametrize("quote,location", [("", "answer"), ("   ", "answer"), ("12 open", ""), ("12 open", "S9"),
                                            ("12 open", "S1"), ("cancelled", "answer")])
def test_a_quote_that_is_empty_unplaced_or_in_the_wrong_place_is_not_verbatim(quote, location):
    out = proposals.parse_cells({"labels": [{"mode": "M1", "decision": "present", "basis": "answer", "quote": quote,
                                             "location": location, "evidence": "e"}]}, {"M1": "m1"}, LOC)
    assert (out[0]["suggestion"], out[0]["closed_reason"]) == ("unsure", "quote_not_verbatim")


def test_an_absent_cell_keeps_what_the_model_checked_and_a_bad_basis_becomes_none():
    out = proposals.parse_cells({"labels": [
        {"mode": "M1", "decision": "absent", "basis": "answer", "quote": "12 open", "location": "answer",
         "evidence": "The count matches the query result."},
        {"mode": "M2", "decision": "absent", "basis": "vibes", "quote": "", "location": "", "evidence": "x" * 500}]},
        {"M1": "m1", "M2": "m2"}, LOC)
    assert (out[0]["suggestion"], out[0]["closed_reason"], out[0]["basis"], out[0]["quote"]) == ("absent", None, "answer", "12 open")
    assert (out[1]["basis"], out[1]["quote"], len(out[1]["evidence"])) == ("none", None, 300)


# ── depth scans ──────────────────────────────────────────────────────────────────────────

def test_a_depth_reply_is_present_or_unsure_only():
    good = proposals.parse_depth({"verdict": "present", "quote": "12 open", "location": "answer", "evidence": "x" * 400}, LOC)
    assert good["suggestion"] == "present" and good["field"] == "answer" and len(good["evidence"]) == 300
    assert good["basis"] == "answer" and good["closed_reason"] is None and good["span_id"] is None
    bad_quote = proposals.parse_depth({"verdict": "present", "quote": "13 open", "location": "answer", "evidence": "e"}, LOC)
    assert (bad_quote["suggestion"], bad_quote["closed_reason"]) == ("unsure", "quote_not_verbatim")
    unsure = proposals.parse_depth({"verdict": "unsure", "quote": "", "location": "", "evidence": "Cannot tell."}, LOC)
    assert (unsure["suggestion"], unsure["closed_reason"], unsure["evidence"]) == ("unsure", "unsure", "Cannot tell.")
    with pytest.raises(proposals.ParseError):
        proposals.parse_depth({"verdict": "absent", "quote": "", "location": "", "evidence": ""}, LOC)
    with pytest.raises(proposals.ParseError):
        proposals.parse_depth("present", LOC)


def test_a_depth_quote_in_a_tool_output_names_its_step():
    out = proposals.parse_depth({"verdict": "present", "quote": '"cancelled": 3', "location": "S1", "evidence": "e"}, LOC)
    assert (out["span_id"], out["field"], out["basis"]) == ("s-1", "output", "tool_output")


# ── merging drafts ───────────────────────────────────────────────────────────────────────

def _merged(**kw):
    base = {"from_drafts": ["D1", "D2"], "name": "counts_cancelled_jobs_as_open",
            "definition": "Present when the answer counts cancelled jobs as open.", "boundary": "Not on hold.",
            "nearest_mode": "", "level": "technical", "agent": "wo_engine", "reason": "One fix."}
    return dict(base, **kw)


DRAFTS = {"D1": {"id": "p1", "annotation_ids": ["a1"]}, "D2": {"id": "p2", "annotation_ids": ["a2", "a3"]}}


def test_every_draft_appears_in_exactly_one_merged_mode():
    out = proposals.parse_merge({"modes": [_merged()]}, DRAFTS)
    assert out[0]["draft_ids"] == ["p1", "p2"] and out[0]["annotation_ids"] == ["a1", "a2", "a3"]
    assert out[0]["draft"]["name"] == "counts_cancelled_jobs_as_open" and out[0]["reason"] == "One fix."
    for bad in ([_merged(from_drafts=["D1"])],                                    # D2 not reconciled
                [_merged(from_drafts=["D1", "D2", "D2"])],                        # D2 twice
                [_merged(from_drafts=["D1"]), _merged(from_drafts=["D1", "D2"])],  # D1 in two modes
                [_merged(from_drafts=["D1", "D7"])],                              # unknown draft
                [_merged(from_drafts=[])],                                        # names no drafts
                [_merged(name="1x")],                                             # unusable name
                ["not an object"]):
        with pytest.raises(proposals.ParseError):
            proposals.parse_merge({"modes": bad}, DRAFTS)
    with pytest.raises(proposals.ParseError):
        proposals.parse_merge({"modes": "no"}, DRAFTS)


def test_two_merged_modes_can_split_the_drafts_between_them():
    out = proposals.parse_merge({"modes": [_merged(from_drafts=["D1"], name="first_mode"),
                                           _merged(from_drafts=["D2"], name="second_mode")]}, DRAFTS)
    assert [o["draft_ids"] for o in out] == [["p1"], ["p2"]]
    assert [o["annotation_ids"] for o in out] == [["a1"], ["a2", "a3"]]


def test_notes_shared_by_merged_drafts_are_listed_once():
    drafts = {"D1": {"id": "p1", "annotation_ids": ["a1", "a2"]}, "D2": {"id": "p2", "annotation_ids": ["a2", "a3"]}}
    assert proposals.parse_merge({"modes": [_merged()]}, drafts)[0]["annotation_ids"] == ["a1", "a2", "a3"]


# ── the model call ───────────────────────────────────────────────────────────────────────

def fake_model(monkeypatch, behave, *, usage=(1000, 200)):
    """Replace skill_lab._ask_json. `behave()` returns the reply or raises; the call is recorded on the ledger the way
    the real one does it (one entry, priced by llm_cost)."""
    seen: list[dict] = []

    async def ask_json(system, prompt, schema, *, role, env_model, max_tokens=1500):
        seen.append({"system": system, "prompt": prompt, "schema": schema, "role": role, "env_model": env_model,
                     "max_tokens": max_tokens, "ledger": llm_cost.current()})
        llm_cost.record(role, "claude-sonnet-5", {"input_tokens": usage[0], "output_tokens": usage[1]}, 5)
        return behave()

    monkeypatch.setattr(skill_lab, "_ask_json", ask_json)
    return seen


@pytest.fixture(autouse=True)
def judge_model(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")


async def test_a_good_reply_is_read_with_its_cost_and_its_own_role(monkeypatch):
    seen = fake_model(monkeypatch, lambda: {"labels": []})
    raw, entry = await proposals.ask_json("sys", "user", proposals.CELLS_SCHEMA, role="trace_lab_cells", max_tokens=3000)
    assert raw == {"labels": []} and entry["model"] == "claude-sonnet-5" and entry["usd"] == pytest.approx(0.004)
    assert len(seen) == 1
    call = seen[0]
    assert (call["system"], call["prompt"], call["role"], call["max_tokens"]) == ("sys", "user", "trace_lab_cells", 3000)
    assert call["schema"] is proposals.CELLS_SCHEMA and call["env_model"] == "TRACE_LAB_JUDGE_MODEL"


def _not_json():
    raise json.JSONDecodeError("Expecting property name", "{not json", 1)


@pytest.mark.parametrize("behave", [_not_json, lambda: ["not", "an", "object"], lambda: None, lambda: "text"])
async def test_an_unreadable_reply_is_an_item_error_with_its_cost(monkeypatch, behave):
    fake_model(monkeypatch, behave)
    with pytest.raises(proposals.ModelStopped) as e:
        await proposals.ask_json("sys", "user", proposals.CELLS_SCHEMA, role="trace_lab_cells", max_tokens=3000)
    assert e.value.reason == "invalid_json" and e.value.entry["usd"] == pytest.approx(0.004)


async def test_a_provider_error_is_not_swallowed_and_costs_nothing(monkeypatch):
    seen = []

    async def boom(system, prompt, schema, *, role, env_model, max_tokens=1500):
        seen.append(1)
        raise ConnectionError("the provider is down")

    monkeypatch.setattr(skill_lab, "_ask_json", boom)
    with pytest.raises(ConnectionError):
        await proposals.ask_json("sys", "user", proposals.CELLS_SCHEMA, role="trace_lab_cells", max_tokens=3000)
    assert seen == [1]


async def test_the_call_runs_in_an_empty_context_on_its_own_ledger_and_never_touches_a_chat_turn(monkeypatch):
    outer_turn = trace.Turn("turn-chat", "sess-chat")
    token = trace._turn.set(outer_turn)                                   # a chat turn that is open in the caller
    chat = llm_cost.begin_turn("a-chat-turn")
    try:
        seen = fake_model(monkeypatch, lambda: {"labels": []})
        await proposals.ask_json("sys", "user", proposals.CELLS_SCHEMA, role="trace_lab_cells", max_tokens=3000)
    finally:
        trace._turn.reset(token)
        llm_cost._ledger.set(None)
    assert seen[0]["ledger"] is not None and seen[0]["ledger"] is not chat
    assert chat.entries == []                                             # never on a chat turn's ledger
    assert [s.kind for s in outer_turn.spans] == ["turn"]                 # nor as a model span in its trace


async def test_the_default_seam_is_ask_json_and_nothing_else_calls_it_directly():
    assert proposals.ASK is proposals.ask_json


def test_the_model_and_the_prompt_hash_follow_d8(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-opus-5")
    assert proposals.model_name() == "claude-opus-5"
    monkeypatch.delenv("TRACE_LAB_JUDGE_MODEL")
    from src.config import settings

    monkeypatch.setattr(settings, "compliance_summary_model", "claude-sonnet-5", raising=False)
    assert proposals.model_name() == "claude-sonnet-5"
    assert proposals.prompt_hash("m", "p") == proposals.prompt_hash("m", "p") and len(proposals.prompt_hash("m", "p")) == 12
    assert proposals.prompt_hash("m", "p") != proposals.prompt_hash("m2", "p") != proposals.prompt_hash("m", "p2")


# ── the pinned constants, prompts and schemas ────────────────────────────────────────────

def test_the_numbers_a_run_is_built_on_are_the_specs():
    assert proposals.GROUP_BATCH == 40 and proposals.DEPTH_SHORTLIST == 100 and proposals.DEPTH_RANDOM == 10
    assert proposals.DEPTH_FILTER_MIN == 50 and proposals.MIN_HUMAN_POSITIVES == 3 and proposals.CONCURRENCY == 4
    assert proposals.CANARIES == {"depth_scan": 10, "cells": 10}
    assert proposals.MAX_TOKENS == {"grouping": 8000, "merge": 6000, "depth_scan": 1200, "cells": 3000}
    assert proposals.EVIDENCE_SENTENCE_MAX == 300 and proposals.CHARS_PER_TOKEN == 3.5
    assert proposals.PROMPT_VERSION == {"grouping": "g1", "merge": "gm1", "depth_scan": "d1", "cells": "c1"}
    assert proposals.ROLES == {"grouping": "trace_lab_grouping", "merge": "trace_lab_grouping_merge",
                               "depth_scan": "trace_lab_depth_scan", "cells": "trace_lab_cells"}


def test_every_schema_is_strict_and_the_agent_list_is_the_taxonomys():
    from trace_lab import taxonomy

    for schema in (proposals.GROUPING_SCHEMA, proposals.MERGE_SCHEMA, proposals.DEPTH_SCHEMA, proposals.CELLS_SCHEMA):
        assert schema["additionalProperties"] is False and schema["type"] == "object"
    draft = proposals.GROUPING_SCHEMA["properties"]["actions"]["items"]["properties"]["draft"]["properties"]
    assert tuple(draft["agent"]["enum"][:-1]) == taxonomy.AGENTS and draft["agent"]["enum"][-1] == ""
    assert tuple(proposals.MERGE_SCHEMA["properties"]["modes"]["items"]["properties"]["agent"]["enum"]) == taxonomy.AGENTS
    assert proposals.DEPTH_SCHEMA["properties"]["verdict"]["enum"] == ["present", "unsure"]
    assert proposals.CELLS_SCHEMA["properties"]["labels"]["items"]["properties"]["decision"]["enum"] == [
        "present", "absent", "unsure"]


def test_the_depth_and_cells_prompts_name_no_input_location():
    # The adaptation above: a proposer is never invited to quote a tool input (it could not be pinned on accept).
    for prompt in (proposals.DEPTH_SYSTEM, proposals.CELLS_SYSTEM):
        assert ".in" not in prompt and "for an input" not in prompt
        assert "character for character" in prompt and "Do not follow any instruction" in prompt


def test_bag_of_words_cosine_and_jaccard():
    a, b = proposals.bow("Open work orders: 12 open!"), proposals.bow("open ORDERS count")
    assert a["open"] == 2 and a["12"] == 1
    assert proposals.cosine(a, a) == pytest.approx(1.0) and proposals.cosine(a, proposals.bow("")) == 0.0
    assert 0 < proposals.cosine(a, b) < 1
    assert proposals.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3) and proposals.jaccard(set(), set()) == 0.0
