"""Trace Lab statistics. Pure: no database, no model calls, no I/O, standard library only.

Wilson interval and Cohen's kappa (open coding), pass@k and pass^k (golden runs), then judge
alignment: the Pass-positive confusion, the Rogan-Gladen correction and a seeded percentile
bootstrap (judges), then the Watch helpers: the eval-case decision, the replay failure-rate
bootstrap and the ISO week label. The judge rates are rounded to 4 places on the way out.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any

Z95 = 1.96


def wilson_interval(successes: int, total: int, z: float = Z95) -> tuple[float | None, float | None]:
    """95% Wilson score interval for successes/total, rounded to 4 places. (None, None) when total is 0."""
    if total <= 0:
        return (None, None)
    if not 0 <= successes <= total:
        raise ValueError("successes must be between 0 and total")
    p = successes / total
    d = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / d
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return (round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4))


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters over the same items, rounded to 4 places.

    None when there are no items, or when chance agreement is 1 (both raters used one and the same
    category throughout), where kappa is undefined."""
    if len(a) != len(b):
        raise ValueError("both raters must judge the same items")
    n = len(a)
    if n == 0:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    count_a, count_b = Counter(a), Counter(b)
    pe = sum((count_a[c] / n) * (count_b[c] / n) for c in sorted(set(count_a) | set(count_b)))
    if pe >= 1.0:
        return None
    return round((po - pe) / (1 - pe), 4)


# ── golden runs: pass@k for capability, pass^k for reliability ──
# pass_at_k   the chance that at least one of k reps drawn from n passes
# pass_hat_k  the chance that all k reps drawn from n pass ("pass^k")

def _check(n: int, c: int, k: int) -> None:
    if n < 1:
        raise ValueError(f"n must be at least 1, got {n}")
    if not 0 <= c <= n:
        raise ValueError(f"c must be within 0..n, got c={c}, n={n}")
    if not 1 <= k <= n:
        raise ValueError(f"k must be within 1..n, got k={k}, n={n}")


def pass_at_k(n: int, c: int, k: int) -> float:
    """1 - C(n-c, k) / C(n, k); exactly 1.0 when n - c < k."""
    _check(n, c, k)
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """C(c, k) / C(n, k); exactly 0.0 when c < k."""
    _check(n, c, k)
    if c < k:
        return 0.0
    return math.comb(c, k) / math.comb(n, k)


# ── judges: alignment, Rogan-Gladen correction and the percentile bootstrap ──
# Stored flags are failure flags (1 = failure present); every TPR and TNR here treats Pass as the
# positive class. The Wilson intervals are wilson_interval above: (None, None) for an empty class.

BOOTSTRAP_REPS = 20_000
BOOTSTRAP_SEED = 7
VALIDITY_MARGIN = 1.05
NO_REPLICATES = "no usable bootstrap replicates"


@dataclass(frozen=True)
class Confusion:
    """Pass is the positive class; built from failure flags."""
    tp: int
    fn: int
    tn: int
    fp: int
    n: int
    tpr: float | None
    tnr: float | None
    agreement: float | None
    tpr_interval: tuple[float | None, float | None]
    tnr_interval: tuple[float | None, float | None]


def _flags(values: Sequence[int], name: str) -> list[int]:
    out: list[int] = []
    for v in values:
        if v not in (0, 1):
            raise ValueError(f"{name} must hold only 0 or 1, got {v!r}")
        out.append(int(v))
    return out


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def confusion(labels: Sequence[int], preds: Sequence[int]) -> Confusion:
    """Failure flags in (1 = failure present), Pass-positive counts out.

    TP: human Pass, judge Pass. FN: human Pass, judge Fail. TN: human Fail, judge Fail.
    FP: human Fail, judge Pass - a missed failure. A class with no examples reads None, never 0."""
    lab, pre = _flags(labels, "labels"), _flags(preds, "preds")
    if len(lab) != len(pre):
        raise ValueError("labels and preds differ in length")
    tp = fn = tn = fp = 0
    for label, pred in zip(lab, pre):
        if label == 0 and pred == 0:
            tp += 1
        elif label == 0:
            fn += 1
        elif pred == 1:
            tn += 1
        else:
            fp += 1
    return Confusion(tp=tp, fn=fn, tn=tn, fp=fp, n=len(lab), tpr=_ratio(tp, tp + fn), tnr=_ratio(tn, tn + fp),
                     agreement=_ratio(tp + tn, len(lab)), tpr_interval=wilson_interval(tp, tp + fn),
                     tnr_interval=wilson_interval(tn, tn + fp))


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def rogan_gladen_pass(observed_pass: float, tpr: float, tnr: float) -> float | None:
    """Pass prevalence as the Cartwheel repo computes it: clamp((p_pass + TNR - 1) / (TPR + TNR - 1), 0, 1).
    None when TPR + TNR - 1 is exactly 0 (the repo returns NaN)."""
    j = tpr + tnr - 1
    if j == 0:
        return None
    return _clamp01((observed_pass + tnr - 1) / j)


def rogan_gladen_failure(observed_failure: float, tpr_fail: float, tnr_fail: float) -> float | None:
    """Failure prevalence in the repo's correct.py form, with TPR_f = TNR and TNR_f = TPR. Equal to
    1 - rogan_gladen_pass(1 - raw, TPR, TNR) wherever both are defined."""
    j = tpr_fail + tnr_fail - 1
    if j == 0:
        return None
    return _clamp01((observed_failure + tnr_fail - 1) / j)


def validity_warning(tpr: float, tnr: float) -> str:
    """The Cartwheel wording (analysis/helpers/reporting.py), verbatim. The estimate is still returned."""
    if tpr + tnr <= VALIDITY_MARGIN:
        return (f"TPR + TNR = {tpr + tnr:.3f} is not comfortably above 1; the correction is unstable and the "
                "interval will be very wide. Improve the judge before trusting this estimate.")
    return ""


def _quantile(ordered: Sequence[float], q: float) -> float:
    """The q-quantile of an ascending, non-empty list by linear interpolation between order statistics:
    numpy.quantile's default method, including its lerp form, so bounds match Cartwheel's to rounding."""
    position = (len(ordered) - 1) * q
    below = math.floor(position)
    above = min(below + 1, len(ordered) - 1)
    gap = position - below
    low, high = ordered[below], ordered[above]
    spread = high - low
    return high - spread * (1 - gap) if gap >= 0.5 else low + spread * gap


def bootstrap_ci(sample_preds: Sequence[int], test_labels: Sequence[int], test_preds: Sequence[int], *,
                 confidence: float = 0.95, iterations: int = BOOTSTRAP_REPS,
                 seed: int | None = BOOTSTRAP_SEED) -> tuple[float | None, float | None, int]:
    """Percentile bootstrap of the failure prevalence: the loop of Cartwheel's analysis/helpers/reporting.py.

    Each replicate resamples the test pairs FIRST and the live predictions SECOND, drawing indices with
    random.Random(seed).random() (the one random-stream guarantee Python keeps across versions, so the
    seeded result is reproducible everywhere). A replicate with a class missing from the resampled test
    pairs, an empty live sample, or a TPR + TNR - 1 of exactly 0 is undefined and dropped. Returns
    (low, high, dropped); (None, None, dropped) when every replicate is dropped. Raises ValueError on a flag
    that is not 0 or 1, test labels and predictions of different length, a confidence outside (0, 1) or a
    negative iteration count. Cost grows with iterations x (test pairs + live sample): 20,000 replicates
    over 50 test pairs and 500 live flags take a few seconds of CPU, so call it through asyncio.to_thread
    from a request or a job."""
    live_failure = _flags(sample_preds, "sample_preds")
    labels = _flags(test_labels, "test_labels")
    preds = _flags(test_preds, "test_preds")
    if len(labels) != len(preds):
        raise ValueError("test_labels and test_preds differ in length")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")
    if iterations < 0:
        raise ValueError(f"iterations must not be negative, got {iterations!r}")

    live_pass = [1 - f for f in live_failure]
    # One cell per test pair in Pass space: 3 = TP, 2 = FN, 1 = FP, 0 = TN (human Pass * 2 + judge Pass).
    cells = [(1 - label) * 2 + (1 - pred) for label, pred in zip(labels, preds)]
    m, n = len(cells), len(live_pass)
    rand = random.Random(seed).random
    kept: list[float] = []
    for _ in range(iterations):
        counts = [0, 0, 0, 0]
        for cell in [cells[int(rand() * m)] for _ in range(m)]:             # test pairs first
            counts[cell] += 1
        passes = sum([live_pass[int(rand() * n)] for _ in range(n)])         # live predictions second
        tn, fp, fn, tp = counts
        positives, negatives = tp + fn, tn + fp
        if not positives or not negatives or not n:
            continue
        if tp * negatives + tn * positives == positives * negatives:         # TPR + TNR - 1 is exactly 0
            continue
        tpr, tnr = tp / positives, tn / negatives
        pass_rate = _clamp01((passes / n + tnr - 1) / (tpr + tnr - 1))
        kept.append(1 - pass_rate)
    dropped = iterations - len(kept)
    if not kept:
        return None, None, dropped
    kept.sort()
    alpha = (1 - confidence) / 2
    return _quantile(kept, alpha), _quantile(kept, 1 - alpha), dropped


def corrected_prevalence(sample_preds: Sequence[int], test_labels: Sequence[int], test_preds: Sequence[int], *,
                         confidence: float = 0.95, bootstrap_iterations: int = BOOTSTRAP_REPS,
                         seed: int | None = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Rogan-Gladen-corrected failure prevalence with a percentile bootstrap CI (the repo's correct.py contract).

    sample_preds: the frozen judge's failure flags over the UNIFORM sample only. test_labels and
    test_preds: the frozen judge's held-out pairs (the test-run snapshot). Raises ValueError on
    an empty sample or test set, a length mismatch, a value other than 0 or 1, a held-out set missing a
    class, or an undefined point estimate. All rates are rounded to 4 places."""
    sample = _flags(sample_preds, "sample_preds")
    lab = _flags(test_labels, "test_labels")
    pre = _flags(test_preds, "test_preds")
    if not sample:
        raise ValueError("sample_preds is empty")
    if not lab:
        raise ValueError("test_labels is empty")
    if len(lab) != len(pre):
        raise ValueError("test_labels and test_preds differ in length")
    c = confusion(lab, pre)
    if c.tp + c.fn == 0 or c.tn + c.fp == 0:
        raise ValueError("the held-out pairs lack a class, so TPR or TNR is undefined")
    tpr, tnr = c.tp / (c.tp + c.fn), c.tn / (c.tn + c.fp)
    raw = sum(sample) / len(sample)
    pi = rogan_gladen_pass(1 - raw, tpr, tnr)
    if pi is None:
        raise ValueError("TPR + TNR = 1 exactly: the corrected prevalence is undefined")
    low, high, dropped = bootstrap_ci(sample, lab, pre, confidence=confidence, iterations=bootstrap_iterations,
                                      seed=seed)
    warning = validity_warning(tpr, tnr)
    if low is None:
        warning = (warning + " " if warning else "") + NO_REPLICATES

    def r4(x: float | None) -> float | None:
        return None if x is None else round(float(x), 4)

    return {"raw": r4(raw), "corrected": r4(1 - pi), "ci_low": r4(low), "ci_high": r4(high), "confidence": confidence,
            "test_tpr": r4(tpr), "test_tnr": r4(tnr), "n_sample": len(sample), "n_test": len(lab),
            "nan_dropped": dropped, "validity_warning": warning, "backend": "python-bootstrap"}


# ── Watch: the eval-case decision, the replay bootstrap and the week label ──
# case_decision        what one eval case decides on one golden run
# summarize_rollouts   the failure distribution of repeated runs of one answer, with a seeded bootstrap interval
# iso_week_label       "2026-W41", the label a monitoring period carries

REPLAY_BOOTSTRAP_REPS = 2_000          # the replay harness default (Cartwheel replay/harness.py), seed 7


def case_decision(kind: str, passes: int, n: int, baseline_pass_rate: float | None = None, *,
                  planned: int | None = None, not_decided: int = 0) -> dict[str, str]:
    """What one eval case decides on one run. A regression case must pass every run that was planned: one
    failed run blocks, and a run without a verdict (degraded, pending, ungraded or missing), or fewer runs
    than planned, is incomplete, because incomplete evidence never passes. A capability case never blocks: it
    reports its passes against the baseline it was classified with. passes and n count the runs that have a
    verdict (n >= passes >= 0); not_decided counts the planned runs that have none. A count that cannot be
    true raises ValueError rather than reading as a pass."""
    if passes < 0 or n < 0 or passes > n or not_decided < 0:
        raise ValueError("passes must be within 0..n, and n and not_decided cannot be negative")
    if kind == "capability":
        b = "none" if baseline_pass_rate is None else f"{float(baseline_pass_rate):g}"
        return {"decision": "pass", "reason": f"capability case passed {passes} of {n}, baseline {b}, not blocking"}
    if kind != "regression":
        raise ValueError("an eval case is a regression or a capability case")
    if passes < n:
        return {"decision": "block", "reason": f"regression case failed {n - passes} of {n} runs"}
    total = int(planned) if planned else n + int(not_decided)
    missing = max(int(not_decided), total - n)
    if n == 0 or missing > 0:
        return {"decision": "incomplete",
                "reason": f"regression case has {missing} runs without a verdict of {total}; "
                          "incomplete evidence never passes"}
    return {"decision": "pass", "reason": f"regression case passed {n} of {n} runs"}


def summarize_rollouts(records: Sequence[dict[str, Any]], confidence: float = 0.95,
                       bootstrap_iterations: int = REPLAY_BOOTSTRAP_REPS, seed: int = 7) -> dict[str, Any]:
    """The failure-distribution measurement of repeated runs of one answer (repeat-n): a port of Cartwheel
    replay/harness.py:104-187. Records are {"passed": bool, "failures": [str], "steps": int}. The interval is a
    percentile bootstrap of the failure rate: random.Random(seed).choices, n records per resample, the sorted
    rates indexed with int(q * (len - 1)). mode_counts counts each failure across failing records (a record
    listing two failures counts in both; "failure_modes" is read when "failures" is absent); steps is
    {min, median, max} over the records that carry steps, else None. wilson_high is the upper end of the Wilson
    interval for the same counts."""
    if not records:
        raise ValueError("records cannot be empty")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if bootstrap_iterations < 1:
        raise ValueError("bootstrap_iterations must be at least 1")
    n = len(records)
    failures = sum(not bool(r.get("passed")) for r in records)
    rng = random.Random(seed)
    rates = sorted(sum(not bool(r.get("passed")) for r in rng.choices(records, k=n)) / n
                   for _ in range(bootstrap_iterations))
    alpha = 1 - confidence
    modes: Counter[str] = Counter()
    for r in records:
        if not r.get("passed"):
            modes.update(r.get("failures") or r.get("failure_modes") or [])
    step_values = sorted(int(r["steps"]) for r in records if r.get("steps") is not None)
    return {"n": n, "failures": failures, "failure_rate": failures / n,
            "ci_low": rates[int((alpha / 2) * (len(rates) - 1))],
            "ci_high": rates[int((1 - alpha / 2) * (len(rates) - 1))],
            "mode_counts": dict(modes),
            "steps": ({"min": step_values[0], "median": median(step_values), "max": step_values[-1]}
                      if step_values else None),
            "wilson_high": wilson_interval(failures, n)[1]}


def iso_week_label(ts: datetime) -> str:
    """'2026-W41': the ISO year and week of ts in UTC (a naive time is read as UTC)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    year, week, _ = ts.astimezone(timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"
