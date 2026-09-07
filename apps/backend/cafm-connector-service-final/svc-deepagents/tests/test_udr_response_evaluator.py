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
