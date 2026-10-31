"""The Map projection and the Progress metrics, as pure functions: PCA by SVD is deterministic, the fair
sample counts uniform batches only, coverage flags thin dimension values, agreement is measured on blind
first verdicts, and the discovery curve and saturation hint never claim what M2 cannot know
(spec "Open coding ..." 3.5, 3.6, 4; R11)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import sampling, stats
from trace_lab.sampling import Candidate

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def cand(i, *, tokens=1000.0, route="compliance", page="cc", role="admin", batch=None, question="q"):
    return Candidate(turn_id=f"turn-{i:016x}", session_id=f"s-{i}", started_at=T0 + timedelta(minutes=i),
                     features={"turn_position": 1, "llm_calls": 1 + i % 3, "tool_calls": i % 4, "distinct_tools": i % 2,
                               "db_queries": i % 5, "tokens": tokens + 37 * i, "latency_ms": 900 + 11 * i,
                               "answer_chars": 300 + 7 * i},
                     dims={"channel": "chat", "route_target": route, "asker_role": role, "page": page},
                     question=question, batch_id=batch)


def test_points_on_a_line_put_all_variance_on_the_first_axis():
    rows = [[float(i), 2.0 * i, 0, 0, 0, 0, 0, 0] for i in range(10)]
    pts, shares = sampling.pca2(rows)
    assert shares[0] == pytest.approx(1.0, abs=1e-4) and shares[1] == pytest.approx(0.0, abs=1e-4)
    assert pts[0][0] == pytest.approx(-10.0623, abs=1e-4)


def test_projection_signs_do_not_depend_on_row_order():
    rows = sampling.standardize(sampling.feature_matrix([cand(i) for i in range(30)]))
    a, _ = sampling.pca2(rows)
    b, _ = sampling.pca2(list(reversed(rows)))
    assert a == list(reversed(b))


def test_the_map_needs_three_answers_and_numbers_clusters_from_one():
    few = sampling.map_payload([cand(1), cand(2)], reviewed=set(), mine=set())
    assert few["too_few"] is True and few["nodes"] == [] and few["explained_variance"] is None
    pool = [cand(i, batch="b1" if i < 3 else None) for i in range(20)]
    m = sampling.map_payload(pool, reviewed={pool[0].turn_id}, mine={pool[0].turn_id})
    assert m["too_few"] is False and len(m["nodes"]) == 20
    assert {n["cluster"] for n in m["nodes"]} == {c["id"] for c in m["clusters"]} and min(c["id"] for c in m["clusters"]) == 1
    assert len(m["clusters"]) == 8 and sum(c["size"] for c in m["clusters"]) == 20
    first = m["nodes"][0]
    assert first["reviewed"] and first["reviewed_by_me"] and first["batch_id"] == "b1"
    assert first["conversation_key"] == "s:s-0"
    assert [f["key"] for f in m["feature_names"]] == list(sampling.FEATURES)


def test_fair_sample_counts_uniform_batches_only_and_leaves_disputes_out():
    items = ([{"strategy": "uniform", "withdrawn": False, "verdicts": ["wrong"]}] * 3
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": ["right"]}] * 9
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": ["right", "wrong"]}]
             + [{"strategy": "uniform", "withdrawn": False, "verdicts": []}]
             + [{"strategy": "uniform", "withdrawn": True, "verdicts": ["wrong"]}] * 5
             + [{"strategy": "diversity", "withdrawn": False, "verdicts": ["wrong"]}] * 20)
    f = sampling.fair_sample(items)
    assert (f["wrong"], f["right"], f["disputed"], f["unreviewed"], f["items"]) == (3, 9, 1, 1, 14)
    assert f["rate"] == 0.25 and (f["ci_low"], f["ci_high"]) == stats.wilson_interval(3, 12)
    assert f["label"] == "From fair (uniform) batches only"
    assert sampling.fair_sample([])["rate"] is None


def test_coverage_flags_a_value_reviewed_at_under_half_its_share():
    pool = [cand(i, route="compliance") for i in range(60)] + [cand(100 + i, route="wo_engine") for i in range(40)]
    reviewed = {c.turn_id for c in pool[:10]}                       # all ten reviews are compliance
    cov = sampling.coverage(pool, reviewed)
    rows = {r["value"]: r for r in cov["dimensions"]["route_target"]}
    assert rows["wo_engine"]["pool_share"] == 0.4 and rows["wo_engine"]["reviewed_share"] == 0.0
    assert rows["wo_engine"]["under_covered"] is True and rows["compliance"]["under_covered"] is False
    empty = sampling.coverage(pool, set())
    assert all(r["reviewed_share"] is None and not r["under_covered"] for r in empty["dimensions"]["route_target"])


def test_agreement_uses_the_pair_with_most_shared_answers_and_kappa_from_20():
    rows = []
    for i in range(20):
        rows.append({"turn_id": f"t{i}", "reviewer_id": "a", "verdict": "right" if i < 15 else "wrong"})
        rows.append({"turn_id": f"t{i}", "reviewer_id": "b", "verdict": "right" if i < 13 or i == 19 else "wrong"})
    rows.append({"turn_id": "t0", "reviewer_id": "c", "verdict": "wrong"})
    ag = sampling.agreement(rows)
    assert ag["reviewers"] == ["a", "b"] and ag["shared"] == 20
    assert ag["percent"] == 0.85 and ag["kappa"] == pytest.approx(0.625, abs=1e-4) and ag["note"] is None
    few = sampling.agreement(rows[:10])
    assert few["kappa"] is None and few["note"] == "fewer than 20 shared answers"
    assert sampling.agreement([{"turn_id": "t", "reviewer_id": "a", "verdict": "right"}]) is None


def _heads(n, wrong_every=4):
    return [{"turn_id": f"t{i:03d}", "reviewer_id": "a", "reviewer_email": "a@x", "verdict": "wrong" if i % wrong_every == 0 else "right",
             "first_at": T0 + timedelta(minutes=i), "batch_id": "b1" if i < 5 else None} for i in range(n)]


def test_discovery_curve_moves_with_every_block_of_reviews_without_inventing_modes():
    d = sampling.discovery(_heads(25), 10)
    assert [(p["block"], p["reviews_through"], p["wrong"], p["partial"]) for p in d["points"]] == [
        (1, 10, 3, False), (2, 20, 2, False), (3, 25, 2, True)]
    assert all(p["new_modes"] is None for p in d["points"]) and d["modes_available"] is False
    assert d["by_batch"] == [{"batch_id": "b1", "reviews": 5, "wrong": 2, "new_modes": None}]
    assert sampling.discovery(_heads(26), 10)["points"][-1]["reviews_through"] == 26


def test_saturation_is_unavailable_until_modes_exist_then_follows_the_rule():
    pts = sampling.discovery(_heads(30), 10)["points"]
    assert sampling.saturation(pts, 10) == {"available": False, "hint": False, "kind": None,
                                           "reviews_without_new": None, "final_new_modes": None}
    with_modes = [{**p, "new_modes": n} for p, n in zip(pts, [3, 0, 0])]
    s = sampling.saturation(with_modes, 10)
    assert s["hint"] is True and s["kind"] == "quiet" and s["reviews_without_new"] == 20
    assert sampling.saturation([{**p, "new_modes": n} for p, n in zip(pts, [3, 1, 0])], 10)["hint"] is False
    fin = sampling.saturation(with_modes, 10, final_uniform={"complete": True, "new_modes": 2})
    assert fin["kind"] == "final_batch" and fin["final_new_modes"] == 2


def test_plan_view_points_at_the_first_unfinished_step():
    batches = [{"id": "b1", "plan_step": "initial_uniform", "selected": 15, "reviewed": 15, "withdrawn_at": None},
               {"id": "b2", "plan_step": "cluster_representatives", "selected": 9, "reviewed": 2, "withdrawn_at": None},
               {"id": "b3", "plan_step": "cluster_representatives", "selected": 6, "reviewed": 0, "withdrawn_at": "x"}]
    plan = sampling.plan_view(batches, 24)
    assert plan["next_step"] == "cluster_representatives" and plan["target_total"] == 100
    step = plan["steps"][1]
    assert (step["target"], step["selected"], step["reviewed"], step["batch_ids"]) == (15, 9, 2, ["b2"])


def test_open_coding_lists_missing_verdicts_25_at_a_time():
    heads = _heads(3)
    items = [{"turn_id": f"t{i:03d}", "reviews_required": 1} for i in range(40)]
    oc = sampling.open_coding(heads, items)
    assert (oc["reviewed"], oc["sampled"], oc["missing"], oc["missing_more"]) == (3, 40, 37, 12)
    assert len(oc["missing_turns"]) == 25 and oc["wrong"] == 1 and oc["right"] == 2
    assert oc["reviewers"] == [{"email": "a@x", "reviewed": 3, "first_reviewed_at": T0.isoformat()}]
