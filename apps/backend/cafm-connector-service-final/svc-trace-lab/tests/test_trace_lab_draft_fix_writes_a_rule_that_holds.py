"""Agent: draft fix writes a rule the agent actually follows (10 Nov 2026).

R41-5 "Which work orders are delayed because of expired compliance certificates?" counted "5 work orders at risk" and
named none of them, though work_order_blockers returned all five. The loop drafted S-13 into compliance/answering.md and
the rerun still named none: Still failing. Replaying only the analyst on the same data: the drafted text (a generic
paragraph appended after 14,000 characters of rules such as "do not append the full list" and "at most four") listed 0 of
the 5; a rule scoped to the read's own fields (`blocked`, `at_risk`) that names the lines it overrides listed 5 of 5,
twice. Three faults, three fixes:

* the skill was guessed from the question's words by the keyword router, so R41-4's fix went into udr/SKILL.md while
  the compliance agent answered: the skill is now the one the failing answers were routed to;
* the drafting model saw only the documents' titles: it now drafts again with the chosen document's current text and
  the tools and fields the failing answers read, and must scope the rule to them and name what it overrides;
* the section says it takes precedence over anything above it that conflicts.
"""
from __future__ import annotations

from trace_lab import agent


def test_a_route_names_the_engines_that_answered():
    assert agent._engines_of_route("single:compliance") == ["compliance"]
    assert agent._engines_of_route("multi:compliance,contract_performance") == ["compliance", "contract_performance"]
    assert agent._engines_of_route("single:maintenance") == ["wo_engine"], "the work-order agent is called maintenance"
    assert agent._engines_of_route(None) == [] and agent._engines_of_route("general") == []


def test_the_skill_is_the_one_the_failing_answers_were_routed_to():
    meta = {"routes": ["single:compliance", "multi:compliance,contract_performance", "single:contract_performance"]}
    assert agent._skill_from_meta(meta) == "compliance", "the first engine of each route counts most"
    assert agent._skill_from_meta({"routes": []}) is None, "nothing recorded: the caller falls back to the router"


def test_the_tools_and_fields_the_failing_answers_read_are_named():
    out = agent._data_lines({"tools": {"work_order_blockers": ["blocked", "at_risk", "clear"],
                                       "list_blocked_vendors": ["blocked", "at_risk"]}})
    assert "- work_order_blockers: blocked, at_risk, clear" in out
    assert "- list_blocked_vendors: blocked, at_risk" in out
    assert agent._data_lines({"tools": {}}) == "(none recorded)"


def test_the_keys_of_a_read_are_its_list_and_object_fields():
    raw = '{"ok": true, "blocked": [{"wo_code": "W1", "blockers": [{"hard": true}]}], "at_risk": [], "summary": {"blocked": 1}}'
    assert agent._keys_of(raw)[:4] == ["blocked", "blockers", "at_risk", "summary"]
    assert agent._keys_of('{"_truncated": "{\\"blocked\\": [{\\"x\\": 1}], \\"at_risk\\": ['
                          '"}')[:2] == ["blocked", "at_risk"], "a cut record still names its fields"


def test_the_second_draft_sees_the_document_and_the_data():
    p = agent._refine_prompt(rule={"code": "S-13", "text": "Every counted item is listed."},
                             mode={"name": "incomplete_enumeration_of_results", "definition": "counts without items"},
                             notes=["Counts 5 at-risk work orders but never names them."],
                             draft={"title": "List what you count", "text": "List every item."},
                             doc_text="# Answering\n\n- do not append the full list\n- at most four",
                             data="- work_order_blockers: blocked, at_risk")
    for want in ("S-13", "Every counted item is listed.", "incomplete_enumeration_of_results", "never names them",
                 "List every item.", "do not append the full list", "at most four", "work_order_blockers: blocked, at_risk"):
        assert want in p, want
    assert "precedence" in agent.REFINE_SYSTEM.lower() and "field" in agent.REFINE_SYSTEM.lower()


def test_the_section_says_it_takes_precedence():
    s = agent._section("S-13", "At-risk items are listed", "List each at-risk work order by code.")
    assert s.startswith("## At-risk items are listed (S-13)\n\n")
    assert "takes precedence over anything above" in s and s.rstrip().endswith("by code.")


# -- the reviewer keeps what the rule requires (R45, 10 Nov 2026) -------------------------------------------------------
#
# With the scoped S-13 rule the analyst listed the 5 at-risk work orders, then the review step (scope-eval.md, "Is anything
# here that was not asked for?") ordered them removed as "a different, forward-looking question" and the revision dropped
# them: Still failing again. Replayed on the same data: without a clause in the reviewer's document the review said
# revise and asked to remove them; with it, pass. A fix to the writer's document is drafted with that clause for the
# reviewer's, and the two are approved, tested, made live and rejected together.

def test_the_reviewers_document_is_scope_eval_else_review():
    assert agent._review_doc(["SKILL", "answering", "review", "scope-eval"]) == "scope-eval"
    assert agent._review_doc(["SKILL", "answering", "review"]) == "review"
    assert agent._review_doc(["SKILL", "answering"]) is None


def test_the_reviewer_is_told_the_rule_is_earned_not_extra():
    s = agent._keep_section("S-13", "List every `at_risk` work order in a group of its own.")
    assert s.startswith("## Keep what rule S-13 requires (S-13)\n\n")
    assert "List every `at_risk` work order in a group of its own." in s
    for want in ("IS what the question earns", "do not list it as not asked for", "removed", "if it is missing, ask for it"):
        assert want in s, want


def test_the_rule_goes_to_the_writer_even_when_the_model_picks_the_reviewer_s_document():
    """R41-6 (10 Nov 2026): the draft for count_mismatch_with_enumerated_items went to review.md, which the step that
    writes the answer never reads. The writer's document gets the rule; the reviewer's gets its clause beside it."""
    docs = ["SKILL", "answering", "planner", "review", "scope-eval"]
    assert agent._primary_doc("review", docs) == "answering"
    assert agent._primary_doc("scope-eval", docs) == "answering"
    assert agent._primary_doc("planner", docs) == "planner", "a planning failure stays with the planner"
    assert agent._primary_doc("review", ["SKILL", "review"]) == "review", "no writer's document: keep the choice"


def test_a_section_never_carries_its_own_heading_twice():
    """S-14 (10 Nov 2026) came out as two headings and the precedence line twice: the model's text began with its own
    "## ... (S-14)" heading and precedence sentence."""
    body = ("## No two statements may contradict each other (S-14)\n\nThis rule takes precedence over anything above that "
            "conflicts with it, for the case it covers.\n\nBefore returning an answer, re-read every count.")
    s = agent._section("S-14", "Check self-contradiction before shipping", body)
    assert s.count("## ") == 1 and s.count("takes precedence") == 1
    assert s.rstrip().endswith("re-read every count.")
