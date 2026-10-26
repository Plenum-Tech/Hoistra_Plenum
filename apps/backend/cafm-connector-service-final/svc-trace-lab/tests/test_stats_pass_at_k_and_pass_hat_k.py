"""Wilson intervals (wilson_interval), pass@k and pass^k: the numbers every run summary rests on.
15 Oct 2026. Spec: Golden set section §5.5 and §8; values from Cartwheel tests/eval/passk.py and the C1a Wilson rows."""
from __future__ import annotations

import pytest

from trace_lab import stats


@pytest.mark.parametrize("k,n,expected", [
    (36, 38, (0.8271, 0.9854)),
    (10, 12, (0.552, 0.953)),
    (16, 20, (0.584, 0.9193)),
    (37, 39, (0.8311, 0.9858)),
    (10, 11, (0.6226, 0.9838)),
    (0, 5, (0.0, 0.4345)),
    (5, 5, (0.5655, 1.0)),
])
def test_wilson_interval_matches_the_published_rows(k, n, expected):
    assert stats.wilson_interval(k, n) == pytest.approx(expected, abs=1e-4)


def test_wilson_of_nothing_is_two_nones_and_bad_counts_raise():
    assert stats.wilson_interval(0, 0) == (None, None)
    with pytest.raises(ValueError):
        stats.wilson_interval(6, 5)
    assert not hasattr(stats, "wilson"), "one Wilson function: wilson_interval"


def test_pass_at_k_for_eight_reps_with_six_passes():
    assert stats.pass_at_k(8, 6, 1) == pytest.approx(0.75, abs=1e-4)
    assert stats.pass_at_k(8, 6, 2) == pytest.approx(27 / 28, abs=1e-4)
    assert stats.pass_at_k(8, 6, 4) == 1.0          # n - c < k: certain


def test_pass_hat_k_for_eight_reps_with_six_passes():
    assert stats.pass_hat_k(8, 6, 2) == pytest.approx(15 / 28, abs=1e-4)
    assert stats.pass_hat_k(8, 6, 4) == pytest.approx(15 / 70, abs=1e-4)
    assert stats.pass_hat_k(8, 6, 8) == 0.0          # c < k: impossible


@pytest.mark.parametrize("n,c,k", [(0, 0, 0), (4, 5, 1), (3, 1, 0), (3, 1, 4), (3, -1, 1)])
def test_impossible_inputs_raise(n, c, k):
    with pytest.raises(ValueError):
        stats.pass_at_k(n, c, k)
    with pytest.raises(ValueError):
        stats.pass_hat_k(n, c, k)


def test_boundaries_of_pass_at_k_and_pass_hat_k():
    assert stats.pass_at_k(5, 0, 3) == 0.0           # nothing ever passes
    assert stats.pass_at_k(5, 5, 3) == 1.0
    assert stats.pass_hat_k(5, 5, 5) == 1.0          # every rep passes
    assert stats.pass_hat_k(5, 0, 1) == 0.0
    assert stats.pass_at_k(1, 1, 1) == 1.0 and stats.pass_hat_k(1, 0, 1) == 0.0


def test_at_k_one_the_two_forms_equal_the_pass_rate_and_never_cross():
    for n in range(1, 9):
        for c in range(n + 1):
            assert stats.pass_at_k(n, c, 1) == pytest.approx(c / n, abs=1e-12)
            assert stats.pass_hat_k(n, c, 1) == pytest.approx(c / n, abs=1e-12)
            for k in range(1, n + 1):
                assert stats.pass_hat_k(n, c, k) <= stats.pass_at_k(n, c, k) + 1e-12
