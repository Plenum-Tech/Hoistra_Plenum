"""Feature 7 7.3 — unique-table identification (PURE).

Two source sheets can describe the SAME entity under different names ('work_tasks' vs
'assets') due to human-entry inconsistency. This collapses them BEFORE mapping, deciding on
FIVE metadata dimensions simultaneously (7.3 AC1):
  (1) table name, (2) primary-key column identity, (3) column names,
  (4) 3 sample values per column, (5) total column count.

Decision (7.3 AC2/AC3):
  - name similarity >= 0.95 AND metadata similarity >= 0.80  -> auto-consolidate (no prompt),
  - name similarity <  0.95 AND metadata similarity >= 0.80  -> consolidation CANDIDATE (confirm),
  - otherwise                                                -> unique (kept separate).

Pure (operates on the ``build_table_metadata`` dicts) so the thresholds + grouping are
unit-testable. ``apply_consolidation`` merges the auto groups' rows for the pipeline.
"""

from __future__ import annotations

import difflib
import re

NAME_SIM_AUTO = 0.95       # 7.3 AC2
METADATA_SIM_MIN = 0.80    # 7.3 AC2/AC3

# metadata-similarity dimension weights. Schema identity (column names + PK + column count)
# is the reliable signal for "same entity": two sheets of the same entity usually hold
# DIFFERENT rows, so exact sample-value overlap is a weak, secondary confirmation only.
_W_COLNAMES = 0.45
_W_PK = 0.25
_W_COLCOUNT = 0.15
_W_SAMPLES = 0.15


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s).lower())


def _norm_name(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def table_name_similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, _norm_name(a), _norm_name(b)).ratio(), 4)


def _colset(meta: dict) -> set:
    return {_norm(c) for c in meta.get("column_names", []) or []}


def _pkset(meta: dict) -> set:
    return {_norm(c) for c in (meta.get("primary_key") or [])}


def _sampleset(meta: dict) -> set:
    out: set = set()
    for vals in (meta.get("samples_by_column") or {}).values():
        for v in vals or []:
            out.add(str(v).strip().lower())
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def metadata_similarity(meta_a: dict, meta_b: dict) -> float:
    """Composite similarity over dimensions 2-5 (PK identity, column names, sample values,
    column count). 0.0-1.0."""
    colname_sim = _jaccard(_colset(meta_a), _colset(meta_b))
    pka, pkb = _pkset(meta_a), _pkset(meta_b)
    pk_sim = _jaccard(pka, pkb) if (pka or pkb) else 0.0
    sample_sim = _jaccard(_sampleset(meta_a), _sampleset(meta_b))
    ca, cb = int(meta_a.get("column_count", 0) or 0), int(meta_b.get("column_count", 0) or 0)
    colcount_sim = 1.0 - abs(ca - cb) / max(ca, cb, 1)
    return round(
        _W_COLNAMES * colname_sim + _W_SAMPLES * sample_sim + _W_PK * pk_sim + _W_COLCOUNT * colcount_sim,
        4,
    )


def _find(parent: dict, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def identify_unique_tables(
    table_metas: list[dict],
    *,
    name_auto: float = NAME_SIM_AUTO,
    metadata_min: float = METADATA_SIM_MIN,
) -> dict:
    """Decide which source tables are the same entity. ``table_metas`` is a list of
    ``build_table_metadata`` dicts. Returns ``{groups, auto_consolidated, candidates, unique}``."""
    names = [m.get("table") for m in table_metas]
    by_name = {m.get("table"): m for m in table_metas}
    parent = {n: n for n in names}
    candidates: list[dict] = []

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            name_sim = table_name_similarity(a, b)
            meta_sim = metadata_similarity(by_name[a], by_name[b])
            if meta_sim < metadata_min:
                continue
            if name_sim >= name_auto:
                # auto-consolidate (7.3 AC2)
                parent[_find(parent, a)] = _find(parent, b)
            else:
                # consolidation candidate — user confirms (7.3 AC3)
                candidates.append(
                    {
                        "tables": [a, b],
                        "name_similarity": name_sim,
                        "metadata_similarity": meta_sim,
                        "confidence": meta_sim,
                    }
                )

    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(_find(parent, n), []).append(n)

    auto_consolidated = [sorted(members) for members in groups.values() if len(members) > 1]
    unique = sorted(members[0] for members in groups.values() if len(members) == 1)
    return {
        "groups": {rep: sorted(members) for rep, members in groups.items()},
        "auto_consolidated": sorted(auto_consolidated),
        "candidates": candidates,
        "unique": unique,
    }


def apply_consolidation(tables_in: dict[str, list[dict]], auto_consolidated: list[list[str]]) -> dict:
    """Merge the rows of each auto-consolidated group under one representative table name
    (the alphabetically-first member). Tables not in any group are passed through unchanged."""
    grouped: dict[str, str] = {}  # member -> representative
    for group in auto_consolidated or []:
        members = [m for m in group if m in tables_in]
        if len(members) < 2:
            continue
        rep = sorted(members)[0]
        for m in members:
            grouped[m] = rep

    out: dict[str, list[dict]] = {}
    for name, rows in (tables_in or {}).items():
        rep = grouped.get(name, name)
        out.setdefault(rep, [])
        out[rep].extend(rows or [])
    return out
