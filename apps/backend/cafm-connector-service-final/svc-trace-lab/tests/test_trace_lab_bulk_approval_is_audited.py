"""The nine rules of spec §7.8, as pure checks over already-loaded facts: only open suggestions you can see, evidence that
is not repeated, a conflicting note, a believable present rate, a run whose known-answer checks agree, the spot-check
(size, seed, completion, agreement), pace and freshness, and the cells still missing. No database, no network."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, proposals
from trace_lab.review_errors import Conflict

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
SERVED = NOW - timedelta(hours=1)
GOOD = {"r1": {"total": 5, "agree": 5}}


def _p(i, *, kind="cell", suggestion="absent", evidence=None, mode="m1", status="pending", visible=True, run="r1"):
    return {"id": f"p{i:03d}", "kind": kind, "status": status, "suggestion": suggestion, "mode_id": mode, "run_id": run,
            "evidence": evidence if evidence is not None else f"Checked the count for answer {i}.",
            "visible": visible, "is_canary": False}


def _audit(items, **kw):
    args = {"action": "accept", "items": {p["id"]: p for p in items}, "requested": [p["id"] for p in items],
            "served_at": SERVED, "now": NOW, "canaries": GOOD}
    args.update(kw)
    return proposals.audit_bulk(**args)


def _rules(res):
    return sorted(r["rule"] for r in res["refused"])


def test_a_clean_small_bulk_is_allowed():
    res = _audit([_p(i) for i in range(5)])
    assert res["allowed"] and res["write"] == [f"p{i:03d}" for i in range(5)] and res["spot_check"]["ids"] == []
    assert res["refused"] == [] and res["flagged"] == [] and res["warnings"] == []


def test_rule_1_only_open_visible_suggestions_and_dismiss_on_depth_scans_only():
    assert _rules(_audit([_p(0), _p(1, status="accepted")])) == [1]
    assert _rules(_audit([_p(0, visible=False)])) == [1]
    assert _rules(_audit([_p(0)], action="dismiss")) == [1]
    assert _audit([_p(0, kind="depth_scan", suggestion="present")], action="dismiss")["allowed"]


def test_rule_1_names_every_offending_id_and_refuses_what_is_not_a_suggestion_of_a_turn():
    res = _audit([_p(0), _p(1, status="closed"), _p(2, kind="group")])
    assert res["refused"][0]["ids"] == ["p001", "p002"]
    canary = dict(_p(3), is_canary=True)
    assert _rules(_audit([canary])) == [1]
    unknown = proposals.audit_bulk(action="accept", items={}, requested=["nope"], served_at=SERVED, now=NOW, canaries=GOOD)
    assert _rules(unknown) == [1] and not unknown["allowed"]


def test_nothing_requested_is_never_allowed():
    res = _audit([])
    assert not res["allowed"] and res["write"] == [] and res["stats"]["n"] == 0


def test_a_repeated_id_counts_once():
    p = _p(0)
    res = proposals.audit_bulk(action="accept", items={p["id"]: p}, requested=[p["id"]] * 3, served_at=SERVED, now=NOW,
                               canaries=GOOD)
    assert res["write"] == ["p000"] and res["stats"]["n"] == 1


def test_rule_2_repeated_evidence_is_refused():
    res = _audit([_p(i, evidence="The answer counts cancelled jobs.") for i in range(10)])
    assert _rules(res) == [2]
    assert res["refused"][0]["detail"] == "The suggestions repeat the same evidence; decide them one at a time."


def test_rule_2_ignores_case_and_spacing_and_waits_for_ten_suggestions():
    same = ["The answer counts cancelled jobs.", "the  answer counts\ncancelled jobs.  "]
    res = _audit([_p(i, evidence=same[i % 2]) for i in range(10)])
    assert _rules(res) == [2] and res["refused"][0]["distinct_share"] == 0.1
    assert _audit([_p(i, evidence="Same words.") for i in range(9)])["allowed"]            # nine are too few to judge
    nine_in_ten = [_p(i, evidence=f"Distinct reading {i}.") for i in range(9)] + [_p(9, evidence="Distinct reading 0.")]
    assert _audit(nine_in_ten)["allowed"]                                                   # a 0.9 share is enough


def test_rule_3_a_conflicting_note_flags_and_excludes_the_item():
    res = _audit([_p(0), _p(1)], note_conflicts={"p001"})
    assert res["allowed"] and res["write"] == ["p000"] and [f["id"] for f in res["flagged"]] == ["p001"]
    assert res["flagged"][0]["rule"] == 3


def test_rule_4_a_present_rate_below_the_fair_interval_refuses_that_modes_items():
    items = [_p(i) for i in range(20)] + [_p(100 + i, mode="m2") for i in range(3)]
    uniform = {"m1": {"n": 12, "present": 4, "low": 0.14, "high": 0.61}}
    res = _audit(items, uniform=uniform)
    assert 4 in _rules(res) and set(res["write"]) == {f"p{100 + i:03d}" for i in range(3)} - set(res["spot_check"]["ids"])
    high = _audit([_p(i, suggestion="present") for i in range(20)], uniform=uniform)
    assert [w["rule"] for w in high["warnings"]] == [4] and 4 not in _rules(high)


@pytest.mark.parametrize("uniform", [{"m1": {"n": 9, "present": 0, "low": 0.14, "high": 0.61}},       # too few fair answers
                                     {"m1": {"n": 12, "present": 0, "low": None, "high": None}}, {}, None])
def test_rule_4_needs_twenty_cells_and_a_fair_interval_to_judge_by(uniform):
    assert _audit([_p(i) for i in range(20)], uniform=uniform)["allowed"]
    low = {"m1": {"n": 12, "present": 4, "low": 0.14, "high": 0.61}}
    assert _audit([_p(i) for i in range(19)], uniform=low)["allowed"]                       # 19 is not enough to judge a rate


def test_rule_4_a_rate_inside_the_interval_is_quiet():
    inside = {"m1": {"n": 12, "present": 4, "low": 0.0, "high": 1.0}}
    res = _audit([_p(i) for i in range(20)], uniform=inside)
    assert res["allowed"] and res["warnings"] == []


def test_rule_5_the_runs_canaries_must_agree():
    assert _rules(_audit([_p(0)], canaries={"r1": {"total": 4, "agree": 4}})) == [5]
    assert _rules(_audit([_p(0)], canaries={"r1": {"total": 10, "agree": 8}})) == [5]
    assert _audit([_p(0)], canaries={"r1": {"total": 10, "agree": 9}})["allowed"]
    assert _rules(_audit([_p(0)], canaries={})) == [5] and _rules(_audit([_p(0)], canaries=None)) == [5]


def test_rule_5_is_checked_for_each_run_the_selection_came_from():
    res = _audit([_p(0, run="r1"), _p(1, run="r2")], canaries={"r1": {"total": 5, "agree": 5}, "r2": {"total": 5, "agree": 1}})
    assert _rules(res) == [5] and res["refused"][0]["run_id"] == "r2"


def test_rule_6_spot_check_size_seed_completion_and_agreement():
    items = [_p(i) for i in range(22)]
    check = _audit(items)
    seed, spot = check["spot_check"]["seed"], check["spot_check"]["ids"]
    assert len(spot) == 5 and seed == proposals.spot_check_seed([p["id"] for p in items], SERVED)
    assert not set(spot) & set(check["write"])
    incomplete = _audit(items, phase="approve", spot_labels={spot[0]: False})
    assert incomplete["spot_incomplete"] and not incomplete["allowed"]
    one_off = {i: False for i in spot}
    one_off[spot[0]] = True
    res = _audit(items, phase="approve", spot_labels=one_off)
    assert _rules(res) == [6]
    assert res["refused"][0]["detail"] == "Your spot-check disagreed with 1 of 5 suggestions. Decide the rest one at a time."
    assert _audit(items, phase="approve", spot_labels={i: False for i in spot})["allowed"]


def test_rule_6_a_decided_spot_item_is_no_longer_pending_and_is_not_refused_for_it():
    items = [_p(i) for i in range(22)]
    spot = _audit(items)["spot_check"]["ids"]
    for p in items:
        if p["id"] in spot:
            p["status"] = "closed"                                  # a person's decision closed the hidden suggestion
    assert _audit(items, phase="approve", spot_labels={i: False for i in spot})["allowed"]
    assert _rules(_audit(items)) == [1]                              # the same list is not allowed at the check phase


def test_rule_6_the_spot_check_is_a_function_of_the_ids_and_the_clock():
    ids = [f"p{i:03d}" for i in range(40)]
    seed, spot = proposals.spot_check_ids(ids, SERVED)
    assert proposals.spot_check_ids(list(reversed(ids)), SERVED) == (seed, spot)               # the order does not matter
    assert proposals.spot_check_ids(ids, SERVED + timedelta(seconds=1))[0] != seed             # a new list, a new seed
    assert spot == sorted(spot) and len(set(spot)) == len(spot) and set(spot) <= set(ids)
    assert proposals.spot_check_ids(ids[:-1], SERVED)[0] != seed


@pytest.mark.parametrize("n,s", [(19, 0), (20, 5), (100, 5), (101, 6), (200, 10), (500, 25)])
def test_rule_6_the_spot_check_is_five_percent_never_fewer_than_five_and_none_under_twenty(n, s):
    ids = [f"p{i:04d}" for i in range(n)]
    assert len(proposals.spot_check_ids(ids, SERVED)[1]) == s == (0 if n < 20 else max(5, math.ceil(0.05 * n)))


def test_rule_7_pace_and_freshness():
    fast = _audit([_p(i) for i in range(5)], served_at=NOW - timedelta(seconds=10))
    assert _rules(fast) == [7] and fast["refused"][0]["retry_after_s"] == 15
    assert _rules(_audit([_p(0)], served_at=NOW + timedelta(minutes=5))) == [7]
    assert _rules(_audit([_p(0)], served_at=NOW - timedelta(hours=13))) == [7]


def test_rule_7_five_seconds_per_decision_exactly_is_enough_and_a_second_of_clock_skew_is_forgiven():
    assert _audit([_p(i) for i in range(5)], served_at=NOW - timedelta(seconds=25))["allowed"]
    assert _rules(_audit([_p(i) for i in range(5)], served_at=NOW - timedelta(seconds=24))) == [7]
    assert _audit([_p(0)], served_at=NOW - timedelta(seconds=5))["allowed"]
    skewed = _audit([_p(0)], served_at=NOW + timedelta(seconds=1))                          # skew forgiven, pace is not
    assert _rules(skewed) == [7] and skewed["refused"][0]["retry_after_s"] == 6
    assert _audit([_p(0)], served_at=NOW - timedelta(hours=11))["allowed"]


def test_rule_8_undecided_cells_warn_with_the_count():
    res = _audit([_p(0)], undecided_after=3)
    assert res["allowed"] and res["warnings"] == [{"rule": 8, "count": 3,
                                                   "detail": "3 judgments are still missing in these answers."}]


def test_a_refusal_other_than_rule_4_blocks_and_every_refusal_is_listed():
    res = _audit([_p(i, evidence="Same.") for i in range(10)], canaries={"r1": {"total": 1, "agree": 1}},
                 served_at=NOW - timedelta(seconds=1))
    assert _rules(res) == [2, 5, 7] and not res["allowed"]


@pytest.mark.parametrize("action,kind,suggestion,expected", [("accept", "depth_scan", "present", True),
                                                             ("dismiss", "depth_scan", "present", False),
                                                             ("accept", "cell", "present", True),
                                                             ("accept", "cell", "absent", False)])
def test_the_value_a_bulk_would_write(action, kind, suggestion, expected):
    assert proposals.bulk_value(action, {"kind": kind, "suggestion": suggestion}) is expected


@requires_scratch
async def test_bulk_end_to_end_spot_check_stale_skip_and_bulk_basis(lab_scratch, main_scratch):
    turns = [f"turn-r0{i:014d}" for i in range(22)]
    async with main_scratch.begin() as conn:
        for t in turns:
            await fx.add_turn(conn, t)
    async with lab_scratch.begin() as conn:
        mid = await fx.add_mode(conn, "counts_cancelled_jobs_as_open", status="confirmed")
        run = await fx.add_run(conn, "cells", canary_total=5, canary_agree=5)
        pids = []
        for i, t in enumerate(turns):
            pids.append(await fx.add_proposal(conn, run, turn_id=t, mode_id=mid, suggestion="absent",
                                              evidence=f"Checked the count for answer {i}: it excludes cancelled jobs."))
    served = datetime.now(timezone.utc) - timedelta(hours=1)
    check = await proposals.bulk_check(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served)
    assert check["allowed"] and len(check["spot_check"]["items"]) == 5
    assert all(set(it) == {"proposal_id", "turn_id", "mode_id", "mode_name", "question", "base"}
               for it in check["spot_check"]["items"])
    with pytest.raises(Conflict) as e:
        await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served, bases={})
    assert e.value.reason == "spot_check_incomplete"
    seed = check["spot_check"]["seed"]
    for it in check["spot_check"]["items"]:
        await labels.set_label(fx.owner(), None, it["turn_id"], it["mode_id"], present=False, base=it["base"],
                               spot_check_seed=seed)
    spot = {it["proposal_id"] for it in check["spot_check"]["items"]}
    stale = next(p for p in pids if p not in spot)
    out = await proposals.bulk_approve(fx.owner(), None, action="accept", proposal_ids=pids, served_at=served,
                                       bases={stale: "00000000-0000-4000-8000-000000000000"})
    assert {"id": stale, "reason": "stale"} in out["skipped"]
    assert len(out["approved"]) == 22 - 5 - 1
    assert {lab["approval"] for lab in out["labels"]} == {"bulk"}
    assert {lab["source"] for lab in out["labels"]} == {"accepted_label_proposal"}
    bulk = out["labels"][0]["basis"]["bulk"]
    assert bulk["n"] == 17 and bulk["spot_check"]["n"] == 5 and bulk["spot_check"]["agree"] == 5
    async with lab_scratch.connect() as conn:
        n_human = (await conn.execute(text("SELECT count(*) FROM tracelab.labels WHERE approval = 'bulk' "
                                           "AND source IN ('human_structured_label', 'human_open_code')"))).scalar()
    assert n_human == 0
    assert all(not c["human_origin"] or c["approval"] == "single" for c in await labels.human_origin_cells(mid))


def test_rule_4_a_rate_exactly_at_the_lower_bound_is_not_refused():
    items = [_p(i, suggestion="present" if i < 5 else "absent") for i in range(20)]            # 25 % present
    assert _audit(items, uniform={"m1": {"n": 12, "present": 4, "low": 0.25, "high": 0.61}})["allowed"]
    assert 4 in _rules(_audit(items, uniform={"m1": {"n": 12, "present": 4, "low": 0.26, "high": 0.61}}))


def test_rule_4_a_rate_exactly_at_the_upper_bound_does_not_warn():
    items = [_p(i, suggestion="present" if i < 10 else "absent") for i in range(20)]           # 50 % present
    assert _audit(items, uniform={"m1": {"n": 12, "present": 6, "low": 0.1, "high": 0.5}})["warnings"] == []
    warn = _audit(items, uniform={"m1": {"n": 12, "present": 6, "low": 0.1, "high": 0.49}})["warnings"]
    assert [w["rule"] for w in warn] == [4]


def test_rule_4_judges_each_mode_by_its_own_fair_interval():
    items = [_p(i, mode="m1") for i in range(20)] + [_p(100 + i, mode="m2", suggestion="present") for i in range(20)]
    uniform = {"m1": {"n": 12, "present": 6, "low": 0.3, "high": 0.9}, "m2": {"n": 12, "present": 6, "low": 0.3, "high": 0.9}}
    res = _audit(items, uniform=uniform)
    refused = [r for r in res["refused"] if r["rule"] == 4]
    assert [r["mode_id"] for r in refused] == ["m1"] and set(refused[0]["ids"]) == {p["id"] for p in items[:20]}
    assert res["allowed"] and set(res["write"]) <= {p["id"] for p in items[20:]} and res["write"]


def test_rule_6_nine_of_ten_is_exactly_enough_and_eight_is_not():
    items = [_p(i) for i in range(200)]
    served = NOW - timedelta(hours=2)
    spot = _audit(items, served_at=served)["spot_check"]["ids"]
    assert len(spot) == 10
    nine = {i: False for i in spot}
    nine[spot[0]] = True
    assert _audit(items, phase="approve", spot_labels=nine, served_at=served)["allowed"]
    eight = dict(nine)
    eight[spot[1]] = True
    res = _audit(items, phase="approve", spot_labels=eight, served_at=served)
    assert _rules(res) == [6] and "disagreed with 2 of 10" in res["refused"][0]["detail"]


def test_rule_6_a_spot_check_label_for_an_item_outside_the_spot_check_does_not_count():
    items = [_p(i) for i in range(22)]
    spot = _audit(items)["spot_check"]["ids"]
    outside = next(p["id"] for p in items if p["id"] not in spot)
    labels_ = {i: False for i in spot[:4]}
    labels_[outside] = False
    assert _audit(items, phase="approve", spot_labels=labels_)["spot_incomplete"]


def test_rule_7_pace_counts_the_decisions_the_approval_makes_not_the_spot_check():
    items = [_p(i) for i in range(22)]                                       # 5 spot-check items, 17 written: 85 seconds
    assert _audit(items, served_at=NOW - timedelta(seconds=85))["allowed"]
    late = _audit(items, served_at=NOW - timedelta(seconds=84))
    assert _rules(late) == [7] and late["refused"][0]["retry_after_s"] == 1
    assert late["stats"]["seconds_per_decision"] == round(84 / 17, 2)


def test_the_stats_say_how_the_audit_went():
    items = [_p(i) for i in range(5)]
    res = _audit(items, served_at=NOW - timedelta(seconds=50))
    assert res["stats"] == {"n": 5, "write": 5, "distinct_share": 1.0, "seconds_per_decision": 10.0,
                            "canary": {"r1": {"total": 5, "agree": 5}}, "spot_check": None}
