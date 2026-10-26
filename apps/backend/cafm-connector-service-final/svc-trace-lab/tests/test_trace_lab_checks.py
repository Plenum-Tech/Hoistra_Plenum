"""Trace Lab code checks (spec: Judges, code checks ... section 2). 15 Oct 2026.

Golden Rule G0 adaptation: gate spans are not captured, so there is no gate_draft_failed check and
gate_withheld reads only the answer check's own wording in the final answer."""
from __future__ import annotations

import hashlib

from trace_lab import checks
from trace_lab.checks import NormalizedTurn


def _turn(**kw) -> NormalizedTurn:
    base = {"turn_id": "turn-0000000000000001", "question": "How many open jobs at B-301?",
            "answer": "There are 758 open jobs."}
    base.update(kw)
    return NormalizedTurn(**base)


def _tool(output, *, name="query_table", seq=2, truncated=False, error=None):
    return {"span_id": f"s{seq}", "seq": seq, "name": name, "agent": "udr", "input": {}, "output": output,
            "error": error, "truncated": truncated}


def _status(key, turn, params=None):
    return checks.run_check(checks.CHECKS[key], turn, params).status


def test_registry_keys_and_versions_pinned():
    # A behaviour change to any check bumps its version here AND in checks.py: the version is part
    # of the evaluator key and of a code judge's prompt_hash, so a changed check is a new evaluator.
    assert {k: (c.version, c.scope) for k, c in checks.CHECKS.items()} == {
        "no_answer": (1, "live"), "invented_identifiers": (1, "live"), "untraced_figures": (1, "live"),
        "contradicts_results": (1, "live"), "refusal": (1, "live"),
        "gate_withheld": (1, "live"), "expected_values": (1, "case"), "must_contain": (1, "case"),
        "must_not_contain": (1, "case")}
    assert list(checks.CHECKS) == ["no_answer", "invented_identifiers", "untraced_figures", "contradicts_results",
                                   "refusal", "gate_withheld", "expected_values",
                                   "must_contain", "must_not_contain"]
    assert "gate_draft_failed" not in checks.CHECKS
    assert checks.CHECKS["no_answer"].evaluator_key == "code:no_answer@v1"
    assert checks.code_hash("no_answer", 1) == hashlib.sha256(b"code:no_answer@v1").hexdigest()[:12]
    assert len(checks.run_live_checks(_turn())) == 6
    assert set(checks.run_live_checks(_turn())) == {
        "no_answer", "invented_identifiers", "untraced_figures", "contradicts_results", "refusal", "gate_withheld"}
    for c in checks.CHECKS.values():
        assert c.label and "_" not in c.label and "UDR" not in c.label
        assert "_" not in c.description and "UDR" not in c.description


def test_no_answer():
    assert _status("no_answer", _turn()) == "pass"
    assert _status("no_answer", _turn(ok=False, error="timeout")) == "fail"
    assert _status("no_answer", _turn(answer="   ")) == "fail"
    assert _status("no_answer", _turn(answer="gate_interrupt")) == "fail"


def test_invented_identifiers():
    r = checks.run_check(checks.CHECKS["invented_identifiers"],
                         _turn(answer="AHU-09 needs a filter.", tools=(_tool({"rows": [{"code": "AHU-01"}]}),)))
    assert r.status == "fail" and "AHU-09" in r.findings[0] and r.failure_present == 1
    asked = _turn(question="Is AHU-09 overdue?", answer="AHU-09 was not found.", tools=(_tool({"rows": []}),))
    assert _status("invented_identifiers", asked) == "pass"
    assert _status("invented_identifiers", _turn(answer="Checked by EL-UDR.", tools=(_tool({"rows": []}),))) == "pass"
    cut = _turn(answer="AHU-09 needs a filter.", tools=(_tool({"rows": [{"code": "AHU-01"}]}, truncated=True),))
    assert _status("invented_identifiers", cut) == "undecidable"
    seen = _turn(answer="AHU-01 needs a filter.", tools=(_tool({"rows": [{"code": "AHU-01"}]}),))
    assert _status("invented_identifiers", seen) == "pass"
    assert _status("invented_identifiers", _turn(answer="")) == "not_applicable"


def test_untraced_figures():
    assert _status("untraced_figures", _turn(answer="There are 758 open jobs.", tools=(_tool({"count": 758}),))) == "pass"
    r = checks.run_check(checks.CHECKS["untraced_figures"],
                         _turn(answer="There are 791 open jobs in 2026.", tools=(_tool({"count": 758}),)))
    assert r.status == "fail" and r.findings == ("States 791, which no step returned.",)
    cut = _turn(answer="There are 791 open jobs.", tools=(_tool({"count": 758}, truncated=True),))
    assert _status("untraced_figures", cut) == "undecidable"
    assert _status("untraced_figures", _turn(answer="There are 791 open jobs.")) == "not_applicable"
    assert _status("untraced_figures", _turn(answer="There are 791 open jobs.", spans_purged=True)) == "undecidable"


def test_contradicts_results():
    rows = {"rows": [{"code": "AST-1", "replacement_value": 5000}, {"code": "AST-2", "replacement_value": 7000}]}
    r = checks.run_check(checks.CHECKS["contradicts_results"],
                         _turn(question="Replacement cost of grade 4 assets?",
                               answer="Replacement cost unavailable for these assets.", tools=(_tool(rows),)))
    assert r.status == "fail" and "unavailable" in r.findings[0]
    assert _status("contradicts_results", _turn(answer="AST-1 costs 5000.", tools=(_tool(rows),))) == "pass"
    assert _status("contradicts_results", _turn(answer="Replacement cost unavailable.")) == "not_applicable"


def test_gate_withheld_reads_only_the_answer_text():
    # No gate spans under G0: the only evidence is the answer check's own wording in the final answer.
    legacy = "758 jobs.\n\n_Automated accuracy check: flagged for a quick human verification — please double-check._"
    r = checks.run_check(checks.CHECKS["gate_withheld"], _turn(answer=legacy))
    assert r.status == "fail" and r.findings == ("The answer carries the answer check's own warning.",)
    assert r.detail == {"legacy_text": "flagged for a quick human verification"}
    could_not_run = ("758 jobs.\n\n_The automated accuracy check could not run on this answer, so it has not been "
                     "independently verified against the retrieved records._")
    assert _status("gate_withheld", _turn(answer=could_not_run)) == "fail"
    withheld = "I found related data but couldn't fully verify an answer (counts differ). Try naming the table."
    assert _status("gate_withheld", _turn(answer=withheld)) == "fail"
    assert _status("gate_withheld", _turn(answer=legacy.upper())) == "fail"       # matching ignores case
    assert _status("gate_withheld", _turn()) == "not_applicable"
    assert _status("gate_withheld", _turn(answer="")) == "not_applicable"


def test_refusal_excludes_withheld_text():
    assert _status("refusal", _turn(answer="I'm unable to access the vendor ledger.")) == "fail"
    assert _status("refusal", _turn(answer="I can’t help with that request.")) == "fail"
    withheld = "I found related data but couldn't fully verify an answer (counts differ). Try naming the table."
    assert _status("refusal", _turn(answer=withheld)) == "pass"
    assert _status("refusal", _turn()) == "pass"
    assert _status("refusal", _turn(answer="")) == "not_applicable"


def test_expected_values():
    ev = checks.CHECKS["expected_values"]
    assert checks.run_check(ev, _turn(answer="There are 786 open jobs."), {"values": "786"}).status == "pass"
    assert checks.run_check(ev, _turn(answer="There are seven stock-outs."), {"values": "7"}).status == "pass"
    assert checks.run_check(ev, _turn(answer="Spend was £787,412 last year."), {"values": "£787,412"}).status == "pass"
    r = checks.run_check(ev, _turn(answer="There are 783 open jobs."), {"values": "786"})
    assert r.status == "fail" and r.findings[0].startswith("Expected 786")
    assert checks.run_check(ev, _turn()).status == "not_applicable"


def test_must_contain_and_must_not_contain():
    mc, mn = checks.CHECKS["must_contain"], checks.CHECKS["must_not_contain"]
    t = _turn(answer="Ostley   Power Services attends on Monday.")
    assert checks.run_check(mc, t, {"text": "ostley power services"}).status == "pass"
    assert checks.run_check(mc, t, {"text": "Pennard Fire"}).status == "fail"
    assert checks.run_check(mn, t, {"text": "Pennard Fire"}).status == "pass"
    assert checks.run_check(mn, t, {"text": "OSTLEY POWER"}).status == "fail"
    assert checks.run_check(mc, t).status == "not_applicable"
    assert checks.run_check(mn, t).status == "not_applicable"


def test_is_truncated_recognises_three_markers():
    assert checks.is_truncated("x" * 10 + "… [4200 more chars]")
    assert checks.is_truncated({"_truncated": "{\"rows\": ["})
    assert checks.is_truncated({"row_count": 80, "rows": [], "truncated": 30})
    assert checks.is_truncated({"rows": [{"note": "a… [12 more chars]"}]})
    assert not checks.is_truncated({"row_count": 3, "rows": [{"a": 1}]})
    assert not checks.is_truncated({"truncated": False}) and not checks.is_truncated("plain")


def test_check_crash_is_undecidable_never_pass():
    def boom(turn, params):
        raise KeyError("rows")

    c = checks.CodeCheck("boom", 1, "Boom", "Always crashes.", "live", boom)
    r = checks.run_check(c, _turn())
    assert r.status == "undecidable" and r.findings == ("check failed: KeyError",) and r.failure_present is None


def test_a_bad_status_is_undecidable_and_findings_are_bounded():
    bad = checks.CodeCheck("bad", 1, "Bad", "Returns a bad status.", "live",
                           lambda t, p: checks.CheckResult("maybe"))  # type: ignore[arg-type]
    assert checks.run_check(bad, _turn()).status == "undecidable"
    many = checks.CodeCheck("many", 1, "Many", "Many long findings.", "live",
                            lambda t, p: checks.CheckResult("fail", tuple("x" * 400 for _ in range(12))))
    r = checks.run_check(many, _turn())
    assert len(r.findings) == checks.FINDINGS_MAX == 8 and all(len(f) == checks.FINDING_CHARS for f in r.findings)


def test_turn_from_rows_reads_the_stored_shapes():
    turn = {"turn_id": "turn-abc", "session_id": "s1", "organization_id": "0b1c2d3e-0000-4000-8000-000000000001",
            "question": "wrapped prompt", "answer": "758 jobs", "ok": True, "tool_calls": 1}
    spans = [
        {"id": "r", "seq": 0, "kind": "turn", "name": "turn"},
        {"id": "m", "seq": 1, "kind": "llm", "name": "model", "output": {"content": "MODEL-PRIVATE"}},
        {"id": "t", "seq": 2, "kind": "tool", "name": "query_table", "agent": "udr", "input": {"table": "work_orders"},
         "output": {"count": 758}},
        {"id": "d", "seq": 3, "kind": "db", "name": "count jobs",
         "input": {"sql": "SELECT count(*) FROM w", "params": {}, "label": "count jobs"},
         "output": {"row_count": 80, "rows": [{"n": 1}], "truncated": 30}},
    ]
    meta = {"asker_question": "How many open jobs?", "context_text": "Page: Maintenance", "channel": "chat"}
    t = checks.turn_from_rows(turn, spans, meta, session_kind="observed",
                              prior=[{"turn_id": "turn-0", "question": "Hi", "answer": "Hello"}])
    assert t.question == "How many open jobs?" and t.page_context == "Page: Maintenance"
    assert [x["name"] for x in t.tools] == ["query_table"]
    assert t.db[0]["truncated"] is True and t.db[0]["row_count"] == 80 and t.db[0]["label"] == "count jobs"
    assert t.gates == ()
    assert "MODEL-PRIVATE" not in repr(t) and t.spans_purged is False and t.prior[0]["answer"] == "Hello"
    assert checks.turn_from_rows(turn, [], None).spans_purged is True


def test_turn_from_rows_orders_spans_and_parses_json_strings():
    turn = {"turn_id": "turn-abc", "question": "q", "answer": "a" * checks.ANSWER_CAP}
    spans = [
        {"id": "b", "seq": 5, "kind": "tool", "name": "second", "output": '{"rows": [{"code": "AHU-01"}]}'},
        {"id": "a", "seq": 3, "kind": "tool", "name": "first", "output": "not json {"},
    ]
    t = checks.turn_from_rows(turn, spans)
    assert [x["name"] for x in t.tools] == ["first", "second"]
    assert t.tools[1]["output"] == {"rows": [{"code": "AHU-01"}]} and t.tools[0]["output"] == "not json {"
    assert t.answer_truncated is True and t.question == "q"
