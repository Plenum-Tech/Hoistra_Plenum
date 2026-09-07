"""Feature 7 Job 1 — semantic relationship DISCOVERY (LLM-backed runner).

The pure edge-shaping foundation lives in :mod:`udr.semantic_discovery` (normalise a
proposed connection into a ``provenance='llm_inferred'`` edge). This module supplies the two
halves the spec flagged as missing:

  1. a REAL LLM cluster-reader — reads an entity cluster's free text (work-order descriptions,
     inspection notes) and proposes IMPLICIT relationships the schema cannot encode
     (recurring faults, the same defect described twice, causal chains), and
  2. an async RUNNER that assembles clusters from a UDR run and persists the inferred edges,

so the job actually produces edges in the production path — invoked from
``graph/nodes/udr_node.py`` (OFF by default; enable with ``UDR_SEMANTIC_DISCOVERY=1`` and an
Anthropic key, because it costs LLM calls). The pure helpers (``extract_entity_texts``,
``build_discovery_clusters``, ``parse_discovered_edges``) are unit-testable without an LLM.
"""
from __future__ import annotations

import json
import logging

from .semantic_discovery import normalize_discovered_edges

logger = logging.getLogger(__name__)

# Column-name hints for narrative text, plus a length floor for "looks like free text".
_TEXT_HINT_COLS = (
    "description", "desc", "notes", "note", "comment", "comments", "remark", "remarks",
    "summary", "details", "detail", "fault", "issue", "observation", "finding", "diagnosis",
)
_FREE_TEXT_MIN_LEN = 25


def extract_entity_texts(tables: dict, *, max_per_entity: int = 12) -> dict[str, list[str]]:
    """PURE — ``{table: [snippet, ...]}`` of free-text content per entity, to feed the LLM.

    Picks cells in columns whose name hints at narrative text, or any string value long
    enough to read as prose. Entities with no narrative content are omitted."""
    out: dict[str, list[str]] = {}
    for name, rows in (tables or {}).items():
        snippets: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if not isinstance(val, str):
                    continue
                v = val.strip()
                if not v:
                    continue
                lc = str(col).lower()
                if (any(h in lc for h in _TEXT_HINT_COLS) and len(v) >= 3) or len(v) >= _FREE_TEXT_MIN_LEN:
                    snippets.append(f"{col}: {v}")
                    if len(snippets) >= max_per_entity:
                        break
            if len(snippets) >= max_per_entity:
                break
        if snippets:
            out[name] = snippets[:max_per_entity]
    return out


def build_discovery_clusters(graph: dict, texts: dict, *, max_clusters: int = 20) -> list[dict]:
    """PURE — one cluster per entity that has narrative text, carrying its schema-related
    entities so the LLM can reason across the cluster. ``texts`` is the
    :func:`extract_entity_texts` output. Entities with no text are skipped (nothing to read)."""
    related: dict[str, set] = {}
    for r in (graph or {}).get("relationships", []) or []:
        s, d = r.get("src_entity"), r.get("dst_entity")
        if s and d:
            related.setdefault(s, set()).add(d)
            related.setdefault(d, set()).add(s)
    clusters: list[dict] = []
    for entity, snippets in texts.items():
        clusters.append(
            {"entity": entity, "related": sorted(related.get(entity, set())), "texts": snippets}
        )
        if len(clusters) >= max_clusters:
            break
    return clusters


def make_discovery_prompt(cluster: dict) -> str:
    """PURE — the user prompt for one cluster."""
    rel = ", ".join(cluster.get("related") or []) or "(none recorded)"
    texts = "\n".join(f"- {t}" for t in (cluster.get("texts") or [])[:12])
    return f"""You are reviewing operational records for entity "{cluster.get('entity')}".
Schema-defined related entities: {rel}

Free-text records:
{texts}

Identify IMPLICIT relationships these records imply that the schema does NOT already encode
— for example two records describing the SAME underlying defect from different angles, a
recurring fault on the same equipment, or a causal chain between events. Only surface
genuinely non-obvious connections; do NOT restate the schema relationships listed above.

Return JSON only — an array (empty if nothing implicit is found):
[
  {{"src_entity": "...", "dst_entity": "...",
    "rel_type": "RECURRING_FAULT|SAME_DEFECT|CAUSAL|DEPENDS_ON|RELATED",
    "confidence": 0.0-1.0, "evidence": "one-line justification"}}
]"""


def parse_discovered_edges(text: str) -> list[dict]:
    """PURE — extract the JSON array of proposed edges from an LLM response, tolerant of
    ```json fences and surrounding prose. Returns a list of raw dicts; never raises."""
    if not text:
        return []
    s = text.strip()
    if "```" in s:
        start = s.find("```")
        nl = s.find("\n", start)
        if nl != -1:
            s = s[nl + 1:]
        end = s.find("```")
        if end != -1:
            s = s[:end]
    lo, hi = s.find("["), s.rfind("]")
    if lo != -1 and hi > lo:
        s = s[lo:hi + 1]
    try:
        data = json.loads(s)
    except Exception:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


async def run_semantic_discovery(
    tables: dict,
    graph: dict,
    *,
    run_id: str,
    organization_id=None,
    session=None,
    client=None,
    model: str = "claude-haiku-4-5-20251001",
    max_clusters: int = 20,
) -> dict:
    """Assemble clusters from a UDR run, ask the LLM for implicit relationships, and persist
    them as ``provenance='llm_inferred'`` edges. Returns ``{discovered, clusters, edges}``.

    Safe no-op when there is no narrative text. Each cluster's LLM failure is isolated so one
    bad response never sinks the whole run."""
    texts = extract_entity_texts(tables)
    clusters = build_discovery_clusters(graph, texts, max_clusters=max_clusters)
    if not clusters:
        return {"discovered": 0, "clusters": 0, "edges": []}

    if client is None:
        from ..app import get_anthropic_client

        client = get_anthropic_client()

    raw_edges: list[dict] = []
    for cluster in clusters:
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=1024,
                messages=[{"role": "user", "content": make_discovery_prompt(cluster)}],
            )
            txt = resp.content[0].text if resp and getattr(resp, "content", None) else ""
            raw_edges.extend(parse_discovered_edges(txt))
        except Exception as exc:  # isolate one cluster's failure
            logger.warning(f"[llm_discovery] cluster '{cluster.get('entity')}' failed: {exc}")

    edges = normalize_discovered_edges(raw_edges)
    if not edges:
        return {"discovered": 0, "clusters": len(clusters), "edges": []}

    from .persistence import persist_relationships

    written = await persist_relationships(
        edges, run_id=run_id, organization_id=organization_id, session=session
    )
    logger.info(
        f"[llm_discovery] run {run_id}: {written} llm_inferred edges from {len(clusters)} clusters"
    )
    return {"discovered": written, "clusters": len(clusters), "edges": edges}
