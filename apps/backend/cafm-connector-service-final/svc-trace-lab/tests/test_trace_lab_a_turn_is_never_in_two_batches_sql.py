"""Sampling on a scratch Postgres: the pool is one company's chat answers inside the window - no golden run, no
turn without a company, no deleted chat, no superadmin view-as unless asked; a uniform batch of 15 is drawn with its
seed and every reason recorded; the next batch never redraws a sampled turn; a withdrawn batch leaves the queue; the
manifest is the record. The chat's tables are read through the read-only main engine, the batches live in the lab
database, and no statement names both (Golden Rule G0). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import event, text

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import annotations, db, sampling
from trace_lab.annotations import Anchor
from trace_lab.review_errors import Conflict, Invalid, NotFound
from trace_lab_m2_fixtures import NOW, add_thread, add_turn, principal, truncate_chat, turn_id

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
HOME = str(uuid.uuid4())             # the platform team's own company
WINDOW = dict(since=NOW - timedelta(days=7), until=NOW + timedelta(hours=1))
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALTER|DROP|GRANT|COPY)\b", re.I)


@pytest.fixture
async def eng(main_scratch, lab_scratch):  # noqa: F811
    """30 eligible answers of ORG, plus the ones the pool must leave out. Yields the writable main engine."""
    for n in range(30):
        await add_turn(main_scratch, n, ORG, minutes_ago=10 + n, tokens=1000 + 50 * n)
    for n in range(100, 105):
        await add_turn(main_scratch, n, OTHER)
    await add_turn(main_scratch, 200, ORG, session="golden-run-1-case-a-1")
    await add_turn(main_scratch, 201, None)
    await add_thread(main_scratch, "sess-deleted", ORG, deleted=True)
    await add_turn(main_scratch, 202, ORG, session="sess-deleted")
    yield main_scratch
    await truncate_chat(main_scratch)


async def _superadmins(engine, *rows):
    """The platform team's accounts: (user_id, own company) pairs in plenum_cafm.users, as the sign-in service keeps them."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS plenum_cafm.users "
                                "(id uuid PRIMARY KEY, organization_id uuid NOT NULL, platform_role varchar(20) NOT NULL)"))
        for uid, org in rows:
            await conn.execute(text("INSERT INTO plenum_cafm.users (id, organization_id, platform_role) "
                                    "VALUES (CAST(:u AS uuid), CAST(:o AS uuid), 'superadmin')"), {"u": uid, "o": org})


async def _asked_by(engine, tid, user_id):
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE plenum_cafm.agent_trace_turns SET user_id = CAST(:u AS uuid) WHERE turn_id = :t"),
                           {"u": user_id, "t": tid})


def req(**kw):
    base = {"plan_step": "initial_uniform", "strategy": "uniform", "k": 15, "seed": 7, "channel": "chat",
            "reviews_required": 1, "source": "bow", **WINDOW}
    base.update(kw)
    return base


async def _count(engine, table):
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_the_pool_is_one_companys_eligible_chat_answers(eng):
    me = principal(ORG)
    pool, truncated = await sampling.pool(me, ORG, **WINDOW)
    ids = {c.turn_id for c in pool}
    assert len(ids) == 30 and not truncated
    for n in (100, 200, 201, 202):               # another company, golden, no company, deleted chat
        assert turn_id(n) not in ids
    with_deleted, _ = await sampling.pool(me, ORG, **WINDOW, include_deleted=True)
    assert turn_id(202) in {c.turn_id for c in with_deleted}
    assert turn_id(200) not in {c.turn_id for c in with_deleted}      # a golden run is never in the pool


async def test_the_pool_is_a_chat_pool_unless_another_channel_is_asked_for(eng):
    await add_turn(eng, 300, ORG, question="Validate and ingest this document.")
    me = principal(ORG)
    chat, _ = await sampling.pool(me, ORG, **WINDOW)
    uploads, _ = await sampling.pool(me, ORG, **WINDOW, channel="upload")
    assert turn_id(300) not in {c.turn_id for c in chat}
    assert [c.turn_id for c in uploads] == [turn_id(300)] and uploads[0].dims["channel"] == "upload"
    golden, _ = await sampling.pool(me, ORG, **WINDOW, channel="golden")       # never, whatever is asked
    assert golden == []


async def test_superadmin_view_as_answers_are_left_out_unless_asked_for(eng):
    ghost, visiting, at_home = (str(uuid.uuid4()) for _ in range(3))
    await _superadmins(eng, (visiting, HOME), (at_home, ORG))
    await add_turn(eng, 400, ORG)
    await add_turn(eng, 401, ORG)
    await add_turn(eng, 402, ORG)
    await _asked_by(eng, turn_id(400), visiting)         # a superadmin of another company, viewing as ORG
    await _asked_by(eng, turn_id(401), at_home)          # a superadmin asking inside their own company
    await _asked_by(eng, turn_id(402), ghost)            # an ordinary account
    me = principal(ORG)
    ids = {c.turn_id for c in (await sampling.pool(me, ORG, **WINDOW))[0]}
    assert turn_id(400) not in ids and {turn_id(401), turn_id(402)} <= ids and len(ids) == 32
    with_va = {c.turn_id for c in (await sampling.pool(me, ORG, **WINDOW, include_view_as=True))[0]}
    assert turn_id(400) in with_va and len(with_va) == 33


async def test_a_database_without_the_accounts_table_still_gives_a_pool(eng):
    pool, _ = await sampling.pool(principal(ORG), ORG, **WINDOW)         # no plenum_cafm.users here
    assert len(pool) == 30


async def test_the_asker_role_comes_from_the_account_when_it_is_known(eng):
    boss = str(uuid.uuid4())
    await _superadmins(eng, (boss, ORG))
    await _asked_by(eng, turn_id(3), boss)
    pool, _ = await sampling.pool(principal(ORG), ORG, **WINDOW)
    roles = {c.turn_id: c.dims["asker_role"] for c in pool}
    assert roles[turn_id(3)] == "superadmin" and roles[turn_id(4)] is None


async def test_turn_position_counts_the_whole_window_before_sampled_turns_leave_the_pool(eng):
    await add_turn(eng, 500, ORG, session="sess-long", minutes_ago=300)
    await add_turn(eng, 501, ORG, session="sess-long", minutes_ago=200)
    await add_turn(eng, 502, ORG, session="sess-long", minutes_ago=100)
    me = principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=60))      # a draw takes one answer per conversation
    long_ones = {turn_id(500), turn_id(501), turn_id(502)}
    drawn = long_ones & {i["turn_id"] for i in out["items"]}
    assert len(drawn) == 1
    everything, _ = await sampling.pool(me, ORG, **WINDOW, exclude_sampled=False)
    positions = {c.turn_id: c.features["turn_position"] for c in everything}
    assert (positions[turn_id(500)], positions[turn_id(501)], positions[turn_id(502)]) == (1, 2, 3)
    left, _ = await sampling.pool(me, ORG, **WINDOW)           # the drawn one is gone; the others keep their place
    assert {c.turn_id: c.features["turn_position"] for c in left if c.turn_id in long_ones} == {
        t: positions[t] for t in long_ones - drawn}


async def test_a_uniform_batch_of_15_records_its_seed_and_reasons_and_preview_writes_nothing(eng):
    me = principal(ORG)
    pv = await sampling.preview(me, ORG, req())
    assert len(pv["picks"]) == 15 and pv["pool_size"] == 30 and pv["excluded_sampled"] == 0 and not pv["truncated"]
    assert await _count(db.lab_engine(), "tracelab.sample_batches") == 0
    assert await _count(db.lab_engine(), "tracelab.evidence_turns") == 0
    out = await sampling.create_batch(me, ORG, req())
    b = out["batch"]
    assert (b["seed"], b["selected"], b["counts_for_prevalence"], b["strategy"]) == (7, 15, True, "uniform")
    assert b["items"] == 15 and b["feature_version"] == sampling.FEATURE_VERSION
    assert [i["turn_id"] for i in out["items"]] == [p["turn_id"] for p in pv["picks"]]
    assert all(i["reason"] == "uniform pick" for i in out["items"])
    man = await sampling.manifest(me, ORG)
    mb = man["batches"][0]
    assert man["schema_version"] == 1 and mb["seed"] == 7 and len(mb["items"]) == 15 and mb["items"][0]["reason"] == "uniform pick"


async def test_every_sampled_answer_is_copied_into_the_evidence_tables(eng):
    out = await sampling.create_batch(principal(ORG), ORG, req(k=5))
    async with db.lab_engine().connect() as conn:
        rows = (await conn.execute(text("SELECT turn_id, reason FROM tracelab.evidence_turns ORDER BY turn_id"))).all()
    assert [r[0] for r in rows] == sorted(i["turn_id"] for i in out["items"])
    assert {r[1] for r in rows} == {"sampled"}


async def test_a_batch_whose_evidence_cannot_be_kept_is_not_written(eng, monkeypatch):
    async def broken(*a, **kw):
        raise RuntimeError("the lab database is down")

    monkeypatch.setattr(sampling.evidence, "snapshot", broken)
    with pytest.raises(RuntimeError):
        await sampling.create_batch(principal(ORG), ORG, req(k=5))
    assert await _count(db.lab_engine(), "tracelab.sample_batches") == 0


async def test_the_next_batch_never_redraws_a_sampled_turn_and_an_empty_pool_is_refused(eng):
    me = principal(ORG)
    first = {i["turn_id"] for i in (await sampling.create_batch(me, ORG, req()))["items"]}
    second_out = await sampling.create_batch(me, ORG, req(plan_step="extra", seed=8))
    second = {i["turn_id"] for i in second_out["items"]}
    assert len(second) == 15 and not (first & second)
    assert second_out["batch"]["excluded_sampled"] == 15
    with pytest.raises(Invalid) as e:
        await sampling.create_batch(me, ORG, req(plan_step="extra", seed=9))
    assert e.value.reason == "pool_empty"
    assert await _count(db.lab_engine(), "tracelab.sample_batches") == 2          # the refused draw wrote nothing


async def test_a_turn_another_batch_took_first_is_a_409_pool_changed_and_writes_nothing(eng, monkeypatch):
    me = principal(ORG)
    taken = (await sampling.create_batch(me, ORG, req(k=3)))["items"][0]["turn_id"]
    real = sampling._load_pool

    async def stale(*a, **kw):                      # a pool read before the other batch landed: it still holds `taken`
        return await real(*a, **{**kw, "exclude_sampled": False})

    monkeypatch.setattr(sampling, "_load_pool", stale)
    with pytest.raises(Conflict) as e:
        await sampling.create_batch(me, ORG, req(k=60, plan_step="extra"))
    assert e.value.reason == "pool_changed" and e.value.status == 409
    assert taken
    assert await _count(db.lab_engine(), "tracelab.sample_batches") == 1
    assert await _count(db.lab_engine(), "tracelab.sample_items") == 3


async def test_a_dimension_batch_does_not_count_toward_rates(eng):
    out = await sampling.create_batch(principal(ORG), ORG, req(plan_step="dimension", strategy="dimension", k=10,
                                                               dimension="route_target"))
    assert out["batch"]["counts_for_prevalence"] is False
    assert all(i["reason"].startswith("dimension route_target = ") for i in out["items"])


async def test_the_queue_follows_my_verdicts_and_a_withdrawn_batch_leaves_it(eng):
    me, other = principal(ORG), principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=5))
    q = await sampling.queue(me, ORG)
    assert q["remaining"] == 5 and len(q["items"]) == 5
    first = q["items"][0]["turn_id"]
    await annotations.save_verdict(me, ORG, first, verdict="right", note=None, anchor=None, base=None)
    assert first not in {i["turn_id"] for i in (await sampling.queue(me, ORG))["items"]}
    assert first not in {i["turn_id"] for i in (await sampling.queue(other, ORG))["items"]}   # 1 review needed, done
    plan = await sampling.plan_status(me, ORG)
    assert plan["steps"][0]["selected"] == 5 and plan["steps"][0]["reviewed"] == 1 and plan["distinct_sampled"] == 5
    wd = await sampling.withdraw(me, ORG, out["batch"]["id"], reason="Drawn from the wrong period.")
    assert wd["withdrawn_reason"] == "Drawn from the wrong period."
    with pytest.raises(Conflict) as e:
        await sampling.withdraw(me, ORG, out["batch"]["id"], reason="again")
    assert e.value.reason == "already_withdrawn"
    assert (await sampling.queue(me, ORG))["items"] == []
    assert (await sampling.plan_status(me, ORG))["distinct_sampled"] == 1   # only the judged item still counts


async def test_the_queue_keeps_a_batch_open_until_enough_reviewers_have_judged(eng):
    me, other = principal(ORG), principal(ORG)
    await sampling.create_batch(me, ORG, req(k=2, reviews_required=2))
    mine = (await sampling.queue(me, ORG))["items"]
    await annotations.save_verdict(me, ORG, mine[0]["turn_id"], verdict="right", note=None, anchor=None, base=None)
    assert [i["turn_id"] for i in (await sampling.queue(me, ORG))["items"]] == [mine[1]["turn_id"]]
    theirs = (await sampling.queue(other, ORG))["items"]
    assert {i["turn_id"] for i in theirs} == {i["turn_id"] for i in mine}              # the second review is still owed
    assert {i["turn_id"]: i["others_count"] for i in theirs}[mine[0]["turn_id"]] == 1


async def test_the_queue_can_be_limited_to_one_batch(eng):
    me = principal(ORG)
    a = (await sampling.create_batch(me, ORG, req(k=2)))["batch"]["id"]
    b = (await sampling.create_batch(me, ORG, req(k=3, plan_step="extra", seed=2)))["batch"]["id"]
    assert (await sampling.queue(me, ORG, batch_id=a))["remaining"] == 2
    assert (await sampling.queue(me, ORG, batch_id=b))["remaining"] == 3
    with pytest.raises(NotFound):
        await sampling.queue(me, ORG, batch_id="not-a-batch")


async def test_progress_counts_reviews_and_the_fair_sample(eng):
    me = principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=4))
    tids = [i["turn_id"] for i in out["items"]]
    await annotations.save_verdict(me, ORG, tids[0], verdict="right", note=None, anchor=None, base=None)
    await annotations.save_verdict(me, ORG, tids[1], verdict="wrong", note="Counted July.",
                                   anchor=Anchor(None, "answer", "758"), base=None)
    p = await sampling.progress(me, ORG, since=None, until=None, block=5)
    assert p["open_coding"]["reviewed"] == 2 and p["open_coding"]["missing"] == 2
    assert (p["fair_sample"]["wrong"], p["fair_sample"]["right"], p["fair_sample"]["rate"]) == (1, 1, 0.5)
    # open coding's own interim rows (wrong verdicts per block); GET /progress replaces them with the failure-mode
    # curve and hint from Phase 5 (pinned in test_trace_lab_progress_m4)
    interim = await sampling._progress_m2(me, ORG, since=None, until=None, block=5)
    assert interim["discovery"]["points"][0]["reviews_through"] == 2 and interim["saturation"]["available"] is False
    assert isinstance(p["discovery"], list) and "hint" in p["saturation"]
    assert p["coverage"]["pool"] == 30 and p["open_batches"] == 1 and p["scope"] == "company"


async def test_progress_ignores_verdicts_on_golden_answers(eng):
    me = principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=2))
    await annotations.save_verdict(me, ORG, out["items"][0]["turn_id"], verdict="right", note=None, anchor=None, base=None)
    gid = "turn-g00000000000001"
    async with db.lab_engine().begin() as conn:
        from golden_seed import seed_result, seed_run, seed_set
        s = await seed_set(conn, ORG, cases=(("21", ()),))
        run = await seed_run(conn, ORG, s["set_id"])
        await seed_result(conn, ORG, run, s["cases"]["21"], "21", turn_id=gid)
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason) "
            "VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
            {"t": gid, "o": ORG, "j": '{"turn_id": "%s", "organization_id": "%s", "question": "q", "answer": "a"}' % (gid, ORG)})
    await annotations.save_verdict(me, ORG, gid, verdict="right", note=None, anchor=None, base=None)
    p = await sampling.progress(me, ORG, since=None, until=None, block=5)
    assert p["open_coding"]["reviewed"] == 1                              # the golden verdict is not a live-traffic review


async def test_the_platform_team_sees_every_company_but_a_company_sees_only_its_own(eng):
    boss = principal(None, role="superadmin")
    mine, theirs = principal(ORG), principal(OTHER)
    a = (await sampling.create_batch(mine, ORG, req(k=4)))["batch"]["id"]
    b = (await sampling.create_batch(theirs, OTHER, req(k=3)))["batch"]["id"]
    assert {x["id"] for x in await sampling.list_batches(boss, None)} == {a, b}
    assert {x["id"] for x in await sampling.list_batches(mine, ORG)} == {a}
    assert await sampling.batch_detail(mine, ORG, b) is None
    assert (await sampling.batch_detail(mine, ORG, a))["batch"]["id"] == a
    assert await sampling.batch_detail(mine, ORG, "nonsense") is None
    with pytest.raises(NotFound):
        await sampling.withdraw(mine, ORG, b, reason="not mine")              # another company's batch
    man = await sampling.manifest(boss, None)
    assert {x["id"] for x in man["batches"]} == {a, b} and man["plan"] is None
    await annotations.save_verdict(mine, ORG, (await sampling.queue(mine, ORG))["items"][0]["turn_id"],
                                   verdict="right", note=None, anchor=None, base=None)
    plat = await sampling.progress(boss, None, since=None, until=None)
    assert plat["scope"] == "platform" and plat["plan"] is None and plat["coverage"] is None
    assert {r["organization_id"]: (r["sampled"], r["reviewed"]) for r in plat["by_company"]} == {ORG: (4, 1), OTHER: (3, 0)}
    one = await sampling.progress(boss, None, since=None, until=None, filter_org=ORG)
    assert one["scope"] == "company" and one["organization_id"] == ORG and one["open_coding"]["reviewed"] == 1
    with pytest.raises(Invalid) as e:
        await sampling.progress(boss, None, since=None, until=None, filter_org="not-a-company")
    assert e.value.reason == "bad_company"


async def test_the_map_marks_what_is_in_a_batch_and_what_is_reviewed(eng):
    me = principal(ORG)
    out = await sampling.create_batch(me, ORG, req(k=4))
    first = out["items"][0]["turn_id"]
    await annotations.save_verdict(me, ORG, first, verdict="right", note=None, anchor=None, base=None)
    m = await sampling.map_view(me, ORG, **WINDOW)
    assert m["pool_size"] == 30 and not m["truncated"] and not m["too_few"]
    nodes = {n["turn_id"]: n for n in m["nodes"]}
    assert nodes[first]["reviewed"] and nodes[first]["reviewed_by_me"] and nodes[first]["batch_id"] == out["batch"]["id"]
    assert sum(1 for n in m["nodes"] if n["batch_id"]) == 4 and sum(1 for n in m["nodes"] if n["reviewed"]) == 1
    with pytest.raises(Invalid) as e:
        await sampling.map_view(me, ORG, since=NOW - timedelta(days=500), until=NOW)
    assert e.value.reason == "window_outside_retention"


async def test_plan_status_reports_the_pool_left_in_a_window(eng):
    me = principal(ORG)
    await sampling.create_batch(me, ORG, req(k=5))
    plan = await sampling.plan_status(me, ORG, **WINDOW)
    assert plan["pool_size"] == 25 and plan["next_step"] == "initial_uniform"       # 5 of the first step's 15 are drawn


async def test_a_monitor_flagged_batch_records_only_the_companys_unsampled_named_answers(eng):
    me = principal(ORG)
    taken = (await sampling.create_batch(me, ORG, req(k=1)))["items"][0]["turn_id"]
    free = [t for t in (turn_id(n) for n in range(30)) if t != taken][:2]
    named = free + [taken, turn_id(100), turn_id(200), turn_id(201), "turn-not-there"]   # sampled, other company,
    flagged = {"strategy": "monitor_flagged", "seed": 11, "channel": "chat",               # golden, no company, absent
               "params": {"monitor_batch_id": "batch-1", "mode_id": "mode-1"}, **WINDOW,
               "items": [{"turn_id": t, "session_id": None, "reason": "flagged by the judge"} for t in named]}
    out = await sampling.create_batch(me, ORG, flagged)
    b = out["batch"]
    assert (b["strategy"], b["plan_step"], b["counts_for_prevalence"], b["reviews_required"], b["seed"]) == (
        "monitor_flagged", "extra", False, 1, 11)
    assert (b["requested"], b["pool_size"], b["excluded_sampled"], b["selected"]) == (7, 3, 1, 2)
    assert b["feature_version"] is None and b["params"] == {"monitor_batch_id": "batch-1", "mode_id": "mode-1"}
    assert [(i["turn_id"], i["position"], i["reason"]) for i in out["items"]] == [
        (free[0], 1, "flagged by the judge"), (free[1], 2, "flagged by the judge")]
    assert set(free) <= {i["turn_id"] for i in (await sampling.queue(me, ORG))["items"]}
    async with db.lab_engine().connect() as conn:                 # the flagged answers are kept as evidence too
        kept = {r[0] for r in (await conn.execute(text("SELECT turn_id FROM tracelab.evidence_turns"))).all()}
    assert set(free) <= kept
    with pytest.raises(Invalid) as e:
        await sampling.create_batch(me, ORG, flagged)                                   # all three now sampled
    assert e.value.reason == "pool_empty"


async def test_an_enrich_batch_seeds_from_the_companys_wrong_verdicts(eng):
    me = principal(ORG)
    await add_turn(eng, 600, ORG, question="How many chillers are in B-101?", answer="There are 4 chillers in B-101.")
    await annotations.save_verdict(me, ORG, turn_id(600), verdict="wrong", note="Off.",
                                   anchor=Anchor(None, "answer", "4 chillers"), base=None)
    seeds = await sampling._wrong_seeds(ORG)
    assert [s.turn_id for s in seeds] == [turn_id(600)] and "chillers" in (seeds[0].text or "")
    pv = await sampling.preview(me, ORG, req(strategy="enrich", plan_step="depth_search", k=5))
    assert pv["picks"] and all(p["turn_id"] != turn_id(600) for p in pv["picks"])      # the seed is already sampled/judged


async def test_no_statement_names_both_databases_and_the_main_one_is_only_read(eng):
    seen: dict[str, list[str]] = {"main": [], "lab": []}

    def recorder(name):
        def record(conn, cursor, statement, parameters, context, executemany):
            seen[name].append(statement)
        return record

    pairs = ((db.main_engine().sync_engine, recorder("main")), (db.lab_engine().sync_engine, recorder("lab")))
    for engine, fn in pairs:
        event.listen(engine, "before_cursor_execute", fn)
    try:
        me = principal(ORG)
        await _superadmins(eng, (str(uuid.uuid4()), HOME))
        await sampling.pool(me, ORG, **WINDOW)
        await sampling.preview(me, ORG, req(strategy="enrich", plan_step="depth_search", k=5))
        out = await sampling.create_batch(me, ORG, req(k=6))
        await sampling.create_batch(me, ORG, {"strategy": "monitor_flagged", "seed": 1, **WINDOW, "items": [
            {"turn_id": turn_id(29), "reason": "flagged"}]})
        await sampling.list_batches(me, ORG)
        await sampling.batch_detail(me, ORG, out["batch"]["id"])
        await sampling.queue(me, ORG)
        await sampling.plan_status(me, ORG, **WINDOW)
        await sampling.manifest(me, ORG)
        await sampling.map_view(me, ORG, **WINDOW)
        await sampling.progress(me, ORG, since=None, until=None)
        await sampling.progress(principal(None, role="superadmin"), None, since=None, until=None)
        await sampling.withdraw(me, ORG, out["batch"]["id"], reason="Checking the record.")
    finally:
        for engine, fn in pairs:
            event.remove(engine, "before_cursor_execute", fn)
    assert len(seen["main"]) > 10 and len(seen["lab"]) > 20
    for name, other in (("main", "tracelab"), ("lab", "plenum_cafm")):
        stray = [s for s in seen[name] if other in s.lower()]
        assert stray == [], f"the {name} engine was sent SQL naming {other}: {stray[:1]}"
    assert [s for s in seen["main"] if WRITE.match(s)] == []
