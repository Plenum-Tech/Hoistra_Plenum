"""Feature 7 — Stage 10: relationship graph + final metadata (pure).

Consumes the resolved structured layer (tables + PK/FK/Shared classification from
``classify_columns``) and emits:
  - relationships : pre-computed schema-level edges (asset→site, WO→asset, …),
    provenance-labelled 'schema' (LLM-inferred edges from Job 1 are added later
    with provenance 'llm_inferred').
  - tables        : final table metadata (7.11 AC3) — PK, PK associations, FK
    count/columns/associations, all columns, 3 samples, column count.
  - columns       : final column metadata (7.11 AC4).

The output is what the Postgres relationship-metadata layer stores and what the
orchestration agent queries (the 'work cloud'). No DB/LLM here.
"""

from __future__ import annotations

from .primitives import _nonnull, build_column_metadata, column_values


def build_relationship_graph(
    tables: dict[str, dict],
    classification: dict[tuple[str, str], dict],
    dest_table_by_source: dict[str, str] | None = None,
) -> dict:
    """Stage 10. ``tables`` = {name: {rows, columns, pk:[..]}}; ``classification``
    is the output of ``classify_columns``."""
    dest = dest_table_by_source or {}

    # ── edges from FK classifications (referential integrity already enforced) ──
    relationships: list[dict] = []
    fk_by_table: dict[str, list[dict]] = {}
    for (t, c), info in classification.items():
        if info.get("classification") == "FK" and info.get("references"):
            ref = info["references"]
            relationships.append(
                {
                    "src_entity": t,
                    "src_column": c,
                    "rel_type": "REFERENCES",
                    "dst_entity": ref["table"],
                    "dst_column": ref["column"],
                    "provenance": "schema",
                    "confidence": info.get("referential_integrity", 1.0),
                }
            )
            fk_by_table.setdefault(t, []).append(
                {
                    "column": c,
                    "references_table": ref["table"],
                    "references_column": ref["column"],
                }
            )

    # ── final table metadata (7.11 AC3) ──
    table_meta: list[dict] = []
    for t, meta in tables.items():
        cols = list(meta.get("columns", []))
        rows = meta.get("rows", [])
        fks = fk_by_table.get(t, [])
        pk_assoc = [
            {"from_table": r["src_entity"], "from_column": r["src_column"]}
            for r in relationships
            if r["dst_entity"] == t
        ]
        table_meta.append(
            {
                "table": t,
                "dest_udr_table": dest.get(t),
                "primary_key": list(meta.get("pk", []) or []),
                "pk_associations": pk_assoc,
                "fk_count": len(fks),
                "fk_columns": [f["column"] for f in fks],
                "fk_associations": fks,
                "column_names": cols,
                "samples_by_column": {c: _nonnull(column_values(rows, c))[:3] for c in cols},
                "column_count": len(cols),
            }
        )

    # ── final column metadata (7.11 AC4) ──
    column_meta: list[dict] = []
    for t, meta in tables.items():
        rows = meta.get("rows", [])
        for c in meta.get("columns", []):
            info = classification.get((t, c), {})
            column_meta.append(
                build_column_metadata(
                    t, c, rows,
                    classification=info.get("classification"),
                    dest_table=dest.get(t),
                )
            )

    return {"tables": table_meta, "columns": column_meta, "relationships": relationships}


def neighbors(graph: dict, table: str) -> list[dict]:
    """Stage 10 AC6 — the entity 'work cloud': related tables in BOTH directions."""
    out: list[dict] = []
    for r in graph.get("relationships", []):
        if r["src_entity"] == table:
            out.append({"table": r["dst_entity"], "via": r["src_column"], "direction": "out", "rel_type": r["rel_type"]})
        elif r["dst_entity"] == table:
            out.append({"table": r["src_entity"], "via": r["dst_column"], "direction": "in", "rel_type": r["rel_type"]})
    return out


def build_work_cloud(relationships: list[dict], entity_type: str) -> dict:
    """F7-3 — the entity 'work cloud' (7.11 AC6) for one entity TYPE: every related entity
    type in BOTH directions, each edge carrying its join columns + rel_type + provenance +
    confidence. ``relationships`` is the edge list — from :func:`build_relationship_graph`
    or the persisted ``UdrEntityRelationship`` rows (same shape). Pure (no DB)."""
    items: list[dict] = []
    related: list[str] = []
    for r in relationships or []:
        src, dst = r.get("src_entity"), r.get("dst_entity")
        common = {
            "rel_type": r.get("rel_type", "REFERENCES"),
            "provenance": r.get("provenance", "schema"),
            "confidence": r.get("confidence"),
        }
        if src == entity_type:
            related.append(dst)
            items.append(
                {"direction": "out", "via_column": r.get("src_column"),
                 "related_entity": dst, "related_column": r.get("dst_column"), **common}
            )
        elif dst == entity_type:
            related.append(src)
            items.append(
                {"direction": "in", "via_column": r.get("dst_column"),
                 "related_entity": src, "related_column": r.get("src_column"), **common}
            )

    seen: set = set()
    related_types: list[str] = []
    for t in related:
        if t and t not in seen:
            seen.add(t)
            related_types.append(t)
    return {"entity": entity_type, "related_entity_types": related_types, "relationships": items}
