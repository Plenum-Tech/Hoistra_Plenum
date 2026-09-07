"""Feature 7 — Stage 1 pre-processing (User Flow 7.2).

Pure, ``list[dict]``-row based cleaning that runs BEFORE PK/FK structuring:
NaN/null profiling, exact duplicate-row removal, within-table column-merge
candidate detection, a deterministic column-name fallback, and the human-facing
pre-processing summary. No DB, no LLM, stdlib-only — so it is unit-testable and
reusable by both the migration path and the Fiix path (post data-ingestion).

Thresholds mirror §7.2 of FEATURE7_UDR_UNDERSTANDING.md (single source of truth).
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Callable

from .primitives import (
    Records,
    cell_format,
    column_values,
    format_similarity,
    null_rate,
    value_overlap,
    _nonnull,
)

# ── Thresholds (mirror the spec) ───────────────────────────────────────────────
AUTO_MERGE_MIN = 0.95       # 7.2 AC4 — overlap >= 0.95 ⇒ auto-merge the two columns
REVIEW_MIN = 0.60           # 7.2 AC4 — 0.60 <= overlap < 0.95 ⇒ flag for human review
CROSS_MERGE_MIN = 0.90      # 7.2 AC5 — diff-name columns with >=90% value match ⇒ candidate merge
SPLIT_MAX_OVERLAP = 0.30    # 7.2 AC6 — same-name columns with <30% overlap ⇒ split candidate
LLM_MIN_CONFIDENCE = 0.80   # 7.2 AC5 — LLM name gate before presenting to user


# ── 7.2 AC1: NaN / null profiling ──────────────────────────────────────────────
def nan_stats(rows: Records, columns: list[str]) -> dict[str, float]:
    """7.2 AC1 — per-column null rate across the table.

    Returns {column: null_rate} where null_rate is the fraction of rows whose
    value is null/empty (0.0 = no nulls, 1.0 = entirely null). Reuses the shared
    ``primitives.null_rate`` so the definition of "null" stays consistent with the
    rest of the pipeline.
    """
    return {c: null_rate(rows, c) for c in (columns or [])}


# ── 7.2 AC2: exact duplicate-row removal ────────────────────────────────────────
def _row_signature(row: dict) -> tuple[tuple[str, str], ...]:
    """Order-independent, hashable signature of a row's (column, value) pairs."""
    return tuple(sorted((str(k), "" if v is None else str(v)) for k, v in row.items()))


def remove_exact_duplicate_rows(rows: Records) -> tuple[Records, int]:
    """7.2 AC2 — drop rows that are exact duplicates of an earlier row.

    Order-preserving: the FIRST occurrence of each distinct row is kept, every
    later identical row is removed. A row is identical when its full set of
    (column, value) pairs matches (key order does not matter). Returns
    ``(deduped_rows, removed_count)``.
    """
    seen: set[tuple[tuple[str, str], ...]] = set()
    deduped: Records = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sig = _row_signature(r)
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(r)
    removed = len(rows or []) - len(deduped)
    return deduped, removed


# ── 7.2 AC3 / AC4: within-table column-merge candidates ─────────────────────────
def find_column_merge_candidates(rows: Records, columns: list[str]) -> list[dict]:
    """7.2 AC3/AC4 — propose redundant within-table column pairs to merge.

    For every unordered pair of columns in the SAME table, compute the bidirectional
    distinct value overlap (the max of A→B and B→A so a small column fully contained
    in a larger one still scores high). Then classify by 7.2 thresholds:

      - overlap >= ``AUTO_MERGE_MIN`` (0.95)              → action "auto_merge"
      - ``REVIEW_MIN`` (0.60) <= overlap < ``AUTO_MERGE_MIN`` → action "review"
      - overlap < ``REVIEW_MIN``                          → skipped (not a candidate)

    Each candidate item: {column_a, column_b, overlap (4dp), action,
    sample_a:[3], sample_b:[3]}.
    """
    # De-duplicate column names (CMMS/Excel exports often repeat headers) so a column
    # is never paired with itself, which would otherwise produce a spurious self-merge.
    cols = list(dict.fromkeys(c for c in (columns or []) if c))
    candidates: list[dict] = []
    for a, b in combinations(cols, 2):
        # Bidirectional distinct overlap — symmetric so column order does not matter.
        overlap = max(
            value_overlap(rows, a, rows, b),
            value_overlap(rows, b, rows, a),
        )
        if overlap >= AUTO_MERGE_MIN:
            action = "auto_merge"
        elif overlap >= REVIEW_MIN:
            action = "review"
        else:
            continue  # below review floor — not a merge candidate
        candidates.append(
            {
                "column_a": a,
                "column_b": b,
                "overlap": round(overlap, 4),
                "action": action,
                "sample_a": _nonnull(column_values(rows, a))[:3],
                "sample_b": _nonnull(column_values(rows, b))[:3],
            }
        )
    return candidates


# ── 7.2 AC5 / AC6: column-name proposal (LLM hook + deterministic fallback) ──────
# Maps the dominant primitives.cell_format class → a canonical UDR-style name.
_FORMAT_NAME_MAP = {
    "date": "event_date",
    "id_code": "entity_id",
    "integer": "count_or_id",
    "decimal": "amount_or_measure",
    "boolean": "flag",
    "categorical": "category",
    "free_text": "description",
}
_HEURISTIC_CONFIDENCE = 0.6  # 7.2 AC6 — fixed, modest confidence for the fallback


def _dominant_format(samples: list[Any]) -> str:
    """Most common non-empty cell format across the supplied samples."""
    counts: dict[str, int] = {}
    for s in samples or []:
        fmt = cell_format(s)
        if fmt == "empty":
            continue
        counts[fmt] = counts.get(fmt, 0) + 1
    if not counts:
        return "free_text"
    return max(counts, key=lambda k: counts[k])


def propose_column_name(
    samples: list[Any],
    llm: Callable[[list[Any]], Any] | None = None,
) -> dict:
    """7.2 AC5/AC6 — propose a name for a column from its sample values.

    AC5: when an ``llm`` callable is supplied, defer to it. ``llm(samples)`` may
    return either a bare name (``str``) or ``{"name", "confidence"}``. The model's
    own confidence is preserved; a bare name is assumed to meet the ``LLM_MIN_CONFIDENCE``
    gate (0.80). An empty / ``None`` / blank name (or a raising llm) is rejected and we
    fall back to the heuristic — the caller gates on the returned ``confidence`` so a
    low-confidence proposal still flows but is clearly below the 80% bar.

    AC6 (fallback): a deterministic heuristic over the dominant
    ``primitives.cell_format`` (date → "event_date", id_code → "entity_id",
    integer → "count_or_id", decimal → "amount_or_measure", boolean → "flag",
    categorical → "category", free_text → "description"), at ~0.6 confidence.

    Returns {name, confidence, source}.
    """
    if callable(llm):
        try:
            out = llm(samples)
        except Exception:
            out = None
        name: Any = None
        conf: Any = None
        if isinstance(out, dict):
            name, conf = out.get("name"), out.get("confidence")
        elif isinstance(out, str):
            name, conf = out, LLM_MIN_CONFIDENCE
        if name is not None and str(name).strip():
            c = float(conf) if isinstance(conf, (int, float)) else LLM_MIN_CONFIDENCE
            return {"name": str(name).strip(), "confidence": max(0.0, min(1.0, c)), "source": "llm"}
        # llm produced nothing usable → fall through to the deterministic heuristic.

    fmt = _dominant_format(samples)
    name = _FORMAT_NAME_MAP.get(fmt, "description")
    return {"name": name, "confidence": _HEURISTIC_CONFIDENCE, "source": "heuristic"}


# ── 7.2 AC5: cross-table diff-name / same-value merge candidates ─────────────────
def find_cross_table_merge_candidates(tables: dict[str, dict], llm=None) -> list[dict]:
    """7.2 AC5 — columns in DIFFERENT tables with DIFFERENT names but the SAME data.

    For every cross-table column pair whose names differ, if the bidirectional
    distinct value overlap is ≥ ``CROSS_MERGE_MIN`` (0.90) AND the cell-value FORMAT
    profiles agree, propose merging them to a single canonical column. The name is
    inferred from the combined sample values via :func:`propose_column_name` (LLM
    path when provided), so "AssetID" + "asset_no" holding the same ids collapse to
    one column named from the values — not from either header.

    Returns [{table_a, column_a, table_b, column_b, overlap, proposed_name,
    name_confidence, name_source}].
    """
    nodes = [(t, c) for t, meta in tables.items() for c in (meta.get("columns") or [])]
    out: list[dict] = []
    for (ta, ca), (tb, cb) in combinations(nodes, 2):
        if ta == tb or ca == cb:
            continue  # cross-table, different names (AC5)
        ra, rb = tables[ta]["rows"], tables[tb]["rows"]
        overlap = max(value_overlap(ra, ca, rb, cb), value_overlap(rb, cb, ra, ca))
        if overlap < CROSS_MERGE_MIN:
            continue
        if format_similarity(ra, ca, rb, cb) < 0.80:
            continue  # same values implies same format; guards coincidental id overlaps
        proposal = propose_column_name(
            _nonnull(column_values(ra, ca))[:5] + _nonnull(column_values(rb, cb))[:5], llm=llm
        )
        out.append(
            {
                "table_a": ta, "column_a": ca, "table_b": tb, "column_b": cb,
                "overlap": round(overlap, 4),
                "proposed_name": proposal["name"],
                "name_confidence": proposal["confidence"],
                "name_source": proposal["source"],
            }
        )
    return out


# ── 7.2 AC6: same-name / divergent-values split candidates ───────────────────────
def find_same_name_divergent_columns(tables: dict[str, dict], llm=None) -> list[dict]:
    """7.2 AC6 — columns with the SAME name across tables but DIVERGENT values.

    For every cross-table pair sharing a column name, if the bidirectional distinct
    overlap is < ``SPLIT_MAX_OVERLAP`` (0.30) the two are different things wearing the
    same header and must be split. A distinct name is proposed per side from its own
    values via :func:`propose_column_name`.

    Returns [{column_name, table_a, table_b, overlap, proposed_name_a, proposed_name_b,
    name_confidence_a, name_confidence_b}].
    """
    out: list[dict] = []
    names: dict[str, list[tuple[str, str]]] = {}
    for t, meta in tables.items():
        for c in (meta.get("columns") or []):
            names.setdefault(c, []).append((t, c))
    for name, occ in names.items():
        for (ta, ca), (tb, cb) in combinations(occ, 2):
            ra, rb = tables[ta]["rows"], tables[tb]["rows"]
            overlap = max(value_overlap(ra, ca, rb, cb), value_overlap(rb, cb, ra, ca))
            if overlap >= SPLIT_MAX_OVERLAP:
                continue  # they genuinely share values — not a spurious same-name clash
            pa = propose_column_name(_nonnull(column_values(ra, ca))[:5], llm=llm)
            pb = propose_column_name(_nonnull(column_values(rb, cb))[:5], llm=llm)
            out.append(
                {
                    "column_name": name,
                    "table_a": ta, "table_b": tb,
                    "overlap": round(overlap, 4),
                    "proposed_name_a": pa["name"], "name_confidence_a": pa["confidence"],
                    "proposed_name_b": pb["name"], "name_confidence_b": pb["confidence"],
                }
            )
    return out


# ── 7.2 AC7: pre-processing summary ─────────────────────────────────────────────
def preprocessing_summary(
    nan_removed: int,
    duplicate_rows_removed: int,
    columns_merged: int,
    flagged_for_review: int,
) -> dict:
    """7.2 AC7 — the human-facing roll-up of what Stage 1 changed.

    Pure tallies passed in by the orchestrator: cells/columns dropped for being
    all-null, exact duplicate rows removed, columns auto-merged, and pairs flagged
    for manual review. Tallies are coerced to non-negative ints (AC7 contract).
    Returns a flat report dict.
    """
    def _count(v: Any) -> int:
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    return {
        "stage": "preprocessing",
        "nan_removed": _count(nan_removed),
        "duplicate_rows_removed": _count(duplicate_rows_removed),
        "columns_merged": _count(columns_merged),
        "flagged_for_review": _count(flagged_for_review),
    }
