"""The coding agent runs inside Trace Lab and only proposes; people approve; instruction changes live in one place and
are tested before they go live (Bala, 5 Nov 2026). The model and deepagents' instruction-store API are stubbed; the
Trace Lab rows are real (throwaway Postgres). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import agent, labelling, loop, runs
from trace_lab.review_errors import Forbidden, Invalid
from trace_lab_m2_fixtures import principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
NOW = datetime.now(timezone.utc).replace(microsecond=0)
BEARER = "Bearer person-token"


async def _turn(main, tid, *, uid, question, answer):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, 's-1', CAST(:org AS uuid), CAST(:uid AS uuid), 'ann@a.test', :q, :a, 'single:compliance',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "org": ORG, "uid": uid, "q": question, "a": answer, "at": NOW - timedelta(hours=1)})


class FakeStore:
    """deepagents' /api/skill-lab instruction store, as the agent calls it."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str | None]] = []

    async def __call__(self, method, path, authorization, body=None):
        self.calls.append((method, path, authorization))
        if authorization is None:
            raise Forbidden("signed_out", "Sign in again.")
        if method == "POST" and path == "/overlays":
            oid = str(uuid.uuid4())
            self.rows[oid] = {"id": oid, "skill": body["skill"], "doc": body["doc"], "content": body["content"],
                              "status": "proposed", "evidence": body.get("evidence"), "created_by": "boss@plenum.test"}
            return {"ok": True, "id": oid, "status": "proposed"}
        if method == "GET" and path == "/overlays":
            return {"ok": True, "overlays": list(self.rows.values())}
        if method == "GET" and path == "/export":
            return {"ok": True, "files": [{"path": f"skills/{r['skill']}/{r['doc']}.md", "content": r["content"]}
                                          for r in self.rows.values() if r["status"] == "active"]}
        oid, action = path.split("/")[2], path.split("/")[3]
        frm, to = {"test": ("proposed", "testing"), "live": ("testing", "active"), "drop": (None, "rejected")}[action]
        assert frm is None or self.rows[oid]["status"] == frm, (action, self.rows[oid]["status"])
        self.rows[oid]["status"] = to
        return {"ok": True, "id": oid, "status": to}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(agent, "_deepagents", s)
    return s


async def test_the_agent_proposes_from_notes_and_nothing_counts_until_a_person_approves(main_scratch, lab_scratch,  # noqa: F811
                                                                                         monkeypatch):
    uid = str(uuid.uuid4())
    await _turn(main_scratch, "turn-v1", uid=uid, question="Which vendors are not eligible to work here?",
                answer="Five are blocked; the dashboard says nine.")
    await runs.sync_live(None)
    boss = await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)
    code = (await runs.list_runs(boss))["runs"][0]["code"] + "-1"
    await labelling.save_label(labelling.for_org(boss, ORG), "turn-v1", step_key="turn-v1:scenario", label=None,
                               note="Two different blocked counts: 5 and 9.", step_title="The question")
    asked = []

    async def model(system, prompt, schema, max_tokens):
        asked.append(prompt)
        return {"modes": [{"name": "blocked_and_at_risk_conflated", "definition": "Mixes blocked and at-risk vendors.",
                           "existing": False, "questions": [code, "R999-1"]},
                          {"name": "Not A Name!", "definition": "x", "existing": False, "questions": [code]}]}

    monkeypatch.setattr(agent, "_ask", model)
    out = await agent.propose_from_notes(boss)
    assert [m["name"] for m in out["proposed_modes"]] == ["blocked_and_at_risk_conflated"] and out["labels"] == 1
    assert "Two different blocked counts" in asked[0] and code in asked[0]
    assert await runs.list_modes(boss) == [], "an agent's mode is a proposal, not a column"
    pend = await loop.proposals(boss)
    assert pend["modes"][0]["name"] == "blocked_and_at_risk_conflated"
    prog = await runs.progress(boss, code.split("-")[0])
    assert all(md["positives"] == 0 for md in prog["modes"]), "an agent's label never counts"
    again = await agent.propose_from_notes(boss)
    assert again["notes_read"] == 1, "the agent's own suggestion does not stop it reading the note again"
    assert again["labels"] == 0 and again["proposed_modes"] == [], "asked again, it repeats no suggestion"
    person_mode = await runs.save_mode(boss, name="some_confirmed_mode", definition="A mode a person made.")
    await runs.set_cell(boss, code, mode=person_mode["lineage_id"], value="negative")
    assert (await agent.propose_from_notes(boss))["notes_read"] == 0, "a question a person labelled is not read again"
    assert "same CAUSE" in agent.NOTES_SYSTEM, "an existing mode is reused only for the same cause, not a similar symptom"


async def test_rule_to_change_to_test_to_live_to_the_repo(main_scratch, lab_scratch, monkeypatch, store):  # noqa: F811
    uid = str(uuid.uuid4())
    await _turn(main_scratch, "turn-v2", uid=uid, question="Which vendors are not eligible to work here?",
                answer="Five are blocked; the dashboard says nine.")
    await runs.sync_live(None)
    boss = await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)
    code = (await runs.list_runs(boss))["runs"][0]["code"] + "-1"
    mode = await runs.save_mode(boss, name="blocked_and_at_risk_conflated", definition="Mixes blocked and at-risk.")
    await runs.set_cell(boss, code, mode=mode["lineage_id"], value="positive")
    with pytest.raises(Invalid):
        await agent.draft_fix(boss, mode["lineage_id"], authorization=BEARER)      # no rule approved yet

    draft = await loop.draft_spec(boss)
    v = await loop.propose_spec(boss, rules=draft["rules"])
    await loop.decide(boss, "spec_version", v["id"], decision="approved")
    state = await loop.spec_state(boss)
    pub = await agent.publish_spec(state["approved"], authorization=BEARER)
    assert pub["status"] == "testing", "the agent writes the approved SPEC.md to the store, under test, not live"

    async def model(system, prompt, schema, max_tokens):
        assert "S-1" in prompt and "Which vendors" in prompt
        # each document is offered with what it governs, so a filtering mistake can go to the planner's document
        assert "- planner: How the compliance planner turns a question into a register query" in prompt
        assert "- answering: How the compliance analyst writes an answer" in prompt
        return {"doc": "answering", "title": "Blocked and at risk are two lists",
                "text": "Answer NOT ELIGIBLE (block flag) and AT RISK (lapsed, no flag) as two lists.",
                "why": "The answer added the two groups into one count."}

    monkeypatch.setattr(agent, "_ask", model)
    monkeypatch.setattr(agent, "_skill_for", lambda qs: "compliance")
    # 5 Nov 2026: a draft was built on the repo file while a changed answering.md was live, so making it live would
    # have dropped the live change. The draft builds on the newest text: under test, else live, else the file.
    from src.services import skill_overlays as so

    live = "LIVE ANSWERING TEXT. " * 10 + "\n\n## A rule already live (S-0)\n\nKeep me."

    async def no_refresh(force=False):
        return None

    async def nothing_under_test():
        return {}

    monkeypatch.setattr(so, "_active", {("compliance", "answering"): live})
    monkeypatch.setattr(so, "refresh", no_refresh)
    monkeypatch.setattr(so, "testing_texts", nothing_under_test)
    fix = await agent.draft_fix(boss, mode["lineage_id"], authorization=BEARER)
    row = store.rows[fix["id"]]
    assert row["content"].startswith("LIVE ANSWERING TEXT.") and "Keep me." in row["content"], "built on the live text"

    async def under_test():
        return {("compliance", "answering"): "UNDER TEST TEXT"}

    monkeypatch.setattr(so, "testing_texts", under_test)
    assert await agent._base_text("compliance", "answering") == "UNDER TEST TEXT", "text under test is newer than live"
    assert (row["skill"], row["doc"], row["status"]) == ("compliance", "answering", "proposed")
    assert fix["path"] == "skills/compliance/answering.md"
    assert row["content"].rstrip().endswith("as two lists.") and "## Blocked and at risk are two lists (S-1)" in row["content"]
    assert len(row["content"]) > len(fix["section"]) + 100, "the change is added to the document, which is kept whole"

    seen = await agent.changes(boss, authorization=BEARER)
    assert [c["id"] for c in seen["proposed"]] == [fix["id"]] and seen["testing"][0]["skill"] == "spec"
    await agent.approve_change(boss, fix["id"], authorization=BEARER)
    assert store.rows[fix["id"]]["status"] == "testing", "approved means under test, not live"
    view = await loop.overview(boss)
    assert view["modes"][0]["corrections"] == 1, "the approved change is the mode's correction"

    await agent.make_live(boss, fix["id"], authorization=BEARER)
    assert store.rows[fix["id"]]["status"] == "active"
    z = zipfile.ZipFile(io.BytesIO(await agent.export_zip(boss, authorization=BEARER)))
    assert "svc-deepagents/skills/compliance/answering.md" in z.namelist()
    assert all(a == BEARER for _m, _p, a in store.calls), "every store call is made in the person's name"


async def test_a_company_admin_cannot_change_the_instructions(lab_scratch, store):  # noqa: F811
    admin = await labelling.access(principal(ORG, "admin", email="adm@a.test"), None, all_companies_ok=True)
    with pytest.raises(Forbidden):
        await agent.draft_fix(admin, str(uuid.uuid4()), authorization=BEARER)
    with pytest.raises(Forbidden):
        await agent.changes(admin, authorization=BEARER)


async def test_the_agent_reads_only_the_notes_of_the_run_a_person_chose(main_scratch, lab_scratch, monkeypatch):  # noqa: F811
    """5 Nov 2026: asked about a new question, the agent also read older notes on other runs and proposed a mode from
    one of them. It now reads the run the person has open, and says which."""
    await _turn(main_scratch, "turn-a", uid=str(uuid.uuid4()), question="Which certificates expire soon?", answer="Six.")
    await _turn(main_scratch, "turn-b", uid=str(uuid.uuid4()), question="When is the next LOLER due?", answer="None.")
    await runs.sync_live(None)
    boss = await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)
    codes = {r["code"]: r for r in (await runs.list_runs(boss))["runs"]}
    assert len(codes) == 2
    by_turn = {}
    for code in codes:
        for q in (await runs.list_questions(boss, code))["questions"]:
            by_turn[q["question"]] = q["code"]
    qa, qb = by_turn["Which certificates expire soon?"], by_turn["When is the next LOLER due?"]
    for turn, q in (("turn-a", qa), ("turn-b", qb)):
        await labelling.save_label(labelling.for_org(boss, ORG), turn, step_key=turn + ":scenario", label=None,
                                   note="Note on " + q, step_title="The question")
    asked = []

    async def model(system, prompt, schema, max_tokens):
        asked.append(prompt)
        return {"modes": []}

    monkeypatch.setattr(agent, "_ask", model)
    out = await agent.propose_from_notes(boss, run=qa.split("-")[0])
    assert out["notes_read"] == 1 and out["run"] == qa.split("-")[0]
    assert "Note on " + qa in asked[0] and "Note on " + qb not in asked[0], "another run's note is not read"
    with pytest.raises(Exception):
        await agent.propose_from_notes(boss, run="not-a-run")
