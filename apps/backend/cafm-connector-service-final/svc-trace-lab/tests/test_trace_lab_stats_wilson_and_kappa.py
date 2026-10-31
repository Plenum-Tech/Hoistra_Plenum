"""stats.py as Trace Lab creates it: the Wilson interval (the reference values the judge tests pin) and
Cohen's kappa, both pure and rounded to 4 places. Standard library only: no numpy is declared."""
from __future__ import annotations

import pytest

from trace_lab import stats


@pytest.mark.parametrize("k,n,expected", [(16, 20, (0.584, 0.9193)), (0, 5, (0.0, 0.4345)), (5, 5, (0.5655, 1.0)),
                                          (12, 12, (0.7575, 1.0)), (0, 0, (None, None)), (3, 12, (0.0889, 0.5323))])
def test_wilson_interval_reference_values(k, n, expected):
    assert stats.wilson_interval(k, n) == expected


def test_wilson_refuses_impossible_counts():
    with pytest.raises(ValueError):
        stats.wilson_interval(6, 5)
    with pytest.raises(ValueError):
        stats.wilson_interval(-1, 5)


def test_z95_is_the_published_constant_and_z_is_honoured():
    assert stats.Z95 == 1.96
    narrow = stats.wilson_interval(16, 20, z=1.0)
    assert narrow[0] > stats.wilson_interval(16, 20)[0] and narrow[1] < stats.wilson_interval(16, 20)[1]


def test_cohen_kappa():
    a = ["right"] * 15 + ["wrong"] * 5
    b = ["right"] * 13 + ["wrong"] * 6 + ["right"]
    assert stats.cohen_kappa(a, b) == pytest.approx(0.625, abs=1e-4)
    assert stats.cohen_kappa(["right", "wrong"], ["right", "wrong"]) == 1.0
    assert stats.cohen_kappa(["right"] * 4, ["right"] * 4) is None          # chance agreement is 1: undefined
    assert stats.cohen_kappa([], []) is None
    with pytest.raises(ValueError):
        stats.cohen_kappa(["right"], [])


def test_cohen_kappa_extremes_and_extra_categories():
    assert stats.cohen_kappa(["right", "wrong"], ["wrong", "right"]) == -1.0      # perfect disagreement
    assert stats.cohen_kappa(["a", "b", "c", "a"], ["a", "b", "c", "a"]) == 1.0   # more than two categories
    # chance agreement alone: one rater is constant, so kappa is 0
    assert stats.cohen_kappa(["right"] * 4, ["right", "right", "wrong", "wrong"]) == 0.0
    # tuples and strings are sequences too
    assert stats.cohen_kappa(("x", "y"), ("x", "y")) == 1.0
