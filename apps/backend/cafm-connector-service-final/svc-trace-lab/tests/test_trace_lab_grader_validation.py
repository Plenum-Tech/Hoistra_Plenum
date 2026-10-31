"""Grader validation without a database: human labels pooled across companies and reported per company (the
senior's decision 1), TPR and TNR with Wilson intervals, the label minimum from the environment, at most two reps per
case, unsure apart, label sources merged with disagreements left out."""
from __future__ import annotations

import pytest

from trace_lab import grader_validation as gvl
from trace_lab import stats


def recs(org, *, tp=0, fn=0, tn=0, fp=0, unsure=0, start=0):
    out, i = [], start
    for grader, human, n in (("pass", "pass", tp), ("fail", "pass", fn), ("fail", "fail", tn), ("pass", "fail", fp),
                             ("unsure", "pass", unsure)):
        for _ in range(n):
            out.append({"org": org, "case_key": f"c{i}", "grader": grader, "human": human})
            i += 1
    return out


@pytest.fixture(autouse=True)
def no_sources(monkeypatch):
    monkeypatch.setattr(gvl, "_SOURCES", [])
    gvl.clear_cache()


def test_thresholds_come_from_the_environment(monkeypatch):
    assert gvl.thresholds() == {"min_per_class": 20, "min_tpr": 0.9, "min_tnr": 0.9}
    monkeypatch.setenv("TRACE_LAB_GRADER_MIN_PER_CLASS", "30")
    monkeypatch.setenv("TRACE_LAB_GRADER_MIN_TPR", "0.95")
    monkeypatch.setenv("TRACE_LAB_GRADER_MIN_TNR", "0.85")
    assert gvl.thresholds() == {"min_per_class": 30, "min_tpr": 0.95, "min_tnr": 0.85}


def test_pooled_across_companies_and_reported_per_company():
    rows = recs("org-a", tp=20, fn=1, tn=8, fp=1) + recs("org-b", tp=16, fn=1, tn=14, fp=1, start=100)
    m = gvl.metrics(rows, min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    p = m["pooled"]
    assert (p["tp"], p["fn"], p["tn"], p["fp"], p["n_pass"], p["n_fail"]) == (36, 2, 22, 2, 38, 24)
    assert p["tpr"] == pytest.approx(36 / 38, abs=1e-4) and p["tnr"] == pytest.approx(22 / 24, abs=1e-4)
    assert p["wilson"] == {"tpr": list(stats.wilson_interval(36, 38)), "tnr": list(stats.wilson_interval(22, 24))}
    assert m["passes"] is True and m["reason"] == "validated"
    a, b = m["by_company"]
    assert (a["organization_id"], a["n_pass"], a["n_fail"], a["tnr"]) == ("org-a", 21, 9, pytest.approx(8 / 9, abs=1e-4))
    assert (b["organization_id"], b["n_pass"], b["n_fail"]) == ("org-b", 17, 15)
    assert a["n_fail"] < 20 and b["n_fail"] < 20                         # neither company alone has enough


def test_too_few_labels_never_pass_whatever_the_rates():
    m = gvl.metrics(recs("o", tp=36, tn=10), min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    assert m["passes"] is False and "20 human passes and 20 human fails" in m["reason"] and "has 36 and 10" in m["reason"]
    empty = gvl.metrics([], min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    assert empty["passes"] is False and empty["pooled"]["tpr"] is None and empty["by_company"] == []
    assert empty["pooled"]["wilson"] == {"tpr": [None, None], "tnr": [None, None]}


def test_a_low_rate_does_not_pass():
    m = gvl.metrics(recs("o", tp=20, tn=16, fp=4), min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    assert m["passes"] is False and m["reason"].startswith("TNR 0.8")
    m = gvl.metrics(recs("o", tp=17, fn=3, tn=20), min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    assert m["passes"] is False and m["reason"].startswith("TPR 0.85")


def test_at_most_two_reps_of_one_case_count():
    rows = [{"org": "o", "case_key": "c1", "grader": "pass", "human": "pass"}] * 5
    m = gvl.metrics(rows, min_per_class=1, min_tpr=0.0, min_tnr=0.0)
    assert m["pooled"]["tp"] == 2 and m["pooled"]["n"] == 2
    other = [{"org": "o2", "case_key": "c1", "grader": "pass", "human": "pass"}]
    assert gvl.metrics(rows + other, min_per_class=1, min_tpr=0.0, min_tnr=0.0)["pooled"]["tp"] == 3


def test_unsure_is_reported_apart_and_never_agreement():
    m = gvl.metrics(recs("o", tp=5, unsure=5), min_per_class=1, min_tpr=0.0, min_tnr=0.0)
    assert (m["pooled"]["tp"], m["pooled"]["unsure"], m["pooled"]["unsure_rate"], m["pooled"]["tpr"]) == (5, 5, 0.5, 1.0)


def unsure_rows(org, human, n, start=0):
    return [{"org": org, "case_key": f"u{human}{start + i}", "grader": "unsure", "human": human} for i in range(n)]


def test_unsure_rows_never_satisfy_the_class_minimum():
    rows = (recs("o", tp=1) + unsure_rows("o", "pass", 19) + recs("o", tn=1, start=100) + unsure_rows("o", "fail", 19))
    m = gvl.metrics(rows, min_per_class=20, min_tpr=0.9, min_tnr=0.9)
    p = m["pooled"]
    assert (p["n_pass"], p["n_fail"], p["tpr"], p["tnr"]) == (20, 20, 1.0, 1.0)   # reported figures keep unsure
    assert m["passes"] is False and "has 1 and 1" in m["reason"]
    full = recs("o", tp=20, tn=20, start=200) + unsure_rows("o", "pass", 5)
    assert gvl.metrics(full, min_per_class=20, min_tpr=0.9, min_tnr=0.9)["passes"] is True


async def test_label_sources_are_merged_and_disagreements_left_out():
    async def first(ids):
        return {"t1": "pass", "t2": "fail", "t3": "pass", "t9": "maybe"}

    async def second(ids):
        return {"t2": "fail", "t3": "fail"}

    async def broken(ids):
        raise RuntimeError("store down")

    assert await gvl.human_labels(["t1"]) == {}                             # Phase 3: no source, no labels
    for fn in (first, second, broken, first):
        gvl.register_label_source(fn)
    assert gvl._SOURCES == [first, second, broken]
    assert await gvl.human_labels(["t1", "t2", "t3", "t9"]) == {"t1": "pass", "t2": "fail"}
    assert await gvl.human_labels([]) == {}


async def test_passes_is_false_when_validation_cannot_be_read(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("no database")

    monkeypatch.setattr(gvl, "validation", boom)
    assert await gvl.passes("abcdef012345") is False
