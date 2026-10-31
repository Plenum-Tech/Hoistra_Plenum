"""Eval cases (Phase 7, spec "Judges, code checks, monitoring, eval cases and release check" section 5): case shape, rep
verdicts, baselines, decisions, repeat summary, report text, cases and their golden twins, runs through the golden
runner, grading of reps and the release check.

No database, no model, no golden run: every store function and START_GOLDEN_RUN is replaced. The real-SQL side is in
tests/test_trace_lab_eval_cases_sql.py (scratch Postgres, skipped without TRACE_LAB_TEST_DB_URL)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from trace_lab import eval_cases
from trace_lab.monitor import WatchError

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ACTOR = "cccccccc-0000-4000-8000-000000000003"
M1 = "22222222-2222-4222-8222-222222222222"


def _rv(**kw):
    base = dict(rep=1, turn_id="turn-1", outcome="pass", status="completed", check_results=[], judge_results=[],
                golden_expected=True)
    base.update(kw)
    return eval_cases.rep_verdict(**base)


def _reps(*statuses):
    return [{"rep": i, "status": s, "failures": ["check:expected_values"] if s == "failed" else []}
            for i, s in enumerate(statuses, start=1)]


def _summary(statuses, *, k=5, purpose="release_check", kind="regression", rate=None):
    return eval_cases.case_run_summary(_reps(*statuses), k=k, purpose=purpose, kind=kind, baseline_pass_rate=rate)


def test_rep_verdict_precedence():
    fail_check = [{"key": "expected_values", "status": "fail"}]
    na_check = [{"key": "invented_identifiers", "status": "not_applicable"}]
    judge_fail = [{"mode": "wrong_period", "status": "decided", "failure_present": 1}]
    judge_error = [{"mode": "wrong_period", "status": "error", "failure_present": None}]
    judge_wait = [{"mode": "wrong_period", "status": "ungraded", "failure_present": None}]
    assert _rv()["status"] == "passed"
    assert _rv(turn_id=None, outcome=None, status=None)["status"] == "missing"
    assert _rv(outcome="degraded")["status"] == "degraded"
    assert _rv(outcome="error")["status"] == "degraded"                # golden_results.outcome: the service failed 3 times
    v = _rv(check_results=fail_check + na_check, judge_results=judge_fail, outcome="fail")
    assert v["status"] == "failed"                                     # a failure wins over a degraded part
    assert v["failures"] == ["check:expected_values", "judge:wrong_period", "golden:expected_answer"]
    assert _rv(check_results=na_check, outcome="unsure")["status"] == "degraded"
    assert _rv(judge_results=judge_error)["status"] == "degraded"
    assert _rv(judge_results=judge_wait, outcome="unsure")["status"] == "ungraded"
    assert _rv(outcome="unsure")["status"] == "pending"                # the golden grade needs a person
    assert _rv(outcome="pending")["status"] == "pending"               # the golden grade has not been made yet
    assert _rv(outcome="stale_reference")["status"] == "degraded"
    assert _rv(outcome="fail", golden_expected=False)["status"] == "passed"   # the golden grade is not used


def test_a_rep_with_a_judge_that_failed_and_one_not_yet_asked_is_degraded_not_ungraded():
    both = [{"mode": "wrong_period", "status": "error", "failure_present": None},
            {"mode": "invented_code", "status": "ungraded", "failure_present": None}]
    assert _rv(judge_results=both, golden_expected=False)["status"] == "degraded"


def test_rep_verdict_names_why_a_rep_is_degraded_even_with_no_answer_turn():
    """The runner's final row of a case that stopped early has no turn id; its outcome says why."""
    v = _rv(turn_id=None, outcome="degraded")
    assert v["status"] == "degraded" and v["degraded_reason"] == "a step was refused or needed approval"
    v = _rv(turn_id=None, outcome="error")
    assert v["status"] == "degraded" and v["degraded_reason"] == "the service failed three times in a row"
    v = _rv(turn_id=None, outcome="pass")
    assert v["status"] == "degraded" and v["degraded_reason"] == "no answer was recorded"
    v = _rv(outcome="stale_reference")
    assert v["degraded_reason"] == "the expected answer is out of date"
    assert _rv(outcome="stale_reference", golden_expected=False)["status"] == "passed"


def test_rep_verdict_keeps_what_the_grading_found():
    checks = [{"key": "must_contain", "status": "pass"}]
    judges = [{"mode": "wrong_period", "status": "decided", "failure_present": 0}]
    v = _rv(check_results=checks, judge_results=judges, usd=0.0123456789, graded_at="2026-10-15T10:00:00+00:00", steps=4)
    assert v["status"] == "passed" and v["failures"] == [] and v["degraded_reason"] is None
    assert v["checks"] == checks and v["judges"] == judges and v["golden"] == "pass"
    assert (v["usd"], v["graded_at"], v["steps"], v["rep"], v["turn_id"]) == (0.012346, "2026-10-15T10:00:00+00:00", 4, 1, "turn-1")
    assert v["checks"][0] is not checks[0]                              # a copy: the caller's lists are never aliased


def test_a_running_item_or_one_that_never_started_is_missing():
    assert _rv(turn_id=None, outcome=None, status="running")["status"] == "missing"
    assert _rv(turn_id=None, outcome=None, status="infra")["status"] == "missing"


def test_baseline_5_of_5_regression():
    s = _summary(["passed"] * 5, purpose="baseline", kind="unclassified")
    assert s["classification"] == {"kind": "regression", "baseline_pass_rate": None}
    assert s["decision"] == "pass" and s["pass_hat_k"] == 1.0 and s["pass_at_1"] == 1.0


def test_baseline_3_of_5_capability_0_6():
    s = _summary(["passed", "failed", "passed", "failed", "passed"], purpose="baseline", kind="unclassified")
    assert s["classification"] == {"kind": "capability", "baseline_pass_rate": 0.6}
    assert (s["pass_at_1"], s["pass_at_3"], s["pass_at_5"], s["pass_hat_k"]) == (0.6, 1.0, 1.0, 0.0)


def test_baseline_0_of_5_is_a_capability_case_with_rate_zero():
    s = _summary(["failed"] * 5, purpose="baseline", kind="unclassified")
    assert s["classification"] == {"kind": "capability", "baseline_pass_rate": 0.0}


def test_baseline_with_degraded_rep_not_classified():
    s = _summary(["passed", "passed", "degraded", "passed", "passed"], purpose="baseline", kind="unclassified")
    assert s["classification"] is None and s["decision"] == "incomplete" and s["reason"] == "Re-run the missing reps"
    s = _summary(["passed"] * 4, purpose="baseline", kind="unclassified")          # one run never happened
    assert s["classification"] is None and s["missing"] == 1


def test_classify_baseline_wants_exactly_five_decided_reps():
    assert eval_cases.classify_baseline(["passed"] * 4) is None
    assert eval_cases.classify_baseline(["passed"] * 6) is None
    assert eval_cases.classify_baseline(["passed"] * 4 + ["pending"]) is None
    assert eval_cases.classify_baseline(["passed"] * 4 + ["failed"]) == {"kind": "capability", "baseline_pass_rate": 0.8}


def test_regression_blocks_on_any_failed_rep():
    s = _summary(["passed", "passed", "failed", "passed", "passed"])
    assert s["decision"] == "block" and "failed 1 of 5" in s["reason"]


def test_capability_never_blocks():
    s = _summary(["failed"] * 5, kind="capability", rate=0.4)
    assert s["decision"] == "pass" and s["pass_at_1"] == 0.0


def test_degraded_regression_incomplete():
    assert _summary(["passed", "passed", "passed", "passed", "degraded"])["decision"] == "incomplete"
    s = _summary(["passed", "passed", "passed", "passed", "ungraded"])
    assert s["decision"] == "incomplete" and s["reason"].startswith("1 ungraded")
    assert _summary(["passed"] * 3)["decision"] == "incomplete"                     # n < planned


def test_a_run_of_an_unclassified_case_decides_nothing():
    s = _summary(["passed"] * 5, kind="unclassified")
    assert s["decision"] == "incomplete" and s["reason"] == "the case has no baseline yet, so this run decides nothing"


def test_case_run_summary_counts_pending_ungraded_and_missing_apart():
    s = eval_cases.case_run_summary([{"rep": 1, "status": "passed"}, {"rep": 2, "status": "pending"},
                                     {"rep": 3, "status": "ungraded"}, {"rep": 5, "status": "degraded"}],
                                    k=5, purpose="release_check", kind="regression", baseline_pass_rate=None)
    assert (s["n"], s["passes"], s["degraded"], s["pending"], s["missing"]) == (1, 1, 1, 2, 1)
    assert s["pass_at_1"] == 1.0 and s["pass_at_3"] is None and s["pass_hat_k"] is None


def test_release_decision_rules():
    p, b, i = {"decision": "pass"}, {"decision": "block"}, {"decision": "incomplete"}
    assert eval_cases.release_decision([], k=5)[0] == "incomplete"                  # nothing can be certified
    assert eval_cases.release_decision([p, i, b], k=5)[0] == "block"
    assert eval_cases.release_decision([p, i], k=5)[0] == "incomplete"
    assert eval_cases.release_decision([p, p], k=5) == ("pass", "all 2 regression cases passed 5 of 5 runs")


REPORT = {
    "golden_run_id": "9b2f0c1e-0000-4000-8000-00000000abcd", "run_key": "run-20261015T101500Z-9b2f0c1e",
    "organization_id": ORG_A, "decision": "block", "reason": "1 of 2 regression cases failed at least one run",
    "k": 5, "started_at": "2026-10-15T10:15:00+00:00", "finished_at": "2026-10-15T11:02:00+00:00", "usd": 4.8,
    "fingerprint": {"image": "f5-1a2b3c4", "openai_model": "gpt-5.6-terra", "grader_model": "claude-sonnet-5"},
    "graders": {"checks": ["code:invented_identifiers@v1"],
                "judges": [{"mode": "wrong_period", "judge_key": "wrong_period-v2", "prompt_hash": "f85bce23a878"}]},
    "regression": [{"case_key": "e-7f3a19c2", "version": 1, "mode": "wrong_period", "passes": 5, "n": 5,
                    "not_decided": 0, "decision": "pass", "reason": "regression case passed 5 of 5 runs",
                    "turn_ids": [], "judge_changed_since_baseline": False},
                   {"case_key": "e-0b1c2d3e", "version": 2, "mode": "invented_code", "passes": 4, "n": 5,
                    "not_decided": 0, "decision": "block", "reason": "regression case failed 1 of 5 runs",
                    "turn_ids": [], "judge_changed_since_baseline": True}],
    "capability": [{"case_key": "e-11223344", "version": 1, "mode": "wrong_period", "passes": 2, "n": 5,
                    "pass_at_1": 0.4, "pass_at_3": 0.9, "pass_at_5": 1.0, "pass_hat_5": 0.0,
                    "baseline_pass_rate": 0.4, "decision": "pass", "reason": "not blocking"}],
    "runs_without_verdict": [{"case_key": "e-11223344", "rep": 3, "status": "degraded",
                              "reason": "a step was refused or needed approval"}],
    "changes_since_last": [{"case_key": "e-0b1c2d3e", "before": "pass", "after": "block"}]}


def test_release_report_md_has_decision_cases_fingerprint_and_no_internal_names():
    md = eval_cases.render_release_md(REPORT, company="Northbridge Estates Ltd")
    assert md.splitlines()[0] == "# Release check — Northbridge Estates Ltd — run-20261015T101500Z-9b2f0c1e"
    assert "**Decision: BLOCK** — 1 of 2 regression cases failed at least one run" in md
    for part in ("## Run", "## Configuration", "## Regression cases", "## Capability cases",
                 "## Runs without a verdict", "## Changes since the last check"):
        assert part in md
    assert md.index("## Regression cases") < md.index("## Capability cases") < md.index("## Changes since the last check")
    assert "| e-0b1c2d3e v2 | invented_code | 4 of 5 | block | regression case failed 1 of 5 runs |" in md
    assert "| e-11223344 v1 | wrong_period | 0.40 | 0.90 | 1.00 | 0.00 | 0.40 |" in md
    assert "f5-1a2b3c4" in md and "gpt-5.6-terra" in md and "wrong_period-v2" in md
    assert "- e-11223344 run 3: degraded (a step was refused or needed approval)" in md
    assert "- e-0b1c2d3e: pass → block" in md
    assert "?tl=runs&run=9b2f0c1e-0000-4000-8000-00000000abcd" in md
    assert "- Code checks: Names a record code that no tool returned" in md      # by its label, never its key
    for internal in ("svc-", "plenum_", "deepagents", "deep-agents", "UDR", "judge:", "code:", "@v1", "golden:"):
        assert internal not in md, internal


def test_release_report_md_when_nothing_ran_or_changed():
    bare = {"golden_run_id": "r1", "run_key": "run-x", "decision": "incomplete", "reason": "No regression cases yet.",
            "k": 5, "graders": {"checks": ["code:retired_check@v9"], "judges": []}, "changes_since_last": None}
    md = eval_cases.render_release_md(bare, company="Acme")
    assert "No regression cases yet." in md and "None in this check." in md and "None." in md
    assert "No earlier release check." in md and "- Code checks: a retired check" in md
    assert "- Judge versions: none" in md and "- Finished: not finished" in md and "- Build: not recorded" in md
    assert "No changes." in eval_cases.render_release_md(dict(bare, changes_since_last=[]), company="Acme")
    cap = dict(bare, capability=[{"case_key": "e-1", "version": 1, "mode": None, "passes": 0, "n": 0, "pass_at_1": None}])
    assert "| e-1 v1 | — | — | — | — | — | — |" in eval_cases.render_release_md(cap, company="Acme")


def test_repeat_summary_uses_replay_bootstrap(monkeypatch):
    seen = {}

    def fake(records, **kw):
        seen.update(records=records, kw=kw)
        return {"failure_rate": 0.5, "ci_low": 0.0, "ci_high": 1.0, "mode_counts": {"judge:wrong_period": 1},
                "steps": {"min": 2, "median": 3, "max": 4}, "wilson_high": 0.9}

    monkeypatch.setattr(eval_cases.stats, "summarize_rollouts", fake)
    reps = [{"rep": 1, "status": "passed", "failures": [], "steps": 2},
            {"rep": 2, "status": "failed", "failures": ["judge:wrong_period"], "steps": 4},
            {"rep": 3, "status": "degraded", "failures": [], "steps": 3}]
    out = eval_cases.repeat_summary(reps)
    assert seen["records"] == [{"passed": True, "failures": [], "steps": 2},
                               {"passed": False, "failures": ["judge:wrong_period"], "steps": 4}]
    assert seen["kw"] == {}                                           # the replay defaults: 2,000 resamples, seed 7
    assert (out["n"], out["excluded"], out["failure_rate"]) == (2, 1, 0.5)
    assert out["guidance"] == eval_cases.REPLAY_GUIDANCE
    assert eval_cases.repeat_summary([{"rep": 1, "status": "degraded"}])["failure_rate"] is None


def test_repeat_summary_on_the_real_bootstrap_is_deterministic():
    reps = [{"rep": i, "status": "failed" if i <= 3 else "passed", "failures": ["judge:wrong_period"] if i <= 3 else [],
             "steps": 2 + i} for i in range(1, 11)]
    a, b = eval_cases.repeat_summary(reps), eval_cases.repeat_summary(reps)
    assert a == b and (a["n"], a["excluded"], a["failure_rate"]) == (10, 0, 0.3)
    assert a["mode_counts"] == {"judge:wrong_period": 3} and a["ci_low"] <= 0.3 <= a["ci_high"]


def test_case_from_turn_copies_context_and_scope_never_answer():
    turn = SimpleNamespace(question="And in August?", answer="There were 786 work orders.", prior=[
        {"turn_id": "turn-a", "question": "How many work orders were raised at Bishopsgate Tower in July 2026?",
         "answer": "There were 786."}])
    meta = {"context_text": "Page: Maintenance", "asker_role": "admin",
            "asker_scope": json.dumps({"building_ids": None, "acting": False})}
    out = eval_cases.case_from_turn(turn, meta)
    assert out == {"question": "How many work orders were raised at Bishopsgate Tower in July 2026?",
                   "followups": ["And in August?"], "page_context": "Page: Maintenance",
                   "scope": {"asker_role": "admin", "building_ids": None}}
    assert "786" not in json.dumps(out)
    long = SimpleNamespace(question="q6", prior=[{"question": f"q{i}"} for i in range(1, 6)])
    assert eval_cases.case_from_turn(long, None)["question"] == "q2"
    assert eval_cases.case_from_turn(long, None)["followups"] == ["q3", "q4", "q5", "q6"]


def test_case_from_turn_without_recorded_context_reads_the_turn_or_asks_with_none():
    """Under G0 nothing records the page or the asker's scope: the turn's own page context is used when it has one."""
    with_page = SimpleNamespace(question="How many?", prior=(), page_context="Page: Assets")
    assert eval_cases.case_from_turn(with_page, None) == {
        "question": "How many?", "followups": [], "page_context": "Page: Assets",
        "scope": {"asker_role": None, "building_ids": None}}
    bare = {"question": "How many?", "prior": []}
    assert eval_cases.case_from_turn(bare, None)["page_context"] == ""
    assert eval_cases.case_from_turn(bare, {"asker_scope": "not json {"})["scope"] == {"asker_role": None, "building_ids": None}


def test_merge_input_checks_what_the_twin_can_hold():
    base = {"question": "How many?", "followups": ["And?"], "page_context": "Page", "scope": {"asker_role": "admin"}}
    out = eval_cases.merge_input(base, {"question": "  How many in July?  ", "followups": [" a ", "", "b"],
                                        "page_context": None})
    assert out == {"question": "How many in July?", "followups": ["a", "b"], "page_context": None,
                   "scope": {"asker_role": "admin"}}
    assert eval_cases.merge_input(base, None) == base and eval_cases.merge_input(base, {}) == base
    assert eval_cases.merge_input(base, {"page_context": ""})["page_context"] == ""        # '' = ask with no page
    for bad in ({"question": "   "}, {"question": "q" * 2001}, {"followups": ["a"] * 5}, {"followups": ["x" * 2001]},
                {"page_context": "p" * 4001}):
        with pytest.raises(WatchError) as e:
            eval_cases.merge_input(base, bad)
        assert e.value.status == 422 and e.value.reason == "bad_input"
    with pytest.raises(WatchError):                                   # a question that is too long to start with
        eval_cases.merge_input({"question": "q" * 2001}, None)


def test_judges_in_expected_must_be_in_use():
    in_use = {M1: {"mode_id": M1}}
    out = eval_cases.normalise_expected(
        {"checks": [{"check": "expected_values", "values": [{"label": "July", "value": "786", "required": True}]}],
         "judges": [{"mode_id": M1}, {"mode_id": M1}]}, modes_in_use=in_use)
    assert out["judges"] == [{"mode_id": M1}] and out["checks"][0]["check"] == "expected_values"
    assert out["golden_expected"] is True
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"judges": [{"mode_id": "other"}]}, modes_in_use=in_use)
    assert e.value.status == 409 and e.value.reason == "judge_not_frozen"
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"checks": [{"check": "nope"}]}, modes_in_use=in_use)
    assert e.value.status == 422
    with pytest.raises(WatchError) as e:
        eval_cases.normalise_expected({"golden_expected": False}, modes_in_use=in_use)
    assert e.value.reason == "nothing_decides"


def test_expected_defaults_trim_prose_and_allow_a_check_alone():
    out = eval_cases.normalise_expected(None, modes_in_use={})
    assert out == {"assertions": [], "checks": [], "judges": [], "golden_expected": True}
    out = eval_cases.normalise_expected({"assertions": ["a" * 600] * 25, "golden_expected": False,
                                         "checks": [{"check": "must_contain", "text": "786"}]}, modes_in_use={})
    assert len(out["assertions"]) == 20 and len(out["assertions"][0]) == 500 and out["golden_expected"] is False
    assert out["checks"] == [{"text": "786", "check": "must_contain"}]


def test_parse_values_reads_the_golden_grammar_and_says_what_it_cannot():
    assert eval_cases.parse_values(None) == [] and eval_cases.parse_values("   ") == []
    items = eval_cases.parse_values("786; !758")
    assert len(items) == 2 and items[0]["raw"] == "786"
    with pytest.raises(WatchError) as e:
        eval_cases.parse_values("; ".join(["1"] * 500))
    assert e.value.status == 422 and e.value.reason == "bad_expected_values"


def test_edit_creates_version_and_resets_kind():
    old = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "active",
           "kind": "regression", "baseline_pass_rate": None, "baseline_run_id": "r1", "mode_id": M1,
           "source_type": "live_turn", "source_turn_id": "turn-1", "source_batch_id": None,
           "source_golden_case_id": None, "golden_case_id": "g1",
           "input": json.dumps({"question": "How many?", "followups": [], "page_context": "", "scope": {}}),
           "initial_state": {}, "expected": {"assertions": [], "checks": [], "judges": [], "golden_expected": True},
           "rev": 3}
    new = eval_cases.next_version(old, input_override={"question": "How many work orders in July 2026?"})
    assert (new["case_key"], new["version"], new["status"]) == ("e-7f3a19c2", 2, "draft")
    assert new["kind"] == "unclassified" and new["baseline_pass_rate"] is None and new["baseline_run_id"] is None
    assert new["supersedes"] == "c1" and new["golden_case_id"] is None and new["rev"] == 1 and new["id"] != "c1"
    assert new["input"]["question"] == "How many work orders in July 2026?"
    assert eval_cases.twin_key(new["case_key"], new["version"]) == "e-7f3a19c2.v2"


def test_an_edited_capability_case_loses_its_baseline_rate():
    old = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "active",
           "kind": "capability", "baseline_pass_rate": 0.6, "baseline_run_id": "r1", "golden_case_id": "g1",
           "source_type": "golden_case", "source_golden_case_id": "g0", "input": {"question": "q?"}, "expected": {}, "rev": 4}
    new = eval_cases.next_version(old)
    assert (new["kind"], new["baseline_pass_rate"], new["baseline_run_id"], new["golden_case_id"]) == ("unclassified", None, None, None)
    assert new["source_golden_case_id"] == "g0"                          # where it came from stays


def test_next_version_takes_new_state_and_expected_and_never_the_old_runs():
    old = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 2, "status": "draft",
           "kind": "unclassified", "mode_id": M1, "source_type": "monitor", "source_turn_id": "t", "source_batch_id": "b1",
           "source_golden_case_id": None, "input": {"question": "How many?", "followups": []},
           "initial_state": {"as_of": "2026-10-01"}, "expected": {"checks": []}, "rev": 2, "retired_reason": "x"}
    new = eval_cases.next_version(old, initial_state={"as_of": "2026-10-15"}, expected={"golden_expected": True})
    assert new["version"] == 3 and new["initial_state"] == {"as_of": "2026-10-15"}
    assert new["expected"] == {"golden_expected": True} and new["input"] == {"question": "How many?", "followups": []}
    assert new["source_batch_id"] == "b1" and new["retired_reason"] is None
    same = eval_cases.next_version(old)
    assert same["initial_state"] == {"as_of": "2026-10-01"} and same["expected"] == {"checks": []}


def test_case_public_decodes_json_and_carries_the_latest_run():
    row = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "active",
           "kind": "capability", "baseline_pass_rate": 0.6, "input": '{"question": "q"}', "initial_state": "{}",
           "expected": '{"checks": []}', "rev": 4, "secret_column": "left out", "run_golden_run_id": "r9",
           "run_purpose": "release_check", "run_decision": "pass", "run_passes": 5, "run_n": 5, "run_k": 5}
    out = eval_cases.case_public(row)
    assert out["input"] == {"question": "q"} and out["expected"] == {"checks": []} and "secret_column" not in out
    assert out["latest_run"]["golden_run_id"] == "r9" and out["latest_run"]["decision"] == "pass"
    assert eval_cases.case_public({**row, "run_golden_run_id": None})["latest_run"] is None


def test_only_chat_answers_that_ask_for_no_change_replay():
    assert eval_cases.replayable("How many work orders in July?", "chat", "s1")
    assert not eval_cases.replayable("Raise a work order for the boiler", "chat", "s1")
    assert not eval_cases.replayable("How many work orders in July?", "upload", "s1")
    assert not eval_cases.replayable("How many work orders in July?", "chat", "golden-1a2b3c4d-21-r1")


def test_replayable_reads_missing_parts_as_not_replayable_or_plain():
    assert eval_cases.replayable("How many?", "chat", None)
    assert eval_cases.replayable(None, "chat", "s1")
    assert not eval_cases.replayable("How many?", None, "s1")


def test_limits_and_names_are_the_designs():
    assert (eval_cases.BASELINE_K, eval_cases.REPEAT_DEFAULT_N, eval_cases.REPEAT_MIN_N) == (5, 10, 2)
    assert (eval_cases.RELEASE_DEFAULT_K, eval_cases.RELEASE_MAX_K, eval_cases.MAX_FOLLOWUPS) == (5, 10, 4)
    assert eval_cases.REPEAT_MAX_N == eval_cases.runner.HARD_MAX_K == 20
    assert eval_cases.REGRESSION_SET_NAME == eval_cases.golden_store.REGRESSION_SET_NAME == "Regression cases"
    assert eval_cases.twin_key("e-7f3a19c2", 3) == "e-7f3a19c2.v3"
    key = eval_cases.new_case_key()
    assert key.startswith("e-") and len(key) == 10 and key != eval_cases.new_case_key()


def test_the_grading_cap_reads_its_variable(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_EVAL_GRADING_USD_CAP", raising=False)
    assert eval_cases.eval_grading_usd_cap() == 2.0
    monkeypatch.setenv("TRACE_LAB_EVAL_GRADING_USD_CAP", "0.5")
    assert eval_cases.eval_grading_usd_cap() == 0.5


# ---- cases and their golden twins (the company's Regression cases set) -----------------------------------------

def _turn_row(tid, question="How many work orders were raised at Bishopsgate Tower in July 2026?", channel="chat"):
    return {"turn_id": tid, "organization_id": ORG_A, "session_id": "s-1", "question": question, "channel": channel,
            "context_text": "Page: Maintenance", "asker_role": "admin", "asker_scope": None}


def _kept_evidence(monkeypatch):
    """Replaces evidence.snapshot: the calls it gets, in order."""
    calls = []

    async def snapshot(turn_ids, *, organization_id, reason):
        calls.append((list(turn_ids), organization_id, reason))
        return len(turn_ids)

    monkeypatch.setattr(eval_cases.evidence, "snapshot", snapshot)
    return calls


def _a_mode_exists(monkeypatch, known=(M1,)):
    async def mode(mode_id):
        return str(mode_id) in known

    monkeypatch.setattr(eval_cases, "_mode_exists", mode)


async def test_live_turn_case_needs_confirmed_failure_and_expected_answer(monkeypatch):
    inserted, confirmed, order = [], {"value": False}, []
    kept = _kept_evidence(monkeypatch)
    _a_mode_exists(monkeypatch)

    async def turn(org, tid):
        return _turn_row(tid)

    async def is_confirmed(org, tid, mode):
        return confirmed["value"]

    async def no_case(org, tid):
        return None

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def load(ids):                                              # the judge store's loader: {turn_id: NormalizedTurn}
        order.append("load")
        return {t: SimpleNamespace(question="How many work orders were raised at Bishopsgate Tower in July 2026?",
                                   prior=[], page_context=None) for t in ids}

    async def insert(case, twin, **kw):
        order.append("insert")
        inserted.append((case, twin))
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", is_confirmed)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", no_case)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    body = dict(source={"type": "live_turn", "turn_id": "turn-0000000000000001"}, mode_id=M1,
                expected={"judges": [{"mode_id": M1}]}, actor_id=ACTOR, actor_email="reviewer@example.com")
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, expected_answer="There were 786.", **body)
    assert e.value.status == 409 and e.value.reason == "failure_not_confirmed" and inserted == [] and kept == []
    confirmed["value"] = True
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, expected_answer="   ", **body)
    assert e.value.status == 422 and e.value.reason == "expected_answer_required" and inserted == [] and kept == []
    out = await eval_cases.create_case(ORG_A, expected_answer="There were 786 work orders in July 2026.", **body)
    case, twin = inserted[0]
    assert case["status"] == "draft" and case["source_type"] == "live_turn" and case["source_turn_id"] == "turn-0000000000000001"
    assert twin["case_key"] == eval_cases.twin_key(case["case_key"], 1)
    assert twin["expected_answer"] == "There were 786 work orders in July 2026." and twin["evaluation"] == "objective"
    assert twin["question"].startswith("How many work orders") and twin["page_context"] == "Page: Maintenance"
    assert case["expected"]["judges"] == [{"mode_id": M1}] and out["case_key"] == case["case_key"]
    assert kept == [(["turn-0000000000000001"], ORG_A, "proposed")] and order == ["load", "insert"]


async def test_a_case_refuses_the_answer_that_is_not_here_or_cannot_replay_or_already_has_one(monkeypatch):
    turns = {"t-ok": _turn_row("t-ok"), "t-write": _turn_row("t-write", question="Raise a work order for the boiler"),
             "t-upload": _turn_row("t-upload", channel="upload"), "t-has": _turn_row("t-has")}
    kept = _kept_evidence(monkeypatch)

    async def turn(org, tid):
        return turns.get(tid)

    async def in_use(mode_ids=None):
        return []

    async def confirmed(org, tid, mode):
        return True

    async def live(org, tid):
        return {"id": "c0"} if tid == "t-has" else None

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", confirmed)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", live)
    for tid, status, reason in (("t-missing", 404, "turn_not_in_scope"), ("t-write", 409, "turn_not_replayable"),
                                ("t-upload", 409, "turn_not_replayable"), ("t-has", 409, "case_exists")):
        with pytest.raises(WatchError) as e:
            await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": tid}, expected_answer="x",
                                         actor_id=ACTOR, actor_email="r@example.com")
        assert (e.value.status, e.value.reason) == (status, reason), tid
    assert kept == []
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source={"type": "chat"}, actor_id=ACTOR, actor_email=None)
    assert (e.value.status, e.value.reason) == (422, "bad_source")
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source=None, actor_id=ACTOR, actor_email=None)
    assert e.value.reason == "bad_source"


async def test_a_case_names_a_real_mode_and_a_question_its_twin_can_hold(monkeypatch):
    inserted = []
    _kept_evidence(monkeypatch)
    _a_mode_exists(monkeypatch)

    async def turn(org, tid):
        return _turn_row(tid, question="q" * 2500)

    async def yes(org, tid, mode):
        return True

    async def none(org, tid):
        return None

    async def in_use(mode_ids=None):
        return []

    async def load(ids):
        return {}

    async def insert(case, twin, **kw):
        inserted.append(case)
        return case

    for name, fn in {"_turn_for_case": turn, "_failure_confirmed": yes, "_live_case_for_turn": none,
                     "judges_in_use": in_use, "_insert_case": insert}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    body = dict(source={"type": "live_turn", "turn_id": "t-1"}, expected_answer="x", actor_id=ACTOR, actor_email="r@x.test")
    with pytest.raises(WatchError) as e:                              # the twin's question is at most 2,000 characters
        await eval_cases.create_case(ORG_A, **body)
    assert (e.value.status, e.value.reason) == (422, "bad_input") and inserted == []
    out = await eval_cases.create_case(ORG_A, input_override={"question": "How many work orders in July?"}, **body)
    assert out["input"]["question"] == "How many work orders in July?"
    for bad_mode, status in (("not-a-uuid", 404), ("33333333-3333-4333-8333-333333333333", 404)):
        with pytest.raises(WatchError) as e:
            await eval_cases.create_case(ORG_A, mode_id=bad_mode, input_override={"question": "ok?"}, **body)
        assert (e.value.status, e.value.reason) == (status, "mode_not_found")
    assert len(inserted) == 1


async def test_golden_case_source_copies_the_golden_case_as_its_twin(monkeypatch):
    inserted = []
    kept = _kept_evidence(monkeypatch)

    async def golden_case(org, gid):
        return {"id": gid, "question": "How many floors does Bishopsgate Tower have?", "followups": "[]",
                "expected_answer": "22 floors.", "evaluation": "objective",
                "expected_values": json.dumps([{"label": "floors", "kind": "number", "value": 22}]),
                "page_context": None}

    async def in_use(mode_ids=None):
        return []

    async def insert(case, twin, **kw):
        inserted.append((case, twin))
        return case

    monkeypatch.setattr(eval_cases, "_golden_case", golden_case)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    await eval_cases.create_case(ORG_A, source={"type": "golden_case", "golden_case_id": "g-1"}, actor_id=ACTOR,
                                 actor_email=None)
    case, twin = inserted[0]
    assert case["status"] == "draft" and case["source_golden_case_id"] == "g-1" and case["source_turn_id"] is None
    assert twin["expected_answer"] == "22 floors." and twin["expected_values"][0]["value"] == 22
    assert twin["page_context"] is None and case["expected"]["golden_expected"] is True
    assert kept == []                                                 # a golden case is already in the lab's own tables


async def test_a_golden_case_that_is_not_the_companys_is_not_found(monkeypatch):
    async def none(org, gid):
        return None

    async def in_use(mode_ids=None):
        return []

    monkeypatch.setattr(eval_cases, "_golden_case", none)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source={"type": "golden_case", "golden_case_id": "g-9"}, actor_id=ACTOR,
                                     actor_email=None)
    assert (e.value.status, e.value.reason) == (404, "not_found")


async def test_confirm_needs_a_proposed_case_a_fresh_base_and_an_expected_answer(monkeypatch):
    row = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "proposed",
           "rev": 2, "mode_id": M1, "source_turn_id": "turn-1",
           "input": {"question": "How many work orders in July?", "followups": [], "page_context": ""}}
    calls = []

    async def case(org, cid):
        return dict(row)

    async def confirmed(org, tid, mode):
        return True

    async def with_twin(twin, **kw):
        calls.append((twin, kw))
        return "g1"

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", confirmed)
    monkeypatch.setattr(eval_cases, "_with_twin", with_twin)
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, "c1", base=1, expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.status == 409 and e.value.reason == "stale_base"
    await eval_cases.confirm_case(ORG_A, "c1", base=2, expected_answer="There were 786.", actor_id=ACTOR,
                                  actor_email=None)
    twin, kw = calls[0]
    assert twin["case_key"] == "e-7f3a19c2.v1" and twin["expected_answer"] == "There were 786."
    assert kw["params"]["base"] == 2 and "status = 'proposed'" in kw["update_sql"]
    row["status"] = "draft"
    with pytest.raises(WatchError) as e:
        await eval_cases.confirm_case(ORG_A, "c1", base=2, expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.reason == "not_proposed"


async def test_confirm_refuses_an_unknown_case_an_unconfirmed_failure_and_a_missing_answer(monkeypatch):
    row = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "proposed", "rev": 1,
           "mode_id": M1, "source_turn_id": "turn-1", "input": {"question": "q?", "followups": []}}
    state = {"row": row, "confirmed": False}

    async def case(org, cid):
        return dict(state["row"]) if state["row"] else None

    async def confirmed(org, tid, mode):
        assert mode == M1
        return state["confirmed"]

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", confirmed)
    for setup, reason, status in ((lambda: state.update(row=None), "not_found", 404),
                                  (lambda: state.update(row=row), "failure_not_confirmed", 409),
                                  (lambda: state.update(confirmed=True), "expected_answer_required", 422)):
        setup()
        with pytest.raises(WatchError) as e:
            await eval_cases.confirm_case(ORG_A, "c1", base=1, expected_answer="  ", actor_id=ACTOR, actor_email="r@x.test")
        assert (e.value.status, e.value.reason) == (status, reason)


async def test_confirm_of_a_case_that_already_has_a_twin_only_changes_its_status(monkeypatch):
    """repeat-n made the twin from a written answer: confirming the failure then needs no second answer."""
    row = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "proposed", "rev": 1,
           "mode_id": M1, "source_turn_id": "turn-1", "golden_case_id": "g-1", "input": {"question": "q?"}}
    ran = []

    async def case(org, cid):
        return dict(row, status="draft") if ran else dict(row)

    async def confirmed(org, tid, mode):
        return True

    async def set_draft(case_id, base):
        ran.append((case_id, base))

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_failure_confirmed", confirmed)
    monkeypatch.setattr(eval_cases, "_mark_draft", set_draft)
    out = await eval_cases.confirm_case(ORG_A, "c1", base=1, actor_id=ACTOR, actor_email=None)
    assert ran == [("c1", 1)] and out["status"] == "draft"


async def test_propose_from_monitor_skips_turns_that_already_have_a_case(monkeypatch):
    inserted = []
    kept = _kept_evidence(monkeypatch)

    async def turn(org, tid):
        return dict(_turn_row(tid, question=f"How many certificates expire in {tid}?"), context_text=None)

    async def live(org, tid):
        return {"id": "existing"} if tid == "t2" else None

    async def load(ids):
        return {t: SimpleNamespace(question=f"How many certificates expire in {t}?", prior=[], page_context=None)
                for t in ids}

    async def insert(case, twin, **kw):
        inserted.append((case, twin, kw))
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", live)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    ids = await eval_cases.propose_from_monitor(batch={"id": "b1", "started_by": ACTOR, "started_by_email": "o@x"},
                                                mode_id=M1, turn_ids=["t1", "t2", "t3"])
    assert len(ids) == 2 and [c["source_turn_id"] for c, _, _ in inserted] == ["t1", "t3"]
    assert all(c["status"] == "proposed" and c["source_type"] == "monitor" and c["source_batch_id"] == "b1"
               and twin is None for c, twin, _ in inserted)
    assert inserted[0][0]["expected"] == {"assertions": [], "checks": [], "judges": [{"mode_id": M1}],
                                          "golden_expected": True}
    assert [k[0] for k in kept] == [["t1"], ["t3"]] and {k[1] for k in kept} == {ORG_A} and {k[2] for k in kept} == {"proposed"}


async def test_propose_from_monitor_keeps_going_past_an_answer_it_cannot_use(monkeypatch):
    inserted = []
    cases = {"t-write": _turn_row("t-write", question="Raise a work order for the boiler"), "t-gone": None,
             "t-long": _turn_row("t-long", question="q" * 2500), "t-nosnap": _turn_row("t-nosnap"),
             "t-ok": _turn_row("t-ok")}

    async def turn(org, tid):
        assert org is None                                            # a platform batch: each answer's own company decides
        return cases[tid]

    async def snapshot(ids, *, organization_id, reason):
        if ids == ["t-nosnap"]:
            raise RuntimeError("the evidence could not be kept")

    async def none(org, tid):
        return None

    async def load(ids):
        return {}

    async def insert(case, twin, **kw):
        inserted.append(case)
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases.evidence, "snapshot", snapshot)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", none)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    ids = await eval_cases.propose_from_monitor(batch={"id": "b1", "started_by": None}, mode_id=M1, turn_ids=list(cases))
    assert [c["source_turn_id"] for c in inserted] == ["t-ok"] and ids == [inserted[0]["id"]]


async def test_propose_from_monitor_never_makes_more_than_the_cap(monkeypatch):
    inserted = []
    _kept_evidence(monkeypatch)

    async def turn(org, tid):
        return _turn_row(tid)

    async def none(org, tid):
        return None

    async def load(ids):
        return {}

    async def insert(case, twin, **kw):
        inserted.append(case)
        return case

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", none)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    ids = await eval_cases.propose_from_monitor(batch={"id": "b1"}, mode_id=M1, turn_ids=[f"t{i}" for i in range(9)])
    assert len(ids) == len(inserted) == eval_cases.monitor.PROPOSE_CASES_MAX == 5


def test_a_twin_is_recorded_under_a_real_actor_id_and_email():
    with pytest.raises(WatchError) as e:                              # golden_store.regression_set records who made the set
        eval_cases._actor_for_twin(ACTOR, None)
    assert e.value.status == 422 and e.value.reason == "actor_required"
    with pytest.raises(WatchError):
        eval_cases._actor_for_twin(None, "reviewer@example.com")
    assert eval_cases._actor_for_twin(ACTOR, "reviewer@example.com") == (ACTOR, "reviewer@example.com")


def test_a_twin_is_built_from_the_case_and_the_written_answer():
    case = {"organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 3,
            "input": {"question": "How many?", "followups": ["And in August?"], "page_context": "Page: Assets"}}
    twin = eval_cases._twin_from(case, expected_answer="  786.  ", evaluation="objective", values=[{"raw": "786"}],
                                 source="Eval case e-7f3a19c2 v3")
    assert twin == {"org": ORG_A, "case_key": "e-7f3a19c2.v3", "question": "How many?", "followups": ["And in August?"],
                    "expected_answer": "786.", "evaluation": "objective", "expected_values": [{"raw": "786"}],
                    "page_context": "Page: Assets", "source": "Eval case e-7f3a19c2 v3"}
    assert eval_cases._twin_from(case, expected_answer="x" * 7000, evaluation="judgment", values=[],
                                 source="s")["expected_answer"] == "x" * 6000


def test_a_new_case_starts_at_version_one_unclassified_with_todays_date():
    case = eval_cases._new_case(ORG_A, "e-00000001", status="draft", source_type="live_turn", inp={"question": "q?"},
                                expected={"checks": []}, mode_id=M1, source_turn_id="t")
    assert (case["version"], case["kind"], case["status"], case["rev"]) == (1, "unclassified", "draft", 1)
    assert case["baseline_pass_rate"] is None and case["golden_case_id"] is None and case["supersedes"] is None
    assert case["initial_state"]["as_of"] == eval_cases.datetime.now(eval_cases.timezone.utc).date().isoformat()
    assert eval_cases._new_case(ORG_A, "e-00000002", status="proposed", source_type="monitor", inp={}, expected={},
                                initial_state={"as_of": "2026-01-02"})["initial_state"] == {"as_of": "2026-01-02"}


async def test_edit_makes_a_new_version_with_a_twin_that_keeps_the_written_answer(monkeypatch):
    old = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "status": "active", "rev": 3,
           "kind": "regression", "baseline_pass_rate": None, "baseline_run_id": "r1", "mode_id": M1,
           "source_type": "live_turn", "source_turn_id": "turn-1", "source_batch_id": None, "source_golden_case_id": None,
           "golden_case_id": "g1", "input": {"question": "How many?", "followups": [], "page_context": ""},
           "initial_state": {}, "expected": {"assertions": [], "checks": [], "judges": [], "golden_expected": True}}
    seen = {}

    async def case(org, cid):
        return dict(old)

    async def twin(gid):
        return {"id": gid, "expected_answer": "There were 786.", "evaluation": "judgment",
                "expected_values": json.dumps([{"raw": "786"}])}

    async def insert(new, twin_, *, actor_id, actor_email, supersede=None):
        seen.update(new=new, twin=twin_, supersede=supersede)
        return new

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_twin", twin)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    out = await eval_cases.edit_case(ORG_A, "c1", base=3, input_override={"question": "How many in July 2026?"},
                                     actor_id=ACTOR, actor_email="r@x.test")
    assert out["version"] == 2 and seen["supersede"] == ("c1", 3) and seen["new"]["golden_case_id"] is None
    t = seen["twin"]
    assert t["case_key"] == "e-7f3a19c2.v2" and t["question"] == "How many in July 2026?"
    assert t["expected_answer"] == "There were 786." and t["evaluation"] == "judgment" and t["expected_values"] == [{"raw": "786"}]
    await eval_cases.edit_case(ORG_A, "c1", base=3, expected_answer="786 in July.", evaluation="objective",
                               expected_values="786", actor_id=ACTOR, actor_email="r@x.test")
    t = seen["twin"]
    assert t["expected_answer"] == "786 in July." and t["evaluation"] == "objective" and t["expected_values"][0]["raw"] == "786"


async def test_edit_refuses_what_it_cannot_version(monkeypatch):
    state = {"row": None}

    async def case(org, cid):
        return dict(state["row"]) if state["row"] else None

    async def twin(gid):
        return None

    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_twin", twin)
    base = {"id": "c1", "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "rev": 2, "golden_case_id": None,
            "input": {"question": "q?"}, "expected": {}}
    for row, reason, status in ((None, "not_found", 404), (dict(base, status="retired"), "case_retired", 409),
                                (dict(base, status="superseded"), "case_retired", 409),
                                (dict(base, status="proposed"), "not_confirmed", 409),
                                (dict(base, status="draft", rev=5), "stale_base", 409),
                                (dict(base, status="draft"), "expected_answer_required", 422)):
        state["row"] = row
        with pytest.raises(WatchError) as e:
            await eval_cases.edit_case(ORG_A, "c1", base=2, input_override={"question": "ok?"}, actor_id=ACTOR,
                                       actor_email="r@x.test")
        assert (e.value.status, e.value.reason) == (status, reason)


async def test_a_case_is_not_made_from_an_answer_whose_evidence_cannot_be_kept(monkeypatch):
    inserted = []
    _a_mode_exists(monkeypatch)

    async def snapshot(ids, *, organization_id, reason):
        raise RuntimeError("the lab database refused")

    async def turn(org, tid):
        return _turn_row(tid)

    async def yes(org, tid, mode):
        return True

    async def none(org, tid):
        return None

    async def in_use(mode_ids=None):
        return []

    async def insert(case, twin, **kw):
        inserted.append(case)
        return case

    monkeypatch.setattr(eval_cases.evidence, "snapshot", snapshot)
    for name, fn in {"_turn_for_case": turn, "_failure_confirmed": yes, "_live_case_for_turn": none,
                     "judges_in_use": in_use, "_insert_case": insert}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    with pytest.raises(WatchError) as e:
        await eval_cases.create_case(ORG_A, source={"type": "live_turn", "turn_id": "t-1"}, expected_answer="x",
                                     actor_id=ACTOR, actor_email="r@x.test")
    assert (e.value.status, e.value.reason) == (503, "evidence_not_kept") and inserted == []


# ---- eval runs through the golden runner: baseline, repeat-n, repeat from an answer, release check -------------

CTX = eval_cases.RunContext(org=ORG_A, principal=SimpleNamespace(role="admin"), authorization="Bearer token")


def _case_row(cid, *, status="draft", kind="unclassified", key=None, version=1, twin="g-1", rate=None):
    return {"id": cid, "organization_id": ORG_A, "case_key": key or "e-7f3a19c2", "version": version,
            "status": status, "kind": kind, "baseline_pass_rate": rate, "baseline_run_id": None, "mode_id": M1,
            "golden_case_id": twin, "rev": 1, "source_turn_id": "turn-1",
            "input": {"question": "How many work orders in July 2026?", "followups": [], "page_context": ""}}


def _stub_runs(monkeypatch, cases, *, run_open=False):
    log = SimpleNamespace(started=[], case_runs=[], baselines=[])

    async def start(**kw):
        log.started.append(kw)
        return {"id": f"run-{len(log.started)}", "label": "run-20261015T101500Z-0000000a", "status": "running"}

    async def case(org, cid):
        return dict(cases[cid]) if cid in cases else None

    async def twin_ref(c):
        if not c.get("golden_case_id"):
            raise WatchError(409, "expected_answer_required", "no twin")
        return "set-regression", eval_cases.twin_key(c["case_key"], c["version"])

    async def is_open(run_id):
        return run_open

    async def add_runs(rows):
        log.case_runs.extend(rows)

    async def begin(case_id, run_id):
        log.baselines.append((case_id, run_id))

    async def active(org):
        return [dict(c) for c in cases.values() if c["status"] == "active"]

    monkeypatch.setattr(eval_cases, "START_GOLDEN_RUN", start)
    monkeypatch.setattr(eval_cases, "_case", case)
    monkeypatch.setattr(eval_cases, "_twin_ref", twin_ref)
    monkeypatch.setattr(eval_cases, "_run_open", is_open)
    monkeypatch.setattr(eval_cases, "_add_case_runs", add_runs)
    monkeypatch.setattr(eval_cases, "_begin_baseline", begin)
    monkeypatch.setattr(eval_cases, "_active_cases", active)
    return log


async def test_the_default_run_starter_asks_the_golden_runner_with_the_callers_bearer(monkeypatch):
    seen = {}

    async def start_run(*, org, principal, authorization, body):
        seen.update(org=org, principal=principal, authorization=authorization, body=body)
        return {"ok": True, "run": {"id": "run-1", "label": "run-x", "status": "running"}, "estimate": {}}

    monkeypatch.setattr(eval_cases.runner, "start_run", start_run)
    assert eval_cases.START_GOLDEN_RUN is eval_cases._start_golden_run
    out = await eval_cases.START_GOLDEN_RUN(ctx=CTX, set_id="set-1", case_keys=("e-1.v1", "e-2.v1"), k=5,
                                            purpose="release_check", usd_cap=1.5, note="n" * 600)
    assert out == {"id": "run-1", "label": "run-x", "status": "running"}
    assert seen["org"] == ORG_A and seen["principal"] is CTX.principal and seen["authorization"] == "Bearer token"
    assert seen["body"] == {"set_id": "set-1", "case_keys": ["e-1.v1", "e-2.v1"], "k": 5, "purpose": "release_check",
                            "usd_cap": 1.5, "context_default": "", "note": "n" * 500}


async def test_baseline_runs_five_reps_on_the_twin(monkeypatch):
    log = _stub_runs(monkeypatch, {"c1": _case_row("c1")})
    out = await eval_cases.start_baseline(CTX, "c1")
    kw = log.started[0]
    assert (kw["k"], kw["purpose"], kw["case_keys"], kw["set_id"]) == (5, "baseline", ["e-7f3a19c2.v1"], "set-regression")
    assert kw["ctx"] is CTX and log.baselines == [("c1", "run-1")] and out["golden_run"]["id"] == "run-1"


async def test_baseline_refuses_proposed_classified_and_running_cases(monkeypatch):
    cases = {"p": _case_row("p", status="proposed", twin=None), "a": _case_row("a", status="active", kind="regression"),
             "b": _case_row("b", status="baselining"), "r": _case_row("r", status="retired"),
             "s": _case_row("s", status="superseded")}
    log = _stub_runs(monkeypatch, cases, run_open=True)
    for cid, reason in (("p", "not_confirmed"), ("a", "already_classified"), ("b", "baseline_running"),
                        ("r", "case_retired"), ("s", "case_retired")):
        with pytest.raises(WatchError) as e:
            await eval_cases.start_baseline(CTX, cid)
        assert e.value.status == 409 and e.value.reason == reason
    with pytest.raises(WatchError) as e:
        await eval_cases.start_baseline(CTX, "nope")
    assert (e.value.status, e.value.reason) == (404, "not_found")
    assert log.started == []


async def test_a_baseline_whose_run_has_ended_can_be_started_again(monkeypatch):
    log = _stub_runs(monkeypatch, {"b": _case_row("b", status="baselining")}, run_open=False)
    await eval_cases.start_baseline(CTX, "b")
    assert log.baselines == [("b", "run-1")]


async def test_write_intent_turn_not_replayable(monkeypatch):
    log = _stub_runs(monkeypatch, {})
    _kept_evidence(monkeypatch)
    turns = {"t-write": _turn_row("t-write", question="Raise a work order for the boiler in Plant Room 2"),
             "t-upload": _turn_row("t-upload", channel="upload"), "t-ok": _turn_row("t-ok")}

    async def turn(org, tid):
        return turns.get(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    for tid in ("t-write", "t-upload"):
        with pytest.raises(WatchError) as e:
            await eval_cases.repeat_turn(CTX, tid, n=10, mode_ids=[M1], expected_answer="x", actor_id=ACTOR,
                                         actor_email=None)
        assert e.value.status == 409 and e.value.reason == "turn_not_replayable"
    with pytest.raises(WatchError) as e:
        await eval_cases.repeat_turn(CTX, "t-ok", n=10, mode_ids=["not-in-use"], expected_answer="x", actor_id=ACTOR,
                                     actor_email=None)
    assert e.value.reason == "judge_not_frozen"
    with pytest.raises(WatchError) as e:
        await eval_cases.repeat_turn(CTX, "t-ok", n=10, mode_ids=[], expected_answer="x", actor_id=ACTOR, actor_email=None)
    assert e.value.reason == "judge_not_frozen"
    with pytest.raises(WatchError) as e:
        await eval_cases.repeat_turn(CTX, "t-none", n=10, mode_ids=[M1], expected_answer="x", actor_id=ACTOR,
                                     actor_email=None)
    assert (e.value.status, e.value.reason) == (404, "turn_not_in_scope")
    assert log.started == []


async def test_repeat_turn_creates_a_case_with_a_twin_then_repeats_it(monkeypatch):
    cases = {}
    log = _stub_runs(monkeypatch, cases)
    kept = _kept_evidence(monkeypatch)

    async def turn(org, tid):
        return _turn_row(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def no_case(org, tid):
        return None

    async def unconfirmed(org, tid, mode):
        return False

    async def load(ids):
        return {t: SimpleNamespace(question="How many work orders were raised at Bishopsgate Tower in July 2026?",
                                   prior=[], page_context=None) for t in ids}

    async def insert(case, twin, **kw):
        assert twin["expected_answer"] == "There were 786."
        cases[case["id"]] = dict(case, golden_case_id="g-new")
        return case

    for name, fn in {"_turn_for_case": turn, "judges_in_use": in_use, "_live_case_for_turn": no_case,
                     "_failure_confirmed": unconfirmed, "_insert_case": insert}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    out = await eval_cases.repeat_turn(CTX, "turn-9", n=10, mode_ids=[M1], expected_answer="There were 786.",
                                       actor_id=ACTOR, actor_email="reviewer@example.com")
    (case,) = cases.values()
    assert case["status"] == "proposed" and case["expected"]["judges"] == [{"mode_id": M1}]
    assert log.started[0]["k"] == 10 and log.started[0]["purpose"] == "repeat"
    assert log.case_runs == [{"case_id": case["id"], "run_id": "run-1", "purpose": "repeat", "k": 10}]
    assert out["golden_run"]["id"] == "run-1" and out["case"]["case_key"] == case["case_key"]
    assert kept == [(["turn-9"], ORG_A, "proposed")]
    with pytest.raises(WatchError) as e:
        await eval_cases.start_repeat(CTX, case["id"], n=21)
    assert e.value.status == 422


async def test_repeat_turn_of_a_confirmed_failure_makes_a_draft(monkeypatch):
    cases = {}
    _stub_runs(monkeypatch, cases)
    _kept_evidence(monkeypatch)

    async def turn(org, tid):
        return _turn_row(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def none(org, tid):
        return None

    async def confirmed(org, tid, mode):
        return True

    async def load(ids):
        return {}

    async def insert(case, twin, **kw):
        cases[case["id"]] = dict(case, golden_case_id="g-new")
        return case

    for name, fn in {"_turn_for_case": turn, "judges_in_use": in_use, "_live_case_for_turn": none,
                     "_failure_confirmed": confirmed, "_insert_case": insert}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    await eval_cases.repeat_turn(CTX, "turn-9", n=3, mode_ids=[M1, M1], expected_answer="x", actor_id=ACTOR,
                                 actor_email="r@x.test")
    (case,) = cases.values()
    assert case["status"] == "draft" and case["expected"]["judges"] == [{"mode_id": M1}]    # a mode named twice counts once


async def test_repeat_turn_reuses_the_live_case_and_gives_a_twinless_one_its_twin(monkeypatch):
    cases = {"c1": _case_row("c1", status="proposed", twin=None), "c2": _case_row("c2", key="e-00000002", twin="g-2")}
    log = _stub_runs(monkeypatch, cases)
    twins, live = [], {"value": "c1"}

    async def turn(org, tid):
        return _turn_row(tid)

    async def in_use(mode_ids=None):
        return [{"mode_id": M1}]

    async def live_case(org, tid):
        return dict(cases[live["value"]])

    async def with_twin(twin, **kw):
        twins.append((twin, kw))
        cases["c1"]["golden_case_id"] = "g-1"
        return "g-1"

    async def insert(*a, **kw):
        raise AssertionError("a live case is reused, never made again")

    monkeypatch.setattr(eval_cases, "_turn_for_case", turn)
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    monkeypatch.setattr(eval_cases, "_live_case_for_turn", live_case)
    monkeypatch.setattr(eval_cases, "_with_twin", with_twin)
    monkeypatch.setattr(eval_cases, "_insert_case", insert)
    with pytest.raises(WatchError) as e:                              # no twin yet needs a written answer
        await eval_cases.repeat_turn(CTX, "turn-1", n=4, mode_ids=[M1], actor_id=ACTOR, actor_email="r@x.test")
    assert e.value.reason == "expected_answer_required" and twins == []
    await eval_cases.repeat_turn(CTX, "turn-1", n=4, mode_ids=[M1], expected_answer="There were 786.", actor_id=ACTOR,
                                 actor_email="r@x.test")
    twin, kw = twins[0]
    assert twin["case_key"] == "e-7f3a19c2.v1" and twin["expected_answer"] == "There were 786."
    assert "golden_case_id IS NULL" in kw["update_sql"] and log.started[0]["case_keys"] == ["e-7f3a19c2.v1"]
    live["value"] = "c2"                                              # a case with a twin repeats without a new one
    await eval_cases.repeat_turn(CTX, "turn-2", n=4, mode_ids=[M1], actor_id=ACTOR, actor_email=None)
    assert len(twins) == 1 and log.started[1]["case_keys"] == ["e-00000002.v1"]


async def test_repeat_of_a_retired_or_unknown_case_is_refused(monkeypatch):
    log = _stub_runs(monkeypatch, {"r": _case_row("r", status="retired"), "p": _case_row("p", status="proposed", twin=None)})
    for cid, status, reason in (("nope", 404, "not_found"), ("r", 409, "case_retired"),
                                ("p", 409, "expected_answer_required")):
        with pytest.raises(WatchError) as e:
            await eval_cases.start_repeat(CTX, cid, n=5)
        assert (e.value.status, e.value.reason) == (status, reason)
    for n in (0, 1, 21):
        with pytest.raises(WatchError) as e:
            await eval_cases.start_repeat(CTX, "r", n=n)
        assert (e.value.status, e.value.reason) == (422, "bad_n")
    assert log.started == []


async def test_release_check_runs_every_active_case_once_with_k(monkeypatch):
    cases = {"r1": _case_row("r1", status="active", kind="regression", key="e-00000001"),
             "r2": _case_row("r2", status="active", kind="regression", key="e-00000002"),
             "c1": _case_row("c1", status="active", kind="capability", key="e-00000003", rate=0.4),
             "d1": _case_row("d1", status="draft", key="e-00000004")}
    log = _stub_runs(monkeypatch, cases)
    out = await eval_cases.start_release_check(CTX, k=5, include_capability=True)
    kw = log.started[0]
    assert kw["purpose"] == "release_check" and kw["k"] == 5
    assert kw["case_keys"] == ["e-00000001.v1", "e-00000002.v1", "e-00000003.v1"]
    assert [r["case_id"] for r in log.case_runs] == ["r1", "r2", "c1"] and out["cases"] == {"regression": 2, "capability": 1}
    await eval_cases.start_release_check(CTX, k=3, include_capability=False)
    assert log.started[1]["case_keys"] == ["e-00000001.v1", "e-00000002.v1"]
    for bad in (11, 0):
        with pytest.raises(WatchError) as e:
            await eval_cases.start_release_check(CTX, k=bad)
        assert (e.value.status, e.value.reason) == (422, "bad_k")
    for c in cases.values():
        c["status"] = "draft"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5)
    assert e.value.status == 409 and e.value.reason == "no_active_cases"


async def test_release_check_needs_every_twin_in_one_set(monkeypatch):
    cases = {"r1": _case_row("r1", status="active", kind="regression", key="e-00000001"),
             "r2": _case_row("r2", status="active", kind="regression", key="e-00000002")}
    log = _stub_runs(monkeypatch, cases)

    async def twin_ref(c):
        return ("set-a" if c["case_key"] == "e-00000001" else "set-b"), eval_cases.twin_key(c["case_key"], 1)

    monkeypatch.setattr(eval_cases, "_twin_ref", twin_ref)
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5)
    assert (e.value.status, e.value.reason) == (409, "cases_in_several_sets") and log.started == []


async def test_a_release_check_of_only_capability_cases_is_not_run_when_they_are_left_out(monkeypatch):
    log = _stub_runs(monkeypatch, {"c1": _case_row("c1", status="active", kind="capability", rate=0.4)})
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5, include_capability=False)
    assert e.value.reason == "no_active_cases" and log.started == []


def test_the_limits_per_purpose_sit_inside_the_golden_models_and_its_cap():
    from pydantic import ValidationError

    from trace_lab.routes_golden import GoldenRunStart

    assert GoldenRunStart(purpose="repeat", k=eval_cases.REPEAT_MAX_N).k == 20    # the golden model already takes 1-20
    with pytest.raises(ValidationError):
        GoldenRunStart(purpose="repeat", k=21)
    assert (eval_cases.BASELINE_K, eval_cases.RELEASE_MAX_K, eval_cases.REPEAT_MAX_N) == (5, 10, eval_cases.runner.HARD_MAX_K)


async def test_reps_above_the_deployment_cap_are_refused_before_any_run_starts(monkeypatch):
    cases = {"c1": _case_row("c1"), "r1": _case_row("r1", status="active", kind="regression", key="e-00000001")}
    log = _stub_runs(monkeypatch, cases)
    monkeypatch.setattr(eval_cases.runner, "max_k", lambda: 3)            # TRACE_LAB_GOLDEN_MAX_K=3 on this deployment
    with pytest.raises(WatchError) as e:
        await eval_cases.start_baseline(CTX, "c1")                        # a baseline is 5 runs
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_repeat(CTX, "c1", n=10)
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    with pytest.raises(WatchError) as e:
        await eval_cases.start_release_check(CTX, k=5)
    assert e.value.status == 422 and e.value.reason == "too_many_reps"
    with pytest.raises(WatchError) as e:                                  # one above the cap is already too many
        await eval_cases.start_release_check(CTX, k=4)
    assert e.value.reason == "too_many_reps" and log.started == []
    out = await eval_cases.start_release_check(CTX, k=3)                  # inside the cap
    assert log.started[0]["k"] == 3 and out["cases"]["regression"] == 1


# ---- grading reps: on read (free) and by "Grade now" (a capped job on the golden runner's grading worker) -------

from decimal import Decimal  # noqa: E402


def _grading_setup(monkeypatch, *, purpose="release_check", k=1, finals=None, expected=None, kind="regression",
                   case_status="active", in_use_rows=None, reps=None, answer="There were 786 work orders."):
    run = {"id": "run-1", "label": "run-20261015T101500Z-0000000a", "purpose": purpose, "k": k, "job_id": "job-run",
           "job_status": "running", "usd_spent": Decimal("0.5"), "started_at": None, "finished_at": None,
           "fingerprint": {}, "organization_id": ORG_A}
    cr = {"id": "cr-1", "eval_case_id": "c1", "golden_run_id": "run-1", "purpose": purpose, "k": k, "reps": reps or [],
          "organization_id": ORG_A, "case_key": "e-7f3a19c2", "version": 1, "case_status": case_status, "kind": kind,
          "baseline_pass_rate": None, "baseline_run_id": "run-1", "mode_id": M1, "mode_name": "wrong_period",
          "twin_key": "e-7f3a19c2.v1",
          "expected": expected or {"assertions": [], "checks": [], "judges": [{"mode_id": M1}], "golden_expected": False}}
    s = SimpleNamespace(saved=[], classified=[], asked=[], reserved=[], preds=[], loaded=[], reserved_usd=[], released=[])
    finals = finals if finals is not None else {
        ("e-7f3a19c2.v1", 1): {"turn_id": "turn-g1", "outcome": "pass", "graded_at": None}}

    async def golden_run(org, rid):
        return dict(run)

    async def case_runs(rid):
        return [dict(cr)]

    async def get_finals(rid):
        return finals

    async def in_use(mode_ids=None):
        return in_use_rows if in_use_rows is not None else [
            {"mode_id": M1, "mode_name": "wrong_period", "judge_id": "j1", "judge_key": "wrong_period-v2",
             "kind": "llm", "model": "claude-sonnet-5", "prompt_text": "prompt", "prompt_hash": "abc123abc123",
             "check_key": None, "check_version": None}]

    async def load(ids):                                              # the judge store's loader: {turn_id: NormalizedTurn}
        s.loaded.extend(ids)
        return {t: SimpleNamespace(turn_id=t, question="q", tool_calls=3, answer=answer) for t in ids}

    async def no_predictions(ph, ids):
        return {}

    async def save_run(case_run_id, reps_, summary, graders, repeat):
        s.saved.append({"reps": reps_, **summary, "graders": graders, "repeat": repeat})

    async def classify(case_id, cls, rid):
        s.classified.append((case_id, cls, rid))

    async def ask(model, prompt, judge_text):
        s.asked.append(judge_text)
        return SimpleNamespace(status="decided", failure_present=1, critique="Quotes June.", usd=0.02, error=None)

    async def reserve(job_id, usd):
        s.reserved.append(job_id)
        s.reserved_usd.append(usd)

    async def save(job_id, *, verdicts, prediction=None, usd=None, released=Decimal(0), done_delta=0, progress=None):
        s.preds.append((job_id, prediction, usd))
        s.released.append(released)

    for name, fn in {"_golden_run": golden_run, "_case_runs": case_runs, "_finals": get_finals,
                     "judges_in_use": in_use, "_save_case_run": save_run, "_classify": classify}.items():
        monkeypatch.setattr(eval_cases, name, fn)
    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", load)
    monkeypatch.setattr(eval_cases.monitor, "_predictions", no_predictions)
    monkeypatch.setattr(eval_cases.monitor, "_save", save)
    monkeypatch.setattr(eval_cases.judges, "ASK", ask)
    monkeypatch.setattr(eval_cases.judges, "judge_input",
                        lambda t: SimpleNamespace(text="evidence", chars=100, truncated=False, sha="sha"))
    monkeypatch.setattr(eval_cases.jobs, "reserve", reserve)
    monkeypatch.setattr(eval_cases.monitor, "call_usd", lambda model, prompt, chars: Decimal("0.03"))
    return s


async def test_reading_an_eval_run_never_calls_a_judge(monkeypatch):
    s = _grading_setup(monkeypatch)
    out = await eval_cases.eval_run(ORG_A, "run-1")
    assert s.asked == [] and s.reserved == [] and s.preds == []
    assert s.saved[0]["reps"][0]["status"] == "ungraded"
    assert s.saved[0]["decision"] == "incomplete" and s.saved[0]["reason"].startswith("1 ungraded")
    assert out["case_runs"][0]["reps"][0]["status"] == "ungraded"


async def test_grading_calls_the_judge_and_charges_the_job_it_runs_under(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    assert s.asked == ["evidence"] and s.reserved == ["job-grade"]
    job_id, prediction, usd = s.preds[0]
    assert job_id == "job-grade" and prediction["judge"]["prompt_hash"] == "abc123abc123"
    assert prediction["organization_id"] == ORG_A and prediction["turn_id"] == "turn-g1"     # judges.write_prediction's arguments
    assert prediction["pred"]["failure_present"] == 1 and usd == Decimal("0.02")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "failed" and rep["failures"] == ["judge:wrong_period"] and s.saved[0]["decision"] == "block"
    assert s.saved[0]["graders"]["judges"] == [{"mode_id": M1, "judge_id": "j1", "judge_key": "wrong_period-v2",
                                                "prompt_hash": "abc123abc123"}]


async def test_a_judge_call_is_reserved_at_its_estimate_and_the_reservation_is_given_back_with_its_result(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    assert s.reserved_usd == [Decimal("0.03")] and s.released == [Decimal("0.03")]


async def test_a_read_never_calls_a_judge_even_when_a_job_is_named(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=False, job_id="job-grade")
    assert s.asked == [] and s.reserved == [] and s.preds == [] and s.saved[0]["reps"][0]["status"] == "ungraded"


async def test_grading_without_a_job_charges_the_golden_runs_own_job(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True)
    assert s.reserved == ["job-run"] and s.asked == ["evidence"]


async def test_a_judge_call_the_job_cannot_pay_for_leaves_the_rep_ungraded(monkeypatch):
    for exc in (eval_cases.jobs.CapReached("job-grade"), eval_cases.jobs.JobNotRunning("job-grade")):
        s = _grading_setup(monkeypatch)

        async def refuse(job_id, usd, exc=exc):
            raise exc

        monkeypatch.setattr(eval_cases.jobs, "reserve", refuse)
        await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
        assert s.asked == [] and s.preds == [] and s.saved[0]["reps"][0]["status"] == "ungraded"


async def test_a_judge_that_raises_is_an_error_never_a_pass(monkeypatch):
    s = _grading_setup(monkeypatch)

    async def boom(model, prompt, judge_text):
        raise TimeoutError("the model did not answer")

    monkeypatch.setattr(eval_cases.judges, "ASK", boom)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "degraded" and rep["degraded_reason"] == "the judge for wrong_period returned no verdict"
    _, prediction, _ = s.preds[0]
    assert prediction["pred"]["status"] == "error" and prediction["pred"]["error"] == "TimeoutError"


async def test_a_cached_prediction_decides_for_free_even_when_reading(monkeypatch):
    s = _grading_setup(monkeypatch)

    async def cached(ph, ids):
        assert ph == "abc123abc123"
        return {"turn-g1": {"status": "decided", "failure_present": 0}}

    monkeypatch.setattr(eval_cases.monitor, "_predictions", cached)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.asked == [] and s.reserved == [] and s.saved[0]["reps"][0]["status"] == "passed"
    assert s.saved[0]["decision"] == "pass"


CODE_JUDGE = {"mode_id": M1, "mode_name": "wrong_period", "judge_id": "j9", "judge_key": "no_answer-v0", "kind": "code",
              "model": None, "prompt_text": None, "prompt_hash": "code00000000", "check_key": "no_answer",
              "check_version": 1}


async def test_a_code_judge_runs_its_pinned_check_for_free(monkeypatch):
    s = _grading_setup(monkeypatch, in_use_rows=[CODE_JUDGE])
    calls = []

    def fake_check(judge, turn):
        calls.append((judge["check_key"], judge["check_version"], turn.turn_id))
        return {"status": "decided", "failure_present": 1}

    monkeypatch.setattr(eval_cases.judges, "code_prediction", fake_check)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    assert calls == [("no_answer", 1, "turn-g1")] and s.asked == [] and s.reserved == [] and s.preds == []
    assert s.saved[0]["reps"][0]["status"] == "failed" and s.saved[0]["reps"][0]["failures"] == ["judge:wrong_period"]


async def test_a_code_judge_whose_check_changed_since_it_was_registered_is_an_error_not_a_pass(monkeypatch):
    s = _grading_setup(monkeypatch, in_use_rows=[dict(CODE_JUDGE, check_version=99)])     # the real judges.code_prediction
    await eval_cases.eval_run(ORG_A, "run-1")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "degraded" and rep["judges"][0]["status"] == "error" and rep["judges"][0]["failure_present"] is None


async def test_baseline_refresh_classifies_the_case(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None}
              for r in range(1, 6)}
    s = _grading_setup(monkeypatch, purpose="baseline", k=5, finals=finals, kind="unclassified",
                       case_status="baselining",
                       expected={"assertions": [], "checks": [{"check": "must_contain", "text": "786"}], "judges": [],
                                 "golden_expected": True})
    monkeypatch.setattr(eval_cases.checks, "run_check", lambda check, turn, params: eval_cases.checks.CheckResult("pass"))
    await eval_cases.eval_run(ORG_A, "run-1")
    assert [r["status"] for r in s.saved[0]["reps"]] == ["passed"] * 5 and s.asked == []
    assert s.classified == [("c1", {"kind": "regression", "baseline_pass_rate": None}, "run-1")]


async def test_a_baseline_is_classified_only_for_the_case_that_is_baselining_on_this_run(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None} for r in range(1, 6)}
    expected = {"assertions": [], "checks": [], "judges": [], "golden_expected": True}
    s = _grading_setup(monkeypatch, purpose="baseline", k=5, finals=finals, kind="unclassified", case_status="active",
                       expected=expected)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["classification"] == {"kind": "regression", "baseline_pass_rate": None} and s.classified == []
    s = _grading_setup(monkeypatch, purpose="release_check", k=5, finals=finals, case_status="baselining", expected=expected)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.classified == []                                         # only a baseline run classifies


async def test_decided_reps_are_final_and_never_graded_again(monkeypatch):
    old = [{"rep": 1, "status": "failed", "failures": ["judge:wrong_period"], "turn_id": "turn-g1"},
           {"rep": 2, "status": "ungraded", "turn_id": "turn-g2"}]
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None} for r in (1, 2)}
    s = _grading_setup(monkeypatch, k=2, finals=finals, reps=old,
                       expected={"assertions": [], "checks": [], "judges": [], "golden_expected": True})
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.loaded == ["turn-g2"]                                    # rep 1 is kept as it was
    assert [r["status"] for r in s.saved[0]["reps"]] == ["failed", "passed"] and s.saved[0]["reps"][0] is old[0]


async def test_a_degraded_rep_is_not_loaded_even_when_it_has_a_turn(monkeypatch):
    finals = {("e-7f3a19c2.v1", 1): {"turn_id": "turn-g1", "outcome": "degraded", "graded_at": None}}
    s = _grading_setup(monkeypatch, finals=finals)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.loaded == [] and s.saved[0]["reps"][0]["status"] == "degraded"
    assert s.saved[0]["reps"][0]["turn_id"] == "turn-g1"


async def test_a_case_check_runs_with_its_own_parameters_on_the_answer(monkeypatch):
    expected = {"assertions": [], "checks": [{"check": "must_contain", "text": "786"}], "judges": [], "golden_expected": False}
    s = _grading_setup(monkeypatch, expected=expected, answer="There were 786 work orders.")
    await eval_cases.eval_run(ORG_A, "run-1")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "passed" and rep["checks"] == [{"key": "must_contain", "status": "pass", "findings": []}]
    assert s.saved[0]["graders"]["checks"] == ["code:must_contain@v1"]
    s = _grading_setup(monkeypatch, expected=expected, answer="There were 758 work orders.")
    await eval_cases.eval_run(ORG_A, "run-1")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "failed" and rep["failures"] == ["check:must_contain"]
    assert rep["checks"][0]["findings"] == ['Leaves out "786".']


async def test_a_repeat_run_stores_its_failure_summary(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None} for r in (1, 2)}
    expected = {"assertions": [], "checks": [{"check": "must_contain", "text": "786"}], "judges": [], "golden_expected": False}
    s = _grading_setup(monkeypatch, purpose="repeat", k=2, finals=finals, expected=expected, kind="unclassified")
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["repeat"]["n"] == 2 and s.saved[0]["repeat"]["failure_rate"] == 0.0
    s = _grading_setup(monkeypatch, purpose="release_check", k=2, finals=finals, expected=expected)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["repeat"] is None                                # only a repeat run measures a failure rate


async def test_a_rep_the_runner_could_not_answer_reads_without_loading_an_answer(monkeypatch):
    finals = {("e-7f3a19c2.v1", 1): {"turn_id": None, "outcome": "error", "graded_at": None},
              ("e-7f3a19c2.v1", 2): {"turn_id": None, "outcome": "degraded", "graded_at": None}}
    s = _grading_setup(monkeypatch, k=3, finals=finals)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.loaded == []
    reps = s.saved[0]["reps"]
    assert [r["status"] for r in reps] == ["degraded", "degraded", "missing"]
    assert reps[0]["degraded_reason"] == "the service failed three times in a row"
    assert s.saved[0]["decision"] == "incomplete"


async def test_an_answer_the_judge_store_cannot_read_is_a_degraded_rep(monkeypatch):
    s = _grading_setup(monkeypatch)

    async def nothing(ids):
        return {}

    monkeypatch.setattr(eval_cases.judges, "load_judge_turns", nothing)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["reps"][0]["status"] == "degraded" and s.saved[0]["reps"][0]["degraded_reason"] == "no answer was recorded"


async def test_the_judge_store_is_asked_only_for_the_modes_a_case_names(monkeypatch):
    s = _grading_setup(monkeypatch, expected={"assertions": [], "checks": [{"check": "must_contain", "text": "786"}],
                                              "judges": [], "golden_expected": True})
    asked = []

    async def in_use(mode_ids=None):
        asked.append(mode_ids)
        return []

    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert asked == [] and s.saved                                    # no case names a judge: nothing is read
    s = _grading_setup(monkeypatch)                                   # a case that names the judge of mode M1
    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)
    await eval_cases.eval_run(ORG_A, "run-1")
    assert asked == [[M1]]


async def test_a_judge_that_is_no_longer_in_use_makes_the_rep_degraded(monkeypatch):
    s = _grading_setup(monkeypatch, in_use_rows=[])
    await eval_cases.eval_run(ORG_A, "run-1")
    rep = s.saved[0]["reps"][0]
    assert rep["status"] == "degraded" and rep["judges"] == [
        {"mode": "wrong_period", "mode_id": M1, "status": "error", "failure_present": None}]
    assert s.saved[0]["graders"] == {"checks": [], "judges": []}


# ---- the graders a case run records are the ones that made its verdicts, not the ones in use at the time of a read --

JUDGE_V2 = {"mode_id": M1, "mode_name": "wrong_period", "judge_id": "j1", "judge_key": "wrong_period-v2", "kind": "llm",
            "model": "claude-sonnet-5", "prompt_text": "prompt", "prompt_hash": "abc123abc123", "check_key": None,
            "check_version": None}                                    # the judge _grading_setup puts in use by default
JUDGE_V3 = dict(JUDGE_V2, judge_id="j3", judge_key="wrong_period-v3", prompt_hash="new333new333")
RECORDED_V2 = {"mode_id": M1, "judge_id": "j1", "judge_key": "wrong_period-v2", "prompt_hash": "abc123abc123"}


def _judges_in_use_now(monkeypatch, rows):
    """The judge store changes under the module: another judge in use, or none."""
    async def in_use(mode_ids=None):
        return rows

    monkeypatch.setattr(eval_cases, "judges_in_use", in_use)


async def _next_reads_see_what_was_stored(monkeypatch, s):
    """The next read gets back the reps and graders the last read stored (through JSON, as the database returns them)."""
    base = (await eval_cases._case_runs("run-1"))[0]

    async def case_runs(rid):
        last = s.saved[-1]
        return [dict(base, reps=json.loads(json.dumps(last["reps"])), graders=json.loads(json.dumps(last["graders"])))]

    monkeypatch.setattr(eval_cases, "_case_runs", case_runs)


async def test_a_reread_after_the_judge_was_replaced_or_retired_keeps_the_graders_that_made_the_verdicts(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    first = s.saved[0]
    assert first["reps"][0]["status"] == "failed" and first["graders"]["judges"] == [RECORDED_V2]
    await _next_reads_see_what_was_stored(monkeypatch, s)
    _judges_in_use_now(monkeypatch, [JUDGE_V3])                       # the judge was replaced by a newer version
    out = await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[1]["graders"] == first["graders"] and s.saved[1]["reps"] == first["reps"]
    assert out["case_runs"][0]["graders"] == first["graders"]
    _judges_in_use_now(monkeypatch, [])                               # then retired altogether
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[2]["graders"] == first["graders"] and s.saved[2]["reps"] == first["reps"]


async def test_a_release_report_read_again_after_a_judge_change_keeps_judge_changed_since_baseline(monkeypatch):
    s = _grading_setup(monkeypatch)
    baseline = {"c1": {"judges": [{"mode_id": M1, "prompt_hash": "abc123abc123"}]}}      # graded by the very same judge

    async def baselines(case_ids):
        return baseline

    monkeypatch.setattr(eval_cases, "_baseline_graders", baselines)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")
    await _next_reads_see_what_was_stored(monkeypatch, s)
    report = await eval_cases.release_report(ORG_A, "run-1")
    assert report["regression"][0]["judge_changed_since_baseline"] is False
    assert report["graders"]["judges"] == [{"mode": "wrong_period", "judge_key": "wrong_period-v2", "prompt_hash": "abc123abc123"}]
    _judges_in_use_now(monkeypatch, [JUDGE_V3])                       # the judge is replaced, and the report is read again
    again = await eval_cases.release_report(ORG_A, "run-1")
    assert again["regression"][0]["judge_changed_since_baseline"] is False and again["graders"] == report["graders"]
    _judges_in_use_now(monkeypatch, [])                               # retired: the report still names the judge that decided
    retired = await eval_cases.release_report(ORG_A, "run-1")
    assert retired["regression"][0]["judge_changed_since_baseline"] is False and retired["graders"] == report["graders"]
    baseline["c1"] = {"judges": [{"mode_id": M1, "prompt_hash": "base00000000"}]}         # a baseline made by another judge
    assert (await eval_cases.release_report(ORG_A, "run-1"))["regression"][0]["judge_changed_since_baseline"] is True
    _judges_in_use_now(monkeypatch, [dict(JUDGE_V2, prompt_hash="base00000000")])         # the old judge is put back in use
    assert (await eval_cases.release_report(ORG_A, "run-1"))["regression"][0]["judge_changed_since_baseline"] is True


async def test_graders_are_built_from_the_reps_so_every_judge_that_decided_one_is_recorded(monkeypatch):
    old = [{"rep": 1, "status": "passed", "turn_id": "turn-g1", "checks": [],
            "judges": [{"mode": "wrong_period", "mode_id": M1, "judge_id": "j0", "judge_key": "wrong_period-v1",
                        "prompt_hash": "old000000000", "status": "decided", "failure_present": 0}]}]
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None} for r in (1, 2)}
    s = _grading_setup(monkeypatch, k=2, finals=finals, reps=old)
    await eval_cases.refresh_run(ORG_A, "run-1", allow_model_calls=True, job_id="job-grade")   # rep 2 is graded by v2 now
    assert [r["status"] for r in s.saved[0]["reps"]] == ["passed", "failed"] and s.saved[0]["reps"][0] is old[0]
    assert s.saved[0]["graders"]["judges"] == [
        RECORDED_V2, {"mode_id": M1, "judge_id": "j0", "judge_key": "wrong_period-v1", "prompt_hash": "old000000000"}]


async def test_a_judge_that_made_no_verdict_is_not_recorded_as_a_grader(monkeypatch):
    s = _grading_setup(monkeypatch)
    await eval_cases.eval_run(ORG_A, "run-1")                         # a read never asks the judge: the rep is ungraded
    assert s.saved[0]["reps"][0]["status"] == "ungraded" and s.saved[0]["graders"]["judges"] == []
    s = _grading_setup(monkeypatch, in_use_rows=[dict(CODE_JUDGE, check_version=99)])       # a judge that could not decide
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["reps"][0]["status"] == "degraded" and s.saved[0]["graders"]["judges"] == []


async def test_a_code_judge_is_recorded_with_its_judge_id_on_the_rep_that_it_decided(monkeypatch):
    s = _grading_setup(monkeypatch, in_use_rows=[CODE_JUDGE])
    monkeypatch.setattr(eval_cases.judges, "code_prediction", lambda judge, turn: {"status": "decided", "failure_present": 0})
    await eval_cases.eval_run(ORG_A, "run-1")
    assert s.saved[0]["reps"][0]["judges"][0]["judge_id"] == "j9"
    assert s.saved[0]["graders"]["judges"] == [{"mode_id": M1, "judge_id": "j9", "judge_key": "no_answer-v0",
                                                "prompt_hash": "code00000000"}]


async def test_a_case_run_holding_two_versions_of_a_judge_is_compared_with_the_baseline_version_by_version(monkeypatch):
    both = [{"mode_id": M1, "judge_id": "j2", "judge_key": "wrong_period-v2", "prompt_hash": "bbbbbbbbbbbb"},
            {"mode_id": M1, "judge_id": "j1", "judge_key": "wrong_period-v1", "prompt_hash": "aaaaaaaaaaaa"}]
    _release_stubs(monkeypatch, [_cr("e-00000001", "regression", "pass", judges=both),
                                 _cr("e-00000002", "regression", "pass", judges=both[::-1])])
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert [r["judge_changed_since_baseline"] for r in report["regression"]] == [True, True]
    #   case 1 baseline: version aaaa only -> a verdict by bbbb is a change, listed first or last
    #   case 2 baseline: version bbbb only -> a verdict by aaaa is a change, listed first or last
    only_baseline = _cr("e-00000001", "regression", "pass", judges=[both[1]])         # decided by the baseline's version only
    _release_stubs(monkeypatch, [only_baseline])
    assert (await eval_cases.release_report(ORG_A, "run-x"))["regression"][0]["judge_changed_since_baseline"] is False


async def test_an_unknown_run_is_not_found_and_a_run_with_no_eval_cases_is_empty(monkeypatch):
    async def none(org, rid):
        return None

    monkeypatch.setattr(eval_cases, "_golden_run", none)
    with pytest.raises(WatchError) as e:
        await eval_cases.eval_run(ORG_A, "run-x")
    assert (e.value.status, e.value.reason) == (404, "not_found")
    _grading_setup(monkeypatch)

    async def no_case_runs(rid):
        return []

    monkeypatch.setattr(eval_cases, "_case_runs", no_case_runs)
    out = await eval_cases.eval_run(ORG_A, "run-1")
    assert out["case_runs"] == [] and out["run"]["id"] == "run-1" and out["run"]["status"] == "running"


async def test_a_run_reads_as_public_json(monkeypatch):
    _grading_setup(monkeypatch)
    out = await eval_cases.eval_run(ORG_A, "run-1")
    assert out["run"]["usd_spent"] == 0.5 and out["run"]["purpose"] == "release_check"
    json.dumps(out)                                                   # nothing in it needs a custom encoder


async def test_grade_rep_refreshes_only_its_rep(monkeypatch):
    finals = {("e-7f3a19c2.v1", r): {"turn_id": f"turn-g{r}", "outcome": "pass", "graded_at": None} for r in (1, 2)}
    s = _grading_setup(monkeypatch, k=2, finals=finals,
                       expected={"assertions": [], "checks": [], "judges": [], "golden_expected": True})

    async def result_row(result_id):
        return {"run_id": "run-1", "organization_id": ORG_A, "case_key": "e-7f3a19c2.v1", "rep": 2}

    monkeypatch.setattr(eval_cases, "_result_row", result_row)
    await eval_cases.grade_rep("11111111-1111-4111-8111-000000000002")
    assert [r["status"] for r in s.saved[0]["reps"]] == ["missing", "passed"]


async def test_grade_rep_never_raises(monkeypatch):
    assert await eval_cases.grade_rep("not-a-uuid") is None

    async def none(result_id):
        return None

    async def boom(result_id):
        raise RuntimeError("database down")

    monkeypatch.setattr(eval_cases, "_result_row", none)
    assert await eval_cases.grade_rep("11111111-1111-4111-8111-000000000002") is None
    monkeypatch.setattr(eval_cases, "_result_row", boom)
    assert await eval_cases.grade_rep("11111111-1111-4111-8111-000000000002") is None


async def test_grade_now_is_a_golden_grade_job_handled_by_the_eval_cases_handler(monkeypatch):
    created, started = {}, []

    async def golden_run(org, rid):
        return {"id": rid}

    async def create(**kw):
        created.update(kw)
        return {"id": "job-grade"}

    async def start(job_id):
        started.append(job_id)
        return True

    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases.jobs, "create", create)
    monkeypatch.setattr(eval_cases.runner, "start_grade_job", start)
    out = await eval_cases.start_grading(ORG_A, "run-1", usd_cap=None, actor_id=ACTOR,
                                         actor_email="reviewer@example.com")
    assert created["kind"] == "golden_grade" and created["scope_key"] == "golden_grade:eval:run-1"
    assert created["organization_id"] == ORG_A and created["usd_cap"] == Decimal(str(eval_cases.eval_grading_usd_cap()))
    assert created["params"] == {"handler": "eval_cases", "golden_run_id": "run-1", "organization_id": ORG_A}
    assert created["actor_id"] == ACTOR and created["actor_email"] == "reviewer@example.com"
    assert started == ["job-grade"] and out["job"] == {"id": "job-grade", "status": "running", "usd_cap": 2.0}
    assert eval_cases.runner.GRADE_HANDLERS["eval_cases"] is eval_cases.grade_job   # run by the golden runner's own worker


async def test_grade_now_says_queued_when_the_worker_did_not_take_the_job(monkeypatch):
    async def golden_run(org, rid):
        return {"id": rid}

    async def create(**kw):
        return {"id": "job-grade"}

    async def not_taken(job_id):
        return False

    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases.jobs, "create", create)
    monkeypatch.setattr(eval_cases.runner, "start_grade_job", not_taken)
    out = await eval_cases.start_grading(ORG_A, "run-1", usd_cap=None, actor_id=ACTOR, actor_email=None)
    assert out["job"]["status"] == "queued"


async def test_grade_now_takes_the_callers_cap_and_refuses_what_it_cannot_start(monkeypatch):
    created = {}

    async def golden_run(org, rid):
        return {"id": rid} if rid == "run-1" else None

    async def create(**kw):
        created.update(kw)
        if kw["scope_key"].endswith("busy"):
            raise eval_cases.jobs.JobConflict("scope")
        return {"id": "job-grade"}

    async def start(job_id):
        return True

    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases.jobs, "create", create)
    monkeypatch.setattr(eval_cases.runner, "start_grade_job", start)
    out = await eval_cases.start_grading(ORG_A, "run-1", usd_cap=0.75, actor_id=ACTOR, actor_email=None)
    assert created["usd_cap"] == Decimal("0.75") and out["job"]["usd_cap"] == 0.75
    with pytest.raises(WatchError) as e:
        await eval_cases.start_grading(ORG_A, "run-x", usd_cap=None, actor_id=ACTOR, actor_email=None)
    assert (e.value.status, e.value.reason) == (404, "not_found")

    async def golden_run_busy(org, rid):
        return {"id": rid}

    monkeypatch.setattr(eval_cases, "_golden_run", golden_run_busy)
    with pytest.raises(WatchError) as e:
        await eval_cases.start_grading(ORG_A, "busy", usd_cap=None, actor_id=ACTOR, actor_email=None)
    assert (e.value.status, e.value.reason) == (409, "grading_running")


async def test_the_grade_handler_returns_capped_while_reps_stay_ungraded_and_done_after(monkeypatch):
    seen = {}

    async def refresh(org, rid, *, allow_model_calls, job_id=None, only=None):
        seen.update(org=org, rid=rid, allow=allow_model_calls, job=job_id)
        return {"run": {}, "case_runs": [{"reps": [{"status": "ungraded"}, {"status": "passed"}]}]}

    async def clean(org, rid, **kw):
        return {"run": {}, "case_runs": [{"reps": [{"status": "passed"}]}]}

    monkeypatch.setattr(eval_cases, "refresh_run", refresh)
    params = {"handler": "eval_cases", "golden_run_id": "run-1", "organization_id": ORG_A}
    job = eval_cases.runner.GradeJob(job_id="job-grade", job={"params": params})            # the runner's own handler argument
    assert await eval_cases.grade_job(job) == "capped"
    assert seen == {"org": ORG_A, "rid": "run-1", "allow": True, "job": "job-grade"}   # charged to the job it runs under
    monkeypatch.setattr(eval_cases, "refresh_run", clean)
    job.job["params"] = json.dumps(params)                                              # a stored job may carry params as text
    assert await eval_cases.grade_job(job) == "done"


def test_the_handler_is_registered_when_the_module_is_imported():
    assert eval_cases.runner.GRADE_HANDLERS.get("eval_cases") is eval_cases.grade_job


# ---- the release check: list and downloadable report ----------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402


def _cr(key, kind, decision, *, passes=5, n=5, degraded=0, pending=0, missing=0, judges=None, reps=None, rate=None):
    return {"eval_case_id": f"id-{key}", "case_key": key, "version": 1, "kind": kind, "mode": "wrong_period",
            "purpose": "release_check", "baseline_pass_rate": rate, "passes": passes, "n": n, "degraded": degraded,
            "pending": pending, "missing": missing, "decision": decision, "reason": f"{kind} {decision}",
            "pass_at_1": passes / 5 if n else None, "pass_at_3": None, "pass_at_5": None, "pass_hat_k": None,
            "graders": {"checks": ["code:invented_identifiers@v1"],
                        "judges": judges or [{"mode_id": M1, "judge_id": "j2", "judge_key": "wrong_period-v2",
                                              "prompt_hash": "bbbbbbbbbbbb"}]},
            "reps": reps or [{"rep": i, "status": "passed", "turn_id": f"turn-{key}-{i}"} for i in range(1, 6)]}


def _release_stubs(monkeypatch, case_runs, *, previous=None, purpose="release_check", started=True):
    refreshed = []

    async def refresh(org, rid, *, allow_model_calls, job_id=None, only=None):
        assert allow_model_calls is False
        refreshed.append(rid)
        return {"run": {"id": rid, "label": "run-20261015T101500Z-0000000a", "purpose": purpose, "k": 5,
                        "status": "done", "started_at": "2026-10-15T10:15:00+00:00", "finished_at": None, "usd_spent": 4.8},
                "case_runs": case_runs}

    async def golden_run(org, rid):
        return {"purpose": purpose, "fingerprint": {"image": "f5-1a2b3c4", "openai_model": "gpt-5.6-terra"},
                "started_at": datetime(2026, 10, 15, 10, 15, tzinfo=timezone.utc) if started else None}

    async def baselines(case_ids):
        return {"id-e-00000001": {"judges": [{"mode_id": M1, "prompt_hash": "aaaaaaaaaaaa"}]},
                "id-e-00000002": {"judges": [{"mode_id": M1, "prompt_hash": "bbbbbbbbbbbb"}]}}

    async def previous_release(org, rid, started_at):
        assert (started_at is not None) == started
        return previous

    monkeypatch.setattr(eval_cases, "refresh_run", refresh)
    monkeypatch.setattr(eval_cases, "_golden_run", golden_run)
    monkeypatch.setattr(eval_cases, "_baseline_graders", baselines)
    monkeypatch.setattr(eval_cases, "_previous_release", previous_release)
    return refreshed


async def test_release_report_counts_regression_only_and_lists_gaps_and_changes(monkeypatch):
    regression_ok = _cr("e-00000001", "regression", "pass")
    regression_gap = _cr("e-00000002", "regression", "incomplete", passes=4, n=4, degraded=1,
                         reps=[{"rep": 1, "status": "passed", "turn_id": "t"}] * 4 +
                              [{"rep": 5, "status": "degraded", "degraded_reason": "a step was refused or needed approval"}])
    capability = _cr("e-00000003", "capability", "pass", passes=2, n=5, rate=0.4,
                     reps=[{"rep": i, "status": "passed" if i <= 2 else "failed"} for i in range(1, 6)])
    _release_stubs(monkeypatch, [regression_ok, regression_gap, capability],
                   previous={"e-00000001": "pass", "e-00000002": "pass"})
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert report["decision"] == "incomplete"                          # the capability case never decides
    assert [r["case_key"] for r in report["regression"]] == ["e-00000001", "e-00000002"]
    assert [c["case_key"] for c in report["capability"]] == ["e-00000003"]
    assert report["regression"][0]["judge_changed_since_baseline"] is True
    assert report["regression"][1]["judge_changed_since_baseline"] is False
    assert report["regression"][1]["not_decided"] == 1
    assert report["runs_without_verdict"][0] == {"case_key": "e-00000002", "rep": 5, "status": "degraded",
                                                 "reason": "a step was refused or needed approval"}
    assert report["changes_since_last"] == [{"case_key": "e-00000002", "before": "pass", "after": "incomplete"}]
    assert report["fingerprint"]["image"] == "f5-1a2b3c4" and report["usd"] == 4.8
    assert report["graders"]["checks"] == ["code:invented_identifiers@v1"]
    assert report["capability"][0]["pass_hat_5"] == 0.0
    assert report["started_at"] == "2026-10-15T10:15:00+00:00" and report["run_key"] == "run-20261015T101500Z-0000000a"
    assert report["graders"]["judges"] == [{"mode": "wrong_period", "judge_key": "wrong_period-v2", "prompt_hash": "bbbbbbbbbbbb"}]
    json.dumps(report)


async def test_a_capability_case_with_no_verdict_never_holds_a_release_back(monkeypatch):
    waiting = _cr("e-00000003", "capability", "incomplete", passes=0, n=0, missing=5, rate=0.4,
                  reps=[{"rep": i, "status": "missing"} for i in range(1, 6)])
    _release_stubs(monkeypatch, [_cr("e-00000001", "regression", "pass"), waiting])
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert report["decision"] == "pass" and len(report["runs_without_verdict"]) == 5     # listed, yet not deciding


async def test_a_release_report_with_no_earlier_check_has_no_change_list(monkeypatch):
    _release_stubs(monkeypatch, [_cr("e-00000001", "regression", "pass")], previous=None)
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert report["decision"] == "pass" and report["reason"] == "all 1 regression cases passed 5 of 5 runs"
    assert report["changes_since_last"] is None and report["runs_without_verdict"] == []
    _release_stubs(monkeypatch, [_cr("e-00000001", "regression", "pass")], previous=None, started=False)
    assert (await eval_cases.release_report(ORG_A, "run-x"))["changes_since_last"] is None


async def test_a_release_report_names_only_a_release_check_and_touches_nothing_else(monkeypatch):
    refreshed = _release_stubs(monkeypatch, [_cr("e-00000001", "regression", "pass")], purpose="baseline")
    with pytest.raises(WatchError) as e:
        await eval_cases.release_report(ORG_A, "run-x")
    assert (e.value.status, e.value.reason) == (404, "not_found")
    assert refreshed == []                                             # a run that is no release check is not even re-graded

    async def none(org, rid):
        return None

    monkeypatch.setattr(eval_cases, "_golden_run", none)               # another company's run, or an unknown one
    with pytest.raises(WatchError) as e:
        await eval_cases.release_report(ORG_A, "run-y")
    assert (e.value.status, e.value.reason) == (404, "not_found") and refreshed == []


async def test_a_release_report_with_a_running_run_lists_the_reps_still_missing(monkeypatch):
    waiting = _cr("e-00000001", "regression", "incomplete", passes=1, n=1, missing=4,
                  reps=[{"rep": 1, "status": "passed", "turn_id": "t"}] + [{"rep": i, "status": "missing"} for i in range(2, 6)])
    _release_stubs(monkeypatch, [waiting])
    report = await eval_cases.release_report(ORG_A, "run-x")
    assert report["decision"] == "incomplete" and report["regression"][0]["not_decided"] == 4
    assert [(g["rep"], g["status"], g["reason"]) for g in report["runs_without_verdict"]] == [(i, "missing", None) for i in range(2, 6)]


async def test_release_list_reads_the_stored_decisions(monkeypatch):
    async def runs(org, limit):
        return [{"id": "r2", "label": "run-b", "k": 5, "started_at": None, "finished_at": None, "job_status": "done"},
                {"id": "r1", "label": "run-a", "k": 5, "started_at": None, "finished_at": None, "job_status": "done"}]

    async def stored(run_ids):
        return [{"golden_run_id": "r2", "decision": "block", "kind": "regression", "case_key": "e-1"},
                {"golden_run_id": "r1", "decision": "pass", "kind": "regression", "case_key": "e-1"},
                {"golden_run_id": "r1", "decision": "pass", "kind": "capability", "case_key": "e-2"}]

    monkeypatch.setattr(eval_cases, "_release_runs", runs)
    monkeypatch.setattr(eval_cases, "_stored_case_runs", stored)
    out = await eval_cases.list_release_checks(ORG_A)
    assert [(c["golden_run_id"], c["decision"]) for c in out] == [("r2", "block"), ("r1", "pass")]
    assert out[1]["run_key"] == "run-a" and out[1]["reason"] == "all 1 regression cases passed 5 of 5 runs"
    assert out[0]["status"] == "done" and out[0]["k"] == 5


async def test_release_list_asks_for_a_sane_number_of_runs(monkeypatch):
    asked = []

    async def runs(org, limit):
        asked.append(limit)
        return []

    async def stored(run_ids):
        return []

    monkeypatch.setattr(eval_cases, "_release_runs", runs)
    monkeypatch.setattr(eval_cases, "_stored_case_runs", stored)
    for limit in (-5, 0, 7, 10_000):
        assert await eval_cases.list_release_checks(ORG_A, limit=limit) == []
    assert asked == [1, 1, 7, 100]


async def test_release_list_of_a_run_with_no_stored_case_runs_is_incomplete(monkeypatch):
    async def runs(org, limit):
        return [{"id": "r9", "label": "run-z", "k": 5, "started_at": None, "finished_at": None, "job_status": "running"}]

    async def stored(run_ids):
        return []

    monkeypatch.setattr(eval_cases, "_release_runs", runs)
    monkeypatch.setattr(eval_cases, "_stored_case_runs", stored)
    (only,) = await eval_cases.list_release_checks(ORG_A)
    assert only["decision"] == "incomplete" and only["status"] == "running"
