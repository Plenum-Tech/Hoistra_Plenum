"""The accuracy check judges rows, not schema dumps, and a verdict on the wrong scale still counts.

Measured 17 Sep 2026 on "which assets to investigate and which parts to reorder":

* The judge returned `"score": 2`. Pydantic refused it, the verdict was discarded, and an answer
  whose "linked assets" column no table supports reached the user with a footnote saying the
  check "could not run". The judge had run; it wrote 2 on a ten-point scale.
* Earlier the same day the judge wrote "the evidence contains only schema metadata and no
  query results". get_schema's output (221 tables, 2,580 columns) had filled the whole
  24,000-character evidence budget and the rows after it were cut off.

Both are pinned here, with the skill rule that stops the column being invented in the first place.
"""
import json

from langchain_core.messages import AIMessage

from src.agents import udr_response_evaluator as ev
from src.agents.skills import agent_system_prompt


class _Judge:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages[-1].content)
        return AIMessage(content=self.payloads.pop(0))


def _verdict(**kw):
    base = {"grounded": True, "answers_question": True, "count_consistent": True, "score": 0.95,
            "issues": [], "corrected_answer": None}
    return json.dumps({**base, **kw})


# ── the score ──────────────────────────────────────────────────────────────────────────

class TestTheScoreScale:

    def test_two_on_a_ten_point_scale_is_read_as_point_two(self):
        e = ev._parse_evaluation(_verdict(score=2))
        assert e.score == 0.2 and e.evaluated

    def test_eighty_five_on_a_hundred_point_scale_is_read_as_point_eight_five(self):
        e = ev._parse_evaluation(_verdict(score=85))
        assert e.score == 0.85

    def test_a_score_already_in_range_is_untouched(self):
        assert ev._parse_evaluation(_verdict(score=0.9)).score == 0.9
        assert ev._parse_evaluation(_verdict(score=1)).score == 1.0

    def test_content_blocks_are_read_as_their_text(self):
        e = ev._parse_evaluation([{"type": "text", "text": _verdict(score=0.9)}])
        assert e.score == 0.9

    async def test_a_verdict_of_two_now_blocks_instead_of_slipping_through(self):
        """The failure as it happened: judge says ungrounded, score 2. Before: 'could not run',
        answer delivered. Now: a real verdict, and the correction is what the user gets."""
        judge = _Judge(
            _verdict(grounded=False, count_consistent=False, score=2,
                     issues=["linked assets are not in any join result"],
                     corrected_answer="10 parts are at or below reorder level; no asset link is recorded for any of them."),
            _verdict(score=0.95),
        )
        answer, e = await ev.evaluate_udr_response(
            user_message="which parts need reorder and which assets use them?",
            answer="8 parts need reorder. PRT-CONT-2P is linked to AHU-01, CHILLER-01.",
            tool_calls=[{"tool": "udr_execute_select", "input": {"sql": "SELECT …"}, "output": {"rows": [{"part_code": "PRT-CONT-2P"}] * 10}}],
            llm=judge,
        )
        assert e.evaluated and e.passed
        assert answer.startswith("10 parts are at or below reorder level")
        assert "could not run" not in answer


# ── the evidence ───────────────────────────────────────────────────────────────────────

class TestWhatTheJudgeSees:

    ROWS = {"tool": "udr_execute_select", "input": {"sql": "SELECT part_code FROM spare_parts"},
            "output": {"rows": [{"part_code": "PRT-BNZ-04", "stock_quantity": 0}]}}

    def test_a_schema_dump_does_not_starve_the_rows(self):
        schema = {"tool": "get_schema", "input": {}, "output": {"tables": {f"t{i}": ["a", "b", "c"] * 20 for i in range(400)}}}
        evidence = ev._evidence([schema, self.ROWS])
        assert "PRT-BNZ-04" in evidence
        assert '"get_schema"' not in evidence

    def test_catalogue_cards_are_not_evidence_either(self):
        cards = [{"tool": "find_tables", "input": {"question": "x"}, "output": {"tables": [{"table": "spare_parts"}]}},
                 {"tool": "table_card", "input": {"table": "spare_parts"}, "output": {"purpose": "…" * 500}}]
        evidence = ev._evidence([*cards, self.ROWS])
        assert "PRT-BNZ-04" in evidence and "find_tables" not in evidence and "table_card" not in evidence

    def test_one_huge_output_is_clipped_so_the_next_call_still_fits(self):
        big = {"tool": "udr_read_records", "input": {"table": "work_orders"}, "output": {"rows": [{"id": i, "notes": "x" * 200} for i in range(500)]}}
        evidence = ev._evidence([big, self.ROWS])
        assert "clipped" in evidence and "PRT-BNZ-04" in evidence
        assert len(evidence) <= ev._MAX_EVIDENCE_CHARS

    def test_a_small_output_is_passed_through_untouched(self):
        assert ev._clipped(self.ROWS) is self.ROWS

    def test_a_page_engines_tools_called_directly_are_evidence_too(self):
        """13:39, 17 Sep: the general loop called the Assets page tools itself on a fanned-out
        turn, the filter kept only UDR tools and task(), and the judge replaced a correct
        two-part answer with 'schema definitions only'."""
        energy = {"tool": "list_asset_conditions", "input": {"band": "threat"},
                  "output": {"assets": [{"asset_code": "ACS-DL-04", "band": "threat", "section_deviation_pct": 31.4}]}}
        evidence = ev._evidence([energy, self.ROWS])
        assert "ACS-DL-04" in evidence and "PRT-BNZ-04" in evidence

    def test_the_plan_the_route_and_the_panel_are_still_not_evidence(self):
        calls = [{"tool": "write_todos", "input": {"todos": ["x"]}, "output": "Plan"},
                 {"tool": "select_skill", "input": {"q": "x"}, "output": {"agent": "udr"}},
                 {"tool": "compliance_pipeline", "input": {}, "output": {"steps": []}},
                 self.ROWS]
        evidence = ev._evidence(calls)
        assert "PRT-BNZ-04" in evidence
        for name in ("write_todos", "select_skill", "compliance_pipeline"):
            assert name not in evidence

    def test_schema_only_turns_still_gate_the_check(self):
        """Excluded from evidence, not from the decision to run."""
        assert ev.has_udr_tool_calls([{"tool": "get_schema", "input": {}, "output": {}}])

    async def test_the_judge_is_told_what_a_relationship_needs(self):
        judge = _Judge(_verdict())
        await ev.evaluate_udr_response(user_message="q", answer="a", tool_calls=[self.ROWS], llm=judge)
        # the system message is the first; the fake records only the human one, so read the source
        import inspect
        src = inspect.getsource(ev.evaluate_udr_response)
        assert "join result" in src and "0.0 to " in src


# ── a correction may not shrink a table ────────────────────────────────────────────────

TABLE_ANSWER = (
    "**9 parts are below reorder level.**\n\n"
    "| Part | Stock | Reorder |\n|---|---:|---:|\n"
    "| P-BRG-001 | 0 | 5 |\n| PRT-BNZ-04 | 0 | 5 |\n| PRT-BRG-88 | 0 | 5 |\n"
    "| P-IMP-001 | 2 | 5 |\n| P-BLT-002 | 3 | 5 |\n"
)
TABLE_ROWS = [{"tool": "udr_execute_select", "input": {"sql": "SELECT …"},
               "output": {"rows": [{"part_code": c} for c in ("P-BRG-001", "PRT-BNZ-04", "PRT-BRG-88", "P-IMP-001", "P-BLT-002")]}}]


class TestACorrectionMayNotShrinkATable:

    def test_rows_are_counted_without_the_header_rule(self):
        assert ev._table_rows(TABLE_ANSWER) == 6  # header + 5 rows
        assert ev._table_rows("no table here") == 0

    async def test_a_correction_that_keeps_three_examples_is_not_delivered(self):
        """14:12, 17 Sep: the judge could not verify every row, so its correction kept three
        rows 'as examples' and turned the rest into prose. The user had asked for the list."""
        judge = _Judge(
            _verdict(grounded=False, count_consistent=False, score=0.4,
                     issues=["output is clipped, so several rows cannot be confirmed"],
                     corrected_answer="The visible evidence confirms these examples: P-BRG-001 (0/5), PRT-BNZ-04 (0/5), PRT-BRG-88 (0/5). A complete list is not available."),
            _verdict(score=0.94),
        )
        out, e = await ev.evaluate_udr_response(user_message="which parts are below reorder level?",
                                                answer=TABLE_ANSWER, tool_calls=TABLE_ROWS, llm=judge)
        assert ev._table_rows(out) == 6, out
        assert "these examples" not in out
        assert "human verification" in out

    async def test_a_correction_that_keeps_the_table_and_fixes_a_number_is_delivered(self):
        fixed = TABLE_ANSWER.replace("**9 parts", "**5 parts")
        judge = _Judge(
            _verdict(grounded=False, count_consistent=False, score=0.6, issues=["headline says 9, table has 5"], corrected_answer=fixed),
            _verdict(score=0.96),
        )
        out, e = await ev.evaluate_udr_response(user_message="q", answer=TABLE_ANSWER, tool_calls=TABLE_ROWS, llm=judge)
        assert out.startswith("**5 parts") and ev._table_rows(out) == 6 and e.passed

    def test_the_judge_is_told_to_keep_tables_and_treat_clipped_as_unverifiable(self):
        import inspect
        src = inspect.getsource(ev.evaluate_udr_response)
        assert "verbatim" in src and "[clipped]" in src and "never replace a table" in src


# ── the skill ──────────────────────────────────────────────────────────────────────────

class TestTheSkillRefusesToGuessLinks:

    def test_the_reorder_recipe_says_where_linked_assets_come_from(self):
        prompt = agent_system_prompt("udr") or ""
        assert "no asset link recorded" in prompt
        assert "from the join and from nothing else" in prompt

    def test_never_fill_a_relationship_column_without_rows(self):
        prompt = agent_system_prompt("udr") or ""
        assert "Never fill a relationship column" in prompt
        assert "Never write a headline count from memory" in prompt
