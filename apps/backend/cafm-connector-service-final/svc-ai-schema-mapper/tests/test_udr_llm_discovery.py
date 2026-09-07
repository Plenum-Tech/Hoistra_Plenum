"""Feature 7 Job 1 — LLM-backed semantic discovery (pure helpers + runner with fakes).
Run: python tests/test_udr_llm_discovery.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

import udr.persistence as persistence  # noqa: E402
from udr.llm_discovery import (  # noqa: E402
    build_discovery_clusters,
    extract_entity_texts,
    make_discovery_prompt,
    parse_discovered_edges,
    run_semantic_discovery,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── extract narrative text per entity ────────────────────────────────────────
tables = {
    "work_orders": [
        {"id": "WO-1", "description": "recurring damp on north wall of Level 3"},
        {"id": "WO-2", "description": "facade inspection — minor crack noted on north elevation"},
        {"id": "WO-3", "status": "open"},  # no narrative
    ],
    "vendors": [{"id": "V-1", "vendor_name": "Apex"}],  # short, no narrative
}
texts = extract_entity_texts(tables)
check("work_orders yields 2 narrative snippets", len(texts.get("work_orders", [])) == 2)
check("vendors (no narrative) omitted", "vendors" not in texts)

# ── cluster carries schema-related entities ──────────────────────────────────
graph = {"relationships": [{"src_entity": "work_orders", "dst_entity": "assets"}]}
clusters = build_discovery_clusters(graph, texts)
check("one cluster for work_orders", len(clusters) == 1 and clusters[0]["entity"] == "work_orders")
check("cluster lists related entity", clusters[0]["related"] == ["assets"])

# ── prompt mentions the entity + the records ─────────────────────────────────
prompt = make_discovery_prompt(clusters[0])
check("prompt names entity + record text", "work_orders" in prompt and "north wall" in prompt)

# ── parse edges from a fenced, prose-wrapped response ─────────────────────────
raw = (
    'Sure:\n```json\n[{"src_entity":"work_orders","dst_entity":"work_orders",'
    '"rel_type":"SAME_DEFECT","confidence":0.8,"evidence":"north wall == north elevation"}]\n```'
)
edges = parse_discovered_edges(raw)
check("parses one edge from fenced json", len(edges) == 1 and edges[0]["rel_type"] == "SAME_DEFECT")
check("non-json parses to empty list", parse_discovered_edges("no json here") == [])


# ── full runner with a fake Anthropic client + stubbed persistence ───────────
class _Content:
    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text):
        self.content = [_Content(text)]


class _Messages:
    async def create(self, **kw):
        return _Msg(
            '[{"src_entity":"work_orders","dst_entity":"work_orders",'
            '"rel_type":"SAME_DEFECT","confidence":0.8,"evidence":"same defect twice"}]'
        )


class FakeClient:
    def __init__(self):
        self.messages = _Messages()


_persisted = {"edges": None}


async def fake_persist(edges, *, run_id, organization_id=None, session=None):
    _persisted["edges"] = edges
    return len(edges)


persistence.persist_relationships = fake_persist

res = asyncio.run(run_semantic_discovery(tables, graph, run_id="r1", client=FakeClient()))
check("runner discovered + persisted 1 edge", res["discovered"] == 1)
check("persisted edge is provenance=llm_inferred", _persisted["edges"][0]["provenance"] == "llm_inferred")
check(
    "no narrative text -> safe no-op",
    asyncio.run(run_semantic_discovery({}, graph, run_id="r1", client=FakeClient()))
    == {"discovered": 0, "clusters": 0, "edges": []},
)

print("\nALL TESTS PASSED")
