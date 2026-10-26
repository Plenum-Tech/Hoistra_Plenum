"""golden_views' pure rules, no database: how a result reads (judgment unvalidated until validation passes), the run
summary (Wilson pass rate, case pass rate, pass^k, cost, the Phase 2 runs-list row, the caveats), run detail with
readings only (never an expected answer or a critique), and two runs compared (flips, like-for-like warnings)."""
from __future__ import annotations

import json

import pytest

from trace_lab import golden_views as gv
from trace_lab import stats

CASES = {k: {"case_key": k, "case_version": f"v-{k}", "question": f"Question {k}?" + ("x" * 400 if k == "d" else ""),
             "followups": ["And then?"] if k == "c" else [], "evaluation": "judgment" if k == "b" else "objective"}
         for k in "abcd"}
RUN = {"id": "r1", "label": "run-1", "status": "paused", "k": 2, "set_id": "s1", "set_name": "Bishopsgate questions",
       "set_version": 3, "set_kind": "scenario", "set_status": "active", "purpose": "adhoc", "started_at": None,
       "finished_at": None, "started_by": "u-1", "started_by_email": "admin@example.com", "usd_cap": 5, "usd_spent": 1.2,
       "estimate_usd": 3, "summary": {"paused_reason": "page_closed", "current": {"case_key": "c", "rep": 2}},
       "params": {"chat_model": "gpt-5.6-terra", "flags": {"hitl_enabled": True}, "git_sha": "abc1234",
                  "context_default": "Bishopsgate Tower", "note": "After the routing change"}}


def row(key, rep, outcome="pass", *, msg_idx=0, grade=None, usd=0.05, ms=1000, refused=(), turn=None):
    return {"id": f"x-{key}{rep}{msg_idx}", "case_key": key, "rep": rep, "msg_idx": msg_idx, "outcome": outcome,
            "grade": json.dumps(grade) if grade is not None else None, "usd": usd, "ms": ms, "refused": list(refused),
            "turn_id": turn or f"t-{key}{rep}{msg_idx}", "session_id": f"golden-r1-{key}-r{rep}", "question": "q"}


def judged(verdict="pass", version="v1"):
    return {"verdict": verdict, "reading": "needs_person", "validated_outcome": verdict, "grader_version": version,
            "evaluation": "judgment", "usd": 0.02, "critique": "SECRET CRITIQUE"}


def graded(verdict="pass", version="v1", usd=0.02, stale=False):
    return {"verdict": verdict, "reading": verdict, "validated_outcome": verdict, "grader_version": version,
            "evaluation": "objective", "usd": usd, "critique": "SECRET CRITIQUE", "stale": stale}


def test_how_a_result_reads():
    assert gv.reading(row("b", 1, "unsure", grade=judged("fail"))) == "unvalidated"
    assert gv.reading(row("b", 1, "unsure", grade=judged("fail")), validated={"v1"}) == "fail"
    assert gv.reading(row("b", 1, "unsure", grade=judged("fail", "v0")), validated={"v1"}) == "unvalidated"
    assert gv.reading(row("a", 1, "unsure", grade=graded("unsure"))) == "unsure"
    for outcome in ("pass", "fail", "degraded", "stale_reference", "error", "pending"):
        assert gv.reading(row("a", 1, outcome)) == outcome
    assert gv.reading(row("a", 1, None)) == "pending" and gv.reading(row("a", 1, "needs_person")) == "pending"


def test_a_follow_up_case_is_final_on_its_last_message():
    items = gv.rep_rows([row("c", 1, "pending"), row("c", 1, "pass", msg_idx=1), row("c", 2, "pending")], CASES)
    assert items[("c", 1)]["final"]["msg_idx"] == 1 and items[("c", 2)]["final"] is None
    assert [r["msg_idx"] for r in items[("c", 1)]["rows"]] == [0, 1]


def test_the_summary():
    rows = [row("a", 1, grade=graded()), row("a", 2, grade=graded()),
            row("b", 1, "unsure", grade=judged("pass")), row("b", 2, "unsure", grade=judged("fail")),
            row("c", 1, "pending"), row("c", 1, "fail", msg_idx=1, grade=graded("fail")),
            row("c", 2, "pending"),
            row("d", 1, "degraded", refused=[{"service": "udr", "method": "POST", "path": "/api/x"}]),
            row("d", 2, "pass", grade=graded(stale=True))]
    s = gv.summarize(RUN, rows, CASES, viewer="u-1")
    assert s["pass_rate"] == {"passes": 2, "graded": 3, "rate": 0.6667, "wilson": list(stats.wilson_interval(2, 3))}
    assert s["pass_rate_incl_stale"] == {"passes": 3, "graded": 4, "rate": 0.75, "wilson": list(stats.wilson_interval(3, 4))}
    assert s["case_pass_rate"] == {"passes": 1, "measured": 2, "rate": 0.5, "wilson": list(stats.wilson_interval(1, 2))}
    assert (s["pass_at_k"], s["pass_hat_k"]) == (1.0, 1.0)                      # only a has two fresh counted reps
    assert {k: v for k, v in s["outcomes"].items() if v} == {"pass": 3, "fail": 1, "unvalidated": 2, "degraded": 1}
    assert (s["mine"], gv.summarize(RUN, rows, CASES, viewer="u-2")["mine"], s["grader_validated"]) == (True, False, False)
    assert (s["paused_reason"], s["current"], s["note"]) == ("page_closed", {"case_key": "c", "rep": 2},
                                                             "After the routing change")
    assert s["params"]["git_sha"] == "abc1234" and s["error"] is None
    assert (s["cases"], s["items_total"], s["items_done"], s["refused"]) == (4, 8, 7, 1)
    assert (s["answers_usd"], s["grading_usd"], s["cost_usd"]) == (0.45, 0.12, 0.57)
    assert (s["run_id"], s["kind"], s["name"], s["recorded"], s["conversation_count"], s["turn_count"]) == \
        ("r1", "golden", "run-1", True, 8, 9)
    assert s["models"] == ["gpt-5.6-terra"] and s["median_ms"] == 1000 and s["caveats"] == list(gv.FIDELITY_CAVEATS)
    assert s["set"] == {"id": "s1", "name": "Bishopsgate questions", "version": 3, "kind": "scenario", "status": "active"}
    v = gv.summarize(RUN, rows, CASES, validated={"v1"})                       # validation passed: b counts now
    assert v["pass_rate"]["graded"] == 5 and v["outcomes"]["unvalidated"] == 0 and v["grader_validated"] is True
    assert gv.summarize(RUN, rows[:2], CASES)["grader_validated"] is None        # no judgment case in the run
    empty = gv.summarize(dict(RUN, k=1), [], CASES)
    assert empty["pass_rate"] == {"passes": 0, "graded": 0, "rate": None, "wilson": None} and empty["pass_hat_k"] is None


def test_a_run_of_a_subset_counts_only_its_cases():
    run = dict(RUN, k=1, params={"case_keys": ["a", "zz"]})
    s = gv.summarize(run, [row("a", 1), row("b", 1)], CASES)
    assert (s["cases"], s["items_total"], s["pass_rate"]["graded"]) == (1, 1, 1)


def test_run_detail_shows_readings_only_and_never_the_expected_answer():
    rows = [row("a", 1, grade=graded()), row("b", 1, "unsure", grade=judged("fail")),
            row("c", 1, "pending", refused=[{"label": "Sending an email", "path": "/api/notify"}]),
            row("d", 1, "fail", grade=graded("fail"))]
    out = gv.detail_cases(dict(RUN, k=2), rows, CASES, revealed={"t-d10"})
    a, b, c, d = out
    assert [r["state"] for r in a["reps"]] == ["done", "not_started"] and a["reps"][0]["outcome"] == "pass"
    assert a["reps"][0]["conversation_key"] == "s:golden-r1-a-r1" and a["case_status"] == "pass"
    assert (a["reps"][0]["turn_id"], a["passes"], a["graded"], a["stale"]) == ("t-a10", 1, 1, False)
    assert b["reps"][0]["outcome"] == "unvalidated" and b["case_status"] == "not_measured" and b["graded"] == 0
    assert c["reps"][0]["state"] == "in_flight" and c["reps"][0]["refused"] == ["Sending an email"] and c["messages"] == 2
    assert (c["reps"][0]["refused_count"], c["reps"][0]["turn_id"]) == (1, "t-c10")
    assert d["reps"][0]["revealed"] is True and a["reps"][0]["revealed"] is False and len(d["question"]) == 300
    text_ = json.dumps(out)
    assert "SECRET CRITIQUE" not in text_ and "expected_answer" not in text_ and "/api/notify" not in text_
    assert "verdict" not in text_


def test_flips_matched_on_key_and_version():
    def bundle(run_id, outcomes, versions=None, keys="abcde", **run):
        cases = {k: {"case_key": k, "case_version": (versions or {}).get(k, "v1"), "question": f"Q {k}?", "followups": []}
                 for k in keys}
        rows = [row(k, 1, o, grade=graded(o) if o in ("pass", "fail") else None) for k, o in outcomes.items()]
        return {"run": dict(RUN, id=run_id, k=1, **run), "rows": rows, "cases": cases, "validated": frozenset()}

    base = bundle("b", {"a": "fail", "b": "pass", "c": "fail", "d": "pass", "e": "degraded", "g": "pass"},
                  keys="abcdeg")
    head = bundle("h", {"a": "pass", "b": "fail", "c": "fail", "d": "fail", "e": "pass", "f": "pass"},
                  versions={"d": "v2"}, keys="abcdef")
    out = gv.compare_runs(base, head)
    assert [r["case_key"] for r in out["flips"]["fixed"]] == ["a"]
    assert [r["case_key"] for r in out["flips"]["regressed"]] == ["b"]
    assert [r["case_key"] for r in out["flips"]["still_failing"]] == ["c"]
    assert [r["case_key"] for r in out["flips"]["now_measured"]] == ["e"]
    assert (out["changed"], out["added"], out["removed"], out["still_passing"]) == (["d"], ["f"], ["g"], 0)
    fixed = out["flips"]["fixed"][0]
    assert fixed["base"]["results"][0]["result_id"] == "x-a10" and fixed["head"]["results"][0]["outcome"] == "pass"
    assert (fixed["head"]["results"][0]["session_id"], fixed["head"]["results"][0]["turn_id"]) == ("golden-r1-a-r1", "t-a10")
    assert out["comparable"] is True and out["like_for_like"] is True and out["warnings"] == []
    assert out["pass_rate"]["delta"] == pytest.approx(out["pass_rate"]["head"]["rate"] - out["pass_rate"]["base"]["rate"],
                                                      abs=1e-4)


@pytest.mark.parametrize("head_over,comparable,warning", [
    ({"grader": "v9"}, False, "The two runs were graded by different grader versions."),
    ({"run": {"set_kind": "regression"}}, False, "The two runs used different golden sets."),
    ({"run": {"params": dict(RUN["params"], chat_model="gpt-6")}}, False, "The two runs used different chat models."),
    ({"run": {"params": dict(RUN["params"], flags={"hitl_enabled": False})}}, True,
     "The two runs had different chat settings."),
    ({"run": {"params": dict(RUN["params"], git_sha="def5678")}}, True,
     "The two runs ran on different builds of the assistant."),
    ({"run": {"params": dict(RUN["params"], context_default="")}}, True,
     "The two runs asked with different page context."),
    ({"run": {"k": 3}}, True, "The two runs used a different number of repetitions."),
    ({"run": {"set_id": "s2"}}, True, "The two runs used different versions of the golden set; cases are matched on "
                                      "their key and version."),
])
def test_like_for_like(head_over, comparable, warning):
    cases = {"a": {"case_key": "a", "case_version": "v1", "question": "Q?", "followups": []}}
    base = {"run": dict(RUN, k=1), "rows": [row("a", 1, "pass", grade=graded())], "cases": cases}
    head_run = {**RUN, "k": 1, "id": "r2", **head_over.get("run", {})}
    head = {"run": head_run, "rows": [row("a", 1, "fail", grade=graded("fail", head_over.get("grader", "v1")))],
            "cases": cases}
    out = gv.compare_runs(base, head)
    assert out["comparable"] is comparable and warning in out["warnings"] and out["like_for_like"] is False
    assert out["flips"]["regressed"][0]["case_key"] == "a"                     # flips are shown either way


def test_cost_reliability_and_the_params_diff():
    cases = {k: {"case_key": k, "case_version": "v1", "question": "Q?", "followups": []} for k in "ab"}
    base = {"run": dict(RUN, k=2), "cases": cases,
            "rows": [row("a", 1, grade=graded()), row("a", 2, "fail", grade=graded("fail")), row("b", 1, grade=graded()),
                     row("b", 2, grade=graded())]}
    head = {"run": dict(RUN, k=1, id="r2"), "cases": cases,
            "rows": [row("a", 1, usd=0.01, grade=graded(usd=0.01)), row("b", 1, usd=0.01, grade=graded(usd=0.01))]}
    out = gv.compare_runs(base, head)
    assert out["pass_hat_k"] == {"base": {"k": 2, "value": 0.5}, "head": {"k": 1, "value": None}, "k_differs": True}
    assert (out["cost"]["base_usd"], out["cost"]["head_usd"], out["cost"]["delta_usd"]) == (0.28, 0.04, -0.24)
    assert out["cost"]["base"] == {"total_usd": 0.28, "answers_usd": 0.2, "grading_usd": 0.08}
    assert out["latency"] == {"base_median_ms": 1000, "head_median_ms": 1000}
    assert gv.params_diff({"a": 1, "f": {"x": True}}, {"a": 1, "f": {"x": False}, "n": "y"}) == \
        {"f.x": [True, False], "n": [None, "y"]}
    assert gv.params_diff(RUN["params"], dict(RUN["params"])) == {}
