"""Value-centric column analysis — identify and merge columns by their DATA, not names.

Covers two requirements the name-based matcher can't:

  (2) Different column names, SAME values  → they are the same column; merge to one.
      e.g. ASSETNUM and asset_ref both hold A-001, A-002 … → one asset-id column.
  (3) Same column name, DIFFERENT values   → decide the real meaning from the values.
      e.g. an 'id' column of W-1001… is a work-order ref, not an asset id.

Pure: operates on list[dict] records; no DB, no LLM.
"""
from __future__ import annotations

import re

_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}")
_CODE_RE = re.compile(r"^[A-Za-z]{0,6}[-_/]?\d+[A-Za-z0-9\-_/]*$")


def _norm_val(v) -> str:
    return re.sub(r"\s+", " ", str(v).strip().lower())


def value_set(records: list, column: str, *, scan_limit: int = 5000) -> frozenset:
    """Distinct non-empty normalized values of a column (capped for cost)."""
    out: set[str] = set()
    for r in (records or [])[:scan_limit]:
        if not isinstance(r, dict):
            continue
        v = r.get(column)
        if v is not None and str(v).strip() != "":
            out.add(_norm_val(v))
    return frozenset(out)


def jaccard(a: frozenset, b: frozenset) -> float:
    """Overlap of two value sets: |A∩B| / |A∪B| (0..1)."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def row_agreement(records: list, col_a: str, col_b: str, *, scan_limit: int = 5000) -> float:
    """Fraction of rows where two columns hold the SAME value, PER ROW (over rows where at least
    one side is non-empty).

    This is the correct test for "the same column recorded twice" (ASSETNUM == asset_ref in every
    row → merge). It deliberately differs from value-SET :func:`jaccard`, which only measures
    whether two columns draw from the same VOCABULARY. Two distinct columns can share a vocabulary
    yet never agree per row — e.g. ``primary_skill`` (HVAC) and ``secondary_skill`` (Plumbing) for
    the same technician: identical value sets, but they are NOT the same field and must not merge.
    """
    agree = 0
    total = 0
    for i, r in enumerate(records or []):
        if i >= scan_limit:
            break
        if not isinstance(r, dict):
            continue
        a = _norm_val(r.get(col_a))
        b = _norm_val(r.get(col_b))
        if a == "" and b == "":
            continue  # both blank — no evidence either way
        total += 1
        if a == b:
            agree += 1
    return agree / total if total else 0.0


def find_duplicate_column_groups(
    records: list,
    columns: list,
    *,
    threshold: float = 0.95,
    min_values: int = 2,
) -> list[list[str]]:
    """Group columns that are the SAME field recorded under different names — merge them.

    "Same field" is decided by PER-ROW agreement (:func:`row_agreement`), NOT value-set overlap:
    two columns merge only when they hold the same value in (near) every row (ASSETNUM == asset_ref
    on every row). Columns that merely share a VOCABULARY but differ per row are NOT merged —
    e.g. ``primary_skill`` and ``secondary_skill`` both draw from the skill list, but a technician's
    primary skill (HVAC) is not their secondary skill (Plumbing), so they stay separate.

    Returns groups of size >= 2, in input order. Columns with fewer than min_values distinct values
    are ignored (too sparse to judge — e.g. an all-blank or single-value column).
    """
    sets: dict[str, frozenset] = {}
    for c in columns:
        s = value_set(records, c)
        if len(s) >= min_values:
            sets[c] = s

    cols = [c for c in columns if c in sets]
    parent = {c: c for c in cols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            # Value-set overlap is a cheap pre-filter (columns that share no vocabulary can't be
            # the same field); the MERGE decision is per-row agreement, so same-vocabulary but
            # distinct columns (primary/secondary skill) are not collapsed.
            if jaccard(sets[cols[i]], sets[cols[j]]) < threshold:
                continue
            if row_agreement(records, cols[i], cols[j]) >= threshold:
                union(cols[i], cols[j])

    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(find(c), []).append(c)
    # preserve input order within and across groups
    ordered = [g for g in (groups[k] for k in groups) if len(g) > 1]
    ordered.sort(key=lambda g: columns.index(g[0]))
    for g in ordered:
        g.sort(key=lambda c: columns.index(c))
    return ordered


REVIEW_OVERLAP_MIN = 0.60   # below AUTO_MERGE (0.95): partial similarity → ask the user
AUTO_MERGE_MIN = 0.95


def _samples(records: list, col: str, k: int = 3) -> list:
    out = []
    for r in (records or []):
        if isinstance(r, dict):
            v = r.get(col)
            if v is not None and str(v).strip() != "":
                out.append(str(v)[:60])
                if len(out) >= k:
                    break
    return out


def find_near_duplicate_pairs(
    records: list,
    columns: list,
    *,
    review_min: float = REVIEW_OVERLAP_MIN,
    auto_min: float = AUTO_MERGE_MIN,
    min_values: int = 2,
) -> list[dict]:
    """Column PAIRS that PARTIALLY agree PER ROW (review_min <= agreement < auto_min) — too
    similar to ignore but not identical enough to auto-merge, so the user is PROMPTED to decide
    whether to merge (Challenge 1). Returns [{column_a, column_b, overlap, sample_a, sample_b}].

    Uses PER-ROW agreement, not value-set overlap, for the same reason as
    :func:`find_duplicate_column_groups`: two columns that share a vocabulary but hold different
    values per row (primary_skill vs secondary_skill) are distinct fields, not merge candidates.
    """
    sets = {c: value_set(records, c) for c in (columns or [])}
    sets = {c: s for c, s in sets.items() if len(s) >= min_values}
    cols = [c for c in (columns or []) if c in sets]
    out: list[dict] = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            # Vocabulary overlap gates the (cheap) candidate set; the review signal is per-row.
            if jaccard(sets[cols[i]], sets[cols[j]]) < review_min:
                continue
            ov = row_agreement(records, cols[i], cols[j])
            if review_min <= ov < auto_min:
                out.append({
                    "column_a": cols[i],
                    "column_b": cols[j],
                    "overlap": round(ov, 2),
                    "sample_a": _samples(records, cols[i]),
                    "sample_b": _samples(records, cols[j]),
                })
    return out


def value_class(records_or_values, column: str | None = None) -> str:
    """Coarse value class of a column (or a raw value list): code / integer / decimal /
    date / boolean / enum / text / unknown — used to decide a column's real meaning from
    its data (requirement 3)."""
    if column is not None:
        vals = [str(r.get(column)) for r in (records_or_values or [])
                if isinstance(r, dict) and r.get(column) not in (None, "")]
    else:
        vals = [str(v) for v in (records_or_values or []) if str(v).strip() != ""]
    vals = [v.strip() for v in vals if v.strip() != ""][:50]
    if not vals:
        return "unknown"
    low = [v.lower() for v in vals]
    if all(v in ("true", "false", "yes", "no", "y", "n") for v in low):
        return "boolean"

    def _is_int(s):
        try:
            int(s.replace(",", ""))
            return True
        except ValueError:
            return False

    def _is_float(s):
        try:
            float(s.replace(",", ""))
            return True
        except ValueError:
            return False

    if all(_is_int(v) for v in vals):
        return "integer"
    if all(_is_float(v) for v in vals):
        return "decimal"
    if all(_DATE_RE.match(v) for v in vals):
        return "date"
    if all(_CODE_RE.match(v) for v in vals):
        return "code"
    if all(len(v) <= 24 and " " not in v for v in vals):
        return "enum"
    return "text"


def is_identity_like(records: list, column: str, *, min_uniqueness: float = 0.85) -> bool:
    """True if a column's values look like an identity / reference key — high uniqueness
    AND a code/integer value shape. Lets the system name a column from its data even when
    the header is uninformative (requirement 2/3)."""
    vals = [str(r.get(column)) for r in (records or [])
            if isinstance(r, dict) and r.get(column) not in (None, "")]
    vals = [v.strip() for v in vals if v.strip() != ""]
    if len(vals) < 3:
        return False
    uniqueness = len(set(_norm_val(v) for v in vals)) / len(vals)
    return uniqueness >= min_uniqueness and value_class(vals) in ("code", "integer")
