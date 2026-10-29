"""The graph-context tools read the record the question names, not the first rows of a table (29 Oct 2026).

answer_with_graph_context pulled three arbitrary rows from each related table and resolve_cross_source_links
five, then told the model to "filter client-side by entity_hint". A building outside that sample — any
building added after the first few, JASMEET among them — read as missing. Both now carry the records
engine's own answer for the question (the ladder that finds a record however it is named), and the
samples are marked as samples.
"""
import asyncio

from src.agents import udr_hybrid_tools as uh


class _T:
    def __init__(self, value):
        self.value, self.calls = value, []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self.value


def _patch(monkeypatch, answer):
    seen = []

    async def _answer(question, principal=None):
        seen.append(question)
        return answer

    class _Resp:
        def json(self):
            return {"matched_tables": ["buildings"], "related_tables": ["floors"]}

    async def _http(*a, **k):
        return _Resp()

    import src.agents.ontology_qa as oq
    monkeypatch.setattr(oq, "answer_question", _answer)
    monkeypatch.setattr(uh, "_http_request", _http)
    monkeypatch.setattr(uh, "udr_read_records", _T({"records": [{"name": "Bishopsgate Tower"}]}))
    monkeypatch.setattr(uh, "query_docs", _T({"answer": ""}))
    monkeypatch.setattr(uh, "semantic_search", _T({}))
    return seen


def test_graph_context_carries_the_records_answer_for_the_question(monkeypatch):
    found = {"ok": True, "found": True, "rows": [{"name": "JASMEET", "floors": 89}]}
    seen = _patch(monkeypatch, found)
    out = asyncio.run(uh.answer_with_graph_context.ainvoke({"question": "Show all recorded details for Jasmeet"}))
    assert seen == ["Show all recorded details for Jasmeet"]
    assert out["records_answer"] == found
    assert "not filtered" in out["note"].lower()


def test_cross_source_links_read_the_named_entity(monkeypatch):
    found = {"ok": True, "found": True, "rows": [{"name": "JASMEET"}]}
    seen = _patch(monkeypatch, found)
    out = asyncio.run(uh.resolve_cross_source_links.ainvoke(
        {"entity_hint": "JASMEET BUILDING", "query": "floors", "table": "buildings", "id_column": "name"}))
    assert seen and "JASMEET BUILDING" in seen[0]
    assert out["records_answer"] == found
