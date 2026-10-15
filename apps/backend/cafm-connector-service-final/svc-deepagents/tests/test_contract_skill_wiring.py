"""The vendor skill documents actually reach the vendor agent.

Compliance loads its documents by name into the pipeline stages that use them
(prompt_doc("compliance", …) in orchestrator.py and meta_tools.py). Contract performance had
none of that: skills.py globs `*/SKILL.md` and nothing else, so every sibling document in
skills/contract-performance/ was on disk and unread. Writing a skill that nothing loads looks
exactly like writing one that works, which is why this file exists.

Two traps it pins:

  * the directory is `contract-performance` and the agent id is `contract_performance` —
    prompt_doc takes the DIRECTORY, and compliance hides the difference because both of its
    names are the same word;
  * prompt_doc raises on a missing or empty document, so a renamed file breaks the import
    rather than quietly dropping a rule the agent was relying on.
"""
from __future__ import annotations

import pytest

from src.agents.meta_tools import CONTRACT_SUBAGENT_DOCS, CONTRACT_SUBAGENT_PROMPT
from src.agents.skills import agent_system_prompt, prompt_doc

#: Something only that document says. If a doc is dropped from the composition, the rule it
#: carried stops reaching the model — these are the rules we would miss most.
A_LINE_FROM = {
    "vocabulary": "capped 60 is not an earned 60",
    "tables": "no read route",
    "tool-selection": "takes a UUID and nothing else",
    "scoring": "delta_arithmetic_mismatch",
    "contract-terms": "defaults_used",
    "domain_contract_knowledge": "say which you ranked on",
    "answering": "You have no analyst",
    "recipes": "list_vendor_scorecards",
    "cross-domain": "Cross-domain",
    "review": "You do not",
    "never": "Never guess, construct or pattern-match",
}


def test_every_document_named_in_the_composition_exists_and_carries_text():
    for name in CONTRACT_SUBAGENT_DOCS:
        body = prompt_doc("contract-performance", name)
        assert body.strip(), f"{name}.md is empty"


def test_the_composition_is_the_set_we_think_it_is():
    # Named, not globbed: a document added to the directory should be a deliberate decision to
    # put it in front of the model, not something that happens because a file appeared.
    assert set(CONTRACT_SUBAGENT_DOCS) == set(A_LINE_FROM)


@pytest.mark.parametrize("name", sorted(A_LINE_FROM))
def test_each_document_reaches_the_prompt(name):
    assert A_LINE_FROM[name] in CONTRACT_SUBAGENT_PROMPT, f"{name}.md did not reach the prompt"


def test_the_agent_prompt_carries_the_skill_and_the_documents_together():
    prompt = agent_system_prompt("contract_performance", extra=CONTRACT_SUBAGENT_PROMPT)
    assert prompt, "the vendor agent has no system prompt at all"
    # From SKILL.md itself — the routing contract.
    assert "Contract Performance" in prompt
    # From the documents — the two rules that most change an answer.
    assert "Never guess, construct or pattern-match" in prompt
    assert "no read route" in prompt


def test_the_documents_live_under_the_hyphenated_directory():
    # The agent id is contract_performance; the directory is contract-performance. Reading the
    # agent id would find nothing, and prompt_doc says so by raising rather than returning "".
    with pytest.raises(RuntimeError):
        prompt_doc("contract_performance", "never")


def test_the_vendor_agent_is_not_running_on_compliance_rules():
    # A copy-paste of the compliance wiring would load compliance's documents here, and the
    # instruction that matters most would be inverted: compliance's sub-agent is told never to
    # write the answer, and this one always does.
    assert "Never write the finished answer yourself" not in CONTRACT_SUBAGENT_PROMPT
    assert "You write the answer" in CONTRACT_SUBAGENT_PROMPT
