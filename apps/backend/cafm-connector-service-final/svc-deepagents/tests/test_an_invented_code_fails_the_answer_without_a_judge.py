"""A code the answer names that no tool returned fails the answer, whatever the model judge says.

The judge decides whether a relationship or a count is supported. Whether a code was ever
*seen* is mechanical: if "AST-GEN-501" is in the answer and in no tool output, it was invented,
and neither a judge that passes it nor a judge that breaks should let it reach the user. Codes
the user typed are exempt — "AST-AHU-601 was not found" names one legitimately.
"""
import json

from langchain_core.messages import AIMessage

from src.agents import udr_response_evaluator as ev


class _Judge:
    def __init__(self, *payloads):
        self.payloads = list(payloads)

    async def ainvoke(self, _messages):
        return AIMessage(content=self.payloads.pop(0))


def _verdict(**kw):
    base = {"grounded": True, "answers_question": True, "count_consistent": True, "score": 0.95,
            "issues": [], "corrected_answer": None}
    return json.dumps({**base, **kw})


ROWS = [{"tool": "udr_execute_select", "input": {"sql": "SELECT …"},
         "output": {"rows": [{"part_code": "PRT-BRG-88", "asset_id": "86e56c75-769b-5c12-ade3-52ebb40657dc"},
                             {"part_code": "PRT-BRG-88", "asset_id": "AST-GEN-501"}]}}]


class TestFindingThem:

    def test_a_code_no_tool_returned_is_reported(self):
        assert ev.ungrounded_identifiers("PRT-BRG-88 is fitted to AHU-01 and CHILLER-01.", ROWS) == ["AHU-01", "CHILLER-01"]

    def test_a_code_a_tool_returned_is_not(self):
        assert ev.ungrounded_identifiers("PRT-BRG-88 links to AST-GEN-501.", ROWS) == []

    def test_a_code_the_user_typed_is_exempt(self):
        assert ev.ungrounded_identifiers("AST-AHU-601 was not found.", ROWS, user_message="tell me about AST-AHU-601") == []

    def test_dates_uuids_and_prose_do_not_match(self):
        text = "On 2026-09-17 the record 86e56c75-769b-5c12-ade3-52ebb40657dc was Re-Ordered by the on-site team."
        assert ev.ungrounded_identifiers(text, ROWS) == []

    def test_each_code_is_reported_once(self):
        assert ev.ungrounded_identifiers("AHU-01, then AHU-01 again, and AHU-01.", ROWS) == ["AHU-01"]

    def test_an_empty_answer_names_nothing(self):
        assert ev.ungrounded_identifiers("", ROWS) == []


class TestTheGate:

    async def test_a_judge_that_passes_an_invented_code_is_overruled(self):
        judge = _Judge(_verdict())
        out, e = await ev.evaluate_udr_response(
            user_message="which assets use PRT-BRG-88?", answer="PRT-BRG-88 is used by AHU-01 and AST-GEN-501.",
            tool_calls=ROWS, llm=judge)
        assert not e.passed and not e.grounded
        assert "AHU-01" in " ".join(e.issues)
        assert "used by AHU-01" not in out  # the invented answer is not what the user gets
        assert "AHU-01" in out  # but they are told which code could not be verified

    async def test_a_broken_judge_no_longer_lets_an_invented_code_through(self):
        class _Broken:
            async def ainvoke(self, _m):
                return AIMessage(content="not json")
        out, e = await ev.evaluate_udr_response(
            user_message="q", answer="The part is used by AHU-01.", tool_calls=ROWS, llm=_Broken())
        assert not e.evaluated
        assert "used by AHU-01" not in out
        assert "could not run" not in out

    async def test_a_correction_that_invents_a_code_is_not_accepted_either(self):
        judge = _Judge(
            _verdict(grounded=False, score=0.3, issues=["wrong"], corrected_answer="PRT-BRG-88 is used by FCU-999."),
            _verdict(),
        )
        out, e = await ev.evaluate_udr_response(
            user_message="q", answer="PRT-BRG-88 is used by AHU-01.", tool_calls=ROWS, llm=judge)
        assert "FCU-999" not in out or "no retrieved record" in out
        assert not e.passed

    async def test_a_grounded_answer_is_untouched(self):
        judge = _Judge(_verdict())
        out, e = await ev.evaluate_udr_response(
            user_message="q", answer="PRT-BRG-88 is linked to AST-GEN-501 in the work-order parts register.",
            tool_calls=ROWS, llm=judge)
        assert e.passed and out.startswith("PRT-BRG-88 is linked")
