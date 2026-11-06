"""A rewrite an admin writes, rather than one the optimiser measured.

The skill lab proposes rewrites by scoring them against the shipped text on replayed questions.
A person reading what reviewers actually said has no replay behind them, and should not need one:
they are allowed to say "the instructions should mention this" and have an admin approve it,
which is the same decision an admin already makes on a measured proposal.

The one thing this route must never do is accept a rewrite that cannot take effect. Only
documents the agents read AT CALL TIME can be overlaid -- a prompt assembled once at import keeps
its shipped text until the process restarts -- so a proposal against one of those would be
approved and change nothing, and the person who wrote it would have no way of telling. Most of
these tests are about that.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agents import skill_lab
from src.api.routes import skill_lab as routes
from src.services import principal as P


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    who = P.Principal(user_id=uuid4(), email="ada@example.com", organization_id=uuid4(),
                      role=role, can_ingest=True, building_ids=())
    app.dependency_overrides[P.current_principal] = lambda: who
    return TestClient(app)


def _tunable() -> tuple[str, str]:
    """One document an overlay actually reaches. Taken from the real set, so this file keeps
    testing the real thing when that set grows."""
    return sorted(skill_lab.TUNABLE)[0]


@pytest.fixture()
def current(monkeypatch):
    """What the document says now, and a capture of what gets proposed."""
    seen: dict = {}

    def _set(text: str):
        monkeypatch.setattr(routes.skill_overlays, "doc", lambda s, d: text)

        async def fake_propose(**kw):
            seen.update(kw)
            return "11111111-1111-4111-8111-111111111111"

        monkeypatch.setattr(routes.skill_overlays, "propose", fake_propose)
        return seen

    return _set


# -- who may write one ---------------------------------------------------------------------------

def test_a_plain_user_cannot_write_a_proposal():
    skill, doc = _tunable()
    r = _client("user").post("/api/skill-lab/proposals",
                             json={"skill": skill, "doc": doc, "content": "anything"})
    assert r.status_code == 403


def test_the_route_exists_on_the_router():
    posts = {r.path for r in routes.router.routes if "POST" in getattr(r, "methods", set())}
    assert "/api/skill-lab/proposals" in posts


# -- a rewrite that could not take effect is refused -----------------------------------------------

def test_a_document_an_overlay_cannot_reach_is_refused():
    """wo-engine/answering is read into a prompt at import. An approved overlay on it changes
    nothing: the lesson is written, approved, and the agents carry on exactly as before."""
    r = _client().post("/api/skill-lab/proposals",
                       json={"skill": "wo-engine", "doc": "answering", "content": "always check the role"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["reason"] == "not_tunable"
    assert "change nothing" in detail["error"]


def test_the_refusal_names_the_documents_that_do_reach_the_agents():
    """A refusal that does not say what IS allowed sends the caller guessing."""
    r = _client().post("/api/skill-lab/proposals", json={"skill": "made-up", "doc": "nope", "content": "x"})
    assert sorted(r.json()["detail"]["tunable"]) == sorted(f"{s}/{d}" for s, d in skill_lab.TUNABLE)


@pytest.mark.parametrize("skill,doc", [
    ("../etc", "passwd"), ("Query-Builder", "context-budget"), ("", "x"),
    ("query-builder", "../../secrets"), ("query builder", "context-budget"),
])
def test_a_name_that_is_not_a_name_never_reaches_the_table(skill, doc):
    """These become the (skill, doc) key the agents look themselves up by."""
    r = _client().post("/api/skill-lab/proposals", json={"skill": skill, "doc": doc, "content": "x"})
    assert r.status_code in (400, 422), f"{skill}/{doc} was accepted"


# -- content or append, never both -----------------------------------------------------------------

def test_content_and_append_are_exclusive():
    skill, doc = _tunable()
    c = _client()
    both = c.post("/api/skill-lab/proposals",
                  json={"skill": skill, "doc": doc, "content": "a", "append": "b"})
    neither = c.post("/api/skill-lab/proposals", json={"skill": skill, "doc": doc})
    assert both.status_code == 400 and neither.status_code == 400


def test_an_append_keeps_what_the_document_already_says(current):
    """A drafted lesson is a paragraph to add, not a rewritten document. Dropping the existing
    instructions is how a tuned document silently gets worse."""
    skill, doc = _tunable()
    seen = current("RULE ONE\nRULE TWO\n")
    r = _client().post("/api/skill-lab/proposals",
                       json={"skill": skill, "doc": doc, "append": "RULE THREE", "note": "from R32"})
    assert r.status_code == 200, r.text
    assert "RULE ONE" in seen["content"] and "RULE TWO" in seen["content"]
    assert seen["content"].rstrip().endswith("RULE THREE")
    assert seen["evidence"]["mode"] == "append"


def test_appending_to_a_document_that_reads_as_empty_is_refused(current):
    """Otherwise the proposal is the addition ALONE, quietly throwing away the whole document."""
    skill, doc = _tunable()
    current("")
    r = _client().post("/api/skill-lab/proposals",
                       json={"skill": skill, "doc": doc, "append": "a new rule"})
    assert r.status_code == 409
    assert "nothing to append to" in r.json()["detail"]


def test_proposing_what_the_document_already_says_is_refused(current):
    skill, doc = _tunable()
    current("  the same words\n")
    r = _client().post("/api/skill-lab/proposals",
                       json={"skill": skill, "doc": doc, "content": "the same words"})
    assert r.status_code == 409


# -- what the approver is told ----------------------------------------------------------------------

def test_a_written_proposal_says_it_was_not_measured(current):
    """These appear beside proposals that won a measured comparison. One with no score behind it
    must not read as one that has."""
    skill, doc = _tunable()
    seen = current("current text")
    r = _client().post("/api/skill-lab/proposals",
                       json={"skill": skill, "doc": doc, "content": "a whole new text",
                             "note": "what R32 showed"})
    assert r.status_code == 200
    assert r.json()["measured"] is False
    assert seen["evidence"]["source"] == "written"
    assert seen["evidence"]["written_by"] == "ada@example.com"
    assert seen["evidence"]["note"] == "what R32 showed"
    assert seen["run_id"] is None, "a written proposal has no run behind it"


def test_writing_one_is_not_approving_one(current):
    """An admin approves on the same screen, and that second pair of eyes is the only control on
    what every agent reads."""
    skill, doc = _tunable()
    current("current text")
    r = _client().post("/api/skill-lab/proposals", json={"skill": skill, "doc": doc, "content": "new"})
    assert r.json()["status"] == "proposed"
    assert "until an admin approves" in r.json()["next"]


def test_the_sizes_it_will_take(current):
    """Long enough for a real document, short enough that nobody pastes a book into the prompt
    every agent reads."""
    skill, doc = _tunable()
    current("current text")
    c = _client()
    assert c.post("/api/skill-lab/proposals",
                  json={"skill": skill, "doc": doc, "content": "x" * 120_001}).status_code == 422
    assert c.post("/api/skill-lab/proposals",
                  json={"skill": skill, "doc": doc, "append": "y" * 40_001}).status_code == 422
