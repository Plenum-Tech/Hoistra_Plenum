from langchain_core.messages import AIMessage

from src.agents.udr_response_evaluator import (
    evaluate_udr_response,
    has_udr_tool_calls,
)


class FakeEvaluator:
    def __init__(self, *payloads: str) -> None:
        self.payloads = list(payloads)

    async def ainvoke(self, _messages):
        return AIMessage(content=self.payloads.pop(0))


def test_detects_direct_and_subagent_udr_calls():
    assert has_udr_tool_calls([{"tool": "query_table", "input": {}, "output": []}])
    assert has_udr_tool_calls(
        [{"tool": "task", "input": {"agent": "udr"}, "output": "result"}]
    )
    assert not has_udr_tool_calls(
        [{"tool": "list_vendor_accreditations", "input": {}, "output": {}}]
    )


async def test_failed_udr_answer_is_replaced_by_evaluator_correction():
    llm = FakeEvaluator(
        """
        {
          "grounded": false,
          "answers_question": false,
          "count_consistent": false,
          "score": 0.2,
          "issues": ["The answer listed rows instead of grouping vendors"],
          "corrected_answer": "No vendors hold more than 2 certificates."
        }
        """,
        """
        {
          "grounded": true,
          "answers_question": true,
          "count_consistent": true,
          "score": 0.98,
          "issues": [],
          "corrected_answer": null
        }
        """,
    )
    answer, evaluation = await evaluate_udr_response(
        user_message="Which vendors hold more than 2 certificates?",
        answer="5 vendor accreditations",
        tool_calls=[
            {
                "tool": "query_table",
                "input": {"table": "certificates"},
                "output": {"rows": []},
            }
        ],
        llm=llm,
    )

    assert evaluation.passed
    assert answer == "No vendors hold more than 2 certificates."


async def test_grounded_udr_answer_passes_unchanged():
    llm = FakeEvaluator(
        """
        {
          "grounded": true,
          "answers_question": true,
          "count_consistent": true,
          "score": 0.96,
          "issues": [],
          "corrected_answer": null
        }
        """
    )
    candidate = "Three assets are overdue."
    answer, evaluation = await evaluate_udr_response(
        user_message="How many assets are overdue?",
        answer=candidate,
        tool_calls=[
            {
                "tool": "query_table",
                "input": {"table": "assets"},
                "output": {"count": 3},
            }
        ],
        llm=llm,
    )

    assert evaluation.passed
    assert answer == candidate


class BrokenEvaluator:
    """A judge whose own output is unreadable — the failure this is all about."""

    def __init__(self, payload: str = "not json at all") -> None:
        self.payload = payload
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return AIMessage(content=self.payload)


class UnreachableEvaluator:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc or RuntimeError("connection refused")

    async def ainvoke(self, _messages):
        raise self.exc


EVIDENCE = [{"tool": "task", "input": {"agent": "udr"},
             "output": "50 readings on MPAN-B-006-2 totalling 923.92 kWh"}]
GOOD_ANSWER = "Riverside Court has 50 half-hourly readings on MPAN-B-006-2, 923.92 kWh."


async def test_a_judge_that_cannot_parse_its_own_output_does_not_bin_the_answer():
    """Three runs in four of a question whose answer was right.

    The UDR sub-agent returned 50 readings on MPAN-B-006-2 totalling 923.92 kWh — exactly
    what the table holds — and the user was told "I found related data but couldn't fully
    verify an answer (Evaluator output failed schema validation)". The evaluator's own JSON
    had failed to parse, and the synthetic verdict it built for itself said grounded=False,
    which is a claim about the answer that it never established.
    """
    answer, evaluation = await evaluate_udr_response(
        user_message="How many readings does Riverside Court have?",
        answer=GOOD_ANSWER,
        tool_calls=EVIDENCE,
        llm=BrokenEvaluator(),
    )
    assert "923.92" in answer, "a correct answer was discarded because the judge broke"
    assert not evaluation.evaluated


async def test_an_unevaluated_answer_says_the_check_did_not_run():
    # And says it about the checker, not the data: "could not be verified" reads as doubt
    # about the records, which is the opposite of what happened.
    answer, _ = await evaluate_udr_response(
        user_message="q", answer=GOOD_ANSWER, tool_calls=EVIDENCE, llm=BrokenEvaluator(),
    )
    assert "could not run" in answer
    assert "couldn't fully verify" not in answer


async def test_an_unreachable_evaluator_is_treated_the_same_way():
    answer, evaluation = await evaluate_udr_response(
        user_message="q", answer=GOOD_ANSWER, tool_calls=EVIDENCE,
        llm=UnreachableEvaluator(),
    )
    assert "923.92" in answer
    assert not evaluation.evaluated


async def test_a_judge_that_runs_and_says_no_still_blocks():
    """The gate is intact. This is the case failing closed is FOR.

    An evaluator that ran, parsed, and found the answer ungrounded has made a finding, and
    that finding withholds the answer exactly as before.
    """
    llm = FakeEvaluator(
        """{"grounded": false, "answers_question": false, "count_consistent": false,
            "score": 0.1, "issues": ["Values do not appear in the evidence"],
            "corrected_answer": null}"""
    )
    answer, evaluation = await evaluate_udr_response(
        user_message="q", answer="Riverside Court used 5,000,000 kWh.",
        tool_calls=EVIDENCE, llm=llm,
    )
    assert "couldn't fully verify" in answer
    assert evaluation.evaluated and not evaluation.grounded


async def test_a_good_answer_still_passes_untouched():
    llm = FakeEvaluator(
        """{"grounded": true, "answers_question": true, "count_consistent": true,
            "score": 0.97, "issues": [], "corrected_answer": null}"""
    )
    answer, evaluation = await evaluate_udr_response(
        user_message="q", answer=GOOD_ANSWER, tool_calls=EVIDENCE, llm=llm,
    )
    assert answer == GOOD_ANSWER
    assert evaluation.passed and evaluation.evaluated


async def test_nothing_is_invented_when_there_is_no_answer_to_keep():
    # An empty candidate plus a broken judge is still nothing. The fallback keeps an answer
    # that exists; it does not manufacture one.
    answer, _ = await evaluate_udr_response(
        user_message="q", answer="   ", tool_calls=EVIDENCE, llm=BrokenEvaluator(),
    )
    assert "couldn't fully verify" in answer


def test_evaluated_defaults_to_true():
    # Every verdict the judge actually returns is an evaluation. Only the two exception
    # paths set it False, so a parsed verdict can never be mistaken for a missing one.
    from src.agents.udr_response_evaluator import UdrEvaluation

    v = UdrEvaluation(grounded=True, answers_question=True, count_consistent=True, score=1.0)
    assert v.evaluated is True
