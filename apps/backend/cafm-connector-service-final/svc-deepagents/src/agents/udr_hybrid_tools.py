"""
Phase 5 — Hybrid UDR tools: structured DB + document vector evidence + cross-source links.
"""
from __future__ import annotations

from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _http_request
from .doc_rag_agent import query_docs, semantic_search
from .meta_tools import get_session_context
from .session_workspace import workspace_snapshot
from .udr_agent import get_schema, udr_read_records


@tool
async def retrieve_workspace_corpus_summary() -> dict:
    """Summarize the current workspace ingestion and UDR readiness (Phase 5 hybrid register)."""
    session_id = get_session_context()
    if not session_id or session_id == "shared":
        return {"error": "No active session"}
    snap = workspace_snapshot(session_id)
    schema = await get_schema.ainvoke({})
    table_count = len((schema.get("tables") or {})) if isinstance(schema, dict) else 0
    return {
        "session_id": session_id,
        "workspace": snap,
        "plenum_cafm_table_count": table_count,
        "guidance": (
            "Use retrieve_vector_evidence for document-backed facts; "
            "udr_read_records / query_table for structured rows."
        ),
    }


@tool
async def retrieve_vector_evidence(query: str, top_k: int = 8) -> dict:
    """Retrieve semantic document chunks (unstructured path) for the workspace corpus."""
    _ = top_k
    return await semantic_search.ainvoke({"query": query})


@tool
async def answer_with_graph_context(question: str, depth: int = 1) -> dict:
    """Graph-driven retrieval for entity/relationship questions.

    Use this whenever the user asks about an entity and its related information — e.g.
    "location based asset details", "work orders for this vendor", "compliance certificates
    for this asset". It (1) expands the question to its RELATED entity cluster via the schema
    PK/FK relationship graph, (2) pulls structured rows for each related table, and (3) pulls
    document chunks — returning one enriched context bundle for you to summarise. The graph
    does the relationship discovery; you only reason + summarise over the result.
    """
    # 1. Plan retrieval: question → related table cluster + FK paths (schema-mapper graph).
    try:
        resp = await _http_request(
            "POST",
            settings.migration_base_url,
            "/api/graph/expand-query",
            service="schema_mapper",
            json={"query": question, "depth": depth, "max_tables": 12},
        )
        plan = resp.json()
    except Exception as exc:  # graph is best-effort — fall back to plain vector search
        docs = await semantic_search.ainvoke({"query": question})
        return {"question": question, "entity_cluster": [], "graph_error": str(exc),
                "document_evidence": docs}

    cluster = list(dict.fromkeys((plan.get("matched_tables") or []) + (plan.get("related_tables") or [])))

    # Keep the bundle small so it never blows the LLM context: a few tables, a few rows
    # each, heavy JSONB columns dropped, long strings truncated.
    _DROP = {"raw_metadata", "document_ids", "embedding", "findings_jsonb"}

    def _slim(row):
        if not isinstance(row, dict):
            return row
        return {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v)
                for k, v in row.items() if k not in _DROP}

    # 2. Pull a few structured rows for the top related tables.
    records: dict = {}
    for t in cluster[:4]:
        try:
            res = await udr_read_records.ainvoke({"table": t, "limit": 3, "offset": 0})
            rows = res.get("records") if isinstance(res, dict) else res
            records[t] = [_slim(r) for r in rows][:3] if isinstance(rows, list) else res
        except Exception:
            continue

    # 3. Pull the top-3 document chunks for the question.
    try:
        docs = await query_docs.ainvoke({"query": question, "top_k": 3})
    except Exception:
        docs = None

    return {
        "question": question,
        "entity_cluster": cluster,
        "relationships": plan.get("relationships", []),
        "primary_keys": plan.get("primary_keys", {}),
        "structured_records": records,
        "records_answer": await _records_answer(question),
        "document_evidence": docs,
        "note": (
            "Answer from records_answer - the records the question names, found however they are "
            "named. structured_records are a few rows per related table, NOT filtered to the question: "
            "never conclude a record is missing because it is not among them. relationships show how "
            "the entities connect (PK/FK)."
        ),
    }


async def _records_answer(question: str):
    """The records engine's answer for the question (agents/ontology_qa.answer_question: the plan,
    the resolution ladder, the caller's scope), or None when it cannot read (29 Oct 2026)."""
    try:
        from . import ontology_qa

        return await ontology_qa.answer_question(question)
    except Exception as exc:  # noqa: BLE001 - the graph bundle stands without it
        return {"ok": False, "error": str(exc)[:200]}


@tool
async def resolve_cross_source_links(
    entity_hint: str,
    query: str,
    table: str = "assets",
    id_column: str = "asset_code",
) -> dict:
    """Link structured UDR rows with unstructured document evidence for one entity (Phase 5)."""
    structured = await udr_read_records.ainvoke(
        {
            "table": table,
            "limit": 5,
            "offset": 0,
        }
    )
    vector = await query_docs.ainvoke({"query": f"{entity_hint} {query}", "top_k": 3})
    return {
        "entity_hint": entity_hint,
        "records_answer": await _records_answer(f"{query} for {entity_hint}".strip()),
        "structured_sample": structured,
        "document_answer": vector,
        "note": ("records_answer holds the record entity_hint names, found however it is named. "
                 "structured_sample is the first rows of the table, NOT filtered: never conclude the "
                 "entity is missing from it. Citations are in document_answer."),
    }
