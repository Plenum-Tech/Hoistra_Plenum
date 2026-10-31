"""GET /progress carries the Phase 5 rows (spec §4): completeness, per mode, discovery, saturation, re-review, unfiled
notes; the enrich batch draws its seeds from a failure mode, or takes a depth scan's decided suggestions in the order
they were decided (spec §3.3). The first part needs no database; the rest needs the scratch database."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
import trace_lab_m2_fixtures as m2
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import sampling, taxonomy
from trace_lab.review_errors import Invalid

NAME = "counts_cancelled_jobs_as_open"
DRAFT = {"name": NAME, "definition": "Present when the answer counts cancelled jobs as open.", "boundary": "",
         "level": "technical", "agent": "wo_engine"}


async def test_progress_returns_the_open_coding_rows_plus_the_taxonomy_rows(monkeypatch):
    calls = []

    async def open_coding(principal, org, *, since=None, until=None, block=10, filter_org=None):
        calls.append(("open_coding", org, filter_org, block))
        return {"plan": {"steps": []}, "open_coding": {"reviewed": 3}, "organization_id": "company-b",
                "discovery": {"points": []}, "saturation": {"available": False}}

    async def extension(principal, org, *, block=10):
        calls.append(("extension", org, block))
        return {"completeness": {"required": 0}, "modes": [], "discovery": [], "discovery_by_batch": [],
                "saturation": {"hint": False, "text": ""}, "re_review": {"count": 0}, "unfiled_notes": 2}

    monkeypatch.setattr(sampling, "_progress_m2", open_coding)
    monkeypatch.setattr(taxonomy, "progress_extension", extension)
    out = await sampling.progress(object(), None, since=None, until=None, block=20, filter_org="company-b")
    assert out["open_coding"] == {"reviewed": 3} and out["unfiled_notes"] == 2 and "completeness" in out
    assert out["discovery"] == [] and out["saturation"] == {"hint": False, "text": ""}      # the taxonomy rows replace the interim ones
    assert calls == [("open_coding", None, "company-b", 20), ("extension", "company-b", 20)]   # same company, same block


async def test_the_taxonomy_rows_read_the_company_the_open_coding_rows_read(monkeypatch):
    seen = []

    async def open_coding(principal, org, **kw):
        return {"organization_id": org}

    async def extension(principal, org, *, block=10):
        seen.append(org)
        return {"unfiled_notes": 0}

    monkeypatch.setattr(sampling, "_progress_m2", open_coding)
    monkeypatch.setattr(taxonomy, "progress_extension", extension)
    await sampling.progress(object(), "company-a", since=None, until=None)
    await sampling.progress(object(), None, since=None, until=None)
    assert seen == ["company-a", None]


# ── the enrich options, checked before anything is read ────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)
MODE, RUN = str(uuid.uuid4()), str(uuid.uuid4())


def _req(**kw):
    base = {"plan_step": "depth_search", "strategy": "enrich", "k": 10, "seed": 7, "channel": "chat", "reviews_required": 1,
            "since": NOW - timedelta(days=3), "until": NOW + timedelta(hours=1)}
    base.update(kw)
    return base


@pytest.mark.parametrize("extra", [{}, {"mode_id": MODE}, {"mode_id": MODE.upper()}, {"source": "bow"},
                                   {"source": "depth_scan", "proposal_run_id": RUN}, {"mode_id": None, "proposal_run_id": None}])
def test_an_enrich_batch_may_seed_from_a_mode_or_from_a_depth_scan(extra):
    sampling.check_request(_req(**extra), now=NOW)


@pytest.mark.parametrize("extra", [{"mode_id": "m1"}, {"mode_id": "not-a-uuid"}, {"source": "depth_scan"}, {"source": "depth_scan", "proposal_run_id": "r1"},
                                   {"source": "depth_scan", "proposal_run_id": RUN, "mode_id": MODE},
                                   {"source": "other"}, {"proposal_run_id": RUN}])
def test_an_enrich_option_that_names_nothing_real_is_refused(extra):
    with pytest.raises(Invalid) as e:
        sampling.check_request(_req(**extra), now=NOW)
    assert e.value.reason == "bad_seed_source" and e.value.error.endswith(".") and e.value.status == 422


@pytest.mark.parametrize("extra", [{"mode_id": MODE}, {"source": "depth_scan", "proposal_run_id": RUN}, {"proposal_run_id": RUN}])
@pytest.mark.parametrize("strategy", ["uniform", "diversity", "dimension", "outlier"])
def test_only_the_enrich_batch_takes_a_failure_mode_or_a_depth_scan(strategy, extra):
    with pytest.raises(Invalid) as e:
        sampling.check_request(_req(strategy=strategy, plan_step="initial_uniform", dimension="channel", **extra), now=NOW)
    assert e.value.reason == "bad_seed_source"


def test_a_depth_scan_batch_has_no_random_picks_and_a_mode_batch_keeps_the_default_share():
    assert sampling.random_share_for(_req(source="depth_scan", proposal_run_id=RUN, random_share=0.4)) == 0.0
    assert sampling.random_share_for(_req(mode_id=MODE)) == 0.2
    assert sampling.random_share_for(_req(random_share=0.3)) == 0.3


def _cand(i, text_, session=None):
    return sampling.Candidate(turn_id=f"t{i}", session_id=session or f"s{i}", started_at=NOW, features={}, dims={}, text=text_,
                              question=text_.split("\n")[0])


def test_picks_in_decision_order_are_only_answers_still_in_the_pool_each_once_with_their_reason():
    pool = [_cand(i, f"question {i}\nanswer {i}") for i in range(6)]
    ordered = [("t4", "scan, accepted"), ("gone", "scan, accepted"), ("t1", "scan, dismissed"), ("t4", "again"),
               ("t0", "scan, accepted"), ("t3", "scan, accepted")]
    picks = sampling.pick_ordered(pool, 3, ordered)
    assert [(p.turn_id, p.reason) for p in picks] == [("t4", "scan, accepted"), ("t1", "scan, dismissed"), ("t0", "scan, accepted")]
    assert sampling.pick_ordered(pool, 10, [])  == [] and sampling.pick_ordered([], 10, ordered) == []


def test_picks_in_decision_order_leave_a_conversation_alone_once_it_has_one_pick():
    pool = [_cand(0, "q\na", session="same"), _cand(1, "q2\nb", session="same"), _cand(2, "q3\nc", session="other")]
    picks = sampling.pick_ordered(pool, 3, [("t0", "x"), ("t1", "y"), ("t2", "z")])
    assert [p.turn_id for p in picks] == ["t0", "t2"]


def test_enrich_names_what_it_was_seeded_from_in_each_reason():
    pool = [_cand(i, f"chillers in building {i}\nthere are {i} chillers") for i in range(4)]
    seeds = [_cand(9, "chillers in building 9\nthere are 9 chillers")]
    default = sampling.pick_enrich(pool, 2, 7, seeds=seeds, random_share=0)
    assert all(p.reason.startswith("similar to a wrong answer (") for p in default)
    named = sampling.pick_enrich(pool, 2, 7, seeds=seeds, random_share=0, seed_label="an answer with this failure mode")
    assert all(p.reason.startswith("similar to an answer with this failure mode (") for p in named)
    assert [p.turn_id for p in named] == [p.turn_id for p in default]


# ── the Phase 5 rows of GET /progress, on real rows ────────────────────────────────────────────────────────

@requires_scratch
async def test_the_extension_reads_real_rows(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-s000000000000001")
    async with lab_scratch.begin() as conn:
        note = await fx.add_note(conn, "turn-s000000000000001")
    mode = (await taxonomy.create(fx.owner(), dict(DRAFT), created_from=[note]))["mode"]
    out = await taxonomy.progress_extension(fx.owner(), None, block=10)
    assert out["completeness"]["modes"] == 0                      # a candidate does not count toward completeness
    per = next(m for m in out["modes"] if m["mode_id"] == mode["id"])
    assert (per["first_failure"], per["any_instance"]) == (1, 1)
    assert per["sample_fraction"]["kind"] == "sample_fraction"
    assert out["discovery"] == [{"block": 1, "reviews_through": 1, "new_modes": 1, "cumulative_modes": 1}]
    assert out["unfiled_notes"] == 0
    assert set(out) == {"completeness", "modes", "discovery", "discovery_by_batch", "saturation", "re_review", "unfiled_notes"}
    assert out["saturation"]["hint"] is False and out["re_review"]["count"] == 0


@requires_scratch
async def test_unfiled_notes_are_counted_and_a_company_reads_only_its_own(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-s100000000000001")
        await fx.add_turn(conn, "turn-s100000000000002", fx.ORG_B)
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-s100000000000001")
        await fx.add_note(conn, "turn-s100000000000002", fx.ORG_B)
    assert (await taxonomy.progress_extension(fx.owner(), None))["unfiled_notes"] == 2
    assert (await taxonomy.progress_extension(fx.admin(), fx.ORG_A))["unfiled_notes"] == 1
    assert (await taxonomy.progress_extension(fx.admin(fx.ORG_B), fx.ORG_B))["unfiled_notes"] == 1
    # a superadmin acting for company B is that company's reviewer, not the platform: B's notes stay blind to them
    assert (await taxonomy.progress_extension(fx.owner(), fx.ORG_B))["unfiled_notes"] == 0
    with pytest.raises(Exception) as e:
        await taxonomy.progress_extension(fx.admin(), fx.ORG_B)
    assert getattr(e.value, "reason", None) == "reviewers_only"


@requires_scratch
async def test_get_progress_answers_both_the_open_coding_and_the_taxonomy_rows(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await fx.add_turn(conn, "turn-s200000000000001")
    async with lab_scratch.begin() as conn:
        await fx.add_note(conn, "turn-s200000000000001")
    out = await sampling.progress(fx.admin(), fx.ORG_A, since=None, until=None, block=5)
    assert out["scope"] == "company" and out["organization_id"] == fx.ORG_A and out["open_coding"]["reviewed"] == 1
    assert out["unfiled_notes"] == 1 and out["completeness"]["reviewed_turns"] == 1
    assert isinstance(out["discovery"], list) and set(out["saturation"]) >= {"hint", "text"}
    platform = await sampling.progress(fx.owner(), None, since=None, until=None)
    assert platform["scope"] == "platform" and platform["unfiled_notes"] == 1
    one = await sampling.progress(fx.owner(), None, since=None, until=None, filter_org=fx.ORG_B)
    assert one["organization_id"] == fx.ORG_B and one["unfiled_notes"] == 0 and one["completeness"]["reviewed_turns"] == 0


# ── enrich: seeds from a mode, candidates from a depth scan ────────────────────────────────────────────────

@requires_scratch
async def test_enrich_inputs_from_a_mode_and_from_a_depth_scan(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        await fx.add_label(conn, "turn-s100000000000001", mid, present=True)
        run = await fx.add_run(conn, "depth_scan", mode_id=mid)
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-s100000000000002", mode_id=mid, status="accepted")
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-s100000000000003", mode_id=mid, status="dismissed")
    async with lab_scratch.connect() as conn:
        seeds = await sampling.enrich_inputs(conn, fx.ORG_A, mode_id=mid)
        ordered = await sampling.enrich_inputs(conn, fx.ORG_A, source="depth_scan", proposal_run_id=run)
    assert seeds == {"seed_turn_ids": ["turn-s100000000000001"]}
    assert sorted(ordered["ordered"]) == [
        ("turn-s100000000000002", f"depth-scan suggestion for {NAME}, accepted"),
        ("turn-s100000000000003", f"depth-scan suggestion for {NAME}, dismissed")]


@requires_scratch
async def test_enrich_inputs_for_the_plain_batch_and_for_things_that_do_not_exist(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        retired = await fx.add_mode(conn, "includes_cancelled_jobs", status="retired")
    async with lab_scratch.connect() as conn:
        assert await sampling.enrich_inputs(conn, fx.ORG_A) == {}                                  # M2's own path
        assert await sampling.enrich_inputs(conn, fx.ORG_A, mode_id=mid) == {"seed_turn_ids": []}   # live, nothing present yet
        assert await sampling.enrich_inputs(conn, fx.ORG_A, source="depth_scan", proposal_run_id="nope") == {"ordered": []}
        assert await sampling.enrich_inputs(conn, fx.ORG_A, source="depth_scan", proposal_run_id=str(uuid.uuid4())) == {"ordered": []}
        for bad in (retired, str(uuid.uuid4())):
            with pytest.raises(Invalid) as e:
                await sampling.enrich_inputs(conn, fx.ORG_A, mode_id=bad)
            assert e.value.reason == "bad_seed_source"


@requires_scratch
async def test_a_depth_scan_batch_only_ever_holds_this_companys_own_decided_suggestions(lab_scratch):
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        run = await fx.add_run(conn, "depth_scan", mode_id=mid)
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-a", mode_id=mid, status="accepted", org=fx.ORG_A)
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-b", mode_id=mid, status="accepted", org=fx.ORG_B)
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-c", mode_id=mid, status="pending", org=fx.ORG_A)
        await fx.add_proposal(conn, run, kind="depth_scan", turn_id="turn-d", mode_id=mid, status="closed",
                              closed_reason="canary", is_canary=True, org=fx.ORG_A)
    async with lab_scratch.connect() as conn:
        got = await sampling.enrich_inputs(conn, fx.ORG_A, source="depth_scan", proposal_run_id=run)
    assert [t for t, _why in got["ordered"]] == ["turn-a"]


def _req_for(**kw):
    base = {"plan_step": "depth_search", "strategy": "enrich", "k": 4, "seed": 7, "channel": "chat", "reviews_required": 1,
            "source": "bow", "since": m2.NOW - timedelta(days=7), "until": m2.NOW + timedelta(hours=1)}
    base.update(kw)
    return base


async def _pool(main, org, n=12):
    ids = []
    for i in range(n):
        subject = "chillers" if i % 3 == 0 else "boilers"
        ids.append(await m2.add_turn(main, i, org, question=f"How many {subject} are in B-{i}?",
                                     answer=f"There are {i} {subject} in B-{i}.", minutes_ago=10 + i))
    return ids


@requires_scratch
async def test_a_mode_batch_looks_for_answers_like_the_ones_that_show_the_failure(lab_scratch, main_scratch):
    ids = await _pool(main_scratch, fx.ORG_A)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        await fx.add_label(conn, ids[0], mid, present=True)                 # the seed: a chiller answer
    me = fx.admin()
    seeded = await sampling.preview(me, fx.ORG_A, _req_for(mode_id=mid, random_share=0.0, k=3))
    assert ids[0] not in {p["turn_id"] for p in seeded["picks"]}                 # the seed itself is never a pick
    chillers = {ids[i] for i in range(12) if i % 3 == 0}
    assert {p["turn_id"] for p in seeded["picks"]} <= chillers                   # 3 other chiller answers are the 3 best matches
    assert all(p["reason"].startswith("similar to an answer with this failure mode (") for p in seeded["picks"])
    assert len(seeded["picks"]) == 3


@requires_scratch
async def test_a_mode_batch_for_a_mode_that_is_gone_is_refused_before_it_draws(lab_scratch, main_scratch):
    await _pool(main_scratch, fx.ORG_A)
    async with lab_scratch.begin() as conn:
        retired = await fx.add_mode(conn, NAME, status="retired")
    for bad in (retired, str(uuid.uuid4())):
        with pytest.raises(Invalid) as e:
            await sampling.preview(fx.admin(), fx.ORG_A, _req_for(mode_id=bad))
        assert e.value.reason == "bad_seed_source"


@requires_scratch
async def test_a_depth_scan_batch_takes_the_decided_suggestions_in_order_and_records_where_it_came_from(lab_scratch, main_scratch):
    ids = await _pool(main_scratch, fx.ORG_A)
    async with main_scratch.begin() as conn:
        await conn.execute(text("UPDATE plenum_cafm.agent_trace_turns SET started_at = started_at"))   # keep the rows as seeded
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        run = await fx.add_run(conn, "depth_scan", mode_id=mid)
        decided = [ids[5], ids[2], ids[9]]
        for k, t in enumerate(decided):
            pid = await fx.add_proposal(conn, run, kind="depth_scan", turn_id=t, mode_id=mid,
                                        status="accepted" if k != 1 else "dismissed")
            await conn.execute(text("UPDATE tracelab.proposals SET decided_at = now() + make_interval(secs => :s) "
                                    "WHERE id = CAST(:p AS uuid)"), {"s": k, "p": pid})
    me = fx.admin()
    req = _req_for(source="depth_scan", proposal_run_id=run, k=5)
    pv = await sampling.preview(me, fx.ORG_A, req)
    assert [p["turn_id"] for p in pv["picks"]] == decided
    assert [p["reason"] for p in pv["picks"]] == [f"depth-scan suggestion for {NAME}, accepted",
                                                  f"depth-scan suggestion for {NAME}, dismissed",
                                                  f"depth-scan suggestion for {NAME}, accepted"]
    out = await sampling.create_batch(me, fx.ORG_A, dict(req, plan_step="depth_search"))
    assert [i["turn_id"] for i in out["items"]] == decided
    params = out["batch"]["params"]
    assert params["source"] == "depth_scan" and params["proposal_run_id"] == run and params["random_share"] == 0.0
    # the next batch cannot redraw them: an answer is in one batch only
    again = await sampling.preview(me, fx.ORG_A, req)
    assert again["picks"] == []


@requires_scratch
async def test_a_mode_batch_records_its_mode_and_the_plain_batch_still_records_none(lab_scratch, main_scratch):
    ids = await _pool(main_scratch, fx.ORG_A)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, NAME, status="confirmed")
        await fx.add_label(conn, ids[0], mid, present=True)
    me = fx.admin()
    seeded = await sampling.create_batch(me, fx.ORG_A, _req_for(mode_id=mid, k=3))
    assert seeded["batch"]["params"]["mode_id"] == mid and seeded["batch"]["params"]["proposal_run_id"] is None
    plain = await sampling.create_batch(me, fx.ORG_A, _req_for(k=3, plan_step="extra"))
    assert plain["batch"]["params"]["mode_id"] is None and plain["batch"]["params"]["source"] == "bow"
