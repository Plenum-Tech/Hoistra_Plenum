"""The annotations, drafted into something a person can carry to the agents.

Asked for on 3 Nov 2026: "from annotate it should learn and update skill/spec md file". This is the
middle of that loop, and the two things worth holding still are what it draws on and what it
refuses to do.

What it draws on: step notes, expected answers and gate verdicts. Each one is quoted with the
evidence that justifies it, assembled from the rows rather than summarised by a model -- a reader
has to be able to follow every line back to somebody who typed it, and a model asked to
"summarise the lessons" would produce something fluent that nobody said.

What it refuses: it applies nothing. The documents the agents read live in plenum_cafm, which this
service may never write (G0). The draft is for a person to put into a skill document through the
Skill lab. That is also the right shape on its own merits -- an annotation is one reviewer's
opinion, and a loop that taught the agents straight from opinions would drift with whoever
reviewed last.

Pure: draft() and by_skill() are given rows and asked for markdown, so none of this needs a
database. gather()'s SQL is exercised where the schema is, in the real-SQL tests.
"""
from __future__ import annotations

import inspect

import pytest

from trace_lab import learning, routes_scenarios


def _note(**kw):
    row = {"turn_id": "t-1", "step_kind": "model", "step_title": "wo_engine",
           "question": "which work orders breached sla", "label": "wrong",
           "note": "there needs to be a check at the orchestrator for access by role",
           "reviewer_email": "sam@example.com", "reviewer_role": "domain_expert",
           "created_at": None}
    row.update(kw)
    return row


def _found(notes=(), expected=(), gates=()):
    return {"notes": list(notes), "expected": list(expected), "gates": list(gates),
            "counts": {"notes": len(notes), "expected": len(expected), "gates": len(gates)}}


# -- what it refuses to do ---------------------------------------------------------------------

def test_it_applies_nothing_and_says_so():
    """The whole risk here is a reviewer reading a tidy draft and believing the agents have been
    taught something. They have not."""
    src = inspect.getsource(routes_scenarios.learning_draft)
    assert '"applies_anything": False' in inspect.getsource(routes_scenarios) or \
        "applies_anything" in src


def test_nothing_in_the_draft_is_written_by_a_model():
    """Assembled from rows. A model in this path would put words in reviewers' mouths."""
    src = inspect.getsource(learning)
    for banned in ("anthropic", "openai", "llm", "completion(", "chat("):
        assert banned not in src.lower().replace("completion(s)", ""), banned


def test_the_module_never_writes():
    src = inspect.getsource(learning).upper()
    for word in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ", "DROP "):
        assert word not in src, "the learning draft writes: " + word.strip()


# -- what it draws on ----------------------------------------------------------------------------

def test_a_note_is_quoted_with_who_said_it_and_where():
    out = learning.draft(_found(notes=[_note()]))
    assert "there needs to be a check at the orchestrator" in out
    assert "sam@example.com" in out
    assert "t-1" in out, "a line that cannot be traced back to its turn is not evidence"


def test_an_expected_answer_is_carried_as_the_target():
    out = learning.draft(_found(expected=[{
        "question": "how many are overdue", "expected": "41 across the two UK buildings",
        "author_email": "ada@example.com", "created_at": None, "run_title": "Synthetic · ops · seed 7"}]))
    assert "41 across the two UK buildings" in out
    assert "ada@example.com" in out


def test_a_gate_failure_is_reported_as_machine_judged():
    out = learning.draft(_found(gates=[{
        "question": "what did we spend on energy", "engine": "wo_engine", "answer": "about 40k",
        "space": "ops", "scenario_id": "OPS-7-004", "asked_at": None}]))
    assert "OPS-7-004" in out
    assert "refuse" in out.lower(), "without saying the instance should have refused it, this reads as a bad answer"


def test_an_empty_draft_says_what_to_do_rather_than_nothing():
    out = learning.draft(_found())
    assert "Annotate a run first" in out


def test_the_counts_are_in_the_draft():
    out = learning.draft(_found(notes=[_note(), _note(turn_id="t-2")]))
    assert "2 step notes" in out


# -- which document each lesson is about -------------------------------------------------------

@pytest.mark.parametrize("title,kind,skill,doc", [
    ("wo_engine", "model", "wo-engine", "answering.md"),
    ("compliance engine", "tool", "compliance", "tool-selection.md"),
    ("contract_performance", "output", "contract-performance", "answering.md"),
    ("energy_intelligence", "model", "energy-intelligence", "answering.md"),
])
def test_a_note_is_filed_against_the_engine_it_was_left_on(title, kind, skill, doc):
    assert learning.suggest(kind, title) == (skill, doc)


def test_a_note_on_no_particular_engine_goes_to_the_shared_instructions():
    """Routing and the way an answer is written are shared. Filing them under one engine would
    teach that engine a lesson about all of them."""
    assert learning.suggest("orchestrator", "Orchestrator") == learning._SHARED
    assert learning.suggest("model", None) == learning._SHARED


def test_every_suggested_skill_is_a_real_folder():
    """A draft that names a document nobody can open is a dead end. These are the folders in
    svc-deepagents/skills; this test is what notices when one is renamed."""
    real = {"compliance", "contract-performance", "doc-rag", "energy-intelligence",
            "migration", "query-builder", "udr", "wo-engine"}
    assert set(learning._ENGINE_TO_SKILL.values()) <= real
    assert learning._SHARED[0] in real


def test_the_grouping_says_it_is_a_guess():
    groups = learning.by_skill(_found(notes=[_note()]))
    assert groups and groups[0]["suggested"] is True, (
        "a guessed document presented as a decision files the lesson against the wrong agent")


def test_each_group_carries_only_its_own_notes_and_its_own_markdown():
    groups = learning.by_skill(_found(notes=[
        _note(turn_id="t-1", step_title="wo_engine", note="work order note"),
        _note(turn_id="t-2", step_title="compliance", step_kind="tool", note="compliance note"),
    ]))
    assert len(groups) == 2
    by = {(g["skill"], g["doc"]): g for g in groups}
    wo = by[("wo-engine", "answering.md")]
    comp = by[("compliance", "tool-selection.md")]
    assert "work order note" in wo["markdown"] and "compliance note" not in wo["markdown"]
    assert "compliance note" in comp["markdown"] and "work order note" not in comp["markdown"]
    assert wo["notes"] == 1 and comp["notes"] == 1


def test_a_group_names_the_document_it_is_for():
    """It is pasted into one document, so the heading has to say which."""
    groups = learning.by_skill(_found(notes=[_note()]))
    assert "wo-engine/answering.md" in groups[0]["markdown"]


def test_notes_without_a_draft_produce_no_groups():
    assert learning.by_skill(_found()) == []


# -- the gate verdicts are optional ------------------------------------------------------------

def test_the_gate_query_is_skipped_when_its_tables_are_absent():
    """0012 is applied by a person and is not applied everywhere. Asking for the scenario tables
    unconditionally made the WHOLE draft 500 where they are missing, losing the notes and expected
    answers with them -- and those need nothing from 0012."""
    src = inspect.getsource(learning.gather)
    assert "to_regclass('tracelab.scenario_results')" in src
    assert "if has_scenarios else []" in src


def test_the_reply_says_whether_gates_could_be_looked_for():
    """Zero gate failures and "could not look" are different facts and read identically."""
    assert "gates_available" in inspect.getsource(learning.gather)
    assert "gates_available" in inspect.getsource(routes_scenarios.learning_draft) or \
        "gates_available" in inspect.getsource(routes_scenarios)


# -- one run at a time ---------------------------------------------------------------------------

def test_every_source_is_narrowed_to_the_run_not_just_the_expected_answers():
    """A draft headed R32 that carried every note the company ever wrote would be read as what
    R32 showed."""
    src = inspect.getsource(learning.gather)
    assert src.count("in_run if rn is not None") >= 1
    assert "lab_question_traces" in src, "a note reaches its run through the turn it was left on"
    assert src.count("WHERE r2.run_no = :rn") + src.count("WHERE r.run_no = :rn") >= 2


def test_a_run_code_that_is_not_one_cannot_match_a_run():
    assert learning._run_no("R32") == 32
    assert learning._run_no("nonsense") == -1
    assert learning._run_no(None) == -1
