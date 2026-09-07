"""Feature 7 Job 1 — semantic relationship DISCOVERY (foundation).

The orchestration LLM reads across an entity cluster (asset history, work-order descriptions,
inspection notes, contractor reports) and surfaces IMPLICIT relationships the schema cannot
encode — recurring faults, causal chains, operational dependencies. These are written back as
edges with ``provenance='llm_inferred'`` — clearly distinguished from schema edges and never
treated as ground truth, only as enriched context.

This module is the PURE foundation: it normalizes LLM-proposed connections into the
``UdrEntityRelationship`` edge shape with ``provenance='llm_inferred'``. The actual LLM
cross-cluster read is an INJECTABLE hook (mirrors ``udr.preprocessing.propose_column_name``),
so the structured part is unit-testable now and the prompt/model can land later. Persistence
reuses :func:`udr.persistence.persist_relationships` — the unique edge key includes
``provenance``, so inferred edges coexist with schema edges without collision.
"""

from __future__ import annotations

LLM_INFERRED = "llm_inferred"


def normalize_discovered_edge(raw: dict, *, default_rel: str = "RELATED") -> dict | None:
    """Coerce one LLM-proposed connection to the edge shape, stamped ``llm_inferred``.
    Returns ``None`` if it lacks a src/dst entity."""
    src = raw.get("src_entity") or raw.get("src") or raw.get("from")
    dst = raw.get("dst_entity") or raw.get("dst") or raw.get("to")
    if not src or not dst:
        return None
    conf = raw.get("confidence")
    try:
        conf = float(conf) if conf is not None else 0.5
    except (TypeError, ValueError):
        conf = 0.5
    return {
        "src_entity": str(src),
        "src_column": raw.get("src_column"),
        "rel_type": str(raw.get("rel_type") or raw.get("relationship") or default_rel),
        "dst_entity": str(dst),
        "dst_column": raw.get("dst_column"),
        "provenance": LLM_INFERRED,
        "confidence": max(0.0, min(1.0, conf)),
        "evidence": raw.get("evidence") or raw.get("rationale"),
    }


def normalize_discovered_edges(raw_edges) -> list[dict]:
    """Normalize a batch of LLM-proposed connections; drops malformed ones."""
    out: list[dict] = []
    for r in raw_edges or []:
        if isinstance(r, dict):
            e = normalize_discovered_edge(r)
            if e is not None:
                out.append(e)
    return out


async def discover_relationships(
    clusters,
    *,
    llm=None,
    run_id: str,
    organization_id=None,
    session=None,
) -> dict:
    """Run Job-1 discovery over entity clusters and persist the inferred edges.

    ``clusters`` is whatever the ``llm`` hook understands (e.g. ``[{entity, text}]``). ``llm``
    is a callable ``cluster -> list[dict]`` of proposed connections; when it is ``None`` this
    is a no-op (foundation present, model not wired). Returns ``{discovered, edges}``."""
    if llm is None:
        return {"discovered": 0, "edges": []}

    edges: list[dict] = []
    for cluster in clusters or []:
        try:
            proposed = llm(cluster)
        except Exception:
            proposed = None
        edges.extend(normalize_discovered_edges(proposed))

    if not edges:
        return {"discovered": 0, "edges": []}

    from .persistence import persist_relationships

    written = await persist_relationships(
        edges, run_id=run_id, organization_id=organization_id, session=session
    )
    return {"discovered": written, "edges": edges}
