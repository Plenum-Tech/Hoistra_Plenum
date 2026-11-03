"""What reviewers leave behind, drafted into something a person can teach the agents from.

Asked for on 3 Nov 2026: an annotation should feed back, so later answers are better for it.

Where it can go is decided by the Golden Rule rather than by preference. The documents the agents
read are plenum_cafm.skill_overlays, and svc-trace-lab may never write that database; the reverse
is blocked too, because nothing may import Trace Lab until Hussain approves the attachment. So
the loop is closed by a person: Trace Lab drafts, with the evidence attached, and the draft is
applied through the Skill lab.

That is also the right shape on its own merits. An annotation is one reviewer's opinion, and a
loop that taught the agents straight from opinions would follow whoever reviewed last. A person
reading the draft is the step where "somebody thought this was wrong" becomes "this is what we
want".

These are about the drafting, which is pure: the gather is one query per source and is covered by
the round-trip tests that run against a scratch database.
"""
from __future__ import annotations

import inspect

import pytest

from trace_lab import learning


def _found(notes=(), expected=(), gates=()):
    return {"notes": list(notes), "expected": list(expected), "gates": list(gates),
            "counts": {"notes": len(notes), "expected": len(expected), "gates": len(gates)}}


NOTE = {"turn_id": "turn-1", "step_kind": "orchestrator", "step_title": "routing by code",
        "question": "Show Bishopsgate Tower work orders by status", "label": "wrong",
        "note": "there needs to be a check at the orchestrator for access/restrictions based on the user role",
        "reviewer_email": "aasim.shaik@plenum-tech.com", "reviewer_role": "platform",
        "created_at": None}
EXPECTED = {"question": "Which vendors are blocked right now?",
            "expected": "The three vendors whose accreditation lapsed, named, with the date each lapsed.",
            "author_email": "aasim.shaik@plenum-tech.com", "created_at": None, "run_title": "R31"}
GATE = {"question": "what documents evidence each certificate", "engine": "compliance",
        "answer": "Here are the 8 certificates for that building.", "space": "ops",
        "scenario_id": "OPS-1-035", "asked_at": None}


# -- what the draft carries ---------------------------------------------------------------------

def test_a_reviewers_words_are_carried_not_paraphrased():
    """The note is the most specific thing anybody says about an answer. A summary of it would be
    a sentence nobody wrote."""
    md = learning.draft(_found(notes=[NOTE]))
    assert "check at the orchestrator for access/restrictions based on the user role" in md


def test_every_lesson_says_who_said_it_and_where():
    md = learning.draft(_found(notes=[NOTE]))
    assert "aasim.shaik@plenum-tech.com" in md
    assert "turn-1" in md, "without the turn there is no way back to the evidence"
    assert "routing by code" in md


def test_an_expected_answer_is_carried_as_the_target():
    md = learning.draft(_found(expected=[EXPECTED]))
    assert "Which vendors are blocked right now?" in md
    assert "three vendors whose accreditation lapsed" in md
    assert "should have been" in md.lower()


def test_a_gate_failure_says_what_was_asked_and_what_came_back():
    md = learning.draft(_found(gates=[GATE]))
    assert "OPS-1-035" in md
    assert "`ops`" in md and "`compliance`" in md
    assert "Here are the 8 certificates" in md


def test_a_gate_failure_is_not_presented_as_an_opinion():
    """It is machine-judged: the question was generated outside the instance, so a refusal was the
    only right answer whatever the text said."""
    md = learning.draft(_found(gates=[GATE]))
    assert "should have refused" in md


def test_notes_are_grouped_by_what_they_are_about():
    """A note on the orchestrator step is about routing; one on a model step is about that
    engine's instructions. Grouping is what makes the draft usable against a document."""
    model_note = {**NOTE, "step_kind": "model", "step_title": "tool decision",
                  "note": "it picked the wrong tool for a vendor question"}
    md = learning.draft(_found(notes=[NOTE, model_note]))
    assert "### orchestrator routing" in md
    assert "### the engine's own instructions" in md


def test_a_step_nobody_mapped_is_filed_rather_than_dropped():
    odd = {**NOTE, "step_kind": "something_new"}
    md = learning.draft(_found(notes=[odd]))
    assert "### general" in md
    assert odd["note"] in md


# -- what it must not do -------------------------------------------------------------------------

def test_the_draft_says_it_applies_nothing():
    md = learning.draft(_found(notes=[NOTE]))
    low = md.lower()
    assert "evidence, not an instruction" in low
    assert "decide what the agents should actually be taught" in low


def test_nothing_here_writes_to_the_application_database():
    """The Golden Rule in one assertion: the documents the agents read are in plenum_cafm, and
    this service may only read that database."""
    src = inspect.getsource(learning)
    assert "plenum_cafm" not in src.replace("plenum_cafm (", "").replace(
        "plenum_cafm\n", "") or "never write" in src
    for word in ("INSERT", "UPDATE", "DELETE", "skill_overlays"):
        assert word not in src.upper().replace("NEVER WRITE", ""), (
            "learning.py contains %s; it reads and drafts, nothing else" % word)


def test_the_draft_is_assembled_rather_than_written_by_a_model():
    """A model asked to summarise the lessons would produce something fluent that nobody said,
    and a reader could not tell which part was evidence."""
    src = inspect.getsource(learning)
    for word in ("openai", "anthropic", "llm", "completion", "chat("):
        assert word not in src.lower(), "the draft calls a model: " + word


# -- an empty lab ----------------------------------------------------------------------------------

def test_with_nothing_annotated_it_says_so():
    md = learning.draft(_found())
    assert "Nothing to draft yet" in md
    assert "Annotate a run first" in md


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-run", "RX"])
def test_a_run_code_that_is_not_one_cannot_select_rows(bad):
    """_run_no feeds a bound parameter; a code that is not one must match nothing rather than
    everything."""
    assert learning._run_no(bad) == -1


@pytest.mark.parametrize("code,want", [("R31", 31), ("r7", 7), ("  R12  ", 12)])
def test_a_run_code_is_read_the_way_the_page_writes_it(code, want):
    assert learning._run_no(code) == want


# -- the markdown has to survive a reviewer's typing --------------------------------------------------

def test_a_note_with_newlines_does_not_break_the_list():
    """A reviewer types into a textarea, so a note arrives with real newlines in it. Left alone
    they would split one note across several list items and the rest would read as separate
    lessons."""
    nl = chr(10)
    messy = {**NOTE, "note": f"first line{nl}second line{nl}{nl}third"}
    md = learning.draft(_found(notes=[messy]))
    body = [ln for ln in md.split(nl) if "first line" in ln]
    assert len(body) == 1, "the note was split across list items"
    assert "second line" in body[0] and "third" in body[0]


def test_a_pipe_in_a_note_is_escaped():
    md = learning.draft(_found(notes=[{**NOTE, "note": "a | b"}]))
    assert "a \\\\| b" in md or "a \\| b" in md


def test_a_very_long_answer_is_cut_rather_than_filling_the_page():
    md = learning.draft(_found(gates=[{**GATE, "answer": "x" * 5000}]))
    assert "…" in md and len(md) < 4000
