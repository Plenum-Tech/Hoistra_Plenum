"""Feature 7 — Stage 8 (Test 1) and Stage 9 (Test 2) validation engines.

Pure functions over the value-centric primitives. They return the exact reports
the spec demands (7.9 / 7.10), including the diagnostics needed for the one-click
remediation paths. No DB/LLM — unit-testable in isolation.

Tables shape: {table_name: {"rows": list[dict], "columns": [..], "pk": [pk_cols]}}.
"""

from __future__ import annotations

from itertools import combinations

from .primitives import THRESHOLDS, distinct_values, value_overlap

TEST1_PASS_RATE_MIN = 0.99  # 7.9 AC3 — fail >1% blocks UDR
TEST2_FAIL_RATE_MAX = 0.01  # 7.10 AC4 — fail >=1% blocks UDR


# ── Stage 8 · Test 1: chunk associations must reference a PRIMARY KEY (7.9) ──────
def _pk_value_index(tables: dict[str, dict]) -> dict[str, set[tuple[str, str]]]:
    """{pk_value: {(table, pk_col), ...}} — the only valid anchors for a chunk."""
    idx: dict[str, set[tuple[str, str]]] = {}
    for t, meta in tables.items():
        for pkc in meta.get("pk", []) or []:
            for v in distinct_values(meta.get("rows", []), pkc):
                idx.setdefault(v, set()).add((t, pkc))
    return idx


def _locate_value(tables: dict[str, dict], value: str) -> list[dict]:
    """Where a value appears as ANY column value → [{table, column}] (fail diagnostics)."""
    out: list[dict] = []
    for t, meta in tables.items():
        for c in meta.get("columns", []):
            if value in distinct_values(meta.get("rows", []), c):
                out.append({"table": t, "column": c})
    return out


def run_test1_chunk_pk(chunks: list[dict], tables: dict[str, dict]) -> dict:
    """7.9 — every chunk's ``entity_id`` must be a PRIMARY KEY value somewhere.

    A pass requires the association value to exist as a PK; a failure reports the
    chunk and the table/column(s) where the value was found (the shared attribute
    that was wrongly used as an anchor) — feeding the 'Fix and Retest' path.
    """
    pk_idx = _pk_value_index(tables)
    total = len(chunks)
    failures: list[dict] = []
    for ch in chunks:
        val = str(ch.get("entity_id", "")).strip()
        if val and val in pk_idx:
            continue
        failures.append(
            {
                "chunk_id": ch.get("chunk_id"),
                "association_value": val,
                "is_primary_key_anywhere": False,
                "found_in": _locate_value(tables, val) if val else [],
            }
        )
    passed = total - len(failures)
    pass_rate = (passed / total) if total else 1.0
    fail_rate = 1.0 - pass_rate
    return {
        "test": "test1_chunk_pk",
        "total_chunks": total,
        "passed": passed,
        "failed": len(failures),
        "pass_rate": round(pass_rate, 4),
        "fail_rate": round(fail_rate, 4),
        "pass_threshold": TEST1_PASS_RATE_MIN,
        "blocks_udr": fail_rate > (1.0 - TEST1_PASS_RATE_MIN) + 1e-9,
        "failures": failures,
    }


# ── 7.6 AC6 + 7.7 AC5 · coverage gate: every column classified AND assigned ──────
def run_coverage_check(tables: dict[str, dict], graph: dict) -> dict:
    """Every source column must (7.6 AC6) carry a PK/FK/Shared classification AND
    (7.7 AC5) have a destination assignment before the UDR is finalised. If either is
    <100%, ``blocks_udr`` is True and the offending columns are listed.
    """
    source_cols = [(t, c) for t, meta in tables.items() for c in meta.get("columns", []) or []]
    total = len(source_cols)
    by_col = {
        (m.get("source_table"), m.get("column")): m
        for m in graph.get("columns", []) or []
        if isinstance(m, dict)
    }
    unclassified: list[dict] = []
    unassigned: list[dict] = []
    for (t, c) in source_cols:
        m = by_col.get((t, c)) or {}
        if m.get("classification") not in ("PK", "FK", "SHARED_ATTRIBUTE"):
            unclassified.append({"table": t, "column": c})
        if not str(m.get("dest_udr_table") or "").strip():
            unassigned.append({"table": t, "column": c})
    classified = total - len(unclassified)
    assigned = total - len(unassigned)
    incomplete = bool(unclassified or unassigned)
    return {
        "test": "coverage_check",
        "total_columns": total,
        "classified": classified,
        "assigned": assigned,
        "classified_pct": round(classified / total, 4) if total else 1.0,
        "assigned_pct": round(assigned / total, 4) if total else 1.0,
        "unclassified": unclassified,
        "unassigned": unassigned,
        "complete": (total > 0) and not incomplete,
        "blocks_udr": incomplete if total else False,
    }


# ── Stage 9 · Test 2: column similarities must be explained by a foreign key (7.10) ─
def _normalize_fks(defined_fks) -> set[frozenset]:
    """Accept [(t1,c1,t2,c2)] or [((t1,c1),(t2,c2))] → set of directionless frozensets."""
    out: set[frozenset] = set()
    for fk in defined_fks or []:
        if len(fk) == 4:
            a, b = (fk[0], fk[1]), (fk[2], fk[3])
        else:
            a, b = tuple(fk[0]), tuple(fk[1])
        out.add(frozenset({a, b}))
    return out


def run_test2_column_fk(
    tables: dict[str, dict],
    defined_fks,
    overlap_threshold: float | None = None,
    coincidental_pairs=None,
) -> dict:
    """7.10 — every ≥30% cross-table column-value overlap must be explained by a FK.

    One unordered pass over cross-table column pairs; a 'hit' is overlap ≥ threshold
    in EITHER direction. A hit with no defined FK is flagged with the 3 resolution
    options. ``fail_rate = flagged / total_pairs_tested``; ≥1% blocks the UDR.

    ``coincidental_pairs`` (7.10 AC6 option 3) are pairs the user documented as an
    intentional value overlap that is NOT a relationship — they are treated as
    explained so the check does not re-flag them on every future run.
    """
    thr = overlap_threshold if overlap_threshold is not None else THRESHOLDS["test2_overlap_flag"]
    fk_set = _normalize_fks(defined_fks)
    coincidental_set = _normalize_fks(coincidental_pairs)
    # child→parents map (directed) so two columns that both reference the SAME parent PK
    # (e.g. after a reference table is created) count as explained — they overlap because
    # they share a parent, which is a defined relationship.
    parents: dict[tuple, set] = {}
    for fk in defined_fks or []:
        ch, pa = ((fk[0], fk[1]), (fk[2], fk[3])) if len(fk) == 4 else (tuple(fk[0]), tuple(fk[1]))
        parents.setdefault(ch, set()).add(pa)
    nodes = [(t, c) for t, meta in tables.items() for c in meta.get("columns", [])]

    total_pairs = 0
    hits: list[dict] = []
    for (ta, ca), (tb, cb) in combinations(nodes, 2):
        if ta == tb:
            continue  # cross-table only
        total_pairs += 1
        o1 = value_overlap(tables[ta]["rows"], ca, tables[tb]["rows"], cb)
        o2 = value_overlap(tables[tb]["rows"], cb, tables[ta]["rows"], ca)
        ov = max(o1, o2)
        if ov < thr:
            continue
        pair = frozenset({(ta, ca), (tb, cb)})
        shared_parent = bool(parents.get((ta, ca), set()) & parents.get((tb, cb), set()))
        by_fk = (pair in fk_set) or shared_parent
        coincidental = pair in coincidental_set
        explained = by_fk or coincidental
        hits.append(
            {
                "table_a": ta,
                "column_a": ca,
                "table_b": tb,
                "column_b": cb,
                "overlap": round(ov, 4),
                "explained_by_fk": by_fk,
                "coincidental": coincidental,
                "explained": explained,
                "resolutions": None
                if explained
                else ["define_fk", "create_reference_table", "mark_coincidental"],
            }
        )

    flagged = [h for h in hits if not h["explained"]]
    fail_rate = (len(flagged) / total_pairs) if total_pairs else 0.0
    return {
        "test": "test2_column_fk",
        "overlap_threshold": thr,
        "total_pairs_tested": total_pairs,
        "similarity_hits": len(hits),
        "explained": len(hits) - len(flagged),
        "flagged_unexplained": len(flagged),
        "fail_rate": round(fail_rate, 4),
        "fail_threshold": TEST2_FAIL_RATE_MAX,
        "blocks_udr": fail_rate >= TEST2_FAIL_RATE_MAX,
        "flags": flagged,
    }


# ── 7.10 AC6/AC7: apply a user's resolution to a flagged pair + re-run Test 2 ────
def _orient_fk(tables: dict[str, dict], a: tuple, b: tuple) -> tuple:
    """Orient a FK child→parent: the column that is a PRIMARY KEY of its table is the
    parent (referenced) side; the other is the child (referencing) side."""
    (ta, ca), (tb, cb) = a, b
    if cb in (tables.get(tb, {}).get("pk") or []):
        return a, b          # child a → parent b
    if ca in (tables.get(ta, {}).get("pk") or []):
        return b, a          # child b → parent a
    return a, b              # neither is a PK — keep given order


def apply_test2_resolutions(
    tables: dict[str, dict],
    defined_fks,
    resolutions,
    *,
    coincidental_pairs=None,
    overlap_threshold: float | None = None,
) -> dict:
    """7.10 AC7 — apply each flagged-pair resolution to the data model, then re-run Test 2.

    Each resolution is ``{table_a, column_a, table_b, column_b, choice}`` where ``choice``
    is one of ``define_fk`` | ``create_reference_table`` | ``mark_coincidental``:
      - define_fk            → formalise a FK edge (oriented child→parent by PK membership).
      - create_reference_table → promote the shared value to a new reference table (PK) and
                                 add FKs from both columns (reuses ``promote_shared_attribute``).
      - mark_coincidental    → record the pair as an intentional non-relationship.

    Returns ``{applied, defined_fks, coincidental_pairs, new_reference_tables, report, tables}``.
    Pure — the caller persists the resulting FKs / reference tables / report.
    """
    from .reference_tables import promote_shared_attribute

    fks: list = list(defined_fks or [])
    coincidental: set = set(_normalize_fks(coincidental_pairs))
    applied: list[dict] = []
    new_ref_tables: list[str] = []

    for r in resolutions or []:
        ta, ca = r.get("table_a"), r.get("column_a")
        tb, cb = r.get("table_b"), r.get("column_b")
        choice = (r.get("choice") or r.get("resolution") or "").strip()
        rec = {"table_a": ta, "column_a": ca, "table_b": tb, "column_b": cb, "choice": choice}
        if not (ta and ca and tb and cb) or not choice:
            applied.append({**rec, "action": "skipped", "reason": "incomplete"})
            continue
        if choice == "define_fk":
            (cT, cC), (pT, pC) = _orient_fk(tables, (ta, ca), (tb, cb))
            fks.append((cT, cC, pT, pC))
            applied.append({**rec, "action": "define_fk", "fk": [cT, cC, pT, pC]})
        elif choice == "create_reference_table":
            res = promote_shared_attribute(tables, [(ta, ca), (tb, cb)])
            ref = res.get("reference_table") or {}
            ref_name = ref.get("table")
            if ref_name and ref.get("pk"):
                tables[ref_name] = {
                    "rows": ref.get("rows", []),
                    "columns": ref.get("columns", []),
                    "pk": ref.get("pk", []),
                }
                new_ref_tables.append(ref_name)
                for rw in res.get("fk_rewrites", []):
                    fks.append(
                        (rw["table"], rw["column"], rw["references_table"], rw["references_column"])
                    )
            applied.append({**rec, "action": "create_reference_table", "reference_table": ref_name})
        elif choice == "mark_coincidental":
            coincidental.add(frozenset({(ta, ca), (tb, cb)}))
            applied.append({**rec, "action": "mark_coincidental"})
        else:
            applied.append({**rec, "action": "skipped", "reason": f"unknown choice '{choice}'"})

    coincidental_list = [sorted([list(p) for p in pair]) for pair in coincidental]
    report = run_test2_column_fk(
        tables, fks, overlap_threshold=overlap_threshold, coincidental_pairs=coincidental_list
    )
    return {
        "applied": applied,
        "defined_fks": fks,
        "coincidental_pairs": coincidental_list,
        "new_reference_tables": new_ref_tables,
        "report": report,
        "tables": tables,
    }
