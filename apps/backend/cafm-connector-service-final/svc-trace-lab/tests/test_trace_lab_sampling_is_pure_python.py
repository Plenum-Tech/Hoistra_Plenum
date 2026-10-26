"""Sampling, k-means and the Map's PCA are pure Python (design R11, G0 addendum: no numpy, no new dependency).
These tests pin that, and the properties the numpy version relied on: the projection is an orthonormal
rotation of the centred data in descending order of variance, it does not depend on row order, and a
hostile or tiny input never raises or returns NaN."""
from __future__ import annotations

import ast
import math
import random
import sys
from pathlib import Path

import pytest

from trace_lab import sampling

SOURCE = Path(sampling.__file__)
STDLIB_OR_LOCAL = set(sys.stdlib_module_names) | {"src", "sqlalchemy"}


def _rows(n: int, d: int, seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    return [[rng.gauss(0, 1) * (d - j) for j in range(d)] for _ in range(n)]


def test_the_module_imports_nothing_beyond_the_standard_library_and_the_package():
    tree = ast.parse(SOURCE.read_text())
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add((node.module or "").split(".")[0])
    assert "numpy" not in roots and "scipy" not in roots
    assert roots <= STDLIB_OR_LOCAL, roots - STDLIB_OR_LOCAL


def test_pca_scores_are_the_centred_data_rotated_in_descending_variance_order():
    x = _rows(60, 8, 3)
    pts, shares = sampling.pca2(x)
    n = len(x)
    mean = [sum(r[j] for r in x) / n for j in range(8)]
    var = [sum(r[j] ** 2 for r in x) / n - mean[j] ** 2 for j in range(8)]
    total = sum(var)
    v1 = sum(p[0] ** 2 for p in pts) / n
    v2 = sum(p[1] ** 2 for p in pts) / n
    assert v1 >= v2 > 0
    assert shares[0] == pytest.approx(v1 / total, abs=2e-4) and shares[1] == pytest.approx(v2 / total, abs=2e-4)
    assert shares[0] >= shares[1] and shares[0] + shares[1] <= 1.0 + 1e-9
    assert sum(p[0] * p[1] for p in pts) / n == pytest.approx(0.0, abs=2e-2)      # uncorrelated axes
    # no loss of distance: two points are never further apart on the plot than in the data
    full = math.dist(x[0], x[1])
    assert math.dist(pts[0], pts[1]) <= full + 1e-3


def test_pca_of_a_known_two_dimensional_cloud():
    # points on the diagonal y = x with a little spread across it: axis 1 is the diagonal, axis 2 the spread
    x = [[t, t] + [0.0] * 6 for t in (-3, -1, 0, 1, 3)] + [[0.5, -0.5] + [0.0] * 6, [-0.5, 0.5] + [0.0] * 6]
    pts, shares = sampling.pca2(x)
    assert shares[0] > 0.95 and shares[1] > 0.0 and shares[0] + shares[1] == pytest.approx(1.0, abs=1e-3)
    assert pts[4][0] == pytest.approx(-pts[0][0], abs=1e-3)                      # symmetric about the centre
    assert abs(pts[0][0]) == pytest.approx(math.sqrt(2) * 3 - 0.0, abs=0.3)


def test_pca_sign_is_fixed_so_the_largest_loading_is_positive():
    # rows t * (1, -3): the first axis is +-(1, -3)/sqrt(10); its largest loading is on the second column, and
    # the rule makes that loading positive, so a row with a negative t scores positive
    line = [[float(t), -3.0 * t] + [0.0] * 6 for t in (-2, -1, 0, 1, 2)]
    pts, _ = sampling.pca2(line)
    assert pts[0][0] == pytest.approx(2 * math.sqrt(10), abs=1e-3) and pts[4][0] == pytest.approx(-2 * math.sqrt(10), abs=1e-3)
    # the same cloud mirrored (rows t * (-1, 3)) has the same axis, so the rule gives the same scores for the same t
    mirrored = [[-v for v in r] for r in line]
    mpts, _ = sampling.pca2(mirrored)
    assert mpts[0][0] == pytest.approx(-2 * math.sqrt(10), abs=1e-3)
    # a repeat run, and a copy of the data, give exactly the same numbers
    x = _rows(40, 8, 9)
    assert sampling.pca2(x) == sampling.pca2([r[:] for r in x])


@pytest.mark.parametrize("axis", [0, 1])
def test_pca_loadings_have_a_positive_largest_entry_on_every_cloud(axis):
    for seed in range(40):
        x = _rows(30, 6, seed)
        pts, _ = sampling.pca2(x)
        n = len(x)
        score = [p[axis] for p in pts]
        mean_s = sum(score) / n
        loading = []
        for j in range(6):
            mean_j = sum(r[j] for r in x) / n
            loading.append(sum((r[j] - mean_j) * (sc - mean_s) for r, sc in zip(x, score)) / n)
        assert loading[max(range(6), key=lambda j: abs(loading[j]))] > 0, seed


def test_pca_edge_cases_never_raise_or_return_nan():
    assert sampling.pca2([]) == ([], (0.0, 0.0))
    pts, shares = sampling.pca2([[1.0, 2.0]])
    assert pts == [(0.0, 0.0)] and shares == (0.0, 0.0)
    flat, fs = sampling.pca2([[5.0] * 8] * 4)
    assert flat == [(0.0, 0.0)] * 4 and fs == (0.0, 0.0)
    one_col, os_ = sampling.pca2([[float(i)] for i in range(5)])                  # one dimension only
    assert os_[0] == pytest.approx(1.0, abs=1e-4) and os_[1] == 0.0 and all(p[1] == 0.0 for p in one_col)
    big, _ = sampling.pca2([[1e9 * i, -1e9 * i] + [0.0] * 6 for i in range(6)])
    assert all(math.isfinite(v) for p in big for v in p)


def test_kmeans_labels_every_point_and_is_reproducible_with_the_same_seed_only():
    x = sampling.standardize(sampling.feature_matrix(_candidates(50)))
    labels, centroids = sampling.kmeans(x, 5, seed=7)
    assert len(labels) == 50 and set(labels) <= set(range(5)) and len(centroids) == 5
    assert sampling.kmeans(x, 5, seed=7) == (labels, centroids)
    assert all(math.isfinite(v) for c in centroids for v in c)
    assert sampling.kmeans(x, 0) == ([], [])
    assert sampling.kmeans(x[:3], 3)[0] != []                                      # k == n


def test_kmeans_sends_a_tied_point_to_the_lowest_cluster_index():
    first, second = random.Random(7).sample(range(3), 2)           # the two rows that become the initial centroids
    vectors: list[list[float]] = [[1.0], [1.0], [1.0]]
    vectors[first], vectors[second] = [0.0], [2.0]
    middle = ({0, 1, 2} - {first, second}).pop()
    labels, _ = sampling.kmeans(vectors, 2, seed=7)
    assert labels[middle] == labels[first] == 0 and labels[second] == 1


def test_kmeans_converges_early_on_separated_groups():
    x = [[0.0, 0.0], [0.1, 0.0], [10.0, 10.0], [10.1, 10.0]]
    labels, centroids = sampling.kmeans(x, 2, seed=7)
    assert labels[0] == labels[1] != labels[2] == labels[3]
    assert sorted(round(c[0], 2) for c in centroids) == [0.05, 10.05]


def test_a_huge_pool_is_clustered_in_a_reasonable_time():
    import time

    x = sampling.standardize(sampling.feature_matrix(_candidates(4000)))
    t0 = time.monotonic()
    sampling.kmeans(x, 15, seed=7)
    sampling.pca2(x)
    assert time.monotonic() - t0 < 20


def _candidates(n: int):
    from datetime import datetime, timedelta, timezone

    rng = random.Random(1)
    t0 = datetime(2026, 10, 10, tzinfo=timezone.utc)
    return [sampling.Candidate(
        turn_id=f"turn-{i:016x}", session_id=f"s-{i}", started_at=t0 + timedelta(minutes=i),
        features={"turn_position": rng.randint(1, 4), "llm_calls": rng.randint(1, 8), "tool_calls": rng.randint(0, 9),
                  "distinct_tools": rng.randint(0, 4), "db_queries": rng.randint(0, 6),
                  "tokens": rng.randint(200, 90000), "latency_ms": rng.randint(300, 60000),
                  "answer_chars": rng.randint(20, 4000)},
        dims={"channel": "chat", "route_target": "compliance", "asker_role": "admin", "page": "cc"}) for i in range(n)]
