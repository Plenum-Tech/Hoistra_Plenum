"""Pure planning: grouping batches by agent and similarity, the depth-scan shortlist (one per conversation plus seeded
random extras, agent filter skipped below 50), call pricing, and what the model is shown for each kind of item (no
asker or reviewer identity in any prompt, R24). No database, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import proposals
from trace_lab.review_errors import Invalid


def _note(i, agent, text_, minute):
    return {"annotation_id": f"a{i}", "span_agent": agent, "note": text_, "created_at": f"2026-10-01T00:{minute:02d}:00"}


# ── grouping batches ─────────────────────────────────────────────────────────────────────

def test_batches_group_by_agent_and_chain_by_similarity():
    notes = [_note(1, None, "counts cancelled jobs as open", 1), _note(2, "compliance", "missing gas certificate", 2),
             _note(3, None, "wrong building name in answer", 3), _note(4, None, "counts cancelled jobs twice", 4)]
    batches = proposals.group_batches(notes, size=40)
    order = [n["annotation_id"] for n in batches[0]]
    assert order == ["a2", "a1", "a4", "a3"]          # 'compliance' < 'orchestrator'; a4 is nearest to a1


def test_batches_are_cut_at_the_batch_size():
    notes = [_note(i, "udr", f"note number {i}", i % 60) for i in range(95)]
    assert [len(b) for b in proposals.group_batches(notes, size=40)] == [40, 40, 15]
    assert [len(b) for b in proposals.group_batches(notes)] == [40, 40, 15]            # the default size is 40
    assert proposals.group_batches([]) == []


def test_every_note_lands_in_exactly_one_batch_whatever_the_order_they_arrive_in():
    notes = [_note(i, ("udr", None, "compliance")[i % 3], f"note {i % 7} words", i % 60) for i in range(50)]
    shuffled = list(reversed(notes))
    a = [n["annotation_id"] for b in proposals.group_batches(notes, size=8) for n in b]
    b = [n["annotation_id"] for b in proposals.group_batches(shuffled, size=8) for n in b]
    assert sorted(a) == sorted(n["annotation_id"] for n in notes) and a == b             # nothing lost, nothing doubled


def test_a_missing_agent_is_the_orchestrator_and_a_missing_date_sorts_first():
    notes = [_note(1, None, "same words", 5), dict(_note(2, "orchestrator", "same words", 1), created_at=None)]
    assert [n["annotation_id"] for n in proposals.group_batches(notes)[0]] == ["a2", "a1"]


# ── the depth-scan shortlist ─────────────────────────────────────────────────────────────

def _pool(n, *, agent="wo_engine", org=None):
    return [{"turn_id": f"t{i:03d}", "session_id": f"s{i // 2}", "text": f"open work orders count {i}",
             "agents": [agent], "route": f"single:{agent}", "organization_id": org} for i in range(n)]


def test_the_shortlist_takes_one_per_conversation_and_seeded_random_extras():
    pool = _pool(300)
    picks, params = proposals.depth_shortlist(pool, ["open work orders count 7"], mode_agent="wo_engine", seed="run-1")
    short = [p for p in picks if p["why"] == "shortlist"]
    rand = [p for p in picks if p["why"] == "random"]
    assert len(short) == 100 and len(rand) == 10 and params["agent_filter"] == "applied"
    sessions = [pool[int(p["turn_id"][1:])]["session_id"] for p in short]
    assert len(set(sessions)) == len(sessions)
    again, _ = proposals.depth_shortlist(pool, ["open work orders count 7"], mode_agent="wo_engine", seed="run-1")
    assert again == picks
    other, _ = proposals.depth_shortlist(pool, ["open work orders count 7"], mode_agent="wo_engine", seed="run-2")
    assert [p for p in other if p["why"] == "shortlist"] == short and other != picks     # only the random extras move


def test_the_best_match_comes_first_and_the_random_extras_are_never_shortlisted_ones():
    pool = _pool(300)
    picks, _ = proposals.depth_shortlist(pool, ["open work orders count 123"], mode_agent="orchestrator", seed="s")
    assert picks[0]["turn_id"] == "t123" and picks[0]["score"] == 1.0
    assert len({p["turn_id"] for p in picks}) == len(picks) == 110
    assert all(p["score"] is None for p in picks if p["why"] == "random")


def test_a_pool_smaller_than_the_shortlist_gives_what_there_is():
    picks, _ = proposals.depth_shortlist(_pool(30), ["x"], mode_agent="orchestrator", seed="s")
    assert [p["why"] for p in picks].count("shortlist") == 15                    # 15 conversations: all of them
    assert [p["why"] for p in picks].count("random") == 10 and len({p["turn_id"] for p in picks}) == 25
    assert proposals.depth_shortlist([], ["x"], mode_agent="udr", seed="s")[0] == []


def test_the_agent_filter_is_skipped_when_it_would_leave_fewer_than_50():
    pool = _pool(40) + _pool(60, agent="compliance")[40:]
    _picks, params = proposals.depth_shortlist(pool, ["x"], mode_agent="wo_engine", seed="s")
    assert params["agent_filter"] == "skipped" and params["agent_filter_kept"] == 40
    _picks, params = proposals.depth_shortlist(pool, ["x"], mode_agent="orchestrator", seed="s")
    assert params["agent_filter"] == "none"


def test_the_agent_filter_keeps_only_turns_that_ran_the_modes_agent():
    pool = _pool(120) + [dict(p, turn_id="u" + p["turn_id"], session_id="u" + p["session_id"])
                         for p in _pool(120, agent="compliance")]
    picks, params = proposals.depth_shortlist(pool, ["open work orders"], mode_agent="compliance", seed="s")
    assert params["agent_filter"] == "applied"
    assert all(p["turn_id"].startswith("u") for p in picks if p["why"] == "shortlist")


def test_the_picks_carry_their_company():
    picks, _ = proposals.depth_shortlist(_pool(8, org="org-1"), ["x"], mode_agent="orchestrator", seed="s")
    assert {p["organization_id"] for p in picks} == {"org-1"}


def test_a_turn_with_no_session_is_its_own_conversation():
    pool = [{"turn_id": f"t{i}", "session_id": None, "text": "same", "agents": [], "route": ""} for i in range(5)]
    picks, _ = proposals.depth_shortlist(pool, ["same"], mode_agent="orchestrator", seed="s")
    assert len(picks) == 5


# ── pricing ──────────────────────────────────────────────────────────────────────────────

def test_call_cost_uses_the_price_table_and_an_unknown_model_cannot_be_priced():
    exp, worst = proposals.call_cost("claude-sonnet-5", "s" * 3500, "", expected_out=300, max_tokens=1200)
    assert exp == round(1000 * 2 / 1e6 + 300 * 10 / 1e6, 6) and worst == round(1000 * 2 / 1e6 + 1200 * 10 / 1e6, 6)
    assert proposals.call_cost("no-such-model", "s", "", expected_out=1, max_tokens=1) == (None, None)


def test_the_expected_output_is_never_above_the_ceiling():
    exp, worst = proposals.call_cost("claude-sonnet-5", "", "", expected_out=5000, max_tokens=1200)
    assert exp == worst == round(1200 * 10 / 1e6, 6)


# ── what the model is shown ──────────────────────────────────────────────────────────────

def test_grouping_prompt_lists_handles_and_says_none_yet_without_modes():
    notes = [{"annotation_id": "a1", "note": "counts cancelled jobs", "quote": "12 open", "field": "answer",
              "span_id": None, "question": "How many?", "answer": "12 open"}]
    system, user, nh, mh = proposals.render_grouping(notes, [], 1, 1)
    assert system == proposals.GROUPING_SYSTEM and nh == {"N1": "a1"} and mh == {}
    assert "EXISTING MODES\nNone yet." in user and "[N1] pinned at: the answer" in user and "NOTES (batch 1 of 1)" in user


def test_grouping_prompt_shows_each_mode_with_where_its_first_notes_came_from():
    notes = [{"annotation_id": "a1", "note": "n1", "quote": "q", "field": "output", "span_id": "s-9", "span_kind": "tool",
              "span_name": "query_table", "span_agent": "tool:udr", "question": "Q" * 500, "answer": "A" * 900},
             {"annotation_id": "a2", "note": "n2", "quote": "", "field": "question", "span_id": None}]
    modes = [{"id": "m1", "name": "counts_cancelled_jobs_as_open", "status": "confirmed", "agent": "wo_engine",
              "definition": "Present when x.", "boundary": "", "origin_notes": ["first", "second", "third", "fourth", ""]},
             {"id": "m2", "name": "invents_asset_codes", "status": "candidate", "agent": "udr", "definition": "d",
              "boundary": "Not B."}]
    _s, user, nh, mh = proposals.render_grouping(notes, modes, 2, 3)
    assert nh == {"N1": "a1", "N2": "a2"} and mh == {"M1": "m1", "M2": "m2"}
    assert "[M1] counts_cancelled_jobs_as_open (confirmed, agent wo_engine)" in user
    assert "Boundary: none written" in user and "Boundary: Not B." in user
    assert "  - first\n  - second\n  - third" in user and "fourth" not in user
    assert 'pinned at: tool "query_table" (tool:udr)' in user and "[N2] pinned at: the question" in user
    assert "Q" * 300 in user and "Q" * 301 not in user and "A" * 600 in user and "A" * 601 not in user
    assert "NOTES (batch 2 of 3)" in user


def test_a_reviewers_identity_never_reaches_the_grouping_prompt():
    note = {"annotation_id": "a1", "note": "counts cancelled jobs", "quote": "12 open", "field": "answer",
            "span_id": None, "reviewer_email": "rita.reviewer@example.test", "reviewer_id": "rev-0000-uuid",
            "organization_id": "org-secret-0000", "turn_id": "turn-secret", "conversation_key": "s:secret"}
    _s, user, _nh, _mh = proposals.render_grouping([note], [], 1, 1)
    for private in ("rita.reviewer", "rev-0000-uuid", "org-secret-0000", "turn-secret", "s:secret", "@"):
        assert private not in user


def test_the_merge_prompt_numbers_notes_across_drafts_and_hands_back_the_drafts():
    drafts = [{"id": "p1", "annotation_ids": ["a1", "a2"], "draft": {"name": "first_mode", "definition": "Present when 1.",
                                                                      "boundary": "B1", "level": "technical", "agent": "udr"}},
              {"id": "p2", "annotation_ids": ["a2", "a3"], "draft": {"name": "second_mode", "definition": "Present when 2.",
                                                                      "boundary": "B2", "level": "domain", "agent": "doc_rag"}}]
    user, handles = proposals.render_merge(drafts)
    assert handles == {"D1": drafts[0], "D2": drafts[1]}
    assert user.startswith("DRAFTS\n[D1] first_mode / Present when 1. / B1 / technical / udr / notes N1, N2")
    assert "[D2] second_mode / Present when 2. / B2 / domain / doc_rag / notes N2, N3" in user


def test_the_depth_prompt_carries_the_mode_and_its_confirmed_and_close_examples():
    mode = {"name": "counts_cancelled_jobs_as_open", "definition": "Present when x.", "boundary": ""}
    pos = [{"quote": "37 open", "note": "Wrong count."}, {"quote": "", "note": "no quote: left out"}, {"quote": "50 open"}]
    system = proposals.render_depth_system(mode, pos, [])
    assert "THE MISTAKE: counts_cancelled_jobs_as_open" in system and "Boundary: none written" in system
    assert '- "37 open" - Wrong count.' in system and '- "50 open" - confirmed by a reviewer' in system
    assert "no quote" not in system and "NOT this mistake:\nNone recorded." in system
    many = proposals.render_depth_system(mode, [{"quote": f"q{i}"} for i in range(9)], [])
    assert "q2" in many and "q3" not in many                                  # three examples at most


def test_the_cells_prompt_gives_each_mode_a_handle_and_its_examples():
    modes = [{"id": "m1", "name": "counts_cancelled_jobs_as_open", "definition": "Present when x.", "boundary": "Not y."},
             {"id": "m2", "name": "invents_asset_codes", "definition": "Present when z."}]
    examples = {"m1": ([{"quote": "37 open", "note": "Wrong count."}] * 3, [{"quote": "12 open", "note": "Right."}])}
    system, handles = proposals.render_cells_system(modes, examples)
    assert handles == {"M1": "m1", "M2": "m2"} and system.startswith("You propose labels for one answer")
    assert "[M1] counts_cancelled_jobs_as_open" in system and "[M2] invents_asset_codes" in system
    assert system.count('"37 open" (Wrong count.)') == 2                        # two present examples at most
    assert 'Not this mode, confirmed by reviewers: "12 open" (Right.)' in system
    assert "Present, confirmed by reviewers: none recorded" in system and "Boundary: none written" in system


def _evidence():
    turn = {"turn_id": "t1", "session_id": "s1", "organization_id": "org-secret-1", "question": "How many are open?",
            "answer": "There are 12 open work orders.", "ok": True, "email": "asker@example.test",
            "user_id": "u-private", "route": "single:wo_engine", "started_at": "2026-10-01T00:00:00+00:00"}
    span = {"id": "s-1", "kind": "tool", "name": "query_table", "agent": "udr", "seq": 1, "input": {"table": "work_orders"},
            "output": [{"open": 12}], "error": None}
    return {"turn": turn, "spans": [span], "earlier": []}


def test_a_cells_item_asks_only_the_modes_still_live_and_names_where_to_look():
    ctx = {"system": "SYS", "by_mode": {"m1": "M1", "m2": "M2"}}
    system, user, extra = proposals._render_item("cells", ctx, {"mode_ids": ["m2", "m9", "m1"]}, _evidence())
    assert system == "SYS" and extra["asked"] == {"M2": "m2", "M1": "m1"}
    assert user.startswith("Decide these modes: M2, M1\n\nQUESTION\nHow many are open?")
    assert set(extra["locations"]) == {"question", "answer", "S1", "S1.in"}
    assert extra["locations"]["S1"]["span_id"] == "s-1" and "[S1] tool \"query_table\" (udr)" in user


def test_a_depth_item_is_the_evidence_alone_under_the_modes_own_system_prompt():
    system, user, extra = proposals._render_item("depth_scan", {"system": "DEPTH SYS"}, {"turn_id": "t1"}, _evidence())
    assert system == "DEPTH SYS" and user.startswith("QUESTION\nHow many are open?") and "asked" not in extra
    assert "12 open work orders" in user


def test_an_askers_identity_never_reaches_a_cells_or_depth_prompt():
    ctx = {"system": "SYS", "by_mode": {"m1": "M1"}}
    for kind in ("cells", "depth_scan"):
        system, user, _extra = proposals._render_item(kind, ctx, {"mode_ids": ["m1"]}, _evidence())
        for private in ("asker@example.test", "u-private", "org-secret-1"):
            assert private not in user and private not in system


# ── the window of a run ──────────────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)


def test_a_naive_time_is_read_as_utc_and_a_window_must_end_after_it_starts():
    naive = datetime(2026, 1, 1, 12, 0)
    assert proposals._aware(naive) == datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    since, until = proposals._check_window(NOW.replace(tzinfo=None) - timedelta(days=2), NOW.replace(tzinfo=None))
    assert since.tzinfo is not None and until > since
    for since, until in ((NOW, NOW), (NOW, NOW - timedelta(days=1)), (None, NOW), (NOW, None), ("x", NOW)):
        with pytest.raises(Invalid) as e:
            proposals._check_window(since, until)
        assert e.value.reason == "bad_window"


def test_a_window_may_not_reach_back_beyond_span_retention(monkeypatch):
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "30")
    proposals._check_window(NOW - timedelta(days=29), NOW)
    with pytest.raises(Invalid) as e:
        proposals._check_window(NOW - timedelta(days=31), NOW)
    assert e.value.reason == "window_outside_retention"


def test_a_time_in_text_or_a_datetime_is_compared_the_same_way():
    assert proposals._when("2026-10-01T00:00:00+00:00") == proposals._when(datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert proposals._when("2026-10-01T00:00:00Z") == proposals._when("2026-10-01T00:00:00")
    assert proposals._when(None) is None and proposals._when("not a time") is None


def test_a_company_is_a_uuid_or_nothing():
    assert proposals._company(None) is None and proposals._company("") is None
    assert proposals._company("11111111-1111-4111-8111-111111111111".upper()) == "11111111-1111-4111-8111-111111111111"
    with pytest.raises(Invalid) as e:
        proposals._company("acme")
    assert e.value.reason == "bad_company"
