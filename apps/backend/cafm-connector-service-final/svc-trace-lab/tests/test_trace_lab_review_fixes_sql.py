"""The 13 Nov 2026 review of Trace Lab, fixed test-first, on a throwaway Postgres.

1. A cleared expected-outcome flag lets the turn be scored again, and while a turn is flagged its earlier score is not
   the session's current verdict.
2. No scenario turn ends up nowhere: one whose session is not filed within an hour goes to its asker's day as before.
4. The automatic scenario read is light: one slow page never stops the round, a waiting conversation is not read again
   every round, and the read runs at most every five minutes.
6. The automatic read files a scenario session only when its asker may curate Trace Lab.
9. A run's labels grid is capped, and says so.
11. A turn with no expected outcome cannot be flagged.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, UID, member, sample_turn, seed_score, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import autosync, eval_judges, eval_store, expected_flags, runs, scenario_file
from trace_lab.review_errors import Invalid

pytestmark = requires_scratch

QUESTION = "Which certificates expire in the next 30 days at B-301?"


def _scenario(scenario_id: str = "EV-001") -> dict:
    expected = {"plan": [{"call_id": "c1", "domain": "compliance", "tool": None, "params": {}, "depends_on": []}],
                "failure_handling": None, "answer_format": "success", "must_include": [], "must_not_include": [],
                "answer": "A short list of certificates."}
    return {"scenario_id": scenario_id, "type": "single_domain", "spec_sections": ["output_format"],
            "role": "compliance_officer", "space": "comp", "scope": {}, "domain_rules": [],
            "turns": [{"message": QUESTION, "expected": expected}]}


async def _asked(lab, scenario_id: str = "EV-001", turn: str = "turn-1") -> str:
    await scenario_file.save_scenarios(member(), [_scenario(scenario_id)], source="import")
    sid = f"scn__baseline_v0__{scenario_id}__1"
    await seed_session(lab, sid, tag="baseline_v0", turn_ids=(turn,), scenario=(scenario_id, 1))
    return sid


async def _rows(engine, sql: str, p: dict | None = None) -> list[tuple]:
    async with engine.connect() as c:
        return [tuple(r) for r in (await c.execute(text(sql), p or {})).all()]


async def _main_turn(main, turn_id: str, session_id: str, *, at: datetime, uid: str = UID, org: str = ORG) -> None:
    from test_trace_lab_eval_store_sql import _main_turn as store

    turn, spans = sample_turn(turn_id, session_id, at=at, org=org)
    await store(main, {**turn, "user_id": uid}, [{**s} for s in spans])


async def _consultant(lab, uid: str) -> None:
    async with lab.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.lab_platform_members (user_id, email, lab_role, status, decided_by)"
            " VALUES (CAST(:u AS uuid), 'c@example.com', 'consultant', 'active', CAST(:u AS uuid))"), {"u": uid})


# ── 1. a cleared flag lets the turn be scored again ──────────────────────────────────────────────────


async def test_a_cleared_flag_lets_the_turn_be_scored_again_and_a_flagged_one_is_not_rewritten(lab_scratch):
    await _asked(lab_scratch)
    await expected_flags.set_flag(member(), "turn-1", flagged=True)
    first = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert first["not_evaluated"] == {"logic": 1}
    still = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert still["traces"] == 0 and still["remaining"] == 0, "while flagged it is not written again every pass"
    await expected_flags.set_flag(member(), "turn-1", flagged=False)
    again = await eval_judges.evaluate_run(member(), "baseline_v0", levels=("logic",))
    assert again["scored"] == {"logic": 1}, "a cleared flag lets the turn be scored"


async def test_while_a_turn_is_flagged_its_earlier_score_is_not_the_sessions_verdict(lab_scratch):
    sid = await _asked(lab_scratch)
    await seed_score(lab_scratch, "turn-1", sid, name="logic", verdict="fail", value=0)
    listed = (await eval_store.list_sessions(member()))["sessions"]
    assert listed[0]["scores"]["logic"]["verdict"] == "fail"
    await expected_flags.set_flag(member(), "turn-1", flagged=True)
    listed = (await eval_store.list_sessions(member()))["sessions"]
    assert listed[0]["scores"]["logic"] is None
    assert (await eval_store.list_sessions(member(), level="logic", verdict="fail"))["sessions"] == []
    got = await eval_store.get_session(member(), sid)
    assert got["traces"][0]["scores"]["logic"] is None and got["traces"][0]["score_history"], "the history stays"
    await expected_flags.set_flag(member(), "turn-1", flagged=False)
    listed = (await eval_store.list_sessions(member()))["sessions"]
    assert listed[0]["scores"]["logic"]["verdict"] == "fail"


# ── 11. no flag without an expected outcome ──────────────────────────────────────────────────────────


async def test_a_turn_with_no_expected_outcome_cannot_be_flagged(lab_scratch):
    await seed_session(lab_scratch, "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70", tag="chat", turn_ids=("turn-c",))
    with pytest.raises(Invalid) as e:
        await expected_flags.set_flag(member(), "turn-c", flagged=True)
    assert e.value.reason == "no_expected_outcome"
    await expected_flags.set_flag(member(), "turn-c", flagged=False)     # clearing is always allowed
    await _asked(lab_scratch, "EV-002", turn="turn-a")
    async with lab_scratch.begin() as c:                                  # a second turn the scenario has no turn for
        await c.execute(text("INSERT INTO tracelab.traces (id, session_id, turn_index, organization_id, status, header)"
                             " VALUES ('turn-b', 'scn__baseline_v0__EV-002__1', 2, CAST(:o AS uuid), 'ok', '{}')"),
                        {"o": ORG})
    with pytest.raises(Invalid):
        await expected_flags.set_flag(member(), "turn-b", flagged=True)
    assert (await expected_flags.set_flag(member(), "turn-a", flagged=True))["flagged"] is True


# ── 2. a scenario turn whose session is not filed goes to the asker's day ───────────────────────────


async def test_a_scenario_turn_not_filed_within_an_hour_goes_to_the_askers_day(main_scratch, lab_scratch):
    now = datetime.now(timezone.utc)
    await _main_turn(main_scratch, "turn-wait", "scn__baseline_v0__NOT-IMPORTED__1", at=now - timedelta(hours=2))
    await _main_turn(main_scratch, "turn-new", "scn__baseline_v0__NOT-IMPORTED-2__1", at=now - timedelta(minutes=10))
    await _main_turn(main_scratch, "turn-filed", "scn__baseline_v0__MT-001__1", at=now - timedelta(hours=2))
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", tag="baseline_v0", turn_ids=("turn-filed",),
                       at=now - timedelta(hours=2))
    out = await runs.sync_live(None, now=now)
    assert out["new_questions"] == 1
    asked = await _rows(lab_scratch, "SELECT l.turn_id, r.kind FROM tracelab.lab_question_traces l JOIN"
                                     " tracelab.lab_questions q ON q.id = l.question_id JOIN tracelab.lab_runs r"
                                     " ON r.id = q.run_id")
    assert asked == [("turn-wait", "production")], "only the unfiled turn older than an hour"
    assert (await runs.sync_live(None, now=now))["new_questions"] == 0, "never twice"


async def test_the_automatic_read_counts_what_it_could_not_file(main_scratch, lab_scratch):
    now = datetime.now(timezone.utc)
    await _consultant(lab_scratch, UID)
    await _main_turn(main_scratch, "turn-w", "scn__baseline_v0__NOT-IMPORTED__1", at=now - timedelta(minutes=5))
    await _main_turn(main_scratch, "turn-k", "scn__bad_tag___X__1", at=now - timedelta(minutes=5))
    out = await autosync.tick(now)
    assert (out["scenario_sessions_waiting"], out["scenario_sessions_skipped"], out["scenario_sessions_other_company"],
            out["scenario_sessions_failed"]) == (1, 1, 0, 0)


# ── 4. a light automatic read ─────────────────────────────────────────────────────────────────────


async def test_one_slow_page_does_not_stop_the_others(main_scratch, lab_scratch, monkeypatch):
    now = datetime.now(timezone.utc)
    await scenario_file.save_scenarios(member(), [_scenario("EV-001"), _scenario("EV-002")], source="import")
    await _main_turn(main_scratch, "turn-1", "scn__baseline_v0__EV-001__1", at=now - timedelta(minutes=5))
    await _main_turn(main_scratch, "turn-2", "scn__baseline_v0__EV-002__1", at=now - timedelta(minutes=4))
    monkeypatch.setattr(eval_store, "INGEST_PAGE", 1)
    real = eval_store._read_page
    calls = []

    async def slow_first(ids):
        calls.append(list(ids))
        if len(calls) == 1:
            raise TimeoutError("canceling statement due to statement timeout")
        return await real(ids)

    monkeypatch.setattr(eval_store, "_read_page", slow_first)
    out = await eval_store.ingest_from_main(since=now - timedelta(hours=1))
    assert (out["failed"], out["sessions"]) == (1, 1)


async def test_a_waiting_conversation_is_not_read_again_every_round(main_scratch, lab_scratch, monkeypatch):
    now = datetime.now(timezone.utc)
    await _main_turn(main_scratch, "turn-w", "scn__baseline_v0__NOT-IMPORTED__1", at=now - timedelta(minutes=5))
    reads = []
    real = eval_store._read_page

    async def counted(ids):
        reads.append(list(ids))
        return await real(ids)

    monkeypatch.setattr(eval_store, "_read_page", counted)
    clock = [1000.0]
    monkeypatch.setattr(eval_store, "_clock", lambda: clock[0])
    first = await eval_store.ingest_from_main(since=now - timedelta(hours=1), remember=True)
    assert first["waiting"] == 1
    second = await eval_store.ingest_from_main(since=now - timedelta(hours=1), remember=True)
    assert (second["waiting"], second["held"]) == (0, 1) and len(reads) == 1, "held, not read again"
    button = await eval_store.ingest_from_main(since=now - timedelta(hours=1))
    assert button["waiting"] == 1, "the button always reads"
    clock[0] += eval_store.HOLD_S + 1
    later = await eval_store.ingest_from_main(since=now - timedelta(hours=1), remember=True)
    assert later["waiting"] == 1, "read again once the hold is over"


async def test_the_scenario_read_runs_at_most_every_five_minutes(lab_scratch, monkeypatch):
    calls = []

    async def ingest(**kw):
        calls.append(kw)
        return {"sessions": 0}

    monkeypatch.setattr(eval_store, "ingest_from_main", ingest)
    now = datetime.now(timezone.utc)
    await autosync.sync_scenario_sessions(now)
    await autosync.sync_scenario_sessions(now + timedelta(minutes=2))
    assert len(calls) == 1
    await autosync.sync_scenario_sessions(now + timedelta(minutes=6))
    assert len(calls) == 2 and calls[0].get("remember") is True and calls[0].get("curators_only") is True


# ── 6. only a curator's typed session id is filed by itself ─────────────────────────────────────────


async def test_the_automatic_read_files_a_scenario_session_only_for_a_curator(main_scratch, lab_scratch):
    now = datetime.now(timezone.utc)
    await scenario_file.save_scenarios(member(), [_scenario("EV-001")], source="import")
    stranger, admin, consultant = (str(uuid.uuid4()) for _ in range(3))
    await _main_turn(main_scratch, "turn-s", "scn__baseline_v0__EV-001__7", at=now - timedelta(minutes=5), uid=stranger)
    await _main_turn(main_scratch, "turn-a", "scn__baseline_v0__EV-001__8", at=now - timedelta(minutes=5), uid=admin)
    await _main_turn(main_scratch, "turn-c", "scn__baseline_v0__EV-001__9", at=now - timedelta(minutes=5), uid=consultant)
    await _consultant(lab_scratch, consultant)
    async with main_scratch.begin() as c:
        await c.execute(text("CREATE TABLE IF NOT EXISTS plenum_cafm.users (id uuid PRIMARY KEY, email text,"
                             " organization_id uuid, platform_role text)"))
        await c.execute(text("INSERT INTO plenum_cafm.users VALUES (CAST(:a AS uuid), 'a@x', CAST(:o AS uuid), 'admin'),"
                             " (CAST(:s AS uuid), 's@x', CAST(:o AS uuid), 'user')"),
                        {"a": admin, "s": stranger, "o": ORG})
    out = await eval_store.ingest_from_main(since=now - timedelta(hours=1), curators_only=True)
    filed = await _rows(lab_scratch, "SELECT id FROM tracelab.sessions ORDER BY id")
    assert filed == [("scn__baseline_v0__EV-001__8",), ("scn__baseline_v0__EV-001__9",)]
    assert out["not_curator"] == 1
    button = await eval_store.ingest_from_main(since=now - timedelta(hours=1))
    assert button["sessions"] == 1, "Read new sessions keeps its own rule"


# ── 9. a capped labels grid says so ──────────────────────────────────────────────────────────────


async def test_a_runs_labels_grid_is_capped_and_says_how_many_there_are(lab_scratch, monkeypatch):
    run = await runs.create_run(member(), title="Big", questions=[{"question": f"Q{i}?"} for i in range(3)])
    monkeypatch.setattr(runs, "MATRIX_MAX", 2)
    out = await runs.matrix(member(), run["code"])
    assert (len(out["questions"]), out["total"], out["more"]) == (2, 3, True)


# ── 3. one label store ─────────────────────────────────────────────────────────────────────────


async def test_a_label_on_a_sessions_run_question_is_seen_by_progress(main_scratch, lab_scratch):
    await _asked(lab_scratch)
    await runs.sync_sessions(None)
    code = (await runs.list_runs(member(), kind="sessions"))["runs"][0]["code"]
    grid = await runs.matrix(member(), code)
    assert [q["turn_ids"] for q in grid["questions"]] == [["turn-1"]]
    lin = (await runs.save_mode(member(), name="Wrong building"))["lineage_id"]
    await runs.set_cell(member(), grid["questions"][0]["code"], mode=lin, value="positive")
    progress = await runs.progress(member(), code)
    assert progress["run"]["code"] == code
    assert [m["positives"] for m in progress["modes"]] == [1], "Progress reads the same label store"
