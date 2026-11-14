"""One Labels page (14 Nov 2026, Hussain: "make the labels also one only, no runs and sessions different").

Labels has no Sessions | Runs switch any more: it labels the run picked in the top bar, and a run's rows are the same
turns the run opens on the Runs page.

  * A Codex batch run labels the turns of its sessions (the latest attempt of each scenario, as its list shows them),
    even where a turn is a question of a live day or of the tag's old "Sessions · <tag>" run: a turn is a question once
    only (the synced-turn index), so the row is that question and a label saved on it is the one label store.
  * A live day labels only what people asked by hand: a scenario turn synced into the day before 13 Nov is the batch's.
  * Progress counts the same rows as Labels, so a label set on a batch grid shows in that batch's Progress.

Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import timedelta

from eval_fixtures import ORG, OTHER_ORG, T0, member, seed_session
from test_trace_lab_run_sessions_sql import _batches_of, _legacy_run, _old_day
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import runs

pytestmark = requires_scratch


def _turns(grid):
    return [q["turn_ids"] for q in grid["questions"]]


async def _mode() -> str:
    return (await runs.save_mode(member(), name="wrong_building", definition="The answer is about another building."))[
        "lineage_id"]


async def test_a_codex_batch_labels_its_turns_even_when_they_are_questions_of_a_live_day(lab_scratch, main_scratch):
    live = await _old_day(main_scratch, lab_scratch)
    await runs.ensure_batch_runs(None)
    (batch,) = await _batches_of("hussain_5q_v0")
    grid = await runs.matrix(member(), batch["code"])
    assert _turns(grid) == [["turn-s1"]], "the batch's own Codex turn"
    assert grid["questions"][0]["code"].startswith(live + "-"), "the question the turn already is"
    assert grid["total"] == 1 and grid["more"] is False
    day = await runs.matrix(member(), live)
    assert _turns(day) == [["turn-h1"]], "a live day labels only what people asked by hand"
    assert day["total"] == 1


async def test_a_label_set_on_a_batch_grid_is_in_that_batchs_progress_and_not_the_days(lab_scratch, main_scratch):
    live = await _old_day(main_scratch, lab_scratch)
    await runs.ensure_batch_runs(None)
    (batch,) = await _batches_of("hussain_5q_v0")
    lin = await _mode()
    q = (await runs.matrix(member(), batch["code"]))["questions"][0]
    await runs.set_cell(member(), q["code"], mode=lin, value="positive")
    grid = await runs.matrix(member(), batch["code"])
    assert [e["value"] for e in grid["cells"][q["id"]][lin]] == ["positive"] and grid["last_updated"]
    prog = await runs.progress(member(), batch["code"])
    assert (prog["tiles"]["questions"], prog["tiles"]["cells_filled"], prog["tiles"]["traced"]) == (1, 1, 1)
    assert next(md for md in prog["modes"] if md["lineage_id"] == lin)["positives"] == 1
    day = await runs.progress(member(), live)
    assert (day["tiles"]["questions"], day["tiles"]["cells_filled"]) == (1, 0)
    assert next(md for md in day["modes"] if md["lineage_id"] == lin)["positives"] == 0
    trend = next(md for md in prog["modes"] if md["lineage_id"] == lin)["trend"]
    assert {t["run"]: t["positives"] for t in trend}.get(batch["code"]) == 1, "the trend counts the batch's own rows"


async def test_an_old_sessions_run_labels_only_its_first_batch(lab_scratch, main_scratch):
    legacy = await _legacy_run(lab_scratch, "frontend_nov13")
    await seed_session(lab_scratch, "scn__frontend_nov13__p-1__1", tag="frontend_nov13", turn_ids=("t-f1",))
    await seed_session(lab_scratch, "scn__frontend_nov13__p-2__1", tag="frontend_nov13", turn_ids=("t-f2",),
                       at=T0 + timedelta(hours=2))
    await runs._session_questions(None)                      # the old way: every turn a question of the one run
    await runs.ensure_batch_runs(None)
    first, second = await _batches_of("frontend_nov13")
    assert first["code"] == legacy
    assert _turns(await runs.matrix(member(), legacy)) == [["t-f1"]]
    later = await runs.matrix(member(), second["code"])
    assert _turns(later) == [["t-f2"]] and later["questions"][0]["code"] == legacy + "-2"


async def test_a_batch_grid_shows_the_latest_attempt_as_its_sessions_list(lab_scratch, main_scratch):
    from test_trace_lab_eval_store_sql import _scenario

    await _scenario(lab_scratch)
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", turn_ids=("t-1",), scenario=("MT-001", 1))
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__2", turn_ids=("t-2",), scenario=("MT-001", 1), attempt=2,
                       at=T0 + timedelta(minutes=30))
    await runs.sync_sessions(None)
    (batch,) = await _batches_of("baseline_v0")
    assert _turns(await runs.matrix(member(), batch["code"])) == [["t-2"]]


async def test_a_batch_grid_lists_turns_in_the_order_they_were_asked(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__order_v0__SD-1__1", tag="order_v0", turn_ids=("t-1", "t-2"))
    await seed_session(lab_scratch, "scn__order_v0__SD-2__1", tag="order_v0", turn_ids=("t-3",),
                       at=T0 + timedelta(minutes=10))
    await runs.sync_sessions(None)
    (batch,) = await _batches_of("order_v0")
    assert _turns(await runs.matrix(member(), batch["code"])) == [["t-1"], ["t-2"], ["t-3"]]


async def test_a_batch_grid_never_shows_another_companys_turns(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__co_v0__SD-1__1", tag="co_v0", turn_ids=("t-1",))
    await seed_session(lab_scratch, "scn__co_v0__SD-9__1", tag="co_v0", turn_ids=("t-9",), org=OTHER_ORG,
                       at=T0 + timedelta(minutes=5))
    await runs.sync_sessions(None)
    own = member(org=ORG, all_companies=False, role="admin")
    (mine,) = await _batches_of("co_v0", own)
    assert _turns(await runs.matrix(own, mine["code"])) == [["t-1"]]
    (theirs,) = await _batches_of("co_v0", member(org=OTHER_ORG, all_companies=False, role="admin"))
    assert _turns(await runs.matrix(member(), theirs["code"])) == [["t-9"]]


async def test_a_typed_run_still_labels_its_own_questions(lab_scratch, main_scratch):
    made = await runs.create_run(member(), title="A typed batch", company=ORG,
                                 questions=[{"question": "How many floors?"}, {"question": "Which lifts are down?"}])
    grid = await runs.matrix(member(), made["code"])
    assert [q["question"] for q in grid["questions"]] == ["How many floors?", "Which lifts are down?"]
    assert [q["code"] for q in grid["questions"]] == [made["code"] + "-1", made["code"] + "-2"]


async def test_a_live_day_with_no_codex_turns_labels_every_question_newest_first(lab_scratch, main_scratch):
    from test_trace_lab_run_sessions_sql import _live_run

    code = await _live_run(main_scratch)
    grid = await runs.matrix(member(), code)
    assert _turns(grid) == [["turn-b1"], ["turn-a2"], ["turn-a1"]], "a day lists its newest question first, as before"
    assert grid["total"] == 3


# ── 14 Nov 2026 (Hussain, on R57's grid showing R51-1 ...): "I need to see the selected run only, not something else; and
# when I select All runs it should show all runs". A batch row names its own session and turn, so the page names it by
# its scenario and opens it inside the batch, never by the code of the run the question row was filed in. ────────────

async def test_a_batch_row_names_its_session_turn_and_scenario(lab_scratch, main_scratch):
    from test_trace_lab_eval_store_sql import _scenario

    await _scenario(lab_scratch)
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", turn_ids=("t-1", "t-2"), scenario=("MT-001", 1))
    await runs.sync_sessions(None)
    (batch,) = await _batches_of("baseline_v0")
    rows = (await runs.matrix(member(), batch["code"]))["questions"]
    assert [(q["session_id"], q["turn_id"], q["scenario_id"], q["turn_no"], q["session_turns"]) for q in rows] == [
        ("scn__baseline_v0__MT-001__1", "t-1", "MT-001", 1, 2), ("scn__baseline_v0__MT-001__1", "t-2", "MT-001", 2, 2)]


async def test_a_live_days_rows_name_no_session(lab_scratch, main_scratch):
    live = await _old_day(main_scratch, lab_scratch)
    (row,) = (await runs.matrix(member(), live))["questions"]
    assert row["session_id"] is None and row["code"].startswith(live + "-")


async def test_all_runs_labels_every_run_once_grouped_in_the_pickers_order(lab_scratch, main_scratch):
    live = await _old_day(main_scratch, lab_scratch)
    await runs.ensure_batch_runs(None)
    (batch,) = await _batches_of("hussain_5q_v0")
    typed = await runs.create_run(member(), title="A typed batch", company=ORG,
                                  questions=[{"question": "How many floors?"}, {"question": "Which lifts are down?"}])
    lin = await _mode()
    await runs.set_cell(member(), typed["code"] + "-1", mode=lin, value="negative")
    got = await runs.matrix_all(member())
    order = [r["code"] for r in (await runs.list_runs(member(), limit=200))["runs"]]
    groups = [g["run"]["code"] for g in got["runs"]]
    assert groups == [c for c in order if c in {live, batch["code"], typed["code"]}], "the top bar's order"
    by = {g["run"]["code"]: [q["turn_ids"] or [q["question"]] for q in g["questions"]] for g in got["runs"]}
    assert by[batch["code"]] == [["turn-s1"]] and by[live] == [["turn-h1"]]
    assert by[typed["code"]] == [["How many floors?"], ["Which lifts are down?"]]
    assert got["total"] == 4 and got["more"] is False and got["modes"] and got["last_updated"]
    q1 = next(q for g in got["runs"] for q in g["questions"] if q["code"] == typed["code"] + "-1")
    assert [e["value"] for e in got["cells"][q1["id"]][lin]] == ["negative"]


async def test_all_runs_skips_runs_with_nothing_to_label_and_stays_in_the_callers_company(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__co_v0__SD-1__1", tag="co_v0", turn_ids=("t-1",))
    await seed_session(lab_scratch, "scn__co_v0__SD-9__1", tag="co_v0", turn_ids=("t-9",), org=OTHER_ORG,
                       at=T0 + timedelta(minutes=5))
    await runs.sync_sessions(None)
    await runs.create_run(member(), title="Empty", company=ORG)
    own = member(org=ORG, all_companies=False, role="admin")
    got = await runs.matrix_all(own)
    assert [[q["turn_ids"] for q in g["questions"]] for g in got["runs"]] == [[["t-1"]]]
    assert {g["run"]["organization_id"] for g in got["runs"]} == {ORG}


async def test_all_runs_stops_at_the_row_limit_and_says_so(lab_scratch, main_scratch, monkeypatch):
    for n in range(3):
        await runs.create_run(member(), title=f"Typed {n}", company=ORG, questions=[{"question": "Q1"}, {"question": "Q2"}])
    monkeypatch.setattr(runs, "MATRIX_MAX", 3)
    got = await runs.matrix_all(member())
    assert sum(len(g["questions"]) for g in got["runs"]) == 3 and got["more"] is True and got["total"] == 6


async def test_the_all_runs_route(lab_scratch, main_scratch):
    import json

    from trace_lab import routes_runs

    await runs.create_run(member(), title="Typed", company=ORG, questions=[{"question": "Q1"}])
    got = await routes_runs.lab_labels_all(m=member())
    assert got["ok"] is True and len(got["runs"]) == 1
    json.dumps(got)
