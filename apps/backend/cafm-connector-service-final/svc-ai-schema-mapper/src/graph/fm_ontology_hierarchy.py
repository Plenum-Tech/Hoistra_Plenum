"""FM ontology hierarchy (parent → child) for ontology-aware hierarchy detection (#7).

Mirrors the frontend fm-ontology.ts taxonomy, keyed by canonical plenum_cafm
table names. Used to SUPPLEMENT the FK scan + Claude semantic pass with
FM-ontology-implied parent→child edges at the hierarchy gate.

Safety: only edges backed by a REAL referencing column are emitted (no phantom
FKs), and every edge is relationship_type="ONTOLOGY" so the customer confirms or
rejects it at Gate 2 — nothing is silently applied.
"""
from __future__ import annotations

import re

# Canonical FM parent → ordered children (plenum_cafm table names).
# Sites → Buildings → Floors → Spaces → Assets → Work Orders → Work Tasks, plus
# the asset/work-order/vendor/tenant side branches.
FM_CHILDREN: dict[str, list[str]] = {
    "sites": ["buildings", "assets", "utilities", "locations"],
    "buildings": ["floors"],
    "floors": ["spaces"],
    "spaces": ["assets"],
    "locations": ["assets"],
    "assets": [
        "work_orders", "scheduled_pm", "preventive_maintenance",
        "maintenance_history", "meter_readings", "asset_readings", "certificates",
    ],
    "work_orders": ["work_order_tasks", "work_order_parts", "resources"],
    "vendors": ["contracts", "vendor_contracts"],
    "tenants": ["contracts"],
}

# child → parent lookup (first parent wins).
FM_PARENT: dict[str, str] = {}
for _p, _kids in FM_CHILDREN.items():
    for _k in _kids:
        FM_PARENT.setdefault(_k, _p)

_CANON_KEYS = set(FM_CHILDREN) | set(FM_PARENT)


def _norm(s: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _sing(s: str) -> str:
    n = _norm(s)
    return n[:-1] if n.endswith("s") else n


def canon_key(name: object) -> str | None:
    """Map a source table / canonical name to its FM ontology key (plural/singular tolerant)."""
    n = _norm(name)
    if not n:
        return None
    for key in _CANON_KEYS:
        nk = _norm(key)
        if n == nk or _sing(n) == _sing(nk):
            return key
    return None


def _find_ref_column(cols: list[str], parent: str) -> str | None:
    """A real column in the child table that references `parent` (by name/ontology),
    e.g. site_id / site_code / location for parent 'sites'. None → no real ref."""
    p = _sing(parent)
    syn = {p}
    if p in ("site", "location"):
        syn |= {"site", "location"}
    for c in cols:
        nc = _norm(c)
        for s in syn:
            if nc in (s, f"{s}id", f"{s}code", f"{s}ref", f"{s}key", f"{s}no"):
                return c
            if nc.startswith(s) and (nc.endswith("id") or nc.endswith("code") or nc.endswith("ref")):
                return c
    return None


def derive_ontology_edges(
    source_to_canon: dict[str, str | None],
    columns_by_table: dict[str, list[str]],
) -> list[dict]:
    """FM ontology parent→child edges for present tables, backed by a real FK column.

    Returns FK-dict-shaped edges (source_table, source_column, target_table,
    target_column, relationship_type=ONTOLOGY, confidence, reasoning).
    """
    canon_to_sources: dict[str, list[str]] = {}
    for src, canon in source_to_canon.items():
        if canon:
            canon_to_sources.setdefault(canon, []).append(src)

    edges: list[dict] = []
    for src, canon in source_to_canon.items():
        if not canon:
            continue
        parent = FM_PARENT.get(canon)
        if not parent:
            continue
        parent_sources = canon_to_sources.get(parent)
        if not parent_sources:
            continue  # parent entity not present in this import
        parent_src = parent_sources[0]
        if parent_src == src:
            continue
        fk_col = _find_ref_column(columns_by_table.get(src, []), parent)
        if not fk_col:
            continue  # no real referencing column → skip (never propose a phantom FK)
        edges.append({
            "source_table": src,
            "source_column": fk_col,
            "target_table": parent_src,
            "target_column": "id",
            "relationship_type": "ONTOLOGY",
            "confidence": 0.6,
            "data_match_rate": 0.0,
            "reasoning": f"FM ontology: {canon} belongs under {parent} (via {fk_col}).",
            "ontology_inferred": True,
        })
    return edges
