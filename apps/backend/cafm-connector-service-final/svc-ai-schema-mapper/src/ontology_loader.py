"""Runtime accessor for the UDR ontology / semantic-mapping RAG foundation.

Loads the ontology JSON (canonical_entities / synonyms / relationships /
hierarchy / mapping dictionaries) from ``svc-ai-schema-mapper/ontology/`` and
exposes lookup helpers for the mapping engine. Pure read-only; cached.

This is the single integration seam between the ontology (Phase I-B) and the
LangGraph mapper. It does NOT change existing matcher behaviour — nodes opt in
by calling these helpers (e.g. resolve_table / resolve_column) alongside the
existing matchers in ``src/matchers/``.

Env override: set ``UDR_ONTOLOGY_DIR`` to point at an alternate ontology dir.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


def _ontology_dir() -> Path:
    override = os.environ.get("UDR_ONTOLOGY_DIR")
    if override:
        return Path(override)
    # src/ontology_loader.py -> service root -> ontology/
    return Path(__file__).resolve().parent.parent / "ontology"


def _norm(term: str) -> str:
    """Normalise a source term for matching: lowercase, non-alnum -> single _."""
    return re.sub(r"[^a-z0-9]+", "_", (term or "").lower()).strip("_")


@lru_cache(maxsize=1)
def _load_json_cached(path_str: str, _mtime: float) -> Any:
    with open(path_str, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load(name: str) -> Any:
    p = _ontology_dir() / name
    # include mtime in the cache key so edits are picked up without a restart
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    return _load_json_cached(str(p), mtime)


# ── Raw documents ────────────────────────────────────────────────────────────
def canonical_entities() -> list[dict]:
    return _load("canonical_entities.json").get("entities", [])


def synonyms() -> dict:
    return _load("synonyms.json")


def relationships() -> list[dict]:
    return _load("relationships.json").get("relationships", [])


def hierarchy() -> dict:
    return _load("hierarchy.json")


def deterministic_aliases() -> dict:
    return _load(os.path.join("mapping_dictionaries", "deterministic_aliases.json"))


# ── Derived indexes ──────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def canonical_table_names() -> tuple[str, ...]:
    return tuple(e["canonical_table"] for e in canonical_entities())


def entity_by_table(table: str) -> Optional[dict]:
    for e in canonical_entities():
        if e["canonical_table"] == table:
            return e
    return None


def _table_alias_index() -> dict[str, str]:
    """normalised term -> canonical table (entity aliases + deterministic dict)."""
    idx: dict[str, str] = {}
    for e in canonical_entities():
        ct = e["canonical_table"]
        idx[_norm(ct)] = ct
        idx[_norm(e["entity"])] = ct
        for a in e.get("aliases", []):
            idx.setdefault(_norm(a), ct)
    for term, ct in (deterministic_aliases().get("table_aliases") or {}).items():
        idx.setdefault(_norm(term), ct)
    return idx


def _table_synonym_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for s in synonyms().get("table_synonyms", []):
        idx.setdefault(_norm(s["source_term"]),
                       {"canonical_table": s["canonical_table"],
                        "confidence": s.get("confidence", 0.9),
                        "cmms_system": s.get("cmms_system")})
    return idx


def _column_synonym_index() -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for s in synonyms().get("column_synonyms", []):
        idx.setdefault(_norm(s["source_term"]), []).append({
            "canonical_table": s.get("canonical_table"),
            "canonical_column": s.get("canonical_column"),
            "confidence": s.get("confidence", 0.9),
            "cmms_system": s.get("cmms_system"),
        })
    return idx


# ── Resolution helpers (deterministic + RAG-synonym tiers) ───────────────────
def resolve_table(term: str) -> Optional[dict]:
    """Resolve a source table name to a canonical table.

    Returns {canonical_table, confidence, method} or None. Deterministic exact
    alias first (1.0), then CMMS synonym (its confidence). Semantic NLP is left
    to the existing semantic mapper for unresolved terms.
    """
    n = _norm(term)
    alias = _table_alias_index().get(n)
    if alias:
        return {"canonical_table": alias, "confidence": 1.0, "method": "deterministic"}
    syn = _table_synonym_index().get(n)
    if syn:
        return {"canonical_table": syn["canonical_table"], "confidence": syn["confidence"],
                "method": "rag_synonym", "cmms_system": syn.get("cmms_system")}
    return None


def resolve_column(term: str, dest_table: Optional[str] = None) -> Optional[dict]:
    """Resolve a source column to a canonical column, optionally constrained to
    a destination table. Deterministic alias (per-table) first, then synonym."""
    n = _norm(term)
    if dest_table:
        col_aliases = (deterministic_aliases().get("column_aliases") or {}).get(dest_table, {})
        for src, canon in col_aliases.items():
            if _norm(src) == n:
                return {"canonical_table": dest_table, "canonical_column": canon,
                        "confidence": 1.0, "method": "deterministic"}
        # exact canonical column name
        ent = entity_by_table(dest_table)
        if ent and any(_norm(c["name"]) == n for c in ent.get("standard_columns", [])):
            return {"canonical_table": dest_table, "canonical_column": term,
                    "confidence": 1.0, "method": "deterministic"}
    matches = _column_synonym_index().get(n, [])
    if dest_table:
        matches = [m for m in matches if m["canonical_table"] == dest_table] or matches
    if matches:
        best = max(matches, key=lambda m: m["confidence"])
        return {"canonical_table": best["canonical_table"], "canonical_column": best["canonical_column"],
                "confidence": best["confidence"], "method": "rag_synonym",
                "cmms_system": best.get("cmms_system")}
    return None


def recommended_structure_md() -> str:
    return hierarchy().get("recommended_structure_md", "")


if __name__ == "__main__":  # smoke test — no DB required
    print("ontology dir:", _ontology_dir())
    print("canonical tables:", len(canonical_table_names()))
    print("relationships:", len(relationships()))
    print("hierarchy spine:", recommended_structure_md())
    for t in ["WO_Header", "EQUI", "assets_export", "manpower", "unknown_xyz"]:
        print(f"  resolve_table({t!r}) ->", resolve_table(t))
    for c, dt in [("WONUM", "work_orders"), ("install_date", "assets"), ("asset_make", None)]:
        print(f"  resolve_column({c!r}, {dt!r}) ->", resolve_column(c, dt))
