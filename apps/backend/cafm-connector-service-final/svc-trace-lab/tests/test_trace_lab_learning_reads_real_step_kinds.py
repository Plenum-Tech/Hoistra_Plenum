"""Learning files each note by the step kinds Trace Lab really stores (10 Nov 2026).

The mapping was written against kinds nobody stores ("model", "tool", "code", "router"). A note is
saved with its message's role -- model_call, tool_call, db_query, plan, assistant -- or 'agent', with
'_output' added for a note on a step's output. None of those matched, so nearly every note was filed
as "general" and named no engine. The trace view also names a step by what it is ("model", "count"),
not by the engine it ran in, so the engine guess found nothing unless the step was the agent itself,
and the work-order agent is called "maintenance", not "wo_engine". Titles now carry the agent:
"Input · model (maintenance)".
"""
from __future__ import annotations

import pytest

from trace_lab import learning


@pytest.mark.parametrize("kind,title,skill", [
    ("model_call", "Input · model (maintenance)", "the engine's own instructions"),
    ("model_call_output", "Output · model (maintenance)", "the engine's own instructions"),
    ("agent", "Input · maintenance", "the engine's own instructions"),
    ("agent_output", "Output · compliance", "the engine's own instructions"),
    ("tool_call", "Input · list_vendor_accreditations (compliance)", "tool choice"),
    ("tool_call_output", "Output · answer_from_records (maintenance)", "tool choice"),
    ("db_query", "Input · count (maintenance)", "the deterministic step"),
    ("db_query_output", "Output · count (maintenance)", "the deterministic step"),
    ("assistant", "Output · chat", "how the answer is written"),
    ("plan", "Input · plan", "orchestrator routing"),
    ("model_call", "Input · router", "orchestrator routing"),
    ("model_call_output", "Output · router", "orchestrator routing"),
    ("orchestrator", None, "orchestrator routing"),
    ("scenario", "The question itself", "general"),
    ("user", "Input · chat", "general"),
    ("system_note", "Input · check", "general"),
])
def test_each_stored_step_kind_is_filed_under_a_lesson(kind, title, skill):
    assert learning._skill_for(kind, title) == skill


@pytest.mark.parametrize("kind,title,expected", [
    ("model_call", "Input · model (maintenance)", ("wo-engine", "answering.md")),
    ("agent_output", "Output · maintenance", ("wo-engine", "answering.md")),
    ("agent", "Input · energy_intelligence", ("energy-intelligence", "SKILL.md")),
    ("tool_call_output", "Output · list_vendor_accreditations (compliance)", ("compliance", "tool-selection.md")),
    ("db_query", "Input · count (contract_performance)", ("contract-performance", "SKILL.md")),
    # The agent a step ran in decides, not a word that happens to be in the step's own name.
    ("tool_call", "Input · list_compliance_certs (maintenance)", ("wo-engine", "tool-selection.md")),
    ("model_call", "Input · router", learning._SHARED),
    ("assistant", "Output · chat", learning._SHARED),
])
def test_a_trace_step_note_is_filed_against_the_engine_it_ran_in(kind, title, expected):
    assert learning.suggest(kind, title) == expected


def test_the_old_kinds_still_work():
    """Drafts already written used these; they keep meaning what they meant."""
    assert learning._skill_for("model") == "the engine's own instructions"
    assert learning._skill_for("tool") == "tool choice"
    assert learning._skill_for("code") == "the deterministic step"
    assert learning._skill_for("output") == "how the answer is written"
    assert learning._skill_for("router") == "orchestrator routing"
    assert learning._skill_for(None) == "general"


def test_the_draft_groups_by_the_real_kinds():
    found = {"notes": [
        {"turn_id": "t-1", "step_kind": "model_call", "step_title": "Input · model (maintenance)",
         "question": "q", "label": "wrong", "note": "it forgot the SLA", "reviewer_email": "a@x"},
        {"turn_id": "t-2", "step_kind": "tool_call_output", "step_title": "Output · lookup (compliance)",
         "question": "q", "label": "wrong", "note": "wrong tool", "reviewer_email": "a@x"},
    ], "expected": [], "gates": []}
    out = learning.draft(found)
    assert "### the engine's own instructions" in out and "### tool choice" in out
    assert "### general" not in out
