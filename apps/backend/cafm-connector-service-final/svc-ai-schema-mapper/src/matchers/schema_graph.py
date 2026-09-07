"""Schema relationship graph + query expansion for graph-driven RAG retrieval.

Turns a natural-language question ("location based asset details") into the set of
RELATED tables/columns to pull, by walking the PK/FK relationship graph derived from
the plenum_cafm schema. The chat/orchestrator calls `expand_query()` to plan retrieval:
the returned cluster (tables + FK paths + columns) tells the RAG layer exactly which
records + vector chunks to fetch before the LLM summarizes.

Pure: derived from plenum_cafm_schema (TABLES + PRIMARY_KEYS); no DB, no LLM.
"""
from __future__ import annotations

import re
from functools import lru_cache


def _singular(t: str) -> str:
    if t.endswith("ies"):
        return t[:-3] + "y"
    if t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


@lru_cache(maxsize=1)
def build_schema_graph() -> dict:
    """Build the table/column/FK relationship graph from the plenum_cafm schema.

    Returns {"tables": {t: {"pk", "columns", "fks": [{"column","references"}]}},
             "referenced_by": {t: [{"table","column"}]}}   (reverse FK index).
    """
    from .plenum_cafm_schema import TABLES, PRIMARY_KEYS

    sing2tbl: dict[str, str] = {}
    for t in TABLES:
        sing2tbl.setdefault(_singular(t), t)

    def fk_target(table: str, col: str) -> str | None:
        for suf in ("_id", "_ref", "_reference"):
            if col.endswith(suf) and len(col) > len(suf):
                ent = col[: -len(suf)].rstrip("_")
                for cand in (ent, sing2tbl.get(ent), ent + "s"):
                    if cand and cand in TABLES and cand != table and cand in PRIMARY_KEYS:
                        return cand
        return None

    tables: dict[str, dict] = {}
    referenced_by: dict[str, list] = {}
    for t, cols in TABLES.items():
        fks = []
        for c in cols:
            ref = fk_target(t, c)
            if ref:
                fks.append({"column": c, "references": ref})
                referenced_by.setdefault(ref, []).append({"table": t, "column": c})
        tables[t] = {"pk": PRIMARY_KEYS.get(t), "columns": cols, "fks": fks}
    return {"tables": tables, "referenced_by": referenced_by}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _match_tables(query: str, graph: dict) -> list[str]:
    """Tables whose name (or a column name) matches a query term."""
    terms = [t for t in _norm(query).split() if len(t) >= 3]
    if not terms:
        return []
    scored: list[tuple[int, str]] = []
    for t, meta in graph["tables"].items():
        score = 0
        tn = _norm(t)
        for term in terms:
            if term in tn or _singular(term) in tn:
                score += 3
            elif any(term in _norm(c) for c in meta["columns"]):
                score += 1
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in scored]


def expand_query(query: str, *, depth: int = 1, max_tables: int = 15) -> dict:
    """Plan graph-driven retrieval for a natural-language question.

    Finds the tables the query is about, then walks FK edges (out: this table's FKs;
    in: tables that reference this table's PK) up to `depth` hops to gather the related
    entity cluster. Returns what the RAG layer should pull:

        {"query", "matched_tables", "related_tables", "relationships":
            [{"from_table","from_column","to_table","kind"}], "primary_keys": {t: pk}}
    """
    graph = build_schema_graph()
    matched = _match_tables(query, graph)[:5]
    if not matched:
        return {"query": query, "matched_tables": [], "related_tables": [],
                "relationships": [], "primary_keys": {}}

    cluster = list(dict.fromkeys(matched))
    rels: list[dict] = []
    frontier = list(matched)
    for _ in range(max(1, depth)):
        nxt: list[str] = []
        for t in frontier:
            for fk in graph["tables"].get(t, {}).get("fks", []):  # outgoing FK
                rels.append({"from_table": t, "from_column": fk["column"],
                             "to_table": fk["references"], "kind": "references"})
                if fk["references"] not in cluster:
                    cluster.append(fk["references"]); nxt.append(fk["references"])
            for src in graph["referenced_by"].get(t, []):          # incoming FK
                rels.append({"from_table": src["table"], "from_column": src["column"],
                             "to_table": t, "kind": "referenced_by"})
                if src["table"] not in cluster:
                    cluster.append(src["table"]); nxt.append(src["table"])
            if len(cluster) >= max_tables:
                break
        frontier = nxt
        if len(cluster) >= max_tables:
            break

    cluster = cluster[:max_tables]
    clset = set(cluster)
    rels = [r for r in rels if r["from_table"] in clset and r["to_table"] in clset]
    # de-dupe relationships
    seen = set()
    uniq = []
    for r in rels:
        key = (r["from_table"], r["from_column"], r["to_table"])
        if key not in seen:
            seen.add(key); uniq.append(r)
    return {
        "query": query,
        "matched_tables": matched,
        "related_tables": [t for t in cluster if t not in matched],
        "relationships": uniq,
        "primary_keys": {t: graph["tables"][t]["pk"] for t in cluster if graph["tables"][t]["pk"]},
    }
