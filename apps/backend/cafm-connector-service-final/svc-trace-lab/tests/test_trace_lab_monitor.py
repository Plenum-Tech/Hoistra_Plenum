"""Monitoring (Phase 7): plan, estimate, worker, threshold action, resume. 17 Oct 2026.

No database and no model: the store functions, judges.ASK and the job helpers are replaced per test. The SQL
that reads the chat's answers is tested on a scratch Postgres in test_trace_lab_monitor_sql.py."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import monitor

UTC = timezone.utc
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)            # a Thursday; last complete week is 2026-W41
ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _ids(n):
    return [{"turn_id": f"t{i}"} for i in range(1, n + 1)]


def test_select_turns_hw7_vector():
    turns = _ids(10)
    before = copy.deepcopy(turns)
    groups = {"g1": lambda t: t["turn_id"] in ("t1", "t4"), "g2": lambda t: t["turn_id"] in ("t4", "t7")}
    plan = monitor.select_turns(turns, 0.2, groups, seed=7)
    assert plan["n_window"] == 10 and len(plan["random"]) == 2
    assert plan["risk_groups"] == {"g1": ["t1", "t4"], "g2": ["t4", "t7"]}
    assert len(plan["to_judge"]) == len(set(plan["to_judge"]))
    assert {"t1", "t4", "t7"} <= set(plan["to_judge"])
    assert plan["to_judge"][:2] == plan["random"]                     # uniform picks are judged first
    assert turns == before                                             # inputs never mutated


def test_same_seed_same_plan():
    groups = {"failed": lambda t: t["turn_id"] == "t3"}
    assert monitor.select_turns(_ids(40), 0.25, groups, seed=7) == monitor.select_turns(_ids(40), 0.25, groups, seed=7)


def test_a_different_seed_draws_a_different_uniform_sample():
    first = monitor.select_turns(_ids(60), 0.25, {}, seed=7)["random"]
    second = monitor.select_turns(_ids(60), 0.25, {}, seed=8)["random"]
    assert len(first) == len(second) == 15 and first != second


def test_rate_outside_0_1_raises():
    for rate in (0, -0.1, 1.5, float("nan")):
        with pytest.raises(ValueError):
            monitor.select_turns(_ids(4), rate, {}, seed=7)
    assert len(monitor.select_turns(_ids(4), 1.0, {}, seed=7)["random"]) == 4


def test_missing_turn_id_raises():
    with pytest.raises(ValueError):
        monitor.select_turns([{"turn_id": "t1"}, {"turn_id": ""}], 0.5, {}, seed=7)


def test_an_empty_window_is_an_empty_plan():
    plan = monitor.select_turns([], 0.25, {"failed": lambda t: True}, seed=7)
    assert plan == {"n_window": 0, "random": [], "risk_groups": {"failed": []}, "to_judge": []}


def test_window_never_exceeds_retention():
    monitor.check_window(NOW - timedelta(days=89), NOW - timedelta(days=80), now=NOW, days=90)
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=89, hours=1), NOW - timedelta(days=80), now=NOW, days=90)
    assert e.value.status == 409 and e.value.reason == "window_beyond_retention"
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=1), NOW + timedelta(hours=1), now=NOW, days=90)
    assert e.value.status == 422 and e.value.reason == "window_in_future"
    with pytest.raises(monitor.WatchError) as e:
        monitor.check_window(NOW - timedelta(days=1), NOW - timedelta(days=2), now=NOW, days=90)
    assert e.value.reason == "bad_window"


def test_retention_days_is_the_one_rule_sampling_uses(monkeypatch):
    from trace_lab import sampling

    monkeypatch.delenv("TRACE_RETENTION_DAYS", raising=False)
    assert monitor.retention_days() == sampling.retention_days() == 90
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "30")
    assert monitor.retention_days() == sampling.retention_days() == 30
    with pytest.raises(monitor.WatchError) as e:                       # the default days come from the same rule
        monitor.check_window(NOW - timedelta(days=30), NOW - timedelta(days=20), now=NOW)
    assert e.value.reason == "window_beyond_retention"
    monkeypatch.setenv("TRACE_RETENTION_DAYS", "not a number")
    assert monitor.retention_days() == 90


def test_the_batch_cost_cap_is_five_dollars_unless_the_environment_says_otherwise(monkeypatch):
    monkeypatch.delenv("TRACE_LAB_MONITOR_USD_CAP", raising=False)
    assert monitor.monitor_usd_cap() == 5.0
    monkeypatch.setenv("TRACE_LAB_MONITOR_USD_CAP", "2.5")
    assert monitor.monitor_usd_cap() == 2.5


def test_last_complete_week_period_label_and_weeks_back():
    start, end = monitor.last_complete_week(NOW)
    assert (start, end) == (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
    assert monitor.period_label(start, end) == "2026-W41"
    assert monitor.period_label(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC)) \
        == "2026-10-01..2026-10-14"
    assert monitor.period_label(datetime(2026, 10, 6, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC)) \
        == "2026-10-06..2026-10-12"                                     # seven days, but not a Monday-to-Monday week
    weeks = monitor.weeks_back(NOW, 3)
    assert [w[0] for w in weeks] == ["2026-W40", "2026-W41", "2026-W42"]
    assert weeks[-1][1] == datetime(2026, 10, 12, tzinfo=UTC)


def test_a_naive_time_is_read_as_utc_and_a_monday_is_its_own_week():
    monday = datetime(2026, 10, 12, 0, 0)                               # naive: UTC
    start, end = monitor.last_complete_week(monday)
    assert (start, end) == (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
    assert monitor.weeks_back(NOW, 0)[0][0] == "2026-W42"               # at least the current week


def test_public_makes_values_json_safe():
    import json
    import uuid
    from decimal import Decimal

    raw = {"id": uuid.UUID(ORG_A), "at": NOW, "d": Decimal("0.25"), "tags": ("a", "b"), "n": {"x": {1, 2}.pop()}}
    out = monitor.public(raw)
    assert out["id"] == ORG_A and out["at"] == NOW.isoformat() and out["d"] == 0.25 and out["tags"] == ["a", "b"]
    json.dumps(out)


def _t(tid, minute, q, *, user="u1", session="s1", kind="observed"):
    return {"turn_id": tid, "started_at": datetime(2026, 10, 6, 9, minute, tzinfo=UTC), "question": q,
            "user_id": user, "session_id": session, "session_kind": kind}


def test_re_ask_predicate():
    turns = [
        _t("a", 0, "How many work orders were raised at Bishopsgate in July 2026?"),
        _t("b", 4, "How many work orders were raised at Bishopsgate Tower in July?"),   # same words: a re-asked
        _t("c", 30, "Which certificates expire next month?"),
        _t("d", 33, "try again"),                                                       # retry phrase: c re-asked
        _t("e", 50, "List the vendors on site", user="u2"),
        _t("f", 51, "List the vendors on site", user="u3"),                             # another person
        _t("g", 52, "What is the EUI of B-101?", kind="synthetic"),
        _t("h", 53, "What is the EUI of B-101?", kind="synthetic"),                     # not an observed session
    ]
    assert monitor.mark_re_asks(turns) == {"a", "c"}
    assert monitor.RISK_GROUPS["re_ask"]({"re_ask": True}) is True
    assert monitor.RISK_GROUPS["thumbs_down"]({"feedback_rating": "down"}) is True
    assert monitor.RISK_GROUPS["failed"]({"ok": None}) is False
    assert monitor.RISK_GROUPS["gate_rewrote"]({"gate_outcomes": ("passed", "corrected")}) is True


def test_a_question_asked_again_after_ten_minutes_is_not_a_re_ask():
    turns = [_t("a", 0, "Which certificates expire next month?"),
             _t("b", 11, "Which certificates expire next month?")]
    assert monitor.mark_re_asks(turns) == set()
    assert monitor.mark_re_asks([_t("a", 0, "Which certificates expire next month?"),
                                 _t("b", 10, "Which certificates expire next month?")]) == {"a"}


def test_a_re_ask_needs_a_session_and_a_person():
    q = "Which certificates expire next month?"
    assert monitor.mark_re_asks([_t("a", 0, q, session=None), _t("b", 1, q, session=None)]) == set()
    assert monitor.mark_re_asks([_t("a", 0, q, user=None), _t("b", 1, q, user=None)]) == set()
    assert monitor.jaccard(None, q) == 0.0 and monitor.jaccard("", "") == 0.0


def test_the_risk_groups_read_only_what_they_name():
    groups = monitor.RISK_GROUPS
    assert groups["thumbs_down"]({"feedback_rating": "up"}) is False and groups["thumbs_down"]({}) is False
    assert groups["failed"]({"ok": False}) is True and groups["failed"]({"ok": True}) is False
    assert groups["gate_rewrote"]({"gate_outcomes": ("passed",)}) is False and groups["gate_rewrote"]({}) is False
    assert groups["code_flagged"]({"code_failed": True}) is True and groups["code_flagged"]({}) is False
    assert groups["re_ask"]({}) is False
    assert set(monitor.RISK_LABELS) == set(groups) and set(monitor.CHEAP_GROUPS) <= set(groups)
    assert monitor.CHEAP_GROUPS == ("thumbs_down", "failed")


# ---- estimate, threshold, review picks, chart, due marker, check summary (pure) -------------------------

from decimal import Decimal  # noqa: E402

from trace_lab import stats  # noqa: E402

ARTIFACT_G_LABELS = [1] * 12 + [0] * 38
ARTIFACT_G_PREDS = [1] * 10 + [0] * 38 + [1] * 2


def _fast(sample, labels, preds):
    """The real correction with 500 bootstrap replicates instead of 20,000: the same arithmetic, a fast test."""
    return stats.corrected_prevalence(sample, labels, preds, bootstrap_iterations=500)


def test_errors_excluded_from_numerator_and_denominator():
    verdicts = ([{"status": "decided", "failure_present": 1}] * 6 + [{"status": "decided", "failure_present": 0}] * 32
                + [{"status": "error", "failure_present": None}] * 2)
    est = monitor.estimate_mode(verdicts=verdicts, test_labels=ARTIFACT_G_LABELS, test_preds=ARTIFACT_G_PREDS,
                                threshold=0.15, n_planned=40, prevalence=_fast)
    assert est["n_sample"] == 38 and est["n_error"] == 2
    assert est["raw"] == round(6 / 38, 4)
    assert est["test_tpr"] == 0.9474 and est["test_tnr"] == 0.8333 and est["n_test"] == 50


def test_undecidable_and_not_applicable_answers_are_counted_apart():
    verdicts = ([{"status": "decided", "failure_present": 0}] * 4 + [{"status": "undecidable", "failure_present": None},
                {"status": "not_applicable", "failure_present": None}, {"status": "not_applicable", "failure_present": None}])
    est = monitor.estimate_mode(verdicts=verdicts, test_labels=ARTIFACT_G_LABELS, test_preds=ARTIFACT_G_PREDS,
                                threshold=None, n_planned=7, prevalence=_fast)
    assert (est["n_sample"], est["n_undecidable"], est["n_not_applicable"], est["n_error"]) == (4, 1, 2, 0)


def test_threshold_is_crossed_by_the_corrected_rate_and_clearly_above_by_the_interval():
    def fake(sample, labels, preds):
        return {"raw": 0.2, "corrected": 0.17, "ci_low": 0.06, "ci_high": 0.28, "confidence": 0.95,
                "test_tpr": 0.9, "test_tnr": 0.8, "n_test": 2, "validity_warning": ""}
    one = [{"status": "decided", "failure_present": 1}]
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=0.15, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is True and est["clearly_above"] is False
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=0.05, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is True and est["clearly_above"] is True
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=0.17, n_planned=1,
                                prevalence=fake)                               # exactly at the threshold is not above it
    assert est["crossed"] is False
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=0.06, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is True and est["clearly_above"] is False
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 0], test_preds=[1, 0], threshold=None, n_planned=1,
                                prevalence=fake)
    assert est["crossed"] is False and est["clearly_above"] is False
    empty = monitor.estimate_mode(verdicts=[], test_labels=[1], test_preds=[1], threshold=0.15, n_planned=0)
    assert empty["corrected"] is None and empty["reason"] == "uniform sample is empty"
    undecided = monitor.estimate_mode(verdicts=[{"status": "error", "failure_present": None}], test_labels=[1],
                                      test_preds=[1], threshold=0.15, n_planned=1)
    assert undecided["corrected"] is None and "no answer in the sample" in undecided["reason"]
    untested = monitor.estimate_mode(verdicts=one, test_labels=[], test_preds=[], threshold=0.15, n_planned=1)
    assert untested["corrected"] is None and "test" in untested["reason"]


def test_an_estimate_the_statistics_refuse_is_a_reason_not_a_crash():
    one = [{"status": "decided", "failure_present": 1}]
    est = monitor.estimate_mode(verdicts=one, test_labels=[1, 1], test_preds=[1, 1], threshold=0.15, n_planned=1,
                                prevalence=_fast)               # the held-out pairs lack a class
    assert est["corrected"] is None and est["crossed"] is False and est["reason"].startswith("no estimate: ")
    assert "class" in est["reason"]


def test_risk_strata_never_enter_the_estimate():
    plan = {"random": ["t1", "t2"], "risk_groups": {"failed": ["t3"]}, "to_judge": ["t1", "t2", "t3"]}
    assert monitor.estimate_ids("llm", plan, ["t1", "t2", "t3", "t4"]) == ["t1", "t2"]
    assert monitor.estimate_ids("code", plan, ["t1", "t2", "t3", "t4"]) == ["t1", "t2", "t3", "t4"]


def test_review_picks_take_uniform_flags_first_then_risk_only():
    plan = {"random": ["t2", "t5", "t9"], "risk_groups": {"thumbs_down": ["t1", "t5"], "failed": ["t7"]},
            "to_judge": ["t2", "t5", "t9", "t1", "t7"]}
    picks = monitor.flagged_for_review(plan, {"t9", "t5", "t1", "t7"}, limit=3)
    assert picks == [{"turn_id": "t5", "group": None}, {"turn_id": "t9", "group": None},
                     {"turn_id": "t1", "group": "thumbs_down"}]
    assert monitor.review_reason("wrong_period-v2", "2026-W41", None) == "flagged by wrong_period-v2 in 2026-W41: uniform sample"
    assert monitor.review_reason("wrong_period-v2", "2026-W41", "failed").endswith(": risk group the answer failed")


def test_a_flag_in_two_risk_groups_is_picked_once_and_the_reason_is_cut_to_300():
    plan = {"random": [], "risk_groups": {"thumbs_down": ["t1"], "failed": ["t1", "t2"]}}
    assert monitor.flagged_for_review(plan, {"t1", "t2"}) == [{"turn_id": "t1", "group": "thumbs_down"},
                                                             {"turn_id": "t2", "group": "failed"}]
    assert monitor.flagged_for_review({}, {"t1"}) == [] and monitor.flagged_for_review(plan, set()) == []
    assert len(monitor.review_reason("k" * 400, "2026-W41", None)) == 300
    assert monitor.review_reason("k", "p", "no_such_group").endswith("risk group no_such_group")


def test_chart_y_max_rule_and_empty_state():
    assert monitor.chart_points([]) == {"points": [], "threshold": None, "y_max": None}
    entries = [{"period_label": "2026-W40", "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2,
                "threshold": 0.15},
               {"period_label": "2026-W41", "raw": 0.19, "corrected": 0.17, "ci_low": 0.06, "ci_high": 0.28,
                "threshold": 0.15}]
    out = monitor.chart_points(entries)
    assert [p["period_label"] for p in out["points"]] == ["2026-W40", "2026-W41"]
    assert out["threshold"] == 0.15 and out["y_max"] == 0.35          # 1.25 x max(0.15, 0.28, 0.17)
    assert monitor.chart_points(entries, periods=1)["points"][0]["period_label"] == "2026-W41"
    flat = monitor.chart_points([{"period_label": "2026-W41", "corrected": 0.0, "ci_high": 0.0, "threshold": None}])
    assert flat["y_max"] == 0.1


def test_a_chart_point_carries_only_the_keys_the_page_draws():
    out = monitor.chart_points([{"period_label": "2026-W41", "corrected": 0.2, "ci_high": 0.3, "threshold": None,
                                 "secret": "x", "crossed": True}])
    assert set(out["points"][0]) == {"period_label", "window_start", "window_end", "batch_id", "raw", "corrected",
                                     "ci_low", "ci_high", "n_sample", "threshold", "crossed", "validity_warning"}
    assert out["points"][0]["crossed"] is True and out["points"][0]["batch_id"] is None
    assert out["threshold"] is None and out["y_max"] == 0.375


def test_due_marker_reads_the_last_complete_week():
    week = {"status": "done", "window_start": datetime(2026, 10, 5, tzinfo=UTC),
            "window_end": datetime(2026, 10, 12, tzinfo=UTC)}
    assert monitor.due_marker([week], now=NOW) == {"period_label": "2026-W41", "is_due": False}
    assert monitor.due_marker([dict(week, status="failed")], now=NOW)["is_due"] is True
    assert monitor.due_marker([dict(week, status="capped")], now=NOW)["is_due"] is False
    assert monitor.due_marker([dict(week, window_end=datetime(2026, 10, 11, tzinfo=UTC))], now=NOW)["is_due"] is True
    assert monitor.due_marker([], now=NOW)["is_due"] is True


def test_code_check_summary_counts_and_rates():
    rows = [{"evaluator_key": "code:no_answer@v1", "status": "decided", "failure_present": 1, "n": 9},
            {"evaluator_key": "code:no_answer@v1", "status": "decided", "failure_present": 0, "n": 403},
            {"evaluator_key": "code:refusal@v1", "status": "undecidable", "failure_present": None, "n": 2}]
    out = monitor.summarise_checks(rows, labels={"code:no_answer@v1": "Gives no answer",
                                                 "code:refusal@v1": "Says it cannot help"},
                                   validated={"code:no_answer@v1"}, n_window=412)
    assert out[0] == {"evaluator_key": "code:no_answer@v1", "label": "Gives no answer", "n": 412, "decided": 412,
                      "flagged": 9, "undecidable": 0, "not_applicable": 0, "error": 0, "rate": 0.0218,
                      "validated": True}
    assert out[1]["rate"] is None and out[1]["undecidable"] == 2 and out[1]["validated"] is False


def test_call_usd_prices_one_judge_call_and_refuses_to_guess():
    usd = monitor.call_usd("claude-sonnet-5", "p" * 3500, 3500)
    assert usd == Decimal(str(round(2000 * 2 / 1e6 + 800 * 10 / 1e6, 6)))   # 2,000 input + 800 output tokens
    assert monitor.call_usd("no-such-model", "p", 10) is None
    assert monitor.call_usd("claude-sonnet-5", None, 0) == Decimal(str(round(800 * 10 / 1e6, 6)))


# ---- store, judges in use, the plan preview and starting a batch -----------------------------------------

ACTOR = "cccccccc-0000-4000-8000-000000000003"
JOB_ID = "11111111-1111-4111-8111-11111111aaaa"


def _window(n=20):
    return [{"turn_id": f"t{i}", "session_id": f"s{i}", "organization_id": ORG_A, "user_id": "u",
             "started_at": datetime(2026, 10, 6, tzinfo=UTC) + timedelta(minutes=i), "ok": i != 4,
             "feedback_rating": "down" if i == 7 else None} for i in range(1, n + 1)]


def _judge(**kw):
    j = {"judge_id": "11111111-1111-4111-8111-111111111111", "judge_key": "wrong_period-v1",
         "mode_id": "22222222-2222-4222-8222-222222222222", "mode_name": "wrong_period", "kind": "llm",
         "model": "claude-sonnet-5", "prompt_text": "p" * 3500, "prompt_hash": "abc123abc123", "threshold": 0.15,
         "evaluator_key": "judge:11111111-1111-4111-8111-111111111111", "test_labels": [1, 0], "test_preds": [1, 0],
         "check_key": None, "check_version": None}
    j.update(kw)
    return j


async def test_window_turns_pages_by_start_time_and_turn_id(monkeypatch):
    rows = _window(120)
    calls = []
    monkeypatch.setattr(monitor, "WINDOW_PAGE", 50)

    async def page(start, end, org, after, limit):
        calls.append((after, limit))
        i = 0 if after is None else next(n for n, r in enumerate(rows) if r["turn_id"] == after[1]) + 1
        return rows[i:i + limit]

    monkeypatch.setattr(monitor, "_window_page", page)
    out = await monitor.window_turns(*monitor.last_complete_week(NOW), None)
    assert [r["turn_id"] for r in out] == [r["turn_id"] for r in rows]
    assert [c[0] for c in calls] == [None, (rows[49]["started_at"], rows[49]["turn_id"]),
                                     (rows[99]["started_at"], rows[99]["turn_id"])] and {c[1] for c in calls} == {50}


async def test_a_window_that_fills_the_last_page_exactly_is_read_once_more(monkeypatch):
    rows = _window(100)
    seen = []
    monkeypatch.setattr(monitor, "WINDOW_PAGE", 50)

    async def page(start, end, org, after, limit):
        i = 0 if after is None else next(n for n, r in enumerate(rows) if r["turn_id"] == after[1]) + 1
        seen.append(len(rows[i:i + limit]))
        return rows[i:i + limit]

    monkeypatch.setattr(monitor, "_window_page", page)
    assert len(await monitor.window_turns(*monitor.last_complete_week(NOW), ORG_A)) == 100
    assert seen == [50, 50, 0]


def test_window_query_reads_only_chat_answers_of_a_company():
    sql = monitor._window_sql()
    for needle in ("= 'chat'", "tt.organization_id IS NOT NULL", "ORDER BY tt.started_at, tt.turn_id", "LIMIT :limit",
                   "(tt.started_at, tt.turn_id) > (", "CAST(:org AS uuid) IS NULL", "FROM plenum_cafm.agent_trace_turns tt"):
        assert needle in sql, needle
    for banned in ("tracelab", "turn_meta", "plenum_tracelab", "INSERT", "UPDATE", "DELETE"):
        assert banned not in sql, banned                               # one database per statement (G0)
    assert "TRUE" in sql and "NOT EXISTS" in monitor._window_sql("NOT EXISTS (SELECT 1)")


async def test_plan_preview_counts_cost_and_cache(monkeypatch):
    turns = _window(20)

    async def fake_window(start, end, org):
        assert (start, end) == (datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC))
        return turns

    plan = monitor.select_turns(turns, 0.25, {g: monitor.RISK_GROUPS[g] for g in monitor.CHEAP_GROUPS}, 7)
    cached = plan["to_judge"][0]

    async def fake_verdicts(ek, ids):
        return {cached: {"turn_id": cached, "status": "decided", "failure_present": 0}} if cached in ids else {}

    async def fake_predictions(ph, ids):
        return {}

    async def fake_in_use(mode_ids=None):
        return [_judge()]

    monkeypatch.setattr(monitor, "window_turns", fake_window)
    monkeypatch.setattr(monitor, "_verdicts", fake_verdicts)
    monkeypatch.setattr(monitor, "_predictions", fake_predictions)
    monkeypatch.setattr(monitor, "judges_in_use", fake_in_use)
    out = await monitor.plan_preview(organization_id=None, now=NOW)
    assert out["period_label"] == "2026-W41" and out["n_window"] == 20 and out["n_random"] == 5
    assert {"t4", "t7"} <= set(plan["to_judge"]) and not {"t4", "t7"} & set(plan["random"])      # two risk-only answers
    assert out["n_to_judge"] == len(plan["to_judge"]) == 7 and out["n_risk_only"] == 2
    per = monitor.call_usd("claude-sonnet-5", "p" * 3500, monitor.EST_EVIDENCE_CHARS)
    assert out["usd_estimate"] == float(round(per * (out["n_to_judge"] - 1), 4))   # the cached answer is free
    assert any("risk groups" in w for w in out["warnings"])
    assert "prompt_text" not in out["judges"][0]
    assert out["risk_groups"] == list(monitor.RISK_GROUPS) and out["unpriced"] == []


async def test_a_cached_prediction_is_free_too(monkeypatch):
    turns = _window(20)

    async def fake_window(start, end, org):
        return turns

    async def none(*a):
        return {}

    async def predictions(ph, ids):
        return {t: {"status": "decided", "failure_present": 0} for t in ids}          # every answer is cached

    async def in_use(mode_ids=None):
        return [_judge()]

    monkeypatch.setattr(monitor, "window_turns", fake_window)
    monkeypatch.setattr(monitor, "_verdicts", none)
    monkeypatch.setattr(monitor, "_predictions", predictions)
    monkeypatch.setattr(monitor, "judges_in_use", in_use)
    out = await monitor.plan_preview(organization_id=None, now=NOW, risk_groups=["failed", "failed"])
    assert out["usd_estimate"] == 0.0 and out["risk_groups"] == ["failed"]            # a repeated group counts once
    assert not any("risk groups" in w for w in out["warnings"])                        # only cheap groups chosen


async def test_plan_preview_warns_when_nothing_can_be_estimated(monkeypatch):
    async def fake_window(start, end, org):
        return _window(2)

    async def in_use(mode_ids=None):
        return []

    monkeypatch.setattr(monitor, "window_turns", fake_window)
    monkeypatch.setattr(monitor, "judges_in_use", in_use)
    out = await monitor.plan_preview(organization_id=ORG_A, now=NOW, sample_rate=0.1, risk_groups=[])
    assert out["n_random"] == 0 and out["usd_estimate"] == 0.0 and out["judges"] == []
    assert any("only the code checks will run" in w for w in out["warnings"])
    assert any("uniform sample is empty" in w for w in out["warnings"])
    assert out["checks"] == monitor.live_checks() and {c["check_key"] for c in out["checks"]}


async def test_a_judge_with_no_price_is_a_warning_and_is_named(monkeypatch):
    async def fake_window(start, end, org):
        return _window(8)

    async def none(*a):
        return {}

    async def in_use(mode_ids=None):
        return [_judge(model="no-such-model")]

    monkeypatch.setattr(monitor, "window_turns", fake_window)
    monkeypatch.setattr(monitor, "_verdicts", none)
    monkeypatch.setattr(monitor, "_predictions", none)
    monkeypatch.setattr(monitor, "judges_in_use", in_use)
    out = await monitor.plan_preview(organization_id=None, now=NOW)
    assert out["usd_estimate"] == 0.0 and out["unpriced"] == ["wrong_period"]
    assert any("has no price" in w for w in out["warnings"])


async def test_plan_preview_refuses_what_a_batch_cannot_do(monkeypatch):
    async def never(*a, **kw):
        raise AssertionError("no answer may be read before a request is refused")

    monkeypatch.setattr(monitor, "window_turns", never)
    cases = [dict(since=NOW - timedelta(days=2)), dict(until=NOW - timedelta(days=1)),                  # one bound only
             dict(since=NOW - timedelta(days=2), until=NOW + timedelta(hours=1)),                       # the future
             dict(since=NOW - timedelta(days=100), until=NOW - timedelta(days=90)),                     # purged steps
             dict(sample_rate=0), dict(sample_rate=1.5), dict(risk_groups=["nonsense"])]
    reasons = []
    for kw in cases:
        with pytest.raises(monitor.WatchError) as e:
            await monitor.plan_preview(organization_id=None, now=NOW, **kw)
        reasons.append((e.value.status, e.value.reason))
    assert reasons == [(422, "bad_window"), (422, "bad_window"), (422, "window_in_future"),
                       (409, "window_beyond_retention"), (422, "bad_rate"), (422, "bad_rate"),
                       (422, "unknown_risk_group")]


async def test_start_batch_refuses_over_cap_and_a_second_open_batch(monkeypatch):
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 10, "n_random": 3, "n_risk_only": 0, "n_to_judge": 3, "judges": [], "checks": [],
               "usd_estimate": 9.0, "sample_rate": 0.25, "seed": 7, "risk_groups": list(monitor.RISK_GROUPS), "warnings": []}

    async def fake_preview(**kw):
        return dict(preview)

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    kw = dict(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7, risk_groups=None, mode_ids=None,
              usd_cap=5.0, actor_id=ACTOR, actor_email="owner@example.com", now=NOW)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(**kw)
    assert e.value.status == 409 and e.value.reason == "over_cap" and e.value.extra["usd_estimate"] == 9.0
    preview["usd_estimate"] = 1.0

    async def conflict(**_):
        raise monitor.jobs.JobConflict()

    monkeypatch.setattr(monitor.jobs, "create", conflict)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(**kw)
    assert e.value.status == 409 and e.value.reason == "batch_running"


async def test_start_batch_creates_the_job_then_the_batch_and_starts_the_worker(monkeypatch):
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 10, "n_random": 3, "n_risk_only": 0, "n_to_judge": 3, "judges": [{"judge_id": "j"}],
               "checks": monitor.live_checks(), "usd_estimate": 1.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"], "warnings": []}
    created, inserted, started = {}, [], []

    async def fake_preview(**kw):
        return dict(preview)

    async def fake_create(**kw):
        created.update(kw)
        return {"id": JOB_ID}

    async def fake_insert(row):
        inserted.append(row)

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor.jobs, "create", fake_create)
    monkeypatch.setattr(monitor, "_insert_batch", fake_insert)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    out = await monitor.start_batch(organization_id=ORG_A, since=None, until=None, sample_rate=0.25, seed=7,
                                    risk_groups=["failed"], mode_ids=None, usd_cap=None, actor_id=ACTOR,
                                    actor_email="owner@example.com", now=NOW)
    assert created["kind"] == "monitor_batch" and created["scope_key"] == f"monitor_batch:{ORG_A}"
    assert created["organization_id"] == ORG_A and created["params"] == {"batch_id": inserted[0]["id"]}
    assert created["usd_cap"] == Decimal(str(monitor.monitor_usd_cap())) and created["total"] == 10
    assert created["actor_id"] == ACTOR and created["usd_estimate"] == Decimal("1.0")
    assert inserted[0]["status"] == "queued" and inserted[0]["organization_id"] == ORG_A
    assert inserted[0]["job_id"] == JOB_ID and inserted[0]["window_start"] == datetime(2026, 10, 5, tzinfo=UTC)
    assert started == [inserted[0]["id"]] and out["batch"]["id"] == inserted[0]["id"] and out["job"]["id"] == JOB_ID
    assert out["batch"]["scope"] == "company" and out["batch"]["status"] == "queued"


async def test_a_platform_batch_holds_the_one_platform_scope(monkeypatch):
    created = {}
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 1, "judges": [], "checks": [], "usd_estimate": 0.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"]}

    async def fake_preview(**kw):
        return dict(preview)

    async def fake_create(**kw):
        created.update(kw)
        return {"id": JOB_ID}

    async def fake_insert(row):
        pass

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor.jobs, "create", fake_create)
    monkeypatch.setattr(monitor, "_insert_batch", fake_insert)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: None)
    out = await monitor.start_batch(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7,
                                    risk_groups=None, mode_ids=None, usd_cap=2, actor_id=ACTOR, actor_email=None, now=NOW)
    assert created["scope_key"] == "monitor_batch:*" and created["organization_id"] is None
    assert created["usd_cap"] == Decimal("2") and out["batch"]["scope"] == "platform"


async def test_a_judge_with_no_price_cannot_be_capped_so_the_batch_does_not_start(monkeypatch):
    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 5, "judges": [], "checks": [], "usd_estimate": 0.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"], "unpriced": ["wrong_period"]}

    async def fake_preview(**kw):
        return dict(preview)

    async def never(**kw):
        raise AssertionError("nothing is created for a batch that is refused")

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor.jobs, "create", never)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(organization_id=None, since=None, until=None, sample_rate=0.25, seed=7,
                                  risk_groups=None, mode_ids=None, usd_cap=5, actor_id=ACTOR, actor_email=None, now=NOW)
    assert e.value.status == 409 and e.value.reason == "model_unpriced" and e.value.extra["modes"] == ["wrong_period"]


async def test_a_batch_that_cannot_be_recorded_gives_its_job_back(monkeypatch):
    from sqlalchemy.exc import IntegrityError

    preview = {"period_label": "2026-W41", "window": {"start": "2026-10-05T00:00:00+00:00", "end": "2026-10-12T00:00:00+00:00"},
               "n_window": 5, "judges": [], "checks": [], "usd_estimate": 0.0, "sample_rate": 0.25, "seed": 7,
               "risk_groups": ["failed"]}
    finished, started, outcome = [], [], {}

    async def fake_preview(**kw):
        return dict(preview)

    async def fake_create(**kw):
        return {"id": JOB_ID}

    async def finish(job_id, status, error=None):
        finished.append((job_id, status))

    async def insert(row):
        raise outcome["error"]

    monkeypatch.setattr(monitor, "plan_preview", fake_preview)
    monkeypatch.setattr(monitor.jobs, "create", fake_create)
    monkeypatch.setattr(monitor.jobs, "finish", finish)
    monkeypatch.setattr(monitor, "_insert_batch", insert)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    kw = dict(organization_id=ORG_A, since=None, until=None, sample_rate=0.25, seed=7, risk_groups=None,
              mode_ids=None, usd_cap=5, actor_id=ACTOR, actor_email=None, now=NOW)
    outcome["error"] = IntegrityError("INSERT", {}, Exception('duplicate key value violates unique constraint "ux_monitor_batches_active"'))
    with pytest.raises(monitor.WatchError) as e:
        await monitor.start_batch(**kw)
    assert e.value.status == 409 and e.value.reason == "batch_running" and finished == [(JOB_ID, "failed")]
    outcome["error"] = IntegrityError("INSERT", {}, Exception('violates check constraint "ck_monitor_batches_window"'))
    with pytest.raises(IntegrityError):                                  # not the one-open-batch rule: not hidden as busy
        await monitor.start_batch(**kw)
    outcome["error"] = RuntimeError("the lab database went away")
    with pytest.raises(RuntimeError):
        await monitor.start_batch(**kw)
    assert finished == [(JOB_ID, "failed")] * 3 and started == []        # the job never keeps the scope held


async def test_judges_in_use_come_from_the_judge_store_with_its_frozen_test_pairs(monkeypatch):
    j, m = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
    row = {"id": j, "mode_id": m, "mode_name": "wrong_period", "mode_status": "confirmed", "judge_key": "wrong_period-v2",
           "kind": "llm", "model": "claude-sonnet-5", "prompt_text": "p", "prompt_hash": "abc123abc123",
           "check_key": None, "check_version": None, "monitor_threshold": 0.15}
    retired = dict(row, id="33333333-3333-4333-8333-333333333333", mode_status="retired", mode_name="old_mode")
    code = dict(row, id="44444444-4444-4444-8444-444444444444", mode_name="no_answer", kind="code", model=None,
                prompt_text=None, check_key="no_answer", check_version=1, monitor_threshold=None)

    async def in_use():
        return {m: row, "mode-old": retired, "mode-code": code}

    asked = []

    async def pairs(judge_id):
        asked.append(judge_id)
        return [1, 0, 1], [1, 0, 0]

    monkeypatch.setattr(monitor.judges, "in_use_judges", in_use)
    monkeypatch.setattr(monitor.judges, "test_pairs", pairs)
    out = await monitor.judges_in_use()
    assert [(x["mode_name"], x["judge_id"], x["threshold"], x["evaluator_key"]) for x in out] == [
        ("no_answer", code["id"], None, "code:no_answer@v1"), ("wrong_period", j, 0.15, f"judge:{j}")]
    assert out[1]["test_labels"] == [1, 0, 1] and out[1]["test_preds"] == [1, 0, 0]
    assert retired["id"] not in asked                                   # a retired mode's judge is never asked for
    assert (await monitor.judges_in_use([m]))[0]["prompt_hash"] == "abc123abc123"
    assert await monitor.judges_in_use(["22222222-2222-4222-8222-2222222222ff"]) == []


async def test_predictions_are_the_judge_stores_cache_decided_rows_only(monkeypatch):
    async def cached(prompt_hash, ids):
        assert prompt_hash == "h" and ids == ["a", "b"]
        return {"a": {"status": "decided", "failure_present": 1}, "b": {"status": "error", "failure_present": None}}

    monkeypatch.setattr(monitor.judges, "cached_predictions", cached)
    assert list(await monitor._predictions("h", ["a", "b"])) == ["a"]
    assert await monitor._predictions("h", []) == {}
    assert await monitor._verdicts("judge:x", []) == {}


async def test_save_writes_the_prediction_through_the_judge_store_in_the_same_transaction(monkeypatch):
    from types import SimpleNamespace

    calls = []

    class Conn:
        async def execute(self, *a, **kw):
            calls.append("verdicts")

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Engine:
        def begin(self):
            return Begin()

    async def write(conn, **kw):
        calls.append(("prediction", sorted(kw)))

    async def checkpoint(conn, job_id, **kw):
        calls.append(("checkpoint", job_id, kw["usd"], kw["released"], kw["done_delta"]))

    monkeypatch.setattr(monitor.db, "lab_engine", lambda: Engine())
    monkeypatch.setattr(monitor.judges, "write_prediction", write)
    monkeypatch.setattr(monitor.jobs, "checkpoint", checkpoint)
    reply = SimpleNamespace(status="decided", failure_present=1, critique="Quotes June.", usd=0.02, error=None)
    arg = monitor.prediction_args(judge={"judge_id": "j1", "prompt_hash": "ph", "model": "claude-sonnet-5"},
                                  turn_id="t1", organization_id=ORG_A, reply=reply,
                                  inp=SimpleNamespace(text="e", chars=100, truncated=False, sha="sha"))
    assert arg["judge"] == {"id": "j1", "prompt_hash": "ph", "model": "claude-sonnet-5"}
    assert arg["pred"]["failure_present"] == 1 and arg["pred"]["usd"] == Decimal("0.02") and arg["pred"]["input_sha"] == "sha"
    await monitor._save(JOB_ID, verdicts=[{"id": "v"}], prediction=arg, usd=Decimal("0.02"))
    assert calls == [("prediction", ["judge", "organization_id", "pred", "turn_id"]), "verdicts",
                     ("checkpoint", JOB_ID, Decimal("0.02"), Decimal(0), 0)]


async def test_save_with_nothing_to_write_still_checkpoints_the_job(monkeypatch):
    calls = []

    class Conn:
        async def execute(self, *a, **kw):
            calls.append("sql")

    class Begin:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, *exc):
            return False

    class Engine:
        def begin(self):
            return Begin()

    async def checkpoint(conn, job_id, **kw):
        calls.append(("checkpoint", kw["done_delta"], kw["progress_patch"]))

    monkeypatch.setattr(monitor.db, "lab_engine", lambda: Engine())
    monkeypatch.setattr(monitor.jobs, "checkpoint", checkpoint)
    await monitor._save(JOB_ID, verdicts=[], done_delta=50, progress={"phase": "checks"})
    assert calls == [("checkpoint", 50, {"phase": "checks"})]


def test_a_prediction_is_an_error_unless_the_judge_gave_a_strict_verdict():
    from types import SimpleNamespace

    judge = {"id": "j1", "prompt_hash": "ph", "model": "m"}
    inp = {"sha": "s", "chars": 10, "truncated": True}
    bad = SimpleNamespace(status="decided", failure_present=2, critique="x", usd=None, error=None)
    pred = monitor.prediction_args(judge=judge, turn_id="t", organization_id=ORG_A, reply=bad, inp=inp)["pred"]
    assert pred["status"] == "error" and pred["failure_present"] is None and pred["error"] == "no verdict"
    assert pred["usd"] is None and pred["evidence_truncated"] is True and pred["evidence_chars"] == 10
    failed = SimpleNamespace(status="error", failure_present=None, critique="", usd=0.01, error="RateLimitError")
    pred = monitor.prediction_args(judge=judge, turn_id="t", organization_id=ORG_A, reply=failed, inp=inp)["pred"]
    assert pred["status"] == "error" and pred["error"] == "RateLimitError" and pred["critique"] is None
    assert pred["usd"] == Decimal("0.01")


def test_a_verdict_row_carries_the_batch_and_the_judge_only_for_a_judge():
    turn = {"turn_id": "t1", "organization_id": ORG_A, "started_at": NOW}
    code = monitor.verdict_row(evaluator_key="code:no_answer@v1", turn=turn, status="decided", failure_present=0,
                               detail={"findings": []}, batch_id="b1")
    assert code["judge_id"] is None and code["mode_id"] is None and code["org"] == ORG_A and code["batch_id"] == "b1"
    assert code["fp"] == 0 and json.loads(code["detail"]) == {"findings": []}
    llm = monitor.verdict_row(evaluator_key="judge:j", turn=turn, status="error", failure_present=None,
                              detail={"error": "x"}, batch_id="b1", judge_id="j", mode_id="m")
    assert llm["judge_id"] == "j" and llm["mode_id"] == "m" and llm["fp"] is None and llm["id"] != code["id"]
    assert "plenum_" not in monitor._UPSERT_VERDICT and "INSERT INTO tracelab.monitor_verdicts" in monitor._UPSERT_VERDICT
    assert "WHERE tracelab.monitor_verdicts.status <> 'decided'" in monitor._UPSERT_VERDICT



# ---- the threshold action (a review batch per company, proposed cases) -----------------------------------

J1 = "11111111-1111-4111-8111-111111111111"
M1 = "22222222-2222-4222-8222-222222222222"
SNAP = {"judge_id": J1, "judge_key": "wrong_period-v2", "mode_id": M1, "mode_name": "wrong_period", "kind": "llm",
        "evaluator_key": f"judge:{J1}", "threshold": 0.15, "prompt_hash": "abc123abc123"}


def _action_setup(monkeypatch, flagged, org_of):
    plan = {"random": ["t1", "t2", "t3", "t4", "t5", "t6", "t7"], "risk_groups": {"thumbs_down": ["t8"]},
            "to_judge": ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"]}
    batch = {"id": "b1", "judges": [SNAP], "plan": plan, "period_label": "2026-W41", "seed": 7,
             "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
             "started_by": ACTOR, "started_by_email": "owner@example.com"}
    created, proposed, existing = [], [], {}

    async def fake_verdicts(ek, ids):
        return {t: {"status": "decided", "failure_present": 1 if t in flagged else 0} for t in ids}

    async def fake_turns(ids):
        return {t: {"organization_id": org_of(t), "session_id": "s-" + t} for t in ids}

    async def fake_existing(batch_id, mode_id):
        return existing.get((batch_id, mode_id), [])

    async def fake_create_batch(principal, org, req):               # the review module's one entry point for batches
        created.append({"principal": principal, "org": org, "req": req})
        bid = "rb-" + org[:4]
        existing.setdefault(("b1", req["params"]["mode_id"]), []).append(bid)
        return {"ok": True, "batch": {"id": bid}, "items": []}

    async def fake_propose(b, mode_id, ids):
        proposed.append((mode_id, list(ids)))
        return ["case-1"]

    monkeypatch.setattr(monitor, "_verdicts", fake_verdicts)
    monkeypatch.setattr(monitor, "_turn_rows", fake_turns)
    monkeypatch.setattr(monitor, "_review_batches_for", fake_existing)
    monkeypatch.setattr(monitor.sampling, "create_batch", fake_create_batch)
    monkeypatch.setattr(monitor, "_propose_cases", fake_propose)
    return batch, plan, created, proposed


async def test_threshold_crossing_opens_review_batch_per_company_and_proposes_cases(monkeypatch):
    flagged = {"t1", "t2", "t3", "t4", "t5", "t6", "t8"}            # six uniform flags and one risk-only flag
    batch, plan, created, proposed = _action_setup(
        monkeypatch, flagged, lambda t: ORG_A if t in ("t1", "t2", "t8") else ORG_B)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []},
                         {"mode_id": "other", "crossed": False, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)
    assert {c["org"]: [i["turn_id"] for i in c["req"]["items"]] for c in created} == {
        ORG_A: ["t1", "t2", "t8"], ORG_B: ["t3", "t4", "t5", "t6"]}
    reasons = {i["turn_id"]: i["reason"] for c in created for i in c["req"]["items"]}
    assert reasons["t1"] == "flagged by wrong_period-v2 in 2026-W41: uniform sample"
    assert reasons["t8"].endswith(": risk group rated down by the asker")
    for c in created:                                               # recorded by the review module, never by monitoring
        req, who = c["req"], c["principal"]
        assert req["strategy"] == "monitor_flagged" and req["plan_step"] == "extra" and req["seed"] == 7
        assert (req["since"], req["until"]) == (batch["window_start"], batch["window_end"])
        assert req["params"] == {"monitor_batch_id": "b1", "mode_id": M1, "judge_id": J1}
        assert (str(who.user_id), who.email, who.role, who.organization_id, who.building_ids, who.can_ingest) == (
            ACTOR, "owner@example.com", "superadmin", None, None, False)       # the monitor batch's starter
    assert proposed == [(M1, ["t1", "t2", "t3", "t4", "t5"])]       # at most 5, uniform flags only
    assert results["modes"][0]["review_batch_ids"] == ["rb-aaaa", "rb-bbbb"]
    assert results["modes"][0]["proposed_case_ids"] == ["case-1"]
    assert results["modes"][1]["review_batch_ids"] == [] and len(proposed) == 1


async def test_review_batch_is_opened_once_per_batch_and_mode(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)
    first = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    again = await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert first == again == ["rb-aaaa"] and len(created) == 1


async def test_nothing_flagged_is_a_409(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, set(), lambda t: ORG_A)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.status == 409 and e.value.reason == "nothing_flagged" and created == []


async def test_a_company_with_no_answer_left_to_review_is_skipped(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1", "t3"}, lambda t: ORG_A if t == "t1" else ORG_B)

    async def create(principal, org, req):
        if org == ORG_B:
            raise monitor.review_errors.Invalid("pool_empty")       # all of that company's answers are in batches already
        created.append(org)
        return {"ok": True, "batch": {"id": "rb-a"}, "items": []}

    monkeypatch.setattr(monitor.sampling, "create_batch", create)
    assert await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch) == ["rb-a"]
    assert created == [ORG_A]

    async def race(principal, org, req):
        raise monitor.review_errors.Conflict("pool_changed")        # another batch took them first

    monkeypatch.setattr(monitor.sampling, "create_batch", race)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.status == 409 and e.value.reason == "nothing_flagged"


async def test_any_other_refusal_of_a_review_batch_is_not_hidden(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)

    async def outside(principal, org, req):
        raise monitor.review_errors.Invalid("window_outside_retention")

    monkeypatch.setattr(monitor.sampling, "create_batch", outside)
    with pytest.raises(monitor.review_errors.Invalid) as e:
        await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.reason == "window_outside_retention"


async def test_an_unknown_batch_or_a_mode_outside_it_is_a_404(monkeypatch):
    batch, plan, created, _ = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)

    async def none(batch_id):
        return None

    monkeypatch.setattr(monitor, "_load_batch", none)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("missing", M1, actor_id=ACTOR, actor_email=None)
    assert e.value.status == 404 and e.value.reason == "not_found"
    with pytest.raises(monitor.WatchError) as e:
        await monitor.open_review_batches("b1", "no-such-mode", actor_id=ACTOR, actor_email=None, batch=batch)
    assert e.value.status == 404 and created == []


async def test_a_flagged_answer_the_chat_no_longer_has_is_left_out_and_the_pick_is_capped(monkeypatch):
    flagged = {f"t{i}" for i in range(1, 8)}
    batch, plan, created, _ = _action_setup(monkeypatch, flagged, lambda t: ORG_A)

    async def fewer(ids):
        return {t: {"organization_id": ORG_A, "session_id": "s"} for t in ids if t != "t2"} | {
            "t3": {"organization_id": None, "session_id": "s"}}                # t2 is gone, t3 has no company

    monkeypatch.setattr(monitor, "_turn_rows", fewer)
    await monitor.open_review_batches("b1", M1, actor_id=ACTOR, actor_email=None, batch=batch)
    assert [i["turn_id"] for i in created[0]["req"]["items"]] == ["t1", "t4", "t5", "t6", "t7"]
    many = {"random": [f"r{i}" for i in range(60)], "risk_groups": {}, "to_judge": [f"r{i}" for i in range(60)]}
    assert len(monitor.flagged_for_review(many, set(many["random"]))) == monitor.FLAGGED_REVIEW_MAX == 50


async def test_only_risk_group_flags_open_a_review_batch_but_propose_no_case(monkeypatch):
    batch, plan, created, proposed = _action_setup(monkeypatch, {"t8"}, lambda t: ORG_A)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)
    assert results["modes"][0]["review_batch_ids"] == ["rb-aaaa"] and proposed == []
    assert results["modes"][0]["proposed_case_ids"] == []


async def test_a_refused_review_batch_does_not_stop_the_cases_and_a_failed_proposal_does_not_fail_the_batch(monkeypatch):
    batch, plan, created, proposed = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)

    async def refused(principal, org, req):
        raise monitor.review_errors.Invalid("window_outside_retention")

    monkeypatch.setattr(monitor.sampling, "create_batch", refused)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)
    assert results["modes"][0]["review_batch_ids"] == [] and proposed == [(M1, ["t1"])]
    assert results["modes"][0]["proposed_case_ids"] == ["case-1"]

    async def broken(b, mode_id, ids):
        raise RuntimeError("the lab database went away")

    monkeypatch.setattr(monitor, "_propose_cases", broken)
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)                   # never raises: the estimates are kept
    assert results["modes"][0]["proposed_case_ids"] == []


async def test_a_batch_with_no_starter_proposes_cases_but_opens_no_review_batch(monkeypatch):
    batch, plan, created, proposed = _action_setup(monkeypatch, {"t1"}, lambda t: ORG_A)
    batch["started_by"] = None
    results = {"modes": [{"mode_id": M1, "crossed": True, "review_batch_ids": [], "proposed_case_ids": []},
                         {"mode_id": "not-in-batch", "crossed": True, "review_batch_ids": [], "proposed_case_ids": []}]}
    await monitor._act_on_crossings(batch, plan, results)
    assert created == [] and proposed == [(M1, ["t1"])] and results["modes"][1]["proposed_case_ids"] == []


async def test_cases_are_proposed_by_the_eval_case_module_imported_when_needed(monkeypatch):
    import sys
    import types

    calls = []

    async def propose_from_monitor(*, batch, mode_id, turn_ids):
        calls.append((batch["id"], mode_id, list(turn_ids)))
        return ["case-9"]

    import trace_lab

    fake = types.ModuleType("trace_lab.eval_cases")
    fake.propose_from_monitor = propose_from_monitor
    monkeypatch.setitem(sys.modules, "trace_lab.eval_cases", fake)
    # `from . import eval_cases` reads the package attribute before sys.modules: once any test has imported the real
    # module (it exists now), that attribute would win over the fake
    monkeypatch.setattr(trace_lab, "eval_cases", fake, raising=False)
    assert await monitor._propose_cases({"id": "b1"}, M1, ["t1", "t2"]) == ["case-9"]
    assert calls == [("b1", M1, ["t1", "t2"])]
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(monitor.__file__).read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("eval_cases" in ast.dump(n) for n in top)               # the two modules never import each other at load


# ---- the batch worker (checks on every answer, judges on the sample, estimates, resume) ----------------

import asyncio  # noqa: E402
import contextvars  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from trace_lab import checks, jobs, judges  # noqa: E402

LIVE = [k for k, c in checks.CHECKS.items() if c.scope == "live"]


class FakeStore:
    """In-memory stand-ins for monitor's store, the turn loader, the checks, the judge and the job helpers."""

    def __init__(self, n, *, fails=(), code_fail=(), unreadable=()):
        self.turns = [{"turn_id": f"t{i:03d}", "session_id": f"s{i}", "organization_id": ORG_A, "user_id": "u",
                       "started_at": datetime(2026, 10, 6, tzinfo=UTC) + timedelta(minutes=i), "ok": True,
                       "feedback_rating": "down" if i % 10 == 0 else None} for i in range(1, n + 1)]
        self.fails, self.code_fail, self.unreadable = set(fails), set(code_fail), set(unreadable)
        self.verdicts, self.predictions = {}, {}
        self.asked, self.loaded_chunks, self.batch_updates, self.snapshots, self.saves = [], [], [], [], []
        self.reserved, self.cap_after, self.acted = 0, None, None
        self.save_error, self.ask_error, self.snapshot_error = None, None, None

    def install(self, monkeypatch):
        store = self

        async def window_turns(start, end, org):
            return list(store.turns)

        async def verdicts(ek, ids):
            return {t: store.verdicts[(ek, t)] for t in ids if (ek, t) in store.verdicts}

        async def predictions(ph, ids):
            return {t: store.predictions[(ph, t)] for t in ids
                    if (ph, t) in store.predictions and store.predictions[(ph, t)]["status"] == "decided"}

        async def save(job_id, *, verdicts, prediction=None, usd=None, released=Decimal(0), done_delta=0, progress=None):
            if prediction is not None and store.save_error is not None:
                raise store.save_error
            store.saves.append({"verdicts": len(verdicts), "usd": usd, "released": released, "done_delta": done_delta,
                                "progress": progress})
            for v in verdicts:
                key = (v["ek"], v["tid"])
                if key in store.verdicts and store.verdicts[key]["status"] == "decided":
                    continue
                store.verdicts[key] = {"turn_id": v["tid"], "status": v["status"], "failure_present": v["fp"],
                                       "organization_id": v["org"], "detail": json.loads(v["detail"])}
            if prediction is not None:                      # judges.write_prediction's keyword arguments
                pred = prediction["pred"]
                store.predictions[(prediction["judge"]["prompt_hash"], prediction["turn_id"])] = {
                    "status": pred["status"], "failure_present": pred["failure_present"], "critique": pred["critique"]}

        async def counts(eks, ids):
            agg, wanted = {}, set(ids)
            for (ek, t), v in store.verdicts.items():
                if ek in eks and t in wanted:
                    k = (ek, v["status"], v["failure_present"])
                    agg[k] = agg.get(k, 0) + 1
            return [{"evaluator_key": k[0], "status": k[1], "failure_present": k[2], "n": n} for k, n in agg.items()]

        async def load_judge_turns(ids):                    # the judge store's loader: {turn_id: NormalizedTurn}
            store.loaded_chunks.append(len(ids))
            return {t: SimpleNamespace(turn_id=t, question=f"question {t}", gates=[], tool_calls=1,
                                       session_kind="observed") for t in ids if t not in store.unreadable}

        def run_live_checks(nt):
            return {k: checks.CheckResult("fail" if (k == LIVE[0] and nt.turn_id in store.code_fail) else "pass")
                    for k in LIVE}

        async def ask(model, prompt_text, judge_text):
            tid = judge_text.split()[-1]
            store.asked.append(tid)
            await asyncio.sleep(0)                          # a real call suspends: other calls may be in flight
            if store.ask_error is not None:
                raise store.ask_error
            return SimpleNamespace(failure_present=1 if tid in store.fails else 0, critique="because", status="decided",
                                   usd=0.01, error=None)

        async def reserve(job_id, usd):
            if store.cap_after is not None and store.reserved >= store.cap_after:
                raise jobs.CapReached()
            store.reserved += 1

        async def judge_row(judge_id):
            return {"id": SNAP["judge_id"], "model": "claude-sonnet-5", "prompt_text": "prompt",
                    "prompt_hash": SNAP["prompt_hash"]}

        async def update_batch(batch_id, *, finished=False, **fields):
            store.batch_updates.append(dict(fields, finished=finished))

        async def in_use(mode_ids=None):
            return [dict(SNAP, test_labels=ARTIFACT_G_LABELS, test_preds=ARTIFACT_G_PREDS)]

        async def validated():
            return set()

        async def act(batch, plan, results):
            store.acted = results

        async def job_get(job_id, *, organization_id):
            return {"status": "running", "usd_spent": Decimal("0.05")}

        async def snapshot(ids, *, organization_id, reason):
            if store.snapshot_error is not None:
                raise store.snapshot_error
            store.snapshots.append((organization_id, list(ids), reason))
            return len(ids)

        for name, fn in {"window_turns": window_turns, "_verdicts": verdicts, "_predictions": predictions,
                         "_save": save, "_verdict_counts": counts, "_judge_row": judge_row,
                         "_update_batch": update_batch, "judges_in_use": in_use, "_validated_check_keys": validated,
                         "_act_on_crossings": act}.items():
            monkeypatch.setattr(monitor, name, fn)
        monkeypatch.setattr(judges, "load_judge_turns", load_judge_turns)
        monkeypatch.setattr(checks, "run_live_checks", run_live_checks)
        monkeypatch.setattr(judges, "judge_input",
                            lambda nt: SimpleNamespace(text=f"evidence {nt.turn_id}", chars=100, truncated=False, sha="sha"))
        monkeypatch.setattr(judges, "ASK", ask)
        monkeypatch.setattr(judges, "JUDGE_CONCURRENCY", 1)
        monkeypatch.setattr(jobs, "reserve", reserve)
        monkeypatch.setattr(jobs, "get", job_get)
        monkeypatch.setattr(monitor.evidence, "snapshot", snapshot)
        return self


def _batch(**kw):
    b = {"id": "b1", "organization_id": None, "window_start": datetime(2026, 10, 5, tzinfo=UTC),
         "window_end": datetime(2026, 10, 12, tzinfo=UTC), "sample_rate": 0.25, "seed": 7,
         "risk_groups": ["thumbs_down"], "judges": [SNAP], "checks": monitor.live_checks(), "plan": None,
         "period_label": "2026-W41", "started_by": ACTOR, "started_by_email": "owner@example.com"}
    b.update(kw)
    return b


def _plan_of(store):
    return next(u["plan"] for u in store.batch_updates if "plan" in u)


async def test_code_checks_cover_every_window_turn(monkeypatch):
    store = FakeStore(120).install(monkeypatch)
    assert await monitor._run(_batch(judges=[]), JOB_ID) == "done"
    assert store.loaded_chunks == [50, 50, 20]
    for c in LIVE:
        ek = checks.CHECKS[c].evaluator_key
        assert all((ek, t["turn_id"]) in store.verdicts for t in store.turns), ek
    assert store.asked == [] and store.snapshots == []                 # no judge, no code flag: nothing to keep
    assert [s["done_delta"] for s in store.saves] == [50, 50, 20]      # one checkpoint per chunk
    assert store.saves[-1]["progress"] == {"phase": "checks", "checked": 120, "n_window": 120}


async def test_judges_run_only_on_planned_turns(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    assert await monitor._run(_batch(), JOB_ID) == "done"
    plan = _plan_of(store)
    assert sorted(store.asked) == sorted(plan["to_judge"]) and len(store.asked) == len(plan["to_judge"])
    assert store.asked[:len(plan["random"])] == plan["random"]
    assert store.snapshots == [(ORG_A, plan["to_judge"], "monitored")]  # every judged answer outlives the span purge


async def test_judged_answers_are_kept_per_company_before_the_first_paid_call(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    for i, t in enumerate(store.turns):
        t["organization_id"] = ORG_A if i % 2 == 0 else ORG_B
    order = []
    real_snapshot, real_ask = monitor.evidence.snapshot, judges.ASK

    async def snapshot(ids, *, organization_id, reason):
        order.append("snapshot")
        return await real_snapshot(ids, organization_id=organization_id, reason=reason)

    async def ask(model, prompt_text, judge_text):
        order.append("ask")
        return await real_ask(model, prompt_text, judge_text)

    monkeypatch.setattr(monitor.evidence, "snapshot", snapshot)
    monkeypatch.setattr(judges, "ASK", ask)
    await monitor._run(_batch(), JOB_ID)
    plan = _plan_of(store)
    assert {org for org, _, _ in store.snapshots} == {ORG_A, ORG_B}
    assert sorted(t for _, ids, _ in store.snapshots for t in ids) == sorted(plan["to_judge"])
    by_id = {t["turn_id"]: t["organization_id"] for t in store.turns}
    assert all(by_id[t] == org for org, ids, _ in store.snapshots for t in ids)    # never another company's answer
    assert order.index("ask") > max(i for i, x in enumerate(order) if x == "snapshot")


async def test_a_batch_whose_evidence_cannot_be_kept_judges_nothing(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    store.snapshot_error = RuntimeError("the lab database went away")
    with pytest.raises(RuntimeError):
        await monitor._run(_batch(), JOB_ID)
    assert store.asked == [] and store.acted is None                    # no paid call whose evidence is not kept


def _code_snap():
    ek = checks.CHECKS[LIVE[0]].evaluator_key
    return dict(SNAP, kind="code", evaluator_key=ek, judge_key="no_answer-v1", mode_name="no_answer", threshold=None)


async def test_a_code_only_batch_keeps_the_answers_its_plan_names(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    assert await monitor._run(_batch(judges=[_code_snap()]), JOB_ID) == "done"
    plan = _plan_of(store)
    assert store.asked == []                                            # no model call
    assert plan["random"] and store.snapshots == [(ORG_A, plan["to_judge"], "monitored")]


async def test_every_code_flagged_answer_is_kept_judged_or_not_one_company_at_a_time(monkeypatch):
    flagged = {f"t{i:03d}" for i in range(1, 41) if i % 3 == 0}
    store = FakeStore(40, code_fail=flagged).install(monkeypatch)
    for i, t in enumerate(store.turns):
        t["organization_id"] = ORG_A if i % 2 == 0 else ORG_B
    order = []
    real_snapshot, real_ask = monitor.evidence.snapshot, judges.ASK

    async def snapshot(ids, *, organization_id, reason):
        order.append("snapshot")
        return await real_snapshot(ids, organization_id=organization_id, reason=reason)

    async def ask(model, prompt_text, judge_text):
        order.append("ask")
        return await real_ask(model, prompt_text, judge_text)

    monkeypatch.setattr(monitor.evidence, "snapshot", snapshot)
    monkeypatch.setattr(judges, "ASK", ask)
    await monitor._run(_batch(), JOB_ID)
    plan = _plan_of(store)
    assert flagged - set(plan["to_judge"])                              # some flags no judge reads: the case at issue
    kept = [t for _, ids, _ in store.snapshots for t in ids]
    assert len(kept) == len(set(kept))                                  # an answer is copied once, not twice
    assert set(kept) == set(plan["to_judge"]) | flagged
    assert len(store.snapshots) == 2 and {org for org, _, _ in store.snapshots} == {ORG_A, ORG_B}
    assert {reason for _, _, reason in store.snapshots} == {"monitored"}
    by_id = {t["turn_id"]: t["organization_id"] for t in store.turns}
    assert all(by_id[t] == org for org, ids, _ in store.snapshots for t in ids)    # never another company's answer
    assert order.index("ask") > max(i for i, x in enumerate(order) if x == "snapshot")


async def test_a_batch_with_no_judge_still_keeps_its_code_flagged_answers(monkeypatch):
    store = FakeStore(40, code_fail={"t005", "t006"}).install(monkeypatch)
    assert await monitor._run(_batch(judges=[]), JOB_ID) == "done"
    assert store.asked == [] and store.snapshots == [(ORG_A, ["t005", "t006"], "monitored")]


async def test_a_code_flag_whose_evidence_cannot_be_kept_stops_the_batch(monkeypatch):
    store = FakeStore(40, code_fail={"t005"}).install(monkeypatch)
    store.snapshot_error = RuntimeError("the lab database went away")
    with pytest.raises(RuntimeError):
        await monitor._run(_batch(judges=[_code_snap()]), JOB_ID)
    assert store.acted is None


async def test_overlapping_batches_reuse_verdicts(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    await monitor._run(_batch(), JOB_ID)
    store.asked.clear()
    await monitor._run(_batch(id="b2"), JOB_ID)
    assert store.asked == []                                          # decided verdicts are reused
    for key in [k for k in store.verdicts if k[0] == SNAP["evaluator_key"]]:
        del store.verdicts[key]
    await monitor._run(_batch(id="b3"), JOB_ID)
    assert store.asked == []                                          # cached predictions are copied, never re-asked
    assert any(k[0] == SNAP["evaluator_key"] for k in store.verdicts)


async def test_random_judged_first_so_capped_batch_stays_uniform(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    store.cap_after = 3
    assert await monitor._run(_batch(), JOB_ID) == "capped"
    plan = _plan_of(store)
    assert store.asked == plan["random"][:3]
    assert not set(store.asked) & (set(plan["to_judge"]) - set(plan["random"]))


async def test_a_capped_batch_still_estimates_from_what_it_judged(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    store.cap_after = 3
    await monitor._run(_batch(), JOB_ID)
    mode = store.acted["modes"][0]
    assert mode["n_sample"] == 3 and mode["raw"] == 0.0


async def test_risk_only_failures_never_lift_the_estimate(monkeypatch):
    probe = FakeStore(40)
    plan = monitor.select_turns(probe.turns, 0.25, {"thumbs_down": monitor.RISK_GROUPS["thumbs_down"]}, 7)
    risk_only = set(plan["risk_groups"]["thumbs_down"]) - set(plan["random"])
    store = FakeStore(40, fails=risk_only).install(monkeypatch)
    await monitor._run(_batch(), JOB_ID)
    mode = store.acted["modes"][0]
    assert mode["raw"] == 0.0 and mode["n_sample"] == len(plan["random"])
    assert mode["flagged"] == len(risk_only) and mode["risk_groups"]["thumbs_down"]["flagged"] == len(risk_only)
    assert store.acted["n_window"] == 40 and store.acted["code_checks"][0]["n"] == 40


async def test_the_results_have_the_shape_the_page_reads(monkeypatch):
    store = FakeStore(40, fails={"t001"}, code_fail={"t002"}).install(monkeypatch)
    await monitor._run(_batch(), JOB_ID)
    res = store.acted
    assert set(res) == {"n_window", "n_random", "n_risk_only", "modes", "code_checks"}
    assert res["n_random"] == 10 and res["n_risk_only"] == len(_plan_of(store)["to_judge"]) - 10
    mode = res["modes"][0]
    assert {"mode_id", "mode_name", "judge_id", "judge_key", "kind", "evaluator_key", "n_sample", "raw", "corrected",
            "ci_low", "ci_high", "threshold", "crossed", "clearly_above", "flagged", "risk_groups", "review_batch_ids",
            "proposed_case_ids", "validity_warning", "reason"} <= set(mode)
    assert mode["review_batch_ids"] == [] and mode["proposed_case_ids"] == []
    flagged = {c["evaluator_key"]: c["flagged"] for c in res["code_checks"]}
    assert flagged[checks.CHECKS[LIVE[0]].evaluator_key] == 1 and sum(flagged.values()) == 1
    last = store.batch_updates[-1]
    assert last["results"] is res and last["usd"] == Decimal("0.05")


async def test_a_resumed_batch_keeps_its_stored_plan(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    stored = {"random": ["t001", "t002"], "risk_groups": {"thumbs_down": []}, "to_judge": ["t001", "t002"]}
    assert await monitor._run(_batch(plan=stored), JOB_ID) == "done"
    assert store.asked == ["t001", "t002"] and not any("plan" in u for u in store.batch_updates)


async def test_an_answer_that_cannot_be_read_is_undecidable_for_the_checks_and_an_error_for_the_judge(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    probe = monitor.select_turns(store.turns, 0.25, {}, 7)
    gone = probe["random"][0]
    store.unreadable = {gone}
    await monitor._run(_batch(risk_groups=[]), JOB_ID)
    ek = checks.CHECKS[LIVE[0]].evaluator_key
    assert store.verdicts[(ek, gone)]["status"] == "undecidable" and store.verdicts[(ek, gone)]["failure_present"] is None
    assert store.verdicts[(SNAP["evaluator_key"], gone)]["status"] == "error"
    assert store.verdicts[(SNAP["evaluator_key"], gone)]["detail"] == {"error": "the answer could not be read"}
    assert gone not in store.asked and len(store.asked) == len(probe["random"]) - 1
    assert store.acted["modes"][0]["n_error"] == 1


async def test_a_failed_judge_call_is_an_error_never_a_pass(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    store.ask_error = TimeoutError("slow")
    await monitor._run(_batch(risk_groups=[]), JOB_ID)
    mine = [v for k, v in store.verdicts.items() if k[0] == SNAP["evaluator_key"]]
    assert mine and {v["status"] for v in mine} == {"error"} and {v["failure_present"] for v in mine} == {None}
    assert {v["detail"]["error"] for v in mine} == {"TimeoutError"}
    assert store.acted["modes"][0]["n_sample"] == 0 and store.acted["modes"][0]["corrected"] is None


async def test_a_save_that_fails_stops_new_calls_and_the_error_is_raised_after_the_calls_in_flight(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    monkeypatch.setattr(judges, "JUDGE_CONCURRENCY", 2)
    store.save_error = RuntimeError("the lab database went away")
    with pytest.raises(RuntimeError, match="went away"):
        await monitor._run(_batch(), JOB_ID)
    assert 1 <= len(store.asked) <= 2 and store.acted is None          # at most the two in flight were ever paid for


async def test_a_judge_that_is_gone_is_skipped(monkeypatch):
    store = FakeStore(40).install(monkeypatch)

    async def gone(judge_id):
        return {}

    monkeypatch.setattr(monitor, "_judge_row", gone)
    assert await monitor._run(_batch(), JOB_ID) == "done"
    assert store.asked == [] and store.acted["modes"][0]["n_sample"] == 0


async def test_a_judge_no_longer_in_use_is_estimated_from_its_own_frozen_test_pairs(monkeypatch):
    store = FakeStore(40).install(monkeypatch)
    asked = []

    async def none_in_use(mode_ids=None):
        return []

    async def pairs(judge_id):
        asked.append(judge_id)
        return ARTIFACT_G_LABELS, ARTIFACT_G_PREDS

    monkeypatch.setattr(monitor, "judges_in_use", none_in_use)
    monkeypatch.setattr(monitor.judges, "test_pairs", pairs)
    await monitor._run(_batch(risk_groups=[]), JOB_ID)
    assert asked == [J1] and store.acted["modes"][0]["n_test"] == 50


async def test_a_code_judge_is_estimated_over_the_whole_window(monkeypatch):
    store = FakeStore(40, code_fail={"t001", "t002"}).install(monkeypatch)
    ek = checks.CHECKS[LIVE[0]].evaluator_key
    snap = dict(SNAP, kind="code", evaluator_key=ek, judge_key="no_answer-v1", mode_name="no_answer", threshold=None)
    await monitor._run(_batch(judges=[snap], risk_groups=[]), JOB_ID)
    mode = store.acted["modes"][0]
    assert mode["kind"] == "code" and mode["n_sample"] == 40 and mode["flagged"] == 2
    assert store.asked == []                                            # a code judge calls no model
    plan = _plan_of(store)                                              # but its planned and flagged answers are kept
    assert store.snapshots == [(ORG_A, plan["to_judge"] + [t for t in ("t001", "t002") if t not in plan["to_judge"]],
                                "monitored")]


async def test_work_batch_records_a_failure_and_finishes_the_job(monkeypatch):
    updates, finished = [], []

    async def load(batch_id):
        return _batch(job_id=JOB_ID)

    async def claim(job_id, *, worker):
        return {"id": job_id}

    async def boom(batch, job_id):
        raise RuntimeError("database went away")

    async def update(batch_id, *, finished=False, **fields):
        updates.append(dict(fields, finished=finished))

    async def finish(job_id, status, error=None):
        finished.append((status, error))

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_run", boom)
    monkeypatch.setattr(monitor, "_update_batch", update)
    monkeypatch.setattr(jobs, "claim", claim)
    monkeypatch.setattr(jobs, "finish", finish)
    await monitor.work_batch("b1")
    assert updates[0] == {"status": "running", "finished": False}
    assert updates[-1]["status"] == "failed" and updates[-1]["finished"] is True
    assert finished == [("failed", "database went away")]


async def test_work_batch_finishes_done_or_capped_and_never_runs_twice(monkeypatch):
    updates, finished, claims, outcome = [], [], [], {"status": "done", "claim": {"id": JOB_ID}}

    async def load(batch_id):
        return None if batch_id == "missing" else _batch(job_id=JOB_ID)

    async def claim(job_id, *, worker):
        claims.append(worker)
        return outcome["claim"]

    async def run(batch, job_id):
        return outcome["status"]

    async def update(batch_id, *, finished=False, **fields):
        updates.append(dict(fields, finished=finished))

    async def finish(job_id, status, error=None):
        finished.append(status)

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_run", run)
    monkeypatch.setattr(monitor, "_update_batch", update)
    monkeypatch.setattr(jobs, "claim", claim)
    monkeypatch.setattr(jobs, "finish", finish)
    await monitor.work_batch("b1")
    outcome["status"] = "capped"
    await monitor.work_batch("b1")
    assert finished == ["done", "capped"] and [u["status"] for u in updates] == ["running", "done", "running", "capped"]
    assert updates[1]["finished"] is True and claims[0] == jobs.worker_id()
    updates.clear()
    finished.clear()
    outcome["claim"] = None                                             # someone else holds the job, or it ended
    await monitor.work_batch("b1")
    await monitor.work_batch("missing")
    assert updates == [] and finished == []


async def test_a_batch_runs_in_an_empty_context_and_is_kept_until_it_ends(monkeypatch):
    request_scope = contextvars.ContextVar("request_scope", default="empty")
    seen = []
    release = asyncio.Event()

    async def fake_work(batch_id):
        seen.append((batch_id, request_scope.get()))
        await release.wait()

    monkeypatch.setattr(monitor, "work_batch", fake_work)
    request_scope.set("the request that started the batch")
    task = monitor.run_in_background("b1")
    await asyncio.sleep(0)
    assert task in monitor._RUNNING and not task.done()
    release.set()
    await task
    await asyncio.sleep(0)
    assert seen == [("b1", "empty")] and task not in monitor._RUNNING


async def test_settle_marks_a_batch_interrupted_when_its_job_was_and_resume_requeues_it(monkeypatch):
    updates, started, resumed = [], [], []

    async def job_get(job_id, *, organization_id):
        return {"status": "interrupted", "created_at": datetime.now(UTC)}

    async def update(batch_id, *, finished=False, **fields):
        updates.append(fields)

    async def load(batch_id):
        return _batch(id=batch_id, status="running", job_id=JOB_ID, organization_id=None)

    async def resume(job_id, *, usd_cap=None):
        resumed.append(job_id)
        return {"id": job_id}

    monkeypatch.setattr(jobs, "get", job_get)
    monkeypatch.setattr(jobs, "resume", resume)
    monkeypatch.setattr(monitor, "_update_batch", update)
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    rows = await monitor._settle([_batch(status="running", job_id=JOB_ID)])
    assert rows[0]["status"] == "interrupted" and updates[0] == {"status": "interrupted"}
    out = await monitor.resume_batch("b9", org=None)
    assert out["batch"]["status"] == "queued" and started == ["b9"] and resumed == [JOB_ID]
    with pytest.raises(monitor.WatchError) as e:
        await monitor.resume_batch("b9", org=ORG_A)                  # another scope reads as not found
    assert e.value.status == 404


async def test_settle_reads_the_job_to_say_what_became_of_a_batch(monkeypatch):
    updates = []
    jobs_by_id = {}

    async def job_get(job_id, *, organization_id):
        return jobs_by_id.get(job_id)

    async def update(batch_id, *, finished=False, **fields):
        updates.append((batch_id, fields["status"]))

    monkeypatch.setattr(jobs, "get", job_get)
    monkeypatch.setattr(monitor, "_update_batch", update)
    old = datetime.now(UTC) - timedelta(seconds=jobs.STALE_AFTER_S + 60)
    jobs_by_id.update({"j-ok": {"status": "running"}, "j-stale": {"status": "queued", "created_at": old},
                       "j-fresh": {"status": "queued", "created_at": datetime.now(UTC)},
                       "j-failed": {"status": "failed"}, "j-cancelled": {"status": "cancelled"},
                       "j-done": {"status": "done"}})
    rows = [_batch(id=f"b-{k}", status="running", job_id=k) for k in jobs_by_id] + [
        _batch(id="b-none", status="queued", job_id="j-missing"), _batch(id="b-fin", status="done", job_id="j-failed"),
        _batch(id="b-nojob", status="running", job_id=None)]
    out = {r["id"]: r["status"] for r in await monitor._settle(rows)}
    assert out == {"b-j-ok": "running", "b-j-stale": "interrupted", "b-j-fresh": "running", "b-j-failed": "failed",
                   "b-j-cancelled": "failed", "b-j-done": "running", "b-none": "queued", "b-fin": "done",
                   "b-nojob": "running"}
    assert sorted(updates) == [("b-j-cancelled", "failed"), ("b-j-failed", "failed"), ("b-j-stale", "interrupted")]


async def test_only_an_interrupted_batch_resumes_and_a_busy_scope_says_so(monkeypatch):
    state = {"status": "done"}

    async def load(batch_id):
        return _batch(id=batch_id, status=state["status"], job_id=JOB_ID, organization_id=ORG_A)

    async def conflict(job_id, *, usd_cap=None):
        raise jobs.JobConflict(job_id)

    started = []
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "run_in_background", lambda batch_id: started.append(batch_id))
    for status in ("done", "capped", "failed"):                          # only an interrupted batch is put back
        state["status"] = status
        with pytest.raises(monitor.WatchError) as e:
            await monitor.resume_batch("b9", org=ORG_A)
        assert e.value.status == 409 and e.value.reason == "not_interrupted"
    state["status"] = "interrupted"
    monkeypatch.setattr(jobs, "resume", conflict)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.resume_batch("b9", org=ORG_A)
    assert e.value.status == 409 and e.value.reason == "batch_running" and started == []

    async def missing(batch_id):
        return None

    monkeypatch.setattr(monitor, "_load_batch", missing)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.resume_batch("nope", org=ORG_A)
    assert e.value.status == 404


# ---- reads: batches with the Due marker, batch detail, chart, code-check trend, prevalence (Phase 7 task 3) ----

import uuid  # noqa: E402


def _row(bid, org, **kw):
    r = {"id": bid, "organization_id": uuid.UUID(org) if org else None, "period_label": "2026-W41",
         "window_start": datetime(2026, 10, 5, tzinfo=UTC), "window_end": datetime(2026, 10, 12, tzinfo=UTC),
         "sample_rate": Decimal("0.25"), "seed": 7, "risk_groups": ["thumbs_down"], "judges": [SNAP], "checks": [],
         "plan": {"random": ["t1", "t2"], "risk_groups": {"thumbs_down": ["t3"]}, "to_judge": ["t1", "t2", "t3"]},
         "results": None, "status": "done", "job_id": None, "usd": Decimal("0.4"), "usd_cap": Decimal("5"),
         "started_by_email": "owner@example.com", "started_at": NOW, "finished_at": NOW, "error": None}
    r.update(kw)
    return r


async def _same(rows):
    return [dict(r) for r in rows]


async def test_company_scope_and_platform_batch_hidden_from_admin(monkeypatch):
    platform, company = _row("b-p", None), _row("b-a", ORG_A)
    seen, flagged_orgs = {}, []

    async def rows(org, limit, before):
        seen["list"] = org
        return [company] if org else [platform, company]

    async def scope_rows(org):
        seen["scope"] = org
        return [company] if org else [platform]

    async def load(bid):
        return {"b-p": platform, "b-a": company}.get(bid)

    async def flagged(eks, ids, *, org, offset, limit):
        flagged_orgs.append(org)
        return []

    monkeypatch.setattr(monitor, "_batch_rows", rows)
    monkeypatch.setattr(monitor, "_scope_batches", scope_rows)
    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    out = await monitor.list_batches(org=ORG_A, now=NOW)
    assert seen == {"list": ORG_A, "scope": ORG_A}
    assert [b["id"] for b in out["batches"]] == ["b-a"] and out["batches"][0]["scope"] == "company"
    assert out["due"] == {"period_label": "2026-W41", "is_due": False}
    for bid, org in (("b-p", ORG_A), ("b-a", ORG_B)):
        with pytest.raises(monitor.WatchError) as e:
            await monitor.batch_detail(bid, org=org)
        assert e.value.status == 404
    await monitor.batch_detail("b-a", org=ORG_A)
    await monitor.batch_detail("b-p", org=None)
    assert flagged_orgs == [ORG_A, None]


async def test_the_platform_owner_sees_every_batch_and_the_due_marker_is_for_the_scope_viewed(monkeypatch):
    platform, company = _row("b-p", None, status="failed"), _row("b-a", ORG_A)
    scopes = []

    async def rows(org, limit, before):
        return [platform, company]

    async def scope_rows(org):
        scopes.append(org)
        return [company] if org else [platform]                 # the platform's own batch failed: last week is still due

    monkeypatch.setattr(monitor, "_batch_rows", rows)
    monkeypatch.setattr(monitor, "_scope_batches", scope_rows)
    monkeypatch.setattr(monitor, "_settle", _same)
    out = await monitor.list_batches(org=None, now=NOW)
    assert [(b["id"], b["scope"]) for b in out["batches"]] == [("b-p", "platform"), ("b-a", "company")]
    assert scopes == [None] and out["due"] == {"period_label": "2026-W41", "is_due": True}
    json.dumps(out)                                              # ids, dates and money are JSON-safe


async def test_list_batches_passes_the_page_to_the_store_and_settles_both_reads(monkeypatch):
    seen = {}
    settled = []

    async def rows(org, limit, before):
        seen.update(org=org, limit=limit, before=before)
        return [_row("b-a", ORG_A)]

    async def scope_rows(org):
        return [_row("b-s", ORG_A)]

    async def settle(rows_):
        settled.append([r["id"] for r in rows_])
        return [dict(r) for r in rows_]

    monkeypatch.setattr(monitor, "_batch_rows", rows)
    monkeypatch.setattr(monitor, "_scope_batches", scope_rows)
    monkeypatch.setattr(monitor, "_settle", settle)
    await monitor.list_batches(org=ORG_A, limit=5, before=NOW, now=NOW)
    assert seen == {"org": ORG_A, "limit": 5, "before": NOW} and settled == [["b-a"], ["b-s"]]


async def test_batch_detail_names_the_group_of_each_flagged_answer(monkeypatch):
    row = _row("b-a", ORG_A)

    async def load(bid):
        return row

    async def flagged(eks, ids, *, org, offset, limit):
        assert eks == [SNAP["evaluator_key"]] and ids == ["t1", "t2", "t3"] and org == ORG_A
        return [{"turn_id": "t2", "evaluator_key": SNAP["evaluator_key"], "critique": "Quotes June, not July.",
                 "question": "How many work orders in July?"},
                {"turn_id": "t3", "evaluator_key": SNAP["evaluator_key"], "critique": "x", "question": "q"}]

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    out = await monitor.batch_detail("b-a", org=ORG_A)
    assert [(f["turn_id"], f["group"], f["mode_name"]) for f in out["flagged"]] == [
        ("t2", "uniform sample", "wrong_period"), ("t3", "rated down by the asker", "wrong_period")]
    assert "plan" not in out["batch"]


async def test_batch_detail_redacts_secrets_before_it_cuts_the_question_and_the_critique(monkeypatch):
    secret = "sk-abcdefghijklmnopqrstuv"

    async def load(bid):
        return _row("b-a", ORG_A)

    async def flagged(eks, ids, *, org, offset, limit):
        return [{"turn_id": "t1", "evaluator_key": SNAP["evaluator_key"], "question": "Use " + secret + " " + "q" * 400,
                 "critique": "The answer repeats Bearer abcdefghijklmnop. " + "c" * 700},
                {"turn_id": "t2", "evaluator_key": SNAP["evaluator_key"], "question": None, "critique": None}]

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    first, second = (await monitor.batch_detail("b-a", org=ORG_A))["flagged"]
    assert secret not in first["question"] and "[redacted key]" in first["question"] and len(first["question"]) == 300
    assert "abcdefghijklmnop" not in first["critique"] and "Bearer [redacted]" in first["critique"]
    assert len(first["critique"]) == 600
    assert (second["question"], second["critique"]) == ("", "")


async def test_a_secret_that_straddles_the_cut_is_redacted_whole_not_left_in_part(monkeypatch):
    secret = "sk-abcdefghijklmnopqrstuv"

    async def load(bid):
        return _row("b-a", ORG_A)

    async def flagged(eks, ids, *, org, offset, limit):
        # the cut falls inside the key: cutting first would leave "sk-abcdef", which no redaction rule can recognise
        return [{"turn_id": "t1", "evaluator_key": SNAP["evaluator_key"], "question": "q" * 290 + " " + secret,
                 "critique": "c" * 590 + " " + secret}]

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    row = (await monitor.batch_detail("b-a", org=ORG_A))["flagged"][0]
    assert "sk-" not in row["question"] and "sk-" not in row["critique"]
    assert len(row["question"]) == 300 and len(row["critique"]) == 600


async def test_batch_detail_can_ask_for_one_failure_mode_and_one_page(monkeypatch):
    other = dict(SNAP, judge_id="33333333-3333-4333-8333-333333333333", mode_id="44444444-4444-4444-8444-444444444444",
                 mode_name="no_answer", evaluator_key="judge:33333333-3333-4333-8333-333333333333")
    seen = {}

    async def load(bid):
        return _row("b-a", ORG_A, judges=[SNAP, other])

    async def flagged(eks, ids, *, org, offset, limit):
        seen.update(eks=eks, offset=offset, limit=limit)
        return [{"turn_id": "t1", "evaluator_key": other["evaluator_key"], "critique": "c", "question": "q"}]

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", _same)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    out = await monitor.batch_detail("b-a", org=ORG_A, mode_id=other["mode_id"], offset=20, limit=10)
    assert seen == {"eks": [other["evaluator_key"]], "offset": 20, "limit": 10}
    assert [(f["mode_name"], f["group"]) for f in out["flagged"]] == [("no_answer", "uniform sample")]
    both = await monitor.batch_detail("b-a", org=ORG_A)
    assert seen["eks"] == [SNAP["evaluator_key"], other["evaluator_key"]]
    assert {j["mode_name"] for j in both["batch"]["judges"]} == {"wrong_period", "no_answer"}


async def test_batch_detail_of_an_unreadable_id_is_a_404_not_a_crash(monkeypatch):
    async def load(bid):
        return None

    monkeypatch.setattr(monitor, "_load_batch", load)
    with pytest.raises(monitor.WatchError) as e:
        await monitor.batch_detail("nope", org=None)
    assert (e.value.status, e.value.reason) == (404, "not_found")


async def test_a_batch_detail_settles_the_batch_it_reads(monkeypatch):
    async def load(bid):
        return _row("b-a", ORG_A, status="running", job_id="j1")

    async def settle(rows_):
        return [dict(rows_[0], status="interrupted")]

    async def flagged(eks, ids, *, org, offset, limit):
        return []

    monkeypatch.setattr(monitor, "_load_batch", load)
    monkeypatch.setattr(monitor, "_settle", settle)
    monkeypatch.setattr(monitor, "_flagged_rows", flagged)
    assert (await monitor.batch_detail("b-a", org=ORG_A))["batch"]["status"] == "interrupted"


async def test_chart_reads_the_latest_batch_per_period(monkeypatch):
    async def mode(mode_id):
        return {"id": M1, "name": "wrong_period"} if mode_id == M1 else None

    async def periods(org):
        assert org is None
        week = {"window_start": datetime(2026, 9, 28, tzinfo=UTC), "window_end": datetime(2026, 10, 5, tzinfo=UTC)}
        return [dict(week, id="b40", period_label="2026-W40", results={"modes": [
                    {"mode_id": M1, "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2, "threshold": 0.15,
                     "crossed": False, "n_sample": 100, "validity_warning": ""}]}),
                dict(week, id="b41", period_label="2026-W41", results={"modes": [{"mode_id": "other"}]})]

    monkeypatch.setattr(monitor, "_mode", mode)
    monkeypatch.setattr(monitor, "_period_batches", periods)
    out = await monitor.chart(org=None, mode_id=M1)
    assert out["mode"] == {"id": M1, "name": "wrong_period"}
    assert [p["batch_id"] for p in out["points"]] == ["b40"] and out["y_max"] == 0.25 and out["threshold"] == 0.15
    with pytest.raises(monitor.WatchError) as e:
        await monitor.chart(org=None, mode_id="not-a-mode")
    assert e.value.status == 404


async def test_a_chart_is_for_the_scope_it_is_asked_for_and_keeps_the_last_periods(monkeypatch):
    asked = []

    async def mode(mode_id):
        return {"id": M1, "name": "wrong_period"}

    async def periods(org):
        asked.append(org)
        return [{"id": f"b{i}", "period_label": f"2026-W{i}", "window_start": datetime(2026, 9, i, tzinfo=UTC),
                 "window_end": datetime(2026, 9, i + 1, tzinfo=UTC),
                 "results": {"modes": [{"mode_id": M1, "corrected": i / 100, "ci_low": 0.0, "ci_high": i / 50,
                                        "threshold": 0.15, "crossed": False, "n_sample": 10}]}} for i in range(1, 6)]

    monkeypatch.setattr(monitor, "_mode", mode)
    monkeypatch.setattr(monitor, "_period_batches", periods)
    out = await monitor.chart(org=ORG_A, mode_id=M1, periods=2)
    assert asked == [ORG_A] and [p["batch_id"] for p in out["points"]] == ["b4", "b5"]
    json.dumps(out)
    none = await monitor.chart(org=ORG_A, mode_id=M1, periods=0)               # at least one period is always shown
    assert [p["batch_id"] for p in none["points"]] == ["b5"]


async def test_a_chart_for_a_mode_no_batch_estimated_is_empty_not_missing(monkeypatch):
    async def mode(mode_id):
        return {"id": M1, "name": "wrong_period"}

    async def periods(org):
        return [{"id": "b1", "period_label": "2026-W40", "window_start": datetime(2026, 9, 28, tzinfo=UTC),
                 "window_end": datetime(2026, 10, 5, tzinfo=UTC), "results": None}]

    monkeypatch.setattr(monitor, "_mode", mode)
    monkeypatch.setattr(monitor, "_period_batches", periods)
    assert await monitor.chart(org=None, mode_id=M1) == {
        "mode": {"id": M1, "name": "wrong_period"}, "points": [], "threshold": None, "y_max": None}


async def test_code_check_trend_fills_every_week(monkeypatch):
    ek = checks.CHECKS[LIVE[0]].evaluator_key

    async def rows(org, since):
        assert org == ORG_A and since == datetime(2026, 10, 5, tzinfo=UTC)
        return [{"evaluator_key": ek, "week_start": datetime(2026, 10, 5), "n": 412, "decided": 412, "flagged": 9}]

    async def validated():
        return {ek}

    monkeypatch.setattr(monitor, "_check_week_rows", rows)
    monkeypatch.setattr(monitor, "_validated_check_keys", validated)
    out = await monitor.code_check_trend(org=ORG_A, periods=2, now=NOW)
    first = next(c for c in out["checks"] if c["evaluator_key"] == ek)
    assert first["validated"] is True and first["label"] == checks.CHECKS[LIVE[0]].label
    assert first["weeks"] == [{"period_label": "2026-W41", "n": 412, "flagged": 9, "rate": 0.0218},
                              {"period_label": "2026-W42", "n": 0, "flagged": 0, "rate": None}]
    assert len(out["checks"]) == len(LIVE)


async def test_a_check_with_no_decided_answer_has_no_rate_and_an_unvalidated_check_says_so(monkeypatch):
    ek = checks.CHECKS[LIVE[0]].evaluator_key

    async def rows(org, since):
        return [{"evaluator_key": ek, "week_start": datetime(2026, 10, 5), "n": 10, "decided": 0, "flagged": 0},
                {"evaluator_key": "code:not_a_live_check@v1", "week_start": datetime(2026, 10, 5), "n": 5, "decided": 5,
                 "flagged": 5},
                {"evaluator_key": ek, "week_start": datetime(2026, 9, 28), "n": 7, "decided": 7, "flagged": 1}]

    async def validated():
        return set()

    monkeypatch.setattr(monitor, "_check_week_rows", rows)
    monkeypatch.setattr(monitor, "_validated_check_keys", validated)
    out = await monitor.code_check_trend(org=None, periods=2, now=NOW)
    first = next(c for c in out["checks"] if c["evaluator_key"] == ek)
    assert first["validated"] is False
    assert first["weeks"][0] == {"period_label": "2026-W41", "n": 10, "flagged": 0, "rate": None}   # all undecidable
    assert all(c["evaluator_key"] != "code:not_a_live_check@v1" for c in out["checks"])             # only live checks
    json.dumps(out)


async def test_the_trend_reads_between_one_and_fifty_two_weeks(monkeypatch):
    sinces = []

    async def rows(org, since):
        sinces.append(since)
        return []

    async def validated():
        return set()

    monkeypatch.setattr(monitor, "_check_week_rows", rows)
    monkeypatch.setattr(monitor, "_validated_check_keys", validated)
    for asked, got in ((0, 1), (-5, 1), (500, 52), (12, 12)):
        out = await monitor.code_check_trend(org=None, periods=asked, now=NOW)
        assert len(out["checks"][0]["weeks"]) == got
    assert sinces[0] == datetime(2026, 10, 12, tzinfo=UTC)                       # one week: the current one


async def test_latest_prevalence_reads_the_latest_platform_batch_that_estimated_the_mode(monkeypatch):
    week = {"window_start": datetime(2026, 9, 28, tzinfo=UTC), "window_end": datetime(2026, 10, 5, tzinfo=UTC)}
    estimated = {"mode_id": M1, "raw": 0.12, "corrected": 0.1, "ci_low": 0.02, "ci_high": 0.2, "confidence": 0.95,
                 "n_sample": 100, "threshold": 0.15, "validity_warning": "", "reason": None}

    async def periods(org):
        assert org is None                                            # the failure report is the platform owner's view
        return [dict(week, id="b40", period_label="2026-W40", results={"modes": [estimated]}),
                dict(week, id="b41", period_label="2026-W41", results={"modes": [{"mode_id": "other"}]})]

    monkeypatch.setattr(monitor, "_period_batches", periods)
    found, reason = await monitor.latest_prevalence(M1)
    assert reason == "" and found["period"] == "2026-W40" and found["batch_id"] == "b40"
    assert (found["corrected"], found["ci_low"], found["ci_high"], found["n_sample"]) == (0.1, 0.02, 0.2, 100)
    none, why = await monitor.latest_prevalence("a-mode-no-batch-estimated")
    assert none is None and why == "Monitoring has not produced an estimate for this mode yet."
    estimated["corrected"], estimated["reason"] = None, "the judge has no held-out test result"
    assert await monitor.latest_prevalence(M1) == (None, "the judge has no held-out test result")


async def test_the_newest_batch_that_estimated_a_mode_wins_and_an_unexplained_gap_has_a_reason(monkeypatch):
    week = {"window_start": datetime(2026, 9, 28, tzinfo=UTC), "window_end": datetime(2026, 10, 5, tzinfo=UTC)}

    def est(corrected, **kw):
        return {"mode_id": M1, "raw": corrected, "corrected": corrected, "ci_low": 0.0, "ci_high": 0.3,
                "confidence": 0.95, "n_sample": 50, "threshold": 0.15, "validity_warning": "thin test set", **kw}

    state = {"modes": [est(0.05), est(0.08)]}

    async def periods(org):
        return [dict(week, id="b39", period_label="2026-W39", results={"modes": [state["modes"][0]]}),
                dict(week, id="b40", period_label="2026-W40", results={"modes": [state["modes"][1]]})]

    monkeypatch.setattr(monitor, "_period_batches", periods)
    found, reason = await monitor.latest_prevalence(M1)
    assert (found["period"], found["corrected"], found["validity_warning"], reason) == ("2026-W40", 0.08, "thin test set", "")
    state["modes"][1] = est(None, reason=None)                                   # newest batch could not estimate, no reason
    assert await monitor.latest_prevalence(M1) == (None, "The latest batch could not estimate this mode.")
    json.dumps(found)


async def test_m5_failure_report_seam_reads_the_latest_prevalence(monkeypatch):
    async def fake(mode_id):
        return {"period": "2026-W41", "corrected": 0.17}, ""

    monkeypatch.setattr(monitor, "latest_prevalence", fake)
    assert await judges.latest_prevalence(M1) == ({"period": "2026-W41", "corrected": 0.17}, "")


# ---- "checked automatically vs by people" (spec 6) ------------------------------------------------------------

def test_automation_buckets_and_validated_sources_only():
    weeks = monitor.weeks_back(NOW, 2)                                # 2026-W41 (complete), 2026-W42 (this week)
    w41, w42 = datetime(2026, 10, 6, 10, tzinfo=UTC), datetime(2026, 10, 13, 10, tzinfo=UTC)
    auto = [{"turn_id": "a1", "at": w41, "source": "code_validated", "validated": True, "is_golden": False},
            {"turn_id": "a2", "at": w41, "source": "judge_frozen", "validated": True, "is_golden": False},
            {"turn_id": "g1", "at": w41, "source": "golden_auto", "validated": True, "is_golden": True},
            {"turn_id": "x1", "at": w41, "source": "code_validated", "validated": False, "is_golden": False},
            {"turn_id": "a3", "at": w42, "source": "code_validated", "validated": True, "is_golden": False}]
    human = [{"turn_id": "a2", "at": w41, "source": "open_coding", "is_golden": False},
             {"turn_id": "h1", "at": w41, "source": "labels", "is_golden": False},
             {"turn_id": "g2", "at": w41, "source": "golden_person", "is_golden": True}]
    out = monitor.bucket_automation(
        weeks=weeks, auto=auto, human=human,
        golden_acc=[{"at": w41, "correct": True}, {"at": w41, "correct": False}],
        live_acc=[{"at": w41, "right": True}],
        modes=[{"mode_id": "m1", "confirmed_at": w41 - timedelta(days=3), "retired_at": None},
               {"mode_id": "m2", "confirmed_at": w42, "retired_at": None}],
        automated=[{"mode_id": "m1", "since": w41}])
    w = out[0]
    assert w["week"] == "2026-W41"
    assert (w["checked_any"], w["auto_only"], w["people_only"], w["both"]) == (5, 2, 2, 1)
    assert w["auto_share"] == 0.4 and w["people_share"] == 0.6 and w["auto_share"] + w["people_share"] == 1
    assert w["auto_by_source"] == {"golden_auto": 1, "code_validated": 1, "judge_frozen": 1}   # x1 left out
    assert w["people_by_source"] == {"open_coding": 1, "labels": 1, "golden_person": 1}
    assert (w["answers_live"], w["answers_golden"]) == (3, 2)
    assert (w["modes_confirmed"], w["modes_automated"]) == (1, 1)
    assert w["golden_accuracy"]["decided"] == 2 and w["golden_accuracy"]["rate"] == 0.5
    lo, hi = stats.wilson_interval(1, 1)
    assert w["live_accuracy"] == {"reviewed": 1, "right": 1, "rate": 1.0, "low": lo, "high": hi}
    assert (out[1]["checked_any"], out[1]["auto_share"], out[1]["modes_confirmed"]) == (1, 1.0, 2)
    empty = monitor.bucket_automation(weeks=weeks[:1], auto=[], human=[], golden_acc=[], live_acc=[], modes=[],
                                      automated=[])[0]
    assert empty["auto_share"] is None and empty["people_share"] is None and empty["golden_accuracy"]["rate"] is None


def _bucket(weeks=None, **kw):
    args = {"auto": [], "human": [], "golden_acc": [], "live_acc": [], "modes": [], "automated": []}
    args.update(kw)
    return monitor.bucket_automation(weeks=weeks or monitor.weeks_back(NOW, 2), **args)


def test_a_week_is_from_its_monday_up_to_the_next_one_and_a_naive_time_is_utc():
    mon41, mon42 = datetime(2026, 10, 5, tzinfo=UTC), datetime(2026, 10, 12, tzinfo=UTC)
    auto = [{"turn_id": "edge-start", "at": mon41, "source": "code_validated", "validated": True, "is_golden": False},
            {"turn_id": "edge-end", "at": mon42, "source": "code_validated", "validated": True, "is_golden": False},
            {"turn_id": "naive", "at": datetime(2026, 10, 11, 23, 59), "source": "judge_frozen", "validated": True,
             "is_golden": False},
            {"turn_id": "no-time", "at": None, "source": "judge_frozen", "validated": True, "is_golden": False}]
    w41, w42 = _bucket(auto=auto)
    assert w41["checked_any"] == 2 and w42["checked_any"] == 1               # Monday 00:00 starts a week, never ends one
    assert w41["auto_by_source"] == {"golden_auto": 0, "code_validated": 1, "judge_frozen": 1}


def test_an_answer_checked_by_two_evaluators_counts_once_and_a_person_beats_the_machine():
    w = datetime(2026, 10, 6, 10, tzinfo=UTC)
    auto = [{"turn_id": "a", "at": w, "source": "code_validated", "validated": True, "is_golden": False},
            {"turn_id": "a", "at": w, "source": "judge_frozen", "validated": True, "is_golden": False},
            {"turn_id": "b", "at": w, "source": "code_validated", "validated": True, "is_golden": False}]
    human = [{"turn_id": "b", "at": w, "source": "labels", "is_golden": False},
             {"turn_id": "b", "at": w, "source": "labels", "is_golden": False}]
    out = _bucket(auto=auto, human=human)[0]
    assert (out["checked_any"], out["auto_only"], out["people_only"], out["both"]) == (2, 1, 0, 1)
    assert out["auto_share"] == 0.5 and out["people_share"] == 0.5
    assert out["auto_by_source"]["code_validated"] == 2 and out["auto_by_source"]["judge_frozen"] == 1
    assert out["people_by_source"]["labels"] == 1                             # one answer, however many labels


def test_the_shares_always_add_up_to_one_and_an_unvalidated_check_alone_means_nothing_checked():
    w = datetime(2026, 10, 6, 10, tzinfo=UTC)
    third = _bucket(auto=[{"turn_id": "a", "at": w, "source": "code_validated", "validated": True, "is_golden": False}],
                    human=[{"turn_id": t, "at": w, "source": "labels", "is_golden": False} for t in ("b", "c")])[0]
    assert third["auto_share"] == 0.3333 and third["people_share"] == 0.6667
    assert round(third["auto_share"] + third["people_share"], 4) == 1
    only_raw = _bucket(auto=[{"turn_id": "x", "at": w, "source": "code_validated", "validated": False,
                              "is_golden": False}])[0]
    assert only_raw["checked_any"] == 0 and only_raw["auto_share"] is None and only_raw["people_share"] is None


def test_a_mode_counts_once_confirmed_and_not_after_it_is_retired_and_is_automated_only_when_confirmed():
    w41, w42 = datetime(2026, 10, 6, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC)
    modes = [{"mode_id": "kept", "confirmed_at": w41, "retired_at": None},
             {"mode_id": "retired-early", "confirmed_at": w41 - timedelta(days=9), "retired_at": w41},
             {"mode_id": "retired-later", "confirmed_at": w41, "retired_at": w42},
             {"mode_id": "unconfirmed", "confirmed_at": None, "retired_at": None}]
    automated = [{"mode_id": "kept", "since": w41}, {"mode_id": "kept", "since": w41 + timedelta(days=1)},
                 {"mode_id": "unconfirmed", "since": w41}, {"mode_id": "retired-later", "since": w42}]
    first, second = _bucket(modes=modes, automated=automated)
    assert (first["modes_confirmed"], first["modes_automated"]) == (2, 1)       # kept + retired-later; only kept is automated
    assert (second["modes_confirmed"], second["modes_automated"]) == (1, 1)  # retired-later retired inside week 2


def test_accuracy_counts_only_what_happened_in_the_week_with_a_wilson_interval():
    w41, w42 = datetime(2026, 10, 6, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC)
    golden = [{"at": w41, "correct": True}] * 9 + [{"at": w41, "correct": False}, {"at": w42, "correct": False}]
    live = [{"at": w41, "right": True}, {"at": w41, "right": False}, {"at": w42, "right": True}]
    first, second = _bucket(golden_acc=golden, live_acc=live)
    assert first["golden_accuracy"]["decided"] == 10 and first["golden_accuracy"]["correct"] == 9
    assert first["golden_accuracy"]["rate"] == 0.9
    lo, hi = stats.wilson_interval(9, 10)
    assert (first["golden_accuracy"]["low"], first["golden_accuracy"]["high"]) == (lo, hi) and lo < 0.9 < hi
    assert first["live_accuracy"]["reviewed"] == 2 and first["live_accuracy"]["rate"] == 0.5
    assert second["golden_accuracy"]["decided"] == 1 and second["golden_accuracy"]["rate"] == 0.0
    assert second["live_accuracy"]["rate"] == 1.0
    json.dumps(first)                                                          # the week is JSON-ready as built


def test_a_golden_answer_is_never_counted_as_a_live_answer():
    w = datetime(2026, 10, 6, 10, tzinfo=UTC)
    out = _bucket(auto=[{"turn_id": "g1", "at": w, "source": "golden_auto", "validated": True, "is_golden": True}],
                  human=[{"turn_id": "g1", "at": w, "source": "golden_person", "is_golden": True},
                         {"turn_id": "L1", "at": w, "source": "labels", "is_golden": False}])[0]
    assert (out["answers_golden"], out["answers_live"], out["checked_any"]) == (1, 1, 2)
    assert out["both"] == 1 and out["people_only"] == 1 and out["auto_only"] == 0


async def test_automation_trend_reads_the_whole_span_once(monkeypatch):
    seen = {}

    async def rows(org, start, end):
        seen.update(org=org, start=start, end=end)
        return {"auto": [], "human": [], "golden_acc": [], "live_acc": [], "modes": [], "automated": []}

    monkeypatch.setattr(monitor, "_automation_rows", rows)
    out = await monitor.automation_trend(org=ORG_A, weeks=3, now=NOW)
    assert seen == {"org": ORG_A, "start": datetime(2026, 9, 28, tzinfo=UTC), "end": datetime(2026, 10, 19, tzinfo=UTC)}
    assert [w["week"] for w in out["weeks"]] == ["2026-W40", "2026-W41", "2026-W42"]
    assert set(out["definitions"]) >= {"auto_share", "people_share", "golden_accuracy", "live_accuracy"}


async def test_automation_trend_clamps_the_span_and_hands_the_store_rows_to_the_buckets(monkeypatch):
    calls = []
    w41 = datetime(2026, 10, 6, 10, tzinfo=UTC)

    async def rows(org, start, end):
        calls.append((org, start, end))
        return {"auto": [{"turn_id": "a1", "at": w41, "source": "code_validated", "validated": True, "is_golden": False}],
                "human": [], "golden_acc": [], "live_acc": [], "modes": [], "automated": []}

    monkeypatch.setattr(monitor, "_automation_rows", rows)
    for asked, got in ((0, 1), (99, 52), (12, 12)):
        out = await monitor.automation_trend(org=None, weeks=asked, now=NOW)
        assert len(out["weeks"]) == got
    assert calls[0][1:] == (datetime(2026, 10, 12, tzinfo=UTC), datetime(2026, 10, 19, tzinfo=UTC))
    again = await monitor.automation_trend(org=None, weeks=2, now=NOW)
    assert again["weeks"][0]["week"] == "2026-W41" and again["weeks"][0]["auto_share"] == 1.0
    assert again["definitions"] == monitor.AUTOMATION_DEFINITIONS
    json.dumps(again)


# ---- golden answers as a source of automatic checks: the FINAL graded row of each case rep ---------------------

def _graded(turn, outcome, *, source="grader", det="all_found", evaluation="objective", reading=None, validated_outcome=None,
            version="v1", at=None):
    return {"turn_id": turn, "at": at or datetime(2026, 10, 6, 10, tzinfo=UTC), "outcome": outcome, "source": source,
            "det_status": det, "evaluation": evaluation, "reading": reading, "validated_outcome": validated_outcome,
            "grader_version": version}


def test_golden_answers_read_as_the_golden_views_read_them_and_only_a_decided_check_is_automatic():
    rows = [_graded("decisive-pass", "pass", det="all_found"),
            _graded("decisive-fail", "fail", det="mismatch"),
            _graded("model-only", "pass", det="none"),                        # the model alone: not validated for objective
            _graded("unclear", "fail", det="unclear"),
            _graded("chat-error", "fail", source="runner", det="none"),         # no model: the answer was an error
            _graded("waiting", "unsure", evaluation="judgment", det="none", reading="needs_person",
                    validated_outcome="pass", version="g-old"),
            _graded("trusted", "unsure", evaluation="judgment", det="none", reading="needs_person",
                    validated_outcome="fail", version="g-ok"),
            _graded("degraded", "degraded"), _graded("stale", "stale_reference"), _graded("error", "error"),
            _graded("pending", "pending"), _graded("just-unsure", "unsure", det="none")]
    auto, acc = monitor.golden_records(rows, validated={"g-ok"})
    by_turn = {a["turn_id"]: a for a in auto}
    assert set(by_turn) == {"decisive-pass", "decisive-fail", "model-only", "unclear", "chat-error", "trusted"}
    assert {t: a["validated"] for t, a in by_turn.items()} == {
        "decisive-pass": True, "decisive-fail": True, "model-only": False, "unclear": False, "chat-error": True,
        "trusted": True}
    assert all(a["source"] == "golden_auto" and a["is_golden"] is True for a in auto)
    assert [(a["correct"]) for a in acc] == [True, False, True, False, False, False]      # trusted read as its fail
    assert len(acc) == len(auto) and all(isinstance(a["at"], datetime) for a in acc)


def test_a_judgment_verdict_the_grader_was_not_yet_trusted_on_is_not_counted_at_all():
    auto, acc = monitor.golden_records([_graded("t", "unsure", evaluation="judgment", det="none", reading="needs_person",
                                                validated_outcome="pass", version="g-old")], validated=set())
    assert auto == [] and acc == []
    auto, acc = monitor.golden_records([], validated={"x"})
    assert auto == [] and acc == []


def test_every_read_names_only_trace_labs_own_tables():
    sqls = [monitor._AUTO_VERDICTS_SQL, monitor._GOLDEN_FINAL_SQL, monitor._EVAL_REPS_SQL, *monitor._HUMAN_SQL,
            monitor._LIVE_ACC_SQL, monitor._MODES_SQL, monitor._AUTOMATED_SQL]
    for sql in sqls:
        assert "tracelab." in sql and "plenum_" not in sql and "turn_meta" not in sql, sql
    for cols in (monitor._LIST_COLS, monitor._DUE_COLS):                         # a list never carries the sample's ids
        assert "plan" not in cols.split(", ") and "id" in cols.split(", ")
