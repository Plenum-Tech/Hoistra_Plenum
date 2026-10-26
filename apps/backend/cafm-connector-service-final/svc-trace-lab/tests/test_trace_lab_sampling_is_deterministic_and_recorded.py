"""Sampling for open coding is deterministic and explainable: the same pool, seed and strategy give the
same picks, every pick carries its reason, no conversation is picked twice, and the diversity, dimension,
outlier and enrich strategies follow the spec's rules (spec "Open coding, sampling ..." 3.2-3.3)."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import sampling
from trace_lab.review_errors import Invalid
from trace_lab.sampling import Candidate

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


def cand(i: int, *, session: str | None = None, tokens: float | None = 1000, llm: float = 2, tools: float = 1,
         distinct: float = 1, dbq: float = 0, latency: float = 3000, chars: float = 400, pos: float = 1,
         route: str = "compliance", channel: str = "chat", role: str | None = "admin", page: str | None = "cc",
         text: str | None = None, question: str | None = None) -> Candidate:
    tid = f"turn-{i:016x}"
    return Candidate(
        turn_id=tid, session_id=session if session is not None else f"sess-{i}", started_at=T0 + timedelta(minutes=i),
        features={"turn_position": pos, "llm_calls": llm, "tool_calls": tools, "distinct_tools": distinct,
                  "db_queries": dbq, "tokens": tokens, "latency_ms": latency, "answer_chars": chars},
        dims={"channel": channel, "route_target": route, "asker_role": role, "page": page},
        text=text, question=question)


def clustered_pool(groups: int, per: int) -> list[Candidate]:
    """`groups` well-separated groups of `per` turns; group g scales every feature by 10**g."""
    pool = []
    for g in range(groups):
        for j in range(per):
            i = g * 100 + j
            f = 10 ** (g % 4) * (1 + 0.01 * j)
            pool.append(cand(i, tokens=1000 * f * (g + 1), llm=2 * f, tools=1 + g, distinct=g, dbq=g * 3,
                             latency=1000 * f, chars=100 * (g + 1) * (1 + 0.02 * j), pos=1 + j % 3))
    return pool


def test_uniform_is_repeatable_and_reasoned():
    pool = [cand(i) for i in range(60)]
    a = sampling.pick_uniform(pool, 15, 7)
    b = sampling.pick_uniform(list(reversed(pool)), 15, 7)
    assert [p.turn_id for p in a] == [p.turn_id for p in b]          # pool order does not matter: sorted first
    assert len(a) == 15 and len({p.turn_id for p in a}) == 15
    assert all(p.reason == "uniform pick" for p in a)
    assert [p.turn_id for p in sampling.pick_uniform(pool, 15, 8)] != [p.turn_id for p in a]


def test_no_strategy_picks_two_turns_of_one_conversation():
    pool = [cand(i, session=f"s-{i // 3}") for i in range(60)]          # 20 conversations of 3 turns
    for picks in (sampling.pick_uniform(pool, 30, 7), sampling.pick_diversity(pool, 30, 7),
                  sampling.pick_dimension(pool, 30, 7, dimension="route_target"), sampling.pick_outliers(pool, 30)):
        keys = [f"s-{int(p.turn_id[5:], 16) // 3}" for p in picks]
        assert len(keys) == len(set(keys))
    assert len(sampling.pick_uniform(pool, 30, 7)) == 20


def test_diversity_k15_share0_is_15_representatives_over_7_clusters():
    pool = clustered_pool(7, 12)
    picks = sampling.pick_diversity(pool, 15, 7, random_share=0)
    assert len(picks) == 15
    assert all(p.reason.startswith("cluster ") and "representative (distance " in p.reason for p in picks)
    assert {p.cluster for p in picks} == set(range(1, 8))


def test_diversity_k24_default_share_is_16_representatives_over_8_clusters_and_8_random():
    pool = clustered_pool(8, 10)
    picks = sampling.pick_diversity(pool, 24, 7)
    reps = [p for p in picks if p.cluster is not None]
    rand = [p for p in picks if p.reason == "random pick"]
    assert len(reps) == 16 and len(rand) == 8
    assert {p.cluster for p in reps} == set(range(1, 9))


def test_representatives_are_closest_to_their_centroid_first():
    pool = clustered_pool(3, 9)
    picks = sampling.pick_diversity(pool, 6, 7, random_share=0)   # 3 clusters, 2 rounds
    first_round = picks[:3]
    for p in first_round:
        same = [q for q in picks if q.cluster == p.cluster]
        assert same[0] is p and all(p.score <= q.score for q in same)


def test_kmeans_with_seed_7_is_stable():
    x = sampling.standardize(sampling.feature_matrix(clustered_pool(4, 6)))
    a = sampling.kmeans(x, 4, seed=7)
    b = sampling.kmeans(x, 4, seed=7)
    assert a == b
    assert len(set(a[0])) == 4
    assert sampling.kmeans([], 3) == ([], [])
    assert len(sampling.kmeans(x[:2], 5)[1]) == 2                      # k is capped at n


def test_standardize_uses_population_deviation_and_keeps_flat_columns_at_zero():
    z = sampling.standardize([[1.0, 5.0], [3.0, 5.0]])
    assert z == [[-1.0, 0.0], [1.0, 0.0]]


def test_feature_matrix_fills_nulls_with_the_median_before_log1p():
    pool = [cand(1, tokens=None), cand(2, tokens=99), cand(3, tokens=999)]
    col = [row[sampling.FEATURES.index("tokens")] for row in sampling.feature_matrix(pool)]
    assert col[1] == pytest.approx(math.log1p(99), abs=1e-4)
    assert col[0] == pytest.approx(math.log1p(549), abs=1e-4)          # median of 99 and 999
    distinct = [row[sampling.FEATURES.index("distinct_tools")] for row in sampling.feature_matrix(pool)]
    assert distinct == [1.0, 1.0, 1.0]                                  # not log-transformed


def test_dimension_quotas_go_to_the_largest_values_and_shortfalls_move_on():
    pool = ([cand(i, route="compliance") for i in range(20)] + [cand(100 + i, route="wo_engine") for i in range(10)]
            + [cand(200 + i, route="energy") for i in range(2)])
    picks = sampling.pick_dimension(pool, 10, 7, dimension="route_target")
    by = {}
    for p in picks:
        by[p.dimension_value] = by.get(p.dimension_value, 0) + 1
    # quotas 4/3/3 by size; energy has only 2, so its shortfall of 1 wraps round to compliance
    assert by == {"compliance": 5, "wo_engine": 3, "energy": 2}
    assert picks[0].reason == "dimension route_target = compliance (quota 5 of 10)"
    assert sampling.dimension_quotas([20, 10, 2], 9) == [4, 3, 2]
    assert sampling.dimension_quotas([1, 1], 5) == [1, 1]
    assert sampling.dimension_quotas([5, 5, 5, 5], 2) == [1, 1, 0, 0]
    picks2 = sampling.pick_dimension(pool[20:] + pool[:20], 9, 7, dimension="route_target")
    assert len(picks2) == 9


def test_dimension_keeps_the_top_ten_values_and_folds_the_rest_and_none():
    pool = [cand(i, page=f"p{i:02d}" if i < 12 else None) for i in range(14)]
    names = [v for v, _ in sampling.dimension_groups(pool, "page")]
    assert "(other)" in names and "(none)" in names and len(names) == 11


@pytest.mark.parametrize("dim", ["feedback_rating", "ok", "verdict"])
def test_outcome_fields_are_refused_as_dimensions(dim):
    with pytest.raises(Invalid) as e:
        sampling.pick_dimension([cand(1)], 5, 7, dimension=dim)
    assert e.value.reason == "dimension_not_allowed"


def test_outliers_come_from_both_tails_furthest_past_the_fence_first():
    pool = [cand(i, tokens=1000 + i) for i in range(20)] + [cand(50, tokens=40000), cand(51, tokens=9000),
                                                           cand(52, tokens=-20000)]
    picks = sampling.pick_outliers(pool, 5)
    assert [p.turn_id for p in picks] == [cand(50).turn_id, cand(52).turn_id, cand(51).turn_id]
    assert picks[0].reason.startswith("IQR outlier on tokens: 40,000 (fence ")


def test_enrich_ranks_by_similarity_to_wrong_answers_and_keeps_its_random_share():
    seeds = [cand(900, text="How many boilers failed their gas safety check\nThree.")]
    pool = ([cand(i, text=f"How many boilers failed their gas safety check at site {i}\nAnswer {i}",
                  question=f"How many boilers failed their gas safety check at site {i}") for i in range(10)]
            + [cand(100 + i, text=f"Draft an email to vendor {i}\nDone", question=f"Draft an email to vendor {i}")
               for i in range(10)])
    picks = sampling.pick_enrich(pool, 10, 7, seeds=seeds)
    top = [p for p in picks if p.reason.startswith("similar to a wrong answer (")]
    rand = [p for p in picks if p.reason == "random pick"]
    assert len(top) == 8 and len(rand) == 2
    assert all(int(p.turn_id[5:], 16) < 10 for p in top)


def test_enrich_skips_a_question_it_already_picked():
    seeds = [cand(900, text="open work orders at B-301\n")]
    pool = [cand(i, text="open work orders at B-301\nx", question="Open work orders at  B-301") for i in range(5)]
    top = [p for p in sampling.pick_enrich(pool, 5, 7, seeds=seeds, random_share=0)]
    assert len(top) == 1


def test_enrich_without_seeds_falls_back_to_diversity_with_its_share():
    pool = clustered_pool(4, 8)
    assert sampling.pick_enrich(pool, 10, 7, seeds=[]) == sampling.pick_diversity(pool, 10, 7, random_share=0.2)


def test_draw_is_the_same_for_the_same_request():
    pool = clustered_pool(5, 8)
    now = datetime.now(timezone.utc)
    req = {"plan_step": "cluster_representatives", "strategy": "diversity", "k": 15, "seed": 7,
           "since": now - timedelta(days=7), "until": now}
    assert sampling.draw(pool, req) == sampling.draw(list(reversed(pool)), req)
    assert sampling.random_share_for(req) == 0.0
    assert sampling.random_share_for({**req, "plan_step": "extra"}) == pytest.approx(1 / 3)
