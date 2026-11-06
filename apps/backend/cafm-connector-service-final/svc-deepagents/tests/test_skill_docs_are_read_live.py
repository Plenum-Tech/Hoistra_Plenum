"""Every instruction document is read at runtime, from one place (Bala, 5 Nov 2026).

Bala's review of the correction loop:
* the spec MD lives in one place and is read on the fly every time, from the main MD through to the sub-agent MDs;
* an MD update needs no review/merge/rebuild/redeploy of the container;
* the approved text stays out of the repo while testing repeats; the repo gets it only at commit.

The one place is plenum_cafm.skill_overlays (services/skill_overlays.py, the Skill lab's table). Until now only the
context-budget document was read through it: every SKILL.md and reference document was read from disk once and the
sub-agents were built with a fixed prompt string, so a changed document reached nobody until a rebuild. A new status
'testing' holds approved text that only a Trace Lab rerun reads (CANDIDATE); 'active' is what every agent reads.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents import meta_tools, skills
from src.services import skill_overlays as so


@pytest.fixture
def overlays(monkeypatch):
    live: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(so, "_active", live)
    return live


def test_a_reference_document_reads_the_active_overlay_then_the_file(overlays):
    shipped = skills.prompt_doc("compliance", "answering")
    overlays[("compliance", "answering")] = "OVERLAID ANSWERING RULES"
    assert skills.prompt_doc("compliance", "answering") == "OVERLAID ANSWERING RULES"
    overlays.clear()
    assert skills.prompt_doc("compliance", "answering") == shipped


def test_text_under_test_reaches_only_the_rerun_that_set_it(overlays):
    overlays[("compliance", "answering")] = "LIVE"
    tok = so.CANDIDATE.set({("compliance", "answering"): "UNDER TEST"})
    try:
        assert skills.prompt_doc("compliance", "answering") == "UNDER TEST"
    finally:
        so.CANDIDATE.reset(tok)
    assert skills.prompt_doc("compliance", "answering") == "LIVE"


def test_the_main_md_and_the_spec_reach_every_sub_agent_prompt(overlays):
    plain = skills.agent_system_prompt("wo_engine")
    shared = skills.shared_skill()
    overlays[(shared.slug, "SKILL")] = "SHARED CONTRACT, CHANGED"
    overlays[("spec", "SPEC")] = "### S-5 · blocked_and_at_risk_conflated\n\nTwo lists."
    overlays[(skills.skill_for_agent("wo_engine").slug, "SKILL")] = "WO SKILL, CHANGED"
    live = skills.agent_system_prompt("wo_engine")
    assert "SHARED CONTRACT, CHANGED" in live and "WO SKILL, CHANGED" in live and "S-5" in live
    assert live.index("SHARED CONTRACT, CHANGED") < live.index("S-5") < live.index("WO SKILL, CHANGED")
    overlays.clear()
    assert skills.agent_system_prompt("wo_engine") == plain, "with no overlay nothing changes"


def test_a_sub_agent_prompt_is_rebuilt_on_every_model_call(overlays):
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return f"SYSTEM v{calls['n']}"

    prompt = meta_tools.live_prompt(build)
    first = prompt({"messages": [HumanMessage(content="q")]})
    second = prompt({"messages": [HumanMessage(content="q")]})
    assert isinstance(first[0], SystemMessage) and first[0].content == "SYSTEM v1"
    assert second[0].content == "SYSTEM v2" and second[-1].content == "q"


def test_no_sub_agent_is_built_with_a_fixed_prompt_string():
    src = inspect.getsource(meta_tools)
    assert "prompt=agent_system_prompt(" not in src
    assert "COMPLIANCE_SUBAGENT_PROMPT = prompt_doc(" not in src
    assert "shared.body" not in src


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows

    def mappings(self):
        return self


class _Session:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self.log.append((str(sql), dict(params or {})))
        s = str(sql)
        for marker, rows in self.rows:
            if marker in s:
                return _Rows(rows)
        return _Rows([])

    async def commit(self):
        return None


def _db(monkeypatch, rows):
    log: list = []
    from src import database

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(log, rows))
    monkeypatch.setattr(so, "_ready", True)

    async def no_refresh(force=False):
        return None

    monkeypatch.setattr(so, "refresh", no_refresh)
    return log


def test_approval_puts_text_on_test_not_live(monkeypatch):
    log = _db(monkeypatch, [("SELECT skill, doc, status", [("compliance", "answering", "proposed")])])
    out = asyncio.run(so.approve_for_testing("11111111-1111-1111-1111-111111111111", by="hussain@x"))
    assert out["status"] == "testing"
    upd = [(s, p) for s, p in log if s.startswith("UPDATE")]
    assert len(upd) == 1 and upd[0][1]["st"] == "testing", "nothing is retired or made live by an approval"


def test_going_live_retires_the_old_text_for_that_document(monkeypatch):
    log = _db(monkeypatch, [("SELECT skill, doc, status", [("compliance", "answering", "testing")])])
    out = asyncio.run(so.go_live("11111111-1111-1111-1111-111111111111", by="hussain@x"))
    assert out["status"] == "active"
    upd = [s for s, _ in log if s.startswith("UPDATE")]
    assert "status = 'retired'" in upd[0] and "status = 'active'" in upd[0]
    assert upd[1].count(":st") == 1


def test_only_text_on_test_can_go_live(monkeypatch):
    _db(monkeypatch, [("SELECT skill, doc, status", [("compliance", "answering", "proposed")])])
    assert asyncio.run(so.go_live("11111111-1111-1111-1111-111111111111", by="x")) is None


def test_export_gives_the_repo_path_of_every_live_document(monkeypatch):
    _db(monkeypatch, [("WHERE status = 'active'", [("compliance", "answering", "NEW TEXT"), ("spec", "SPEC", "# Spec")])])
    files = {f["path"]: f["content"] for f in asyncio.run(so.export_active())}
    assert set(files) == {"skills/compliance/answering.md", "skills/spec/SPEC.md"}
    assert files["skills/compliance/answering.md"].startswith("---") and files["skills/compliance/answering.md"].rstrip().endswith("NEW TEXT")
    assert files["skills/spec/SPEC.md"] == "# Spec", "the spec has no shipped file yet: committed as written"


def test_a_document_must_exist_to_be_changed():
    assert so.known_doc("compliance", "answering") and so.known_doc("compliance", "SKILL")
    assert so.known_doc("spec", "SPEC")
    assert not so.known_doc("compliance", "../../etc/passwd") and not so.known_doc("nope", "x")


def test_the_instruction_routes_are_admin_only_and_check_the_document(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import skill_lab as routes
    from src.services.principal import current_principal

    app = FastAPI()
    app.include_router(routes.router)
    who = {"admin": True}

    class P:
        email = "hussain@x"

        @property
        def is_admin(self):
            return who["admin"]

    app.dependency_overrides[current_principal] = lambda: P()

    async def propose(**kw):
        return "22222222-2222-2222-2222-222222222222"

    monkeypatch.setattr(so, "propose", propose)
    c = TestClient(app)
    ok = c.post("/api/skill-lab/overlays", json={"skill": "compliance", "doc": "answering", "content": "x"})
    assert ok.status_code == 200 and ok.json()["status"] == "proposed"
    assert c.post("/api/skill-lab/overlays", json={"skill": "compliance", "doc": "nope", "content": "x"}).status_code == 404
    who["admin"] = False
    assert c.post("/api/skill-lab/overlays", json={"skill": "compliance", "doc": "answering", "content": "x"}).status_code == 403
    assert c.get("/api/skill-lab/export").status_code == 403


def test_a_changed_skill_md_keeps_its_front_matter_in_the_repo(monkeypatch):
    _db(monkeypatch, [("WHERE status = 'active'", [("compliance", "SKILL", "CHANGED BODY")])])
    (f,) = asyncio.run(so.export_active())
    assert f["path"] == "skills/compliance/SKILL.md"
    assert f["content"].startswith("---") and "name:" in f["content"].split("---")[1]
    assert f["content"].rstrip().endswith("CHANGED BODY")


# ── the orchestrator's own compliance documents (5 Nov 2026) ─────────────────────────────────────────
# Found on a live question ("Which certificates expire in the next 60 days? ... by building"): the compliance
# planner's prompt was a Python string, so no instruction change could reach it, and the analyst's and reviewer's
# documents were class attributes read once at import, so an approved change to answering.md reached nobody until
# a restart.

_PLANNER_SHA_EMPTY = "5f88de837d06493f55ed67ec69edc29ea670a1b3329f43a8ad8119fffb8c3829"
_PLANNER_SHA_VOCAB = "e5834fe61c00d8e7bb0b4aff068c21967c3d576cd4dc69fcd9c282d7e892593f"
_VOCAB = "The values these columns actually hold:\n- status: Valid, Lapsed\n"


def test_the_compliance_planner_prompt_is_a_document_word_for_word(overlays):
    import hashlib

    from src.agents import orchestrator as orch

    assert so.known_doc("compliance", "planner")
    # The sections the correction loop added and a person committed (S-7, S-9 on 5 Nov 2026) follow the moved
    # prompt; everything before the first of them is the Python string, word for word.
    moved = lambda vocab: orch.compliance_planner_prompt(vocab).split("\n\n## ")[0]
    assert hashlib.sha256(moved("").encode()).hexdigest() == _PLANNER_SHA_EMPTY
    assert hashlib.sha256(moved(_VOCAB).encode()).hexdigest() == _PLANNER_SHA_VOCAB, \
        "the column values go exactly where they went in the Python string"
    sections = orch.compliance_planner_prompt("").split("\n\n## ")[1:]
    assert [s.split("\n")[0][-5:] for s in sections] == ["(S-7)", "(S-9)"], "the committed corrections, in order"


def test_the_compliance_planner_reads_its_document_live(overlays):
    from src.agents import orchestrator as orch

    overlays[("compliance", "planner")] = "PLAN RULES\n{{COLUMN_VALUES}}END"
    assert orch.compliance_planner_prompt("V\n") == "PLAN RULES\nV\nEND"
    overlays.clear()
    assert orch.compliance_planner_prompt("").startswith("You plan what a compliance question needs")


def test_the_compliance_analyst_and_reviewer_read_their_documents_live(overlays):
    from src.agents.orchestrator import DeepAgentOrchestrator as O

    before = (O._ANALYST_PROMPT, O._ANALYST_CONTEXT_DOCS, O._TAXONOMY_DIRECTIVE, O._REVIEWER_PROMPT)
    overlays[("compliance", "answering")] = "NEW ANSWERING"
    overlays[("compliance", "vocabulary")] = "NEW VOCABULARY"
    overlays[("compliance", "taxonomy")] = "NEW TAXONOMY"
    overlays[("compliance", "review")] = "NEW REVIEW"
    assert O._ANALYST_PROMPT == "NEW ANSWERING"
    assert "NEW VOCABULARY" in O._ANALYST_CONTEXT_DOCS and "NEW VOCABULARY" in O._REVIEWER_PROMPT
    assert O._TAXONOMY_DIRECTIVE == "NEW TAXONOMY" and O._REVIEWER_PROMPT.startswith("NEW REVIEW")
    overlays.clear()
    assert (O._ANALYST_PROMPT, O._ANALYST_CONTEXT_DOCS, O._TAXONOMY_DIRECTIVE, O._REVIEWER_PROMPT) == before


def test_no_instruction_document_is_read_once_at_import():
    """Every read of an instruction document happens when it is used, never when a module or class is loaded."""
    import ast
    import pathlib

    readers = {"prompt_doc", "fm_lens", "spec_text", "agent_system_prompt", "skill_body", "_shipped_doc"}
    frozen = []
    for p in pathlib.Path(__file__).resolve().parents[1].joinpath("src").rglob("*.py"):
        def walk(node, in_func):
            for ch in ast.iter_child_nodes(node):
                inside = in_func or isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
                if isinstance(ch, ast.Call) and not in_func:
                    fn = ch.func
                    name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
                    if name in readers:
                        frozen.append(f"{p.name}:{ch.lineno} {name}")
                walk(ch, inside)
        walk(ast.parse(p.read_text(encoding="utf-8")), False)
    assert frozen == [], "read once at import: " + ", ".join(frozen)
