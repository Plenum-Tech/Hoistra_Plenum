"""The user-experience and domain levels (11 Nov 2026, task plan B3), the parts that need no database.

UX: "concise, complete, right template, clear wording on failures and denials", by a model judge plus people.
Domain: "correct against domain rules", by a rubric plus a subject-matter expert.

The model only fills in criteria. The verdict is decided here, in code, from those criteria and from things code can
check on its own (the answer's length, the phrases it must and must not contain), so a judge that calls a long or
incomplete answer fine cannot pass it."""
from __future__ import annotations

from trace_lab import eval_judges

EXPECTED = {"plan": [{"call_id": "c1", "domain": "compliance", "tool": None, "params": {}, "depends_on": []}],
            "failure_handling": None, "answer_format": "success", "must_include": ["Gas Safety", "EICR"],
            "must_not_include": ["I think"], "answer": None}
ANSWER = "Two certificates expire within 30 days: the Gas Safety record and the EICR."
GOOD = {"concise": True, "complete": True, "template": "success", "failure_wording": "n/a", "reason": "Short and complete."}


def test_what_code_can_check_without_a_model():
    pre = eval_judges.ux_precheck(ANSWER, EXPECTED)
    assert pre == {"answer_words": 13, "missing": [], "forbidden_present": []}
    pre = eval_judges.ux_precheck("I think one gas safety record expires.", EXPECTED)
    assert pre["missing"] == ["EICR"], "matching ignores case, so Gas Safety is found"
    assert pre["forbidden_present"] == ["I think"]
    assert eval_judges.ux_precheck("", {})["answer_words"] == 0


def test_all_four_criteria_met_passes_with_a_full_score():
    verdict, value, details = eval_judges.ux_verdict(GOOD, EXPECTED, eval_judges.ux_precheck(ANSWER, EXPECTED))
    assert (verdict, value) == ("pass", 1.0)
    assert details["criteria"] == {"concise": True, "complete": True, "template": True, "failure_wording": True}
    assert details["answer_words"] == 13 and details["evaluator"] == "ux-1"


def test_a_missing_phrase_fails_even_when_the_model_says_the_answer_is_complete():
    pre = eval_judges.ux_precheck("Two certificates expire: the Gas Safety record and one more.", EXPECTED)
    verdict, value, details = eval_judges.ux_verdict(GOOD, EXPECTED, pre)
    assert verdict == "fail" and value == 0.75 and details["criteria"]["complete"] is False
    assert details["missing"] == ["EICR"]


def test_a_phrase_that_must_not_appear_fails_completeness():
    pre = eval_judges.ux_precheck(ANSWER + " I think.", EXPECTED)
    assert eval_judges.ux_verdict(GOOD, EXPECTED, pre)[0] == "fail"


def test_the_wrong_template_fails():
    expected = {**EXPECTED, "answer_format": "partial_success"}
    verdict, _, details = eval_judges.ux_verdict(GOOD, expected, eval_judges.ux_precheck(ANSWER, expected))
    assert verdict == "fail" and details["criteria"]["template"] is False
    assert details["template"] == {"expected": "partial_success", "found": "success"}


def test_an_answer_over_the_word_limit_is_not_concise_whatever_the_model_says():
    long = " ".join(["word"] * (eval_judges.CONCISE_WORDS + 1)) + " Gas Safety EICR"
    verdict, _, details = eval_judges.ux_verdict(GOOD, EXPECTED, eval_judges.ux_precheck(long, EXPECTED))
    assert verdict == "fail" and details["criteria"]["concise"] is False
    assert eval_judges.CONCISE_WORDS == 250


def test_unclear_wording_on_a_failure_fails_and_a_reply_with_holes_is_read_as_no():
    verdict, value, _ = eval_judges.ux_verdict({**GOOD, "failure_wording": "unclear"}, EXPECTED,
                                               eval_judges.ux_precheck(ANSWER, EXPECTED))
    assert verdict == "fail" and value == 0.75
    verdict, value, details = eval_judges.ux_verdict({}, EXPECTED, eval_judges.ux_precheck(ANSWER, EXPECTED))
    assert verdict == "fail" and details["criteria"]["concise"] is False


def test_a_domain_reply_with_no_violations_passes_and_one_with_any_fails_and_asks_for_an_expert():
    ok = eval_judges.domain_verdict({"violations": [], "confidence": "high", "reason": "Consistent with the rules."})
    assert ok[0] == "pass" and ok[1] == 1.0 and ok[2]["needs_sme"] is False
    bad = eval_judges.domain_verdict({"violations": [{"rule": "R-1", "why": "Calls a lapsed certificate current."}],
                                      "confidence": "high", "reason": "One rule broken."})
    assert bad[0] == "fail" and bad[1] == 0.0 and bad[2]["needs_sme"] is True
    assert bad[2]["violations"] == [{"rule": "R-1", "why": "Calls a lapsed certificate current."}]
    unsure = eval_judges.domain_verdict({"violations": [], "confidence": "low", "reason": "The rules do not cover it."})
    assert unsure[0] == "pass" and unsure[2]["needs_sme"] is True, "a pass the judge is unsure of still goes to an expert"
    assert eval_judges.domain_verdict({})[2]["needs_sme"] is True


def test_the_rubric_is_the_scenarios_rules_and_the_knowledge_of_each_area_the_turn_uses(monkeypatch):
    read = []

    def fake(agent, name):
        read.append((agent, name))
        return "A certificate past its expiry date is lapsed." if agent == "compliance" else ""

    monkeypatch.setattr(eval_judges, "READ_DOC", fake)
    scenario = {"domain_rules": [{"id": "R-1", "text": "Gas Safety records are renewed every 12 months."}]}
    rubric = eval_judges.domain_rubric(scenario, EXPECTED)
    assert [r["id"] for r in rubric] == ["R-1", "compliance:knowledge"]
    assert read == [("compliance", "domain_compliance_knowledge")]
    energy = {**EXPECTED, "plan": [{"call_id": "c1", "domain": "energy_intelligence"}]}
    assert eval_judges.domain_rubric({"domain_rules": []}, energy) == [], "an area with no knowledge document has no rubric"


def test_a_knowledge_document_that_cannot_be_read_is_left_out_not_fatal(monkeypatch):
    def broken(agent, name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(eval_judges, "READ_DOC", broken)
    assert eval_judges.domain_rubric({"domain_rules": []}, EXPECTED) == []


def test_the_evidence_given_to_the_domain_judge_is_the_tool_outputs_cut_to_a_limit():
    nodes = [{"id": "r", "type": "agent", "name": "chat", "output": "the answer"},
             {"id": "t", "type": "tool", "name": "list_certificates", "output": {"rows": [{"id": "CERT-1"}]}},
             {"id": "g", "type": "generation", "name": "analyst", "output": {"text": "prose"}},
             {"id": "u", "type": "tool", "name": "big", "output": "x" * 20000}]
    text = eval_judges.evidence_text(nodes)
    assert "list_certificates" in text and "CERT-1" in text and "prose" not in text
    assert len(text) <= eval_judges.EVIDENCE_CHARS + 50
    assert eval_judges.evidence_text([]) == ""
