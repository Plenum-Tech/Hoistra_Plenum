"""grader.grade: the value check first, then the LLM grader (skill lab's _ask_json, faked here: no model is ever
called), critique before verdict, strict parsing that never defaults to pass, refusals degraded and never failed, a
judgment case unsure until the grader is validated, and the grader's cost measured on its own ledger in an empty
context, never on the answer's trace. No database, no network."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from src.agents import llm_cost, skill_lab, trace
from trace_lab import golden, grader

CASE = {"case_key": "21", "question": "How many work orders were raised in August 2026?", "followups": [],
        "evaluation": "objective", "expected_answer": "759 work orders: 733 reactive and 26 inspections.",
        "expected_values": golden.parse_expected_values("759; !Pennard Fire Services"), "verified_on": None,
        "requirement_ids": [], "notes": "SECRET NOTE FOR PEOPLE ONLY"}
CRITIQUE = "The right answer says 759 work orders; the answer says 759, which matches."
STORED = ("pending", "pass", "fail", "unsure", "degraded", "stale_reference", "error")
PROMPTS_SHA256 = "73012209148e50e585d89d87624dbc19a7cc4c1ba80949f917310735301e641e"


def captured(answer="There were 759 work orders.", *, error=None, refused=(), cards=()):
    return SimpleNamespace(answer=answer, error=error, refused=list(refused), cards=list(cards), turn_id="turn-1")


def fake_model(monkeypatch, replies, *, usd_each=0.0):
    """Replace skill_lab._ask_json; each call takes the next reply (an exception is raised)."""
    seen = []

    async def ask_json(system, prompt, schema, *, role, env_model, max_tokens=1500):
        seen.append({"system": system, "prompt": prompt, "schema": schema, "role": role, "env_model": env_model,
                     "max_tokens": max_tokens})
        if usd_each:
            llm_cost.record(role, "claude-sonnet-5", {"input_tokens": int(usd_each * 1e6 / 2), "output_tokens": 0}, 5)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(skill_lab, "_ask_json", ask_json)
    return seen


@pytest.fixture(autouse=True)
def judge_model(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-sonnet-5")


def test_the_prompts_are_verbatim_and_the_critique_comes_first():
    joined = "\n".join((grader.GRADER_SYSTEM, grader.GRADER_OBJECTIVE, grader.GRADER_JUDGMENT))
    assert hashlib.sha256(joined.encode()).hexdigest() == PROMPTS_SHA256, "the grader prompts are verbatim: never edit"
    assert grader.GRADER_SYSTEM.endswith("Write your critique before your verdict.")
    assert list(grader.GRADER_SCHEMA["properties"]) == ["critique", "mismatches", "verdict"]
    assert grader.GRADER_SCHEMA["required"] == ["critique", "mismatches", "verdict"]
    assert grader.GRADER_SCHEMA["properties"]["verdict"]["enum"] == ["pass", "fail", "unsure"]
    assert set(grader.STORED.values()) <= set(STORED)


def test_the_model_and_the_version(monkeypatch):
    from src.config import settings

    assert grader.grader_model() == "claude-sonnet-5"
    monkeypatch.setenv("TRACE_LAB_JUDGE_MODEL", "claude-opus-5")
    v = grader.grader_version()
    assert grader.grader_model() == "claude-opus-5" and len(v) == 12
    assert v == grader.grader_version("claude-opus-5") != grader.grader_version("claude-sonnet-5")
    monkeypatch.delenv("TRACE_LAB_JUDGE_MODEL")
    monkeypatch.setattr(settings, "compliance_summary_model", "claude-haiku-4-5")
    assert grader.grader_model() == "claude-haiku-4-5"
    assert grader.price_known("claude-sonnet-5") and not grader.price_known("no-such-model")
    assert grader.typical_usd("claude-sonnet-5") == pytest.approx(0.022) and grader.typical_usd("no-such-model") is None


async def test_a_right_answer_passes_with_the_value_check_first(monkeypatch):
    seen = fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    g = await grader.grade(CASE, captured())
    assert (g.outcome, g.verdict, g.reading, g.validated_outcome, g.source) == ("pass", "pass", "pass", "pass", "grader")
    assert g.value_check["status"] == "all_found" and g.critique == CRITIQUE and g.mismatches == ()
    assert (g.grader_version, g.model) == (grader.grader_version(), "claude-sonnet-5")
    call = seen[0]
    assert (call["role"], call["env_model"], call["max_tokens"]) == ("tracelab_grader", "TRACE_LAB_JUDGE_MODEL", 1200)
    assert call["system"] == grader.GRADER_SYSTEM and call["schema"] == grader.GRADER_SCHEMA
    assert "SECRET NOTE" not in call["prompt"] and "all_found" not in call["prompt"]
    assert "- 759 (number, exact)" in call["prompt"] and "- must not appear: Pennard Fire Services" in call["prompt"]
    assert "verified by the company on not recorded" in call["prompt"]
    assert call["prompt"].rstrip().endswith('"unsure" as described in step 6.')


async def test_a_value_mismatch_beats_a_lenient_grader(monkeypatch):
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    g = await grader.grade(CASE, captured("There were 758 work orders."))
    assert g.value_check["status"] == "mismatch" and g.value_check["items"][0]["nearest"] == "758"
    assert (g.outcome, g.verdict) == ("unsure", "pass")
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": ["758 not 759"], "verdict": "fail"}])
    assert (await grader.grade(CASE, captured("There were 758 work orders."))).outcome == "fail"


async def test_strict_parsing_retries_once_and_never_defaults_to_pass(monkeypatch):
    fake_model(monkeypatch, [{"critique": "ok", "mismatches": [], "verdict": "pass"},
                             {"critique": CRITIQUE, "mismatches": ["x"], "verdict": "fail"}])
    assert (await grader.grade(CASE, captured())).outcome == "fail"
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "maybe"},
                             {"critique": CRITIQUE, "mismatches": [], "verdict": "unsure"}])
    assert (await grader.grade(CASE, captured())).outcome == "unsure"
    fake_model(monkeypatch, [{"critique": "ok", "mismatches": [], "verdict": "pass"}, ["not", "an", "object"]])
    g = await grader.grade(CASE, captured())
    assert (g.outcome, g.reading, g.verdict) == ("pending", "grading_pending", None) and "JSON object" in g.error
    fake_model(monkeypatch, [RuntimeError("overloaded")])
    g = await grader.grade(CASE, captured())
    assert g.outcome == "pending" and "overloaded" in g.error


async def test_pass_with_mismatches_contradicts_itself_and_is_unsure(monkeypatch):
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": ["26 inspections missing"], "verdict": "pass"}])
    g = await grader.grade(CASE, captured())
    assert (g.verdict, g.outcome, g.mismatches) == ("unsure", "unsure", ("26 inspections missing",))


async def test_a_refusal_anywhere_degrades_without_a_model_call(monkeypatch):
    seen = fake_model(monkeypatch, [])
    refusal = [{"service": "udr", "method": "POST", "reason": "refused_by_trace_lab"}]
    g = await grader.grade(CASE, captured(refused=refusal))
    assert (g.outcome, g.reading, g.verdict, g.source) == ("degraded", "degraded", None, "runner") and seen == []
    assert g.value_check["status"] == "all_found"                        # the free check still ran
    case = dict(CASE, followups=["And how many were reactive?"])
    g = await grader.grade(case, captured(), earlier=[(CASE["question"], captured(refused=refusal))])
    assert g.outcome == "degraded" and seen == []


async def test_a_refusal_with_a_chat_error_is_degraded_never_fail(monkeypatch):
    seen = fake_model(monkeypatch, [])
    refusal = [{"service": "udr", "method": "POST", "reason": "refused_by_trace_lab"}]
    g = await grader.grade(CASE, captured("", error="403 from the proxy", refused=refusal))
    assert (g.outcome, g.reading, g.verdict, g.mismatches) == ("degraded", "degraded", None, ()) and seen == []
    assert "degraded" in g.critique
    case = dict(CASE, followups=["And how many were reactive?"])
    g = await grader.grade(case, captured("", error="boom"), earlier=[(CASE["question"], captured(refused=refusal))])
    assert (g.outcome, g.reading, g.verdict) == ("degraded", "degraded", None) and "degraded" in g.critique


async def test_a_chat_error_fails_and_an_infrastructure_failure_is_an_error(monkeypatch):
    seen = fake_model(monkeypatch, [])
    g = await grader.grade(CASE, captured("", error="The assistant needed too many tool steps."))
    assert (g.outcome, g.verdict, g.source) == ("fail", "fail", "runner") and "too many tool steps" in g.critique
    g = await grader.grade(CASE, captured("", error="503 from the identity service"), infra_failed=True)
    assert (g.outcome, g.reading, g.source) == ("error", "infra_failed", "runner") and seen == []


async def test_lost_follow_up_context_is_never_passed_or_failed(monkeypatch):
    seen = fake_model(monkeypatch, [])
    g = await grader.grade(CASE, captured(), context_lost=True)
    assert (g.outcome, g.reading) == ("degraded", "context_unavailable") and seen == []


async def test_a_stale_reference(monkeypatch):
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": ["758"], "verdict": "fail"}])
    g = await grader.grade(CASE, captured(), stale=True)
    assert (g.outcome, g.stale, g.to_json()["stale"]) == ("stale_reference", True, True)
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    assert (await grader.grade(CASE, captured(), stale=True)).outcome == "pass"
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": ["758"], "verdict": "fail"}])
    old = dict(CASE, verified_on="2020-01-01")                           # stale from the case itself
    assert (await grader.grade(old, captured())).outcome == "stale_reference"
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    assert (await grader.grade(CASE, captured())).stale is False         # never verified: not stale


async def test_a_judgment_case_is_unsure_until_the_grader_is_validated(monkeypatch):
    case = dict(CASE, evaluation="judgment", expected_answer="Name each lapsed certificate and its building.",
                requirement_ids=["REQ-7"])
    seen = fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": ["no building named"], "verdict": "fail"}])
    g = await grader.grade(case, captured())
    assert (g.outcome, g.reading, g.validated_outcome, g.verdict) == ("unsure", "needs_person", "fail", "fail")
    assert g.value_check == {"status": "none", "items": []} and "REQUIREMENTS IT CITES" in seen[0]["prompt"]
    assert "REQ-7" in seen[0]["prompt"]
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    g = await grader.grade(case, captured(), judgment_validated=True)
    assert (g.outcome, g.reading, g.validated_outcome) == ("pass", "pass", "pass")


async def test_follow_ups_send_the_earlier_messages_and_the_last_question(monkeypatch):
    seen = fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    case = dict(CASE, followups=["And how many were reactive?"])
    await grader.grade(case, captured("733 were reactive."), earlier=[(CASE["question"], captured("759 in all."))])
    p = seen[0]["prompt"]
    assert f"Message 1: {CASE['question']}\nAssistant: 759 in all." in p
    assert p.startswith("The company recorded") and "TEST QUESTION (the last message the user sent)\nAnd how many" in p


async def test_a_long_answer_and_big_cards_are_cut_with_a_note(monkeypatch):
    seen = fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}])
    await grader.grade(CASE, captured("x" * 13000, cards=[{"rows": ["y" * 13000]}]))
    p = seen[0]["prompt"]
    assert "[… 1000 characters not shown …]" in p and "[cards cut at 12,000 characters]" in p


async def test_the_graders_cost_is_its_own_and_never_touches_the_answers_trace(monkeypatch):
    fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": "pass"}], usd_each=0.01)
    outer_turn = trace.Turn("turn-answer", "golden-x-21-r1")
    token = trace._turn.set(outer_turn)
    outer_ledger = llm_cost.begin_turn("golden-x-21-r1")
    try:
        g = await grader.grade(CASE, captured())
    finally:
        trace._turn.reset(token)
    assert g.usd == pytest.approx(0.01)
    assert outer_ledger.entries == [] and [s.kind for s in outer_turn.spans] == ["turn"]
    assert llm_cost.current() is outer_ledger


async def test_every_outcome_is_one_golden_results_stores(monkeypatch):
    for verdict in ("pass", "fail", "unsure"):
        for evaluation in ("objective", "judgment"):
            for kw in ({}, {"stale": True}, {"context_lost": True}, {"infra_failed": True}):
                fake_model(monkeypatch, [{"critique": CRITIQUE, "mismatches": [], "verdict": verdict}])
                g = await grader.grade(dict(CASE, evaluation=evaluation), captured(), **kw)
                assert g.outcome in STORED and g.validated_outcome in STORED, (verdict, evaluation, kw)
                j = g.to_json()
                assert set(j) == {"verdict", "critique", "mismatches", "value_check", "grader_version", "model", "usd",
                                  "reading", "validated_outcome", "evaluation", "source", "error", "stale"}
                assert "outcome" not in j and isinstance(j["mismatches"], list)
