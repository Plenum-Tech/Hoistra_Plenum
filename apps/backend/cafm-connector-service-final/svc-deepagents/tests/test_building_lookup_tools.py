"""Three questions about a building that nothing could answer.

Documents, meter readings and invoices all came back "no data" for a building holding all
three. Not one of them was a data problem: there was no structured document lookup (the
question fell through to semantic search, which finds documents whose TEXT mentions a
building — a different and much weaker claim), no tool that read meters or readings, and no
invoice listing at all.

That failure shape is the dangerous one: it reads as a fact about the portfolio rather than
about the tools, and a person has no way to tell the two apart from the answer.

These cover the part worth pinning without a server: that a name is resolved rather than
guessed, that a refusal to resolve is passed on instead of being papered over with another
building's data, and that the graph's branches are read correctly.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import energy_intelligence_agent as energy

RIVERSIDE = "f12e9629-95ed-5722-80cc-a1397f627850"


class Fake:
    """Stands in for http_client.request, answering by path substring."""

    def __init__(self, answers: dict[str, object]):
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, base, path, **kwargs):
        self.calls.append((path, kwargs.get("params") or {}))
        for fragment, body in self.answers.items():
            if fragment in path:
                class R:
                    def json(self_inner):
                        return body
                return R()
        raise AssertionError(f"unexpected path {path}")


RESOLVED = {"ok": True, "outcome": "resolved", "building_id": RIVERSIDE,
            "building": "Riverside Court"}
AMBIGUOUS = {"ok": True, "outcome": "review", "reason": "multiple_name_matches",
             "building_id": None,
             "candidates": [{"name": "Riverside Court"}, {"name": "Riverside House"}]}

GRAPH = {
    "ok": True,
    "name": "Riverside Court",
    "branches": [
        {"table": "assets", "label": "Assets", "count": 6, "rows": [],
         "children": [{"table": "meters", "label": "Meters", "count": 2, "rows": []}]},
        {"table": "documents", "label": "Documents", "count": 2,
         "rows": [
             {"label": "contract.pdf", "detail": "contract", "has_file": True},
             {"label": "readings.csv", "detail": "meter_readings", "has_file": False},
         ],
         "children": [
             {"table": "compliance_certificates", "label": "Certificates", "count": 1,
              "rows": [{"label": "US_FIRE_ALARM_NFPA72", "has_file": True}]}
         ]},
    ],
}


def run(coro):
    return asyncio.run(coro)


def resolve(name, building_id=None):
    return run(energy._resolve_building(name, building_id))


def test_an_explicit_id_is_used_as_given(monkeypatch):
    called = Fake({})
    monkeypatch.setattr(energy, "_request", called)
    assert resolve(None, RIVERSIDE) == (RIVERSIDE, None)
    assert called.calls == []  # no need to ask anything


def test_a_name_is_resolved_to_an_id(monkeypatch):
    fake = Fake({"/buildings/resolve": RESOLVED})
    monkeypatch.setattr(energy, "_request", fake)
    assert resolve("Riverside Court") == (RIVERSIDE, None)
    assert fake.calls[0][1]["name"] == "Riverside Court"


def test_an_ambiguous_name_is_refused_not_guessed(monkeypatch):
    # The resolver declines rather than picking one. Answering under the wrong building
    # misstates two buildings at once, so the refusal is the answer.
    monkeypatch.setattr(energy, "_request", Fake({"/buildings/resolve": AMBIGUOUS}))
    bid, problem = resolve("Riverside")
    assert bid is None
    assert problem["error"] == "building_not_resolved"
    assert problem["asked_for"] == "Riverside"
    assert len(problem["candidates"]) == 2
    assert "Do NOT substitute another building" in problem["guidance"]


def test_neither_a_name_nor_an_id_is_an_error_not_a_portfolio_wide_answer(monkeypatch):
    monkeypatch.setattr(energy, "_request", Fake({}))
    bid, problem = resolve(None, None)
    assert bid is None and "required" in problem["error"]


def test_documents_come_from_the_link_not_from_a_text_search(monkeypatch):
    monkeypatch.setattr(
        energy, "_request", Fake({"/buildings/resolve": RESOLVED, "/graph": GRAPH})
    )
    out = run(energy.list_building_documents.ainvoke({"building_name": "Riverside Court"}))
    assert out["count"] == 2
    assert [r["label"] for r in out["documents"]] == ["contract.pdf", "readings.csv"]
    assert out["building"] == "Riverside Court"


def test_certificates_extracted_from_those_documents_come_too(monkeypatch):
    monkeypatch.setattr(
        energy, "_request", Fake({"/buildings/resolve": RESOLVED, "/graph": GRAPH})
    )
    out = run(energy.list_building_documents.ainvoke({"building_name": "Riverside Court"}))
    assert [r["label"] for r in out["certificates"]] == ["US_FIRE_ALARM_NFPA72"]


def test_a_document_with_no_stored_original_is_reported_not_dropped(monkeypatch):
    # has_file false is a fact about storage, not a reason to omit the row — the document
    # is still filed against the building.
    monkeypatch.setattr(
        energy, "_request", Fake({"/buildings/resolve": RESOLVED, "/graph": GRAPH})
    )
    out = run(energy.list_building_documents.ainvoke({"building_name": "Riverside Court"}))
    assert [r["has_file"] for r in out["documents"]] == [True, False]


def test_an_empty_documents_branch_carries_its_reason(monkeypatch):
    empty = {"ok": True, "name": "Riverside Court", "branches": [
        {"table": "documents", "label": "Documents", "count": 0, "rows": [],
         "empty_reason": "No document has been filed against this building."}]}
    monkeypatch.setattr(
        energy, "_request", Fake({"/buildings/resolve": RESOLVED, "/graph": empty})
    )
    out = run(energy.list_building_documents.ainvoke({"building_name": "Riverside Court"}))
    assert out["count"] == 0
    assert "No document" in out["empty_reason"]


def test_documents_does_not_answer_for_an_unresolved_building(monkeypatch):
    monkeypatch.setattr(energy, "_request", Fake({"/buildings/resolve": AMBIGUOUS}))
    out = run(energy.list_building_documents.ainvoke({"building_name": "Riverside"}))
    assert out["error"] == "building_not_resolved"
    assert "documents" not in out


SUMMARY = {
    "ok": True, "building_id": RIVERSIDE, "meter_count": 1,
    "readings_total": 50, "kwh_total": 923.92,
    "meters": [{"meter_ref": "MPAN-B-006-2", "meter_type": "electricity",
                "readings": 50, "total_kwh": 923.92, "estimated_readings": 2}],
    "registered_without_readings": [{"meter_ref": "MPAN-B-006-3"}],
}


def test_the_meter_summary_is_fetched_for_the_resolved_building(monkeypatch):
    fake = Fake({"/buildings/resolve": RESOLVED, "meter-summary": SUMMARY})
    monkeypatch.setattr(energy, "_request", fake)
    out = run(
        energy.list_building_meter_readings.ainvoke({"building_name": "Riverside Court"})
    )
    assert out["readings_total"] == 50 and out["kwh_total"] == 923.92
    assert out["meters"][0]["meter_ref"] == "MPAN-B-006-2"
    assert RIVERSIDE in fake.calls[-1][0]


def test_a_meter_on_the_register_with_no_readings_is_surfaced(monkeypatch):
    # "A meter with nothing recorded" and "no meter" are different answers, and only one
    # of them is about data being missing.
    monkeypatch.setattr(
        energy, "_request", Fake({"/buildings/resolve": RESOLVED, "meter-summary": SUMMARY})
    )
    out = run(
        energy.list_building_meter_readings.ainvoke({"building_name": "Riverside Court"})
    )
    assert out["registered_without_readings"][0]["meter_ref"] == "MPAN-B-006-3"


def test_meter_readings_does_not_answer_for_an_unresolved_building(monkeypatch):
    monkeypatch.setattr(energy, "_request", Fake({"/buildings/resolve": AMBIGUOUS}))
    out = run(energy.list_building_meter_readings.ainvoke({"building_name": "Riverside"}))
    assert out["error"] == "building_not_resolved"
    assert "meters" not in out


def test_the_meter_tool_is_on_the_energy_engine():
    # A tool the agent cannot see is a tool that does not exist, and the symptom is the
    # same "no data" answer this was all written to fix.
    assert "list_building_meter_readings" in {
        t.name for t in energy.ENERGY_INTELLIGENCE_TOOLS
    }


def test_the_documents_tool_is_on_the_main_list_not_the_energy_engine():
    """Where a tool lives decides which questions can reach it.

    Phase 2 engine tools are bound only after content selects that engine, so while the
    documents tool sat on the energy list a question about a building's documents never
    reached it: the answer fell through to semantic search and reported the documents whose
    text mentions the building instead of the ones filed against it. It answers a question
    about any building, so it belongs where every conversation can reach it.
    """
    from src.agents.orchestrator import ALL_TOOLS, PHASE2_ENGINE_TOOLS

    assert "list_building_documents" in {t.name for t in ALL_TOOLS}
    assert "list_building_documents" not in {
        t.name for t in PHASE2_ENGINE_TOOLS["energy_intelligence"]
    }


def test_the_main_list_stays_under_the_provider_cap():
    # OpenAI Chat Completions rejects a tool array longer than 128, which is the reason the
    # engine lists are kept separate in the first place. Adding to the main list spends
    # that budget, so the limit is stated here rather than discovered in production.
    from src.agents.orchestrator import ALL_TOOLS

    assert len(ALL_TOOLS) <= 128


def test_the_invoice_listing_is_registered_on_its_agent():
    from src.agents.contract_performance_agent import CONTRACT_PERFORMANCE_TOOLS

    assert "list_invoices" in {t.name for t in CONTRACT_PERFORMANCE_TOOLS}


def test_the_doc_rag_subagent_can_read_the_building_linkage():
    """select_skill routes document questions to doc_rag, so the tool must be reachable there.

    Putting list_building_documents on the main tool list was necessary and not sufficient.
    select_skill matches "document" and hands the question to the doc_rag sub-agent, whose
    tools were all semantic — so it answered, correctly and uselessly, "I can't access the
    building graph/linkage tools in this session", and whether the user got a real answer
    depended on the main agent happening to call the tool itself afterwards. It did about
    half the time, which is the worst of both: right often enough to look fine.

    Read off the source because the sub-agents are constructed inside a function, at call
    time, with an LLM this test has no business building.
    """
    from pathlib import Path

    from src.agents import meta_tools

    source = Path(meta_tools.__file__).read_text(encoding="utf-8")
    block = source[source.index('"doc_rag": create_react_agent'):]
    block = block[: block.index("prompt=agent_system_prompt")]
    assert "list_building_documents" in block, (
        "the doc_rag sub-agent cannot see the building linkage, so a question routed to it "
        "can only be answered by semantic search over document text"
    )


def test_the_documents_tool_is_importable_where_the_subagent_imports_it():
    # The sub-agent imports it from energy_intelligence_agent; if it moves, this fails here
    # rather than at the first document question in production.
    from src.agents.energy_intelligence_agent import list_building_documents

    assert list_building_documents.name == "list_building_documents"
