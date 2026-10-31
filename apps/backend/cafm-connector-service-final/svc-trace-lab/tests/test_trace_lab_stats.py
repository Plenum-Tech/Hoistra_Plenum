"""Trace Lab judge statistics: Pass-positive confusion, Rogan-Gladen correction and the seeded percentile
bootstrap (spec: Judges, code checks, monitoring... section 1). 15 Oct 2026.

Pass is the positive class; stored flags are failure flags (1 = failure present). The point estimates
(0.1631 and friends) were recomputed from the committed Cartwheel state and are asserted to 1e-4.

The bootstrap CI is pure Python: random.Random(seed 7), the repo's resampling order (test pairs first,
then live predictions), percentile bounds with numpy's linear interpolation. Its random stream is not
numpy's, so each CI is asserted twice: (a) within +-0.03 of Cartwheel's numpy interval, as a sanity
check, and (b) to this implementation's own deterministic values at 1e-4, computed once and pinned."""
from __future__ import annotations

import ast
import dataclasses
import random
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest

from trace_lab import stats

A = 1e-4
G_LABELS = [1] * 12 + [0] * 38
G_PREDS = [1] * 10 + [0] * 38 + [1] * 2
G_LIVE = [1] * 90 + [0] * 410
G_LIVE_5000 = [1] * 900 + [0] * 4100
G_NUMPY_CI = (0.0587, 0.2733)         # Cartwheel, numpy 2.4.1, 20,000 replicates, seed 7
G_NUMPY_CI_5000 = (0.0631, 0.2653)
SANITY = 0.03
# This implementation's own deterministic values (random.Random seed 7), computed once and pinned.
G_OWN_CI = (0.0587, 0.2768)           # 20,000 replicates
G_OWN_CI_2000 = (0.0559, 0.273)       # 2,000 replicates
G_OWN_CI_5000_2000 = (0.0556, 0.2593)  # 5,000 live flags, 2,000 replicates


@pytest.fixture(scope="module")
def summary_500_live():
    return stats.corrected_prevalence(G_LIVE, G_LABELS, G_PREDS, bootstrap_iterations=2000)


@pytest.fixture(scope="module")
def summary_5000_live():
    return stats.corrected_prevalence(G_LIVE_5000, G_LABELS, G_PREDS, bootstrap_iterations=2000)


def test_wilson_matches_repo():
    cases = {(36, 38): (0.8271, 0.9854), (10, 12): (0.552, 0.953), (37, 39): (0.8311, 0.9858),
             (10, 11): (0.6226, 0.9838), (16, 20): (0.584, 0.9193), (0, 5): (0.0, 0.4345),
             (5, 5): (0.5655, 1.0), (12, 12): (0.7575, 1.0)}
    for (s, n), (lo, hi) in cases.items():
        got = stats.wilson_interval(s, n)
        assert got[0] == pytest.approx(lo, abs=A) and got[1] == pytest.approx(hi, abs=A), (s, n, got)
    assert stats.wilson_interval(0, 0) == (None, None)


def test_confusion_pass_is_positive():
    c = stats.confusion(G_LABELS, G_PREDS)
    assert (c.tp, c.fn, c.tn, c.fp, c.n) == (36, 2, 10, 2, 50)
    assert c.tpr == pytest.approx(0.9474, abs=A) and c.tnr == pytest.approx(0.8333, abs=A)
    assert c.agreement == pytest.approx(0.92, abs=A)
    assert c.tpr_interval == pytest.approx((0.8271, 0.9854), abs=A)
    assert c.tnr_interval == pytest.approx((0.552, 0.953), abs=A)
    assert c.tpr + c.tnr == pytest.approx(1.7807, abs=A)
    assert stats.validity_warning(c.tpr, c.tnr) == ""


def test_confusion_empty_class_reads_none_not_zero():
    c = stats.confusion([0, 0, 0], [0, 1, 0])          # no human Fail at all
    assert c.tnr is None and c.tnr_interval == (None, None)
    assert c.tpr == pytest.approx(0.6667, abs=A)
    with pytest.raises(ValueError):
        stats.confusion([0, 1], [0])
    with pytest.raises(ValueError):
        stats.confusion([0, 2], [0, 1])


def test_confusion_of_nothing_is_all_none_and_is_immutable():
    c = stats.confusion([], [])
    assert (c.tp, c.fn, c.tn, c.fp, c.n) == (0, 0, 0, 0, 0)
    assert c.tpr is None and c.tnr is None and c.agreement is None
    assert c.tpr_interval == (None, None) and c.tnr_interval == (None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.tp = 1                                        # type: ignore[misc]


def test_confusion_counts_each_cell_once_and_takes_booleans_and_rejects_strings():
    # human Pass/judge Pass, human Pass/judge Fail, human Fail/judge Fail, human Fail/judge Pass (a missed failure)
    c = stats.confusion([0, 0, 1, 1], [0, 1, 1, 0])
    assert (c.tp, c.fn, c.tn, c.fp) == (1, 1, 1, 1)
    assert c.tpr == 0.5 and c.tnr == 0.5 and c.agreement == 0.5
    assert stats.confusion([True, False], [True, False]).agreement == 1.0
    for bad in ([0, "1"], [0, None], [0, 0.5], [0, -1]):
        with pytest.raises(ValueError):
            stats.confusion(bad, [0, 1])


def test_rogan_gladen_forms_agree():
    tpr, tnr = 36 / 38, 10 / 12
    pi = stats.rogan_gladen_pass(0.82, tpr, tnr)
    assert pi == pytest.approx(0.8369, abs=A)
    theta = stats.rogan_gladen_failure(0.18, tnr, tpr)  # TPR_f = TNR, TNR_f = TPR
    assert theta == pytest.approx(1 - pi, abs=1e-9)
    assert theta == pytest.approx(0.1631, abs=A)


def test_rogan_gladen_clamps_and_undefined():
    f = stats.rogan_gladen_failure
    assert f(0.15, 0.8, 0.9) == pytest.approx(0.0714, abs=A)
    assert f(0.05, 0.8, 0.9) == 0.0
    assert f(0.95, 0.8, 0.9) == 1.0
    assert f(0.2, 1, 1) == pytest.approx(0.2, abs=A)
    assert f(0.5, 0.5, 0.5) is None
    assert stats.rogan_gladen_pass(0.5, 0.5, 0.5) is None
    assert stats.rogan_gladen_pass(0.99, 0.8, 0.9) == 1.0
    assert stats.rogan_gladen_pass(0.01, 0.8, 0.9) == 0.0


def test_validity_warning():
    assert stats.validity_warning(0.52, 0.5) == (
        "TPR + TNR = 1.020 is not comfortably above 1; the correction is unstable and the interval "
        "will be very wide. Improve the judge before trusting this estimate.")
    assert stats.validity_warning(0.6, 0.6) == ""
    assert stats.VALIDITY_MARGIN == 1.05
    assert stats.validity_warning(0.5, 0.55) != ""                     # exactly at the margin still warns
    assert stats.validity_warning(0.5, 0.5501) == ""


def test_constants_are_the_published_ones():
    assert (stats.BOOTSTRAP_REPS, stats.BOOTSTRAP_SEED, stats.NO_REPLICATES) == (
        20_000, 7, "no usable bootstrap replicates")


def test_corrected_prevalence_artifact_g():
    started = time.perf_counter()
    out = stats.corrected_prevalence(G_LIVE, G_LABELS, G_PREDS)            # the one test at 20,000 replicates
    assert time.perf_counter() - started < 10, "20,000 pure-Python replicates must stay under 10 seconds"
    assert out["raw"] == pytest.approx(0.18, abs=A)
    assert out["corrected"] == pytest.approx(0.1631, abs=A)
    assert out["ci_low"] == pytest.approx(G_NUMPY_CI[0], abs=SANITY)
    assert out["ci_high"] == pytest.approx(G_NUMPY_CI[1], abs=SANITY)
    assert (out["ci_low"], out["ci_high"]) == pytest.approx(G_OWN_CI, abs=A)
    assert out["nan_dropped"] == 0
    assert out["test_tpr"] == pytest.approx(0.9474, abs=A) and out["test_tnr"] == pytest.approx(0.8333, abs=A)
    assert (out["n_sample"], out["n_test"]) == (500, 50)
    assert out["validity_warning"] == "" and out["backend"] == "python-bootstrap" and out["confidence"] == 0.95
    assert out["ci_low"] <= out["corrected"] <= out["ci_high"]


def test_corrected_prevalence_hw7_vector():
    args = ([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], [1, 1, 1, 1, 0, 0, 0, 0], [1, 1, 1, 0, 0, 0, 0, 1])
    out = stats.corrected_prevalence(*args, bootstrap_iterations=500)
    assert out["raw"] == pytest.approx(0.3, abs=A) and out["corrected"] == pytest.approx(0.1, abs=A)
    assert out["test_tpr"] == pytest.approx(0.75, abs=A) and out["test_tnr"] == pytest.approx(0.75, abs=A)
    assert out["ci_low"] <= 0.1 <= out["ci_high"]
    # Cartwheel's numpy run reads (0.0, 1.0) with 27 dropped; the dropped count follows the random stream, so it is
    # this implementation's own value (16), pinned; the interval is the same wide one.
    assert (out["ci_low"], out["ci_high"], out["nan_dropped"]) == (0.0, 1.0, 16)
    assert stats.corrected_prevalence(*args, bootstrap_iterations=500) == out


def test_ci_narrows_as_live_n_grows(summary_500_live, summary_5000_live):
    small, big = summary_500_live, summary_5000_live
    assert big["corrected"] == pytest.approx(0.1631, abs=A) == pytest.approx(small["corrected"], abs=A)
    assert (small["ci_low"], small["ci_high"]) == pytest.approx(G_OWN_CI_2000, abs=A)
    assert (big["ci_low"], big["ci_high"]) == pytest.approx(G_OWN_CI_5000_2000, abs=A)
    assert big["ci_low"] == pytest.approx(G_NUMPY_CI_5000[0], abs=SANITY)
    assert big["ci_high"] == pytest.approx(G_NUMPY_CI_5000[1], abs=SANITY)
    assert big["ci_high"] - big["ci_low"] < small["ci_high"] - small["ci_low"]


def test_ci_stays_wide_on_50_test_labels(summary_500_live, summary_5000_live):
    for out in (summary_500_live, summary_5000_live):
        assert out["ci_high"] - out["ci_low"] > 0.15


def test_no_usable_replicates_reads_none_with_a_warning():
    # seed 7's first test draw is [0, 0]: both pairs are human Fail, so the only replicate misses a class: NaN.
    assert stats.bootstrap_ci([1, 0], [1, 0], [1, 0], iterations=1) == (None, None, 1)
    out = stats.corrected_prevalence([1, 0], [1, 0], [1, 0], bootstrap_iterations=1)
    assert out["corrected"] == pytest.approx(0.5, abs=A)
    assert (out["ci_low"], out["ci_high"], out["nan_dropped"]) == (None, None, 1)
    assert out["validity_warning"] == stats.NO_REPLICATES


def test_corrected_prevalence_rejects_bad_input():
    with pytest.raises(ValueError):
        stats.corrected_prevalence([], G_LABELS, G_PREDS)
    with pytest.raises(ValueError):
        stats.corrected_prevalence([1, 0], [], [])
    with pytest.raises(ValueError):
        stats.corrected_prevalence([1, 0], [1, 0], [1])
    with pytest.raises(ValueError):
        stats.corrected_prevalence([2, 0], [1, 0], [1, 0])
    with pytest.raises(ValueError):
        stats.corrected_prevalence([1, 0], [1, 0], [1, 2])
    with pytest.raises(ValueError):
        stats.corrected_prevalence([1, 0], [0, 0], [0, 0])   # no human Fail: TNR undefined


def test_pass_at_k_repo_values():
    assert stats.pass_at_k(8, 6, 1) == pytest.approx(0.75, abs=A)
    assert stats.pass_at_k(8, 6, 2) == pytest.approx(27 / 28, abs=A)
    assert stats.pass_at_k(8, 6, 4) == 1.0


def test_pass_hat_k_repo_values():
    assert stats.pass_hat_k(8, 6, 2) == pytest.approx(15 / 28, abs=A)
    assert stats.pass_hat_k(8, 6, 4) == pytest.approx(15 / 70, abs=A)
    assert stats.pass_hat_k(8, 6, 8) == 0.0


# ── the bootstrap itself ─────────────────────────────────────────────────────────────────────────────────

def _reference_replicate(rng, sample, labels, preds, *, live_first=False):
    """One replicate written the plain way, in Pass space, on the same random stream as bootstrap_ci:
    test pairs first, live predictions second (live_first swaps them, to prove the order is detectable)."""
    m, n = len(labels), len(sample)

    def draw(count, size):
        return [int(rng.random() * size) for _ in range(count)]

    if live_first:
        live_idx = draw(n, n)
        test_idx = draw(m, m)
    else:
        test_idx = draw(m, m)
        live_idx = draw(n, n)
    lab = [1 - labels[i] for i in test_idx]
    pre = [1 - preds[i] for i in test_idx]
    live = [1 - sample[i] for i in live_idx]
    positives = [i for i in range(m) if lab[i] == 1]
    negatives = [i for i in range(m) if lab[i] == 0]
    if not positives or not negatives or not live:
        return None
    tpr = sum(pre[i] for i in positives) / len(positives)
    tnr = sum(1 - pre[i] for i in negatives) / len(negatives)
    exact = Fraction(sum(pre[i] for i in positives), len(positives)) + Fraction(
        sum(1 - pre[i] for i in negatives), len(negatives)) - 1
    if exact == 0:                      # exactly zero in rational arithmetic, whatever float rounding says
        return None
    return 1 - min(max((sum(live) / n + tnr - 1) / (tpr + tnr - 1), 0.0), 1.0)


def _reference_ci(sample, labels, preds, iterations, seed, confidence=0.95):
    rng = random.Random(seed)
    values = sorted(v for v in (_reference_replicate(rng, sample, labels, preds) for _ in range(iterations))
                    if v is not None)
    if not values:
        return None, None, iterations
    alpha = (1 - confidence) / 2

    def quantile(q):
        position = (len(values) - 1) * q
        lo = int(position)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (position - lo)

    return quantile(alpha), quantile(1 - alpha), iterations - len(values)


def test_each_replicate_resamples_test_pairs_first_then_live_on_one_stream():
    differ = 0
    for seed in range(40):
        rng = random.Random(seed)
        expected = _reference_replicate(rng, G_LIVE, G_LABELS, G_PREDS)
        got = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=1, seed=seed)
        if expected is None:
            assert got == (None, None, 1)
        else:
            assert got[2] == 0 and got[0] == pytest.approx(expected, abs=1e-12) and got[1] == got[0]
        swapped = _reference_replicate(random.Random(seed), G_LIVE, G_LABELS, G_PREDS, live_first=True)
        differ += swapped != expected
    assert differ > 0, "the reference must tell test-first from live-first, or this test proves nothing"


def test_the_stream_runs_on_across_replicates_and_percentiles_interpolate_linearly():
    for seed, iterations in ((7, 60), (3, 101), (11, 33)):
        low, high, dropped = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=iterations, seed=seed)
        ref_low, ref_high, ref_dropped = _reference_ci(G_LIVE, G_LABELS, G_PREDS, iterations, seed)
        assert (low, high) == pytest.approx((ref_low, ref_high), abs=1e-9)
        assert dropped == ref_dropped == 0


def test_the_same_seed_gives_the_same_interval_and_another_seed_a_different_one():
    one = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=300)
    assert one == stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=300, seed=stats.BOOTSTRAP_SEED)
    assert one != stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=300, seed=8)
    unseeded = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=50, seed=None)
    assert unseeded[0] is not None and unseeded[0] <= unseeded[1] and unseeded[2] == 0


def test_a_wider_confidence_gives_a_wider_interval():
    ninety = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, confidence=0.90, iterations=500)
    ninety_nine = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, confidence=0.99, iterations=500)
    assert ninety_nine[0] <= ninety[0] and ninety[1] <= ninety_nine[1]
    assert (ninety_nine[0], ninety_nine[1]) != (ninety[0], ninety[1])


@pytest.mark.parametrize("q,expected", [(0.0, 1.0), (0.25, 1.75), (0.5, 2.5), (0.75, 3.25), (1.0, 4.0),
                                        (0.025, 1.075), (0.975, 3.925)])
def test_quantile_is_numpys_linear_method(q, expected):
    assert stats._quantile([1.0, 2.0, 3.0, 4.0], q) == pytest.approx(expected, abs=1e-12)
    assert stats._quantile([5.0], q) == 5.0


def test_replicates_that_cannot_be_computed_are_dropped_and_counted():
    # every held-out pair is a human Fail: no replicate has a human Pass, whatever the stream says.
    assert stats.bootstrap_ci([1, 0, 0], [1, 1, 1], [1, 0, 1], iterations=40) == (None, None, 40)
    # every held-out pair is a human Pass: no replicate has a human Fail.
    assert stats.bootstrap_ci([1, 0, 0], [0, 0, 0], [0, 1, 0], iterations=40) == (None, None, 40)
    # an empty live sample leaves nothing to correct
    assert stats.bootstrap_ci([], G_LABELS, G_PREDS, iterations=5) == (None, None, 5)
    # no replicates asked for: nothing dropped, nothing to read
    assert stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=0) == (None, None, 0)
    # no held-out pairs at all
    assert stats.bootstrap_ci([1, 0], [], [], iterations=3) == (None, None, 3)


def test_drops_follow_the_plain_reference_including_an_exactly_zero_denominator():
    # four held-out pairs with TPR = TNR = 1/2: a replicate that resamples the original mix has TPR + TNR - 1 = 0,
    # and one that misses a class has no rate at all. Both are dropped, and the survivors match the reference.
    sample, labels, preds = [1, 0, 0, 1, 0], [1, 1, 0, 0], [1, 0, 1, 0]
    for seed in (7, 1, 2):
        low, high, dropped = stats.bootstrap_ci(sample, labels, preds, iterations=300, seed=seed)
        ref_low, ref_high, ref_dropped = _reference_ci(sample, labels, preds, 300, seed)
        assert dropped == ref_dropped and 0 < dropped < 300
        assert (low, high) == pytest.approx((ref_low, ref_high), abs=1e-9)


def test_a_judge_no_better_than_chance_raises_instead_of_dividing_by_zero():
    labels, preds = [1, 1, 0, 0], [1, 0, 1, 0]          # TPR = TNR = 0.5
    with pytest.raises(ValueError, match="TPR \\+ TNR = 1 exactly"):
        stats.corrected_prevalence([1, 0, 0, 1], labels, preds, bootstrap_iterations=10)


def test_the_validity_warning_and_the_missing_replicates_warning_are_joined():
    labels, preds = [1, 1, 1, 0, 0, 0], [1, 0, 0, 0, 1, 1]       # TPR = TNR = 1/3
    out = stats.corrected_prevalence([1, 0, 0, 0], labels, preds, bootstrap_iterations=0)
    assert out["ci_low"] is None and out["ci_high"] is None and out["nan_dropped"] == 0
    assert out["validity_warning"] == f"{stats.validity_warning(1 / 3, 1 / 3)} {stats.NO_REPLICATES}"
    assert out["validity_warning"].startswith("TPR + TNR = 0.667 is not comfortably above 1;")
    assert out["corrected"] == 1.0                                # clamped, still returned


def test_bootstrap_ci_rejects_bad_input():
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 0], [1, 0], [1], iterations=3)
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 2], [1, 0], [1, 0], iterations=3)
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 0], [1, "0"], [1, 0], iterations=3)
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 0], [1, 0], [1, None], iterations=3)
    for bad in (0, 1, -0.5, 1.5):
        with pytest.raises(ValueError):
            stats.bootstrap_ci([1, 0], [1, 0], [1, 0], confidence=bad, iterations=3)
    with pytest.raises(ValueError):
        stats.bootstrap_ci([1, 0], [1, 0], [1, 0], iterations=-1)


def test_the_summary_rounds_every_rate_to_four_places_and_carries_the_published_keys():
    low, high, _ = stats.bootstrap_ci(G_LIVE, G_LABELS, G_PREDS, iterations=200)
    out = stats.corrected_prevalence(G_LIVE, G_LABELS, G_PREDS, bootstrap_iterations=200)
    assert out["ci_low"] == round(low, 4) and out["ci_high"] == round(high, 4)
    for key in ("raw", "corrected", "ci_low", "ci_high", "test_tpr", "test_tnr"):
        assert out[key] == round(out[key], 4)
    assert set(out) == {"raw", "corrected", "ci_low", "ci_high", "confidence", "test_tpr", "test_tnr", "n_sample",
                        "n_test", "nan_dropped", "validity_warning", "backend"}


def test_stats_imports_only_the_standard_library():
    tree = ast.parse((Path(stats.__file__)).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add((node.module or "").split(".")[0])
    assert imported, "the scan found no imports at all"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported - set(sys.stdlib_module_names)
    assert "numpy" not in imported
