"""Feature 7 — value-centric UDR primitives (pure, data-row based).

Operates on ``list[dict]`` records (the same shape as ``parsed_tables`` / Node-1
output). No DB, no LLM, stdlib-only — so it is unit-testable and reusable by the
migration path and the Fiix path (post data-ingestion).

Thresholds mirror §1 of FEATURE7_UDR_UNDERSTANDING.md (single source of truth).
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations
from typing import Any, Iterable

# ── Thresholds (mirror the spec) ───────────────────────────────────────────────
THRESHOLDS = {
    "format_similarity_min": 0.80,          # 7.6 AC2
    "value_pattern_similarity_min": 0.80,   # 7.6 AC3
    "referential_integrity_min": 0.95,      # 7.6 AC4/AC5
    "test2_overlap_flag": 0.30,             # 7.10 AC2
    "composite_pk_max_cols": 3,             # 7.3 AC5 (notional cap)
}

Records = list[dict]
Classification = str  # "PK" | "FK" | "SHARED_ATTRIBUTE"


# ── small helpers ──────────────────────────────────────────────────────────────
def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _nonnull(values: Iterable[Any]) -> list[str]:
    return [s for s in (_norm(v) for v in values) if s != ""]


def column_values(rows: Records, col: str) -> list[Any]:
    return [r.get(col) for r in (rows or []) if isinstance(r, dict)]


def distinct_values(rows: Records, col: str) -> set[str]:
    return set(_nonnull(column_values(rows, col)))


# ── Stage 2: primary-key detection (7.3 AC4-6) ─────────────────────────────────
def null_rate(rows: Records, col: str) -> float:
    """Fraction of rows whose value is null/empty (0.0 = no nulls)."""
    if not rows:
        return 1.0
    vals = column_values(rows, col)
    if not vals:
        return 1.0
    return 1.0 - (len(_nonnull(vals)) / len(vals))


def uniqueness(rows: Records, col: str) -> float:
    """Distinct non-null values / total rows (1.0 = unique per row)."""
    if not rows:
        return 0.0
    return len(distinct_values(rows, col)) / len(rows)


def is_primary_key_column(rows: Records, col: str) -> bool:
    """7.3 AC4 — qualifies iff uniqueness==100% AND null rate==0%."""
    return null_rate(rows, col) == 0.0 and uniqueness(rows, col) >= 1.0


def _composite_unique_nonnull(rows: Records, combo: tuple[str, ...]) -> bool:
    seen: set[tuple[str, ...]] = set()
    for r in rows:
        key = tuple(_norm(r.get(c)) for c in combo)
        if "" in key:           # non-null required on every part
            return False
        if key in seen:         # uniqueness across the combination
            return False
        seen.add(key)
    return True


def _pk_name_rank(col: str) -> int:
    low = col.lower()
    if re.search(r"(^|_)id$", low):
        return 0
    if re.search(r"(^|_)(code|key|no|number)$", low):
        return 1
    return 2


def _tie_break_reason(naturals: list[str], chosen: str) -> str:
    """Human-readable reason a single natural PK was chosen among the qualifying candidates."""
    if len(naturals) == 1:
        return "only candidate"
    rank = _pk_name_rank(chosen)
    if rank == 0:
        return "name rank *_id"
    if rank == 1:
        return "name rank code/key/no"
    return "first qualifying column"


def primary_key_report(rows: Records, columns: list[str]) -> dict:
    """Stage 2 / 7.3 AC4-6 — primary-key detection WITH diagnostics (B8.1).

    Same hard gate as :func:`detect_primary_key` (uniqueness==100% AND null==0%) but also
    surfaces the evidence the UI's Primary-Key Detection table needs:
    ``{kind, columns, surrogate, uniqueness, null_rate, candidate_count, tie_break,
    method, confidence}``.
    """
    cols = [c for c in columns if c]
    naturals = [c for c in cols if is_primary_key_column(rows, c)]
    if naturals:
        ordered = sorted(naturals, key=lambda c: (_pk_name_rank(c), cols.index(c)))
        chosen = ordered[0]
        return {
            "kind": "natural",
            "columns": [chosen],
            "surrogate": False,
            "uniqueness": round(uniqueness(rows, chosen), 4),
            "null_rate": round(null_rate(rows, chosen), 4),
            "candidate_count": len(naturals),
            "tie_break": _tie_break_reason(naturals, chosen),
            "method": "single-column null + unique test",
            "confidence": 1.0,
        }

    max_n = min(int(THRESHOLDS["composite_pk_max_cols"]), len(cols))
    for n in range(2, max_n + 1):
        for combo in combinations(cols, n):
            if _composite_unique_nonnull(rows, combo):
                return {
                    "kind": "composite",
                    "columns": list(combo),
                    "surrogate": False,
                    "uniqueness": 1.0,
                    "null_rate": 0.0,
                    "candidate_count": 0,
                    "tie_break": f"smallest jointly-unique combo ({n} cols)",
                    "method": "composite null + unique test",
                    "confidence": 0.9,
                }

    return {
        "kind": "surrogate",
        "columns": ["_udr_id"],
        "surrogate": True,
        "uniqueness": 1.0,
        "null_rate": 0.0,
        "candidate_count": 0,
        "tie_break": "no natural/composite key — generated surrogate",
        "method": "surrogate fallback",
        "confidence": 0.5,
    }


def detect_primary_key(rows: Records, columns: list[str]) -> dict:
    """Stage 2 / 7.3 AC4-6. Returns {kind, columns, surrogate}.

    kind: 'natural' (single col) | 'composite' (2..N cols) | 'surrogate'.
    Heuristics (id/code naming) only *order* equally-qualified candidates — the
    hard gate is always uniqueness==100% AND null==0%. Thin wrapper over
    :func:`primary_key_report` (which also carries the B8.1 diagnostics)."""
    rep = primary_key_report(rows, columns)
    return {"kind": rep["kind"], "columns": rep["columns"], "surrogate": rep["surrogate"]}


# ── Stage 4a / 5: cell-value format profiling (7.6 AC1) ─────────────────────────
#: A "categorical" column may have at most this fraction of DISTINCT values. Above it the column
#: is near-unique (an identifier code / serial), not an enumerable category. 0.5 keeps true enums
#: (asset_type 14/200, criticality 4/200) categorical while re-bucketing unique codes (model,
#: serial: 200/200) and code-like columns (location 116/200) out of the categorical class.
_CATEGORICAL_MAX_DISTINCT_RATIO = 0.5


def cell_format(value: Any) -> str:
    """Coarse format class of a single cell.

    One of: empty | integer | decimal | date | boolean | id_code | categorical
    | free_text.
    """
    s = _norm(value)
    if s == "":
        return "empty"
    if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", s) or re.fullmatch(r"[-+]?\d+", s):
        return "integer"
    if re.fullmatch(r"[-+]?\d*\.\d+", s):
        return "decimal"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", s) or re.search(
        r"\b\d{1,4}[/\-]\d{1,2}[/\-]\d{1,4}\b", s
    ):
        return "date"
    if s.lower() in {"true", "false", "yes", "no", "y", "n"}:
        return "boolean"
    if re.fullmatch(r"[A-Za-z]{1,8}[-_ ]?\d{2,}", s) or re.fullmatch(
        r"[0-9a-fA-F]{8}-?[0-9a-fA-F-]{8,}", s
    ):
        return "id_code"
    if len(s) <= 40 and " " not in s:
        return "categorical"
    return "free_text"


# Column names that denote a human-readable LABEL, never an identifier code. The
# value-only cell_format() can't tell 'Plant-01' (a site *name*) from 'S-001' (a site *id*) —
# both profile as 'id_code'. When the column name itself says "this is a name/title/label",
# an id_code profile is a false positive, so we demote it so a name column never lands in the
# same format group as the table's real id column (B17.1).
_LABEL_NAME_RE = re.compile(
    r"(^|_)(name|title|label|description|desc|comment|note|notes|remark|remarks|summary)s?($|_)",
    re.IGNORECASE,
)


def column_format(rows: Records, col: str, sample: int = 200) -> str:
    """Dominant cell format across (a sample of) a column's non-null values.

    A column whose *name* marks it as a human label (``*_name``, ``title``, ``description`` …)
    is never reported as ``id_code`` — that class is reserved for identifier columns, so a
    ``site_name`` cannot be grouped with ``site_id``. Such a column is reported as ``categorical``
    (short, single-token labels) or ``free_text`` (contains spaces) instead."""
    vals = _nonnull(column_values(rows, col))[:sample]
    if not vals:
        return "empty"
    counts: dict[str, int] = {}
    for v in vals:
        f = cell_format(v)
        counts[f] = counts.get(f, 0) + 1
    fmt = max(counts, key=lambda k: counts[k])
    if fmt == "id_code" and _LABEL_NAME_RE.search(col):
        # Re-bucket the id_code-looking values by their non-id shape (space ⇒ free_text).
        has_space = any(" " in _norm(v) for v in vals)
        return "free_text" if has_space else "categorical"
    if fmt == "categorical":
        # A categorical column is a SMALL, REPEATED vocabulary (status, criticality, asset_type).
        # A near-unique short token is an identifier code, not an enum — cell_format() can't tell
        # 'M-Y3UFN0'/'SN-DSG2QFPODP' (unique serials/models) from 'FCU'/'Low' at the CELL level,
        # so guard at the COLUMN level: when most values are distinct it is NOT categorical. This
        # stops unique codes grouping with true enums in B17.1's within-table format grouping.
        distinct = len({_norm(v).lower() for v in vals})
        if not _LABEL_NAME_RE.search(col) and distinct / len(vals) >= _CATEGORICAL_MAX_DISTINCT_RATIO:
            has_space = any(" " in _norm(v) for v in vals)
            return "free_text" if has_space else "id_code"
    return fmt


def format_distribution(rows: Records, col: str, sample: int = 200) -> dict[str, float]:
    vals = _nonnull(column_values(rows, col))[:sample]
    if not vals:
        return {"empty": 1.0}
    counts: dict[str, int] = {}
    for v in vals:
        f = cell_format(v)
        counts[f] = counts.get(f, 0) + 1
    n = len(vals)
    dist = {f: c / n for f, c in counts.items()}
    # Keep the distribution CONSISTENT with column_format: a near-unique column whose cells look
    # 'categorical' is really an identifier code (id_code) / free_text at the COLUMN level. Move its
    # categorical mass accordingly so the FORMAT gate does not falsely match a unique-code column
    # (model 'M-Y3UFN0') against a true enum (frequency 'Quarterly') just because some cells share
    # the coarse 'categorical' shape. Mirrors the cardinality guard in column_format().
    if "categorical" in dist and not _LABEL_NAME_RE.search(col):
        distinct = len({_norm(v).lower() for v in vals})
        if distinct / len(vals) >= _CATEGORICAL_MAX_DISTINCT_RATIO:
            has_space = any(" " in _norm(v) for v in vals)
            tgt = "free_text" if has_space else "id_code"
            dist[tgt] = dist.get(tgt, 0.0) + dist.pop("categorical")
    return dist


def format_similarity(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """Format-distribution agreement between two columns (0..1).

    Sum of min-share per shared format class — 1.0 when the two columns have the
    same format profile. Used as the Stage-5 Step-1 FORMAT gate.
    """
    da = format_distribution(rows_a, col_a)
    db = format_distribution(rows_b, col_b)
    if "empty" in da and len(da) == 1:
        return 0.0
    if "empty" in db and len(db) == 1:
        return 0.0
    shared = set(da) & set(db)
    return sum(min(da[f], db[f]) for f in shared)


# ── Stage 9 / 5: value overlap + referential integrity ──────────────────────────
def value_overlap(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """7.10 AC2 — |distinct(A) ∩ distinct(B)| / |distinct(A)|."""
    a = distinct_values(rows_a, col_a)
    if not a:
        return 0.0
    b = distinct_values(rows_b, col_b)
    return len(a & b) / len(a)


def referential_integrity(
    child_rows: Records, child_col: str, parent_rows: Records, parent_col: str
) -> float:
    """7.6 AC4 — fraction of child *occurrences* (rows) whose value exists in the
    parent PK column. Per-row (not distinct): standard FK validity — every FK
    value must resolve to a real PK. Distinct overlap is Test 2's metric instead.
    """
    child_vals = _nonnull(column_values(child_rows, child_col))
    if not child_vals:
        return 0.0
    parent = distinct_values(parent_rows, parent_col)
    matched = sum(1 for v in child_vals if v in parent)
    return matched / len(child_vals)


# ── Stage 5 Step 1: group source columns + classify PK / FK / Shared ────────────
def value_shape(value: Any, max_len: int = 60) -> str:
    """Coarse VALUE-SHAPE skeleton — collapses runs of digits / upper / lower
    letters into class characters with their count, keeping punctuation literal.

    Distinguishes value patterns the format-class profile cannot:
      'A-001'    → 'A-#3'      'AHU-9000' → 'A3-#4'      (both 'id_code'!)
      'S-01'     → 'A-#2'      'W-1001'   → 'A-#4'
      'Gen2'     → 'Aa2#'      'Siemens'  → 'Aa6'
      '2026-01-04' → '#4-#2-#2'

    The character classes ('#', 'A', 'a') with run counts encode the value's
    *shape* compactly. Long free-text values get truncated by ``max_len``.
    """
    s = _norm(value)
    if not s:
        return ""
    s = s[:max_len]
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        cls = None
        if ch.isdigit():
            cls = "#"
        elif ch.isupper():
            cls = "A"
        elif ch.islower():
            cls = "a"
        if cls is None:
            out.append(ch)
            i += 1
            continue
        j = i
        while j < n and (
            (cls == "#" and s[j].isdigit())
            or (cls == "A" and s[j].isupper())
            or (cls == "a" and s[j].islower())
        ):
            j += 1
        run = j - i
        out.append(cls + (str(run) if run > 1 else ""))
        i = j
    return "".join(out)


def value_shape_distribution(rows: Records, col: str, sample: int = 200) -> dict[str, float]:
    """Empirical distribution of value-shape skeletons over a column's non-null values."""
    vals = _nonnull(column_values(rows, col))[:sample]
    if not vals:
        return {"": 1.0}
    counts = Counter(value_shape(v) for v in vals)
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}


def dominant_value_shape(rows: Records, col: str, sample: int = 200) -> str:
    """Most common value-shape skeleton in a column (B18.1 display grouping)."""
    dist = value_shape_distribution(rows, col, sample)
    if "" in dist and len(dist) == 1:
        return ""
    candidates = [(k, v) for k, v in dist.items() if k]
    if not candidates:
        return ""
    return max(candidates, key=lambda kv: kv[1])[0]


def value_shape_similarity(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """Stricter Step-2 value-pattern check — compares VALUE-SHAPE distributions
    (not coarse format classes). Sum of min-share per shared shape — 1.0 when
    every value of both columns follows the same skeleton (e.g. both are 'A-#3').

    Why a second pass after :func:`format_similarity`: two columns can both be
    'id_code' but carry different value shapes — 'A-001' vs 'AHU-9000' — and
    therefore should NOT group. This catches that case.
    """
    da = value_shape_distribution(rows_a, col_a)
    db = value_shape_distribution(rows_b, col_b)
    if "" in da and len(da) == 1:
        return 0.0
    if "" in db and len(db) == 1:
        return 0.0
    shared = set(da) & set(db)
    return sum(min(da[k], db[k]) for k in shared)


def _value_pattern_similarity(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """Stage-5 Step-2 similarity — uses VALUE-SHAPE comparison so 'A-001' and
    'AHU-9000' are correctly separated even though both profile as 'id_code'."""
    return value_shape_similarity(rows_a, col_a, rows_b, col_b)


def _jaccard_value_overlap(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """Jaccard overlap of the two columns' DISTINCT value sets (case-insensitive).

    Used to keep heterogeneous free-text / categorical columns apart: two
    columns only group if they share enough actual values (e.g. 'trade' columns
    both containing {Lift, Mechanical, Fire}), not just a similar shape.
    """
    sa = {v.lower() for v in distinct_values(rows_a, col_a)}
    sb = {v.lower() for v in distinct_values(rows_b, col_b)}
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0


def _containment_overlap(rows_a: Records, col_a: str, rows_b: Records, col_b: str) -> float:
    """Max one-way containment of the two columns' DISTINCT value sets.

    Returns max(|A ∩ B| / |A|, |A ∩ B| / |B|). Captures the FK→PK direction
    that Jaccard misses: a small FK column whose every value is also in the
    PK gets containment 1.0 even when Jaccard is low (because the PK has
    many extra rows the FK doesn't reference).
    """
    sa = {v.lower() for v in distinct_values(rows_a, col_a)}
    sb = {v.lower() for v in distinct_values(rows_b, col_b)}
    if not sa or not sb:
        return 0.0
    inter = sa & sb
    return max(len(inter) / len(sa), len(inter) / len(sb))


# Format classes that have inherently variable per-row shapes — value-shape
# similarity is a poor signal here; require actual value-set overlap instead.
_VARIABLE_SHAPE_FORMATS = {"free_text", "categorical"}
_VALUE_OVERLAP_MIN = 0.50  # Jaccard threshold for variable-shape grouping

# Format classes where two columns with the same shape can still belong to
# unrelated entities (e.g. WorkOrders.id W-#### vs Resources.id R-##). For
# these we require value-set containment (FK→PK style overlap) on top of the
# shape match, so id_codes from disjoint domains stop colliding.
_VALUE_OVERLAP_REQUIRED_FORMATS = {"id_code", "integer", "decimal", "date"}
_CONTAINMENT_MIN = 0.50

# Plain numeric MEASURES (integer/decimal) and DATES share value shapes/ranges far too easily —
# a 0/1 flag ('sla_breached') "contains into" any column that holds a 0 or 1 ('cost_vendor_aed'),
# and any two timestamp columns ('created_at' vs 'completed_at') overlap in time. So containment
# alone wrongly groups semantically-unrelated columns. For these we additionally require the column
# NAMES to be related (same measure/event), so grouping needs BOTH meaning AND values. id_code keeps
# containment-only: FK→PK joins legitimately differ in name (works.asset_id → assets.id).
_NAME_GATED_FORMATS = {"integer", "decimal", "date"}
# Generic unit / qualifier / temporal tokens that don't carry meaning on their own — two columns
# that share ONLY these ('labor_minutes' vs 'travel_minutes' share 'minutes'; 'created_at' vs
# 'completed_at' share 'at') are NOT related.
_UNIT_TOKENS = {
    "min", "mins", "minute", "minutes", "hr", "hrs", "hour", "hours", "sec", "secs", "second",
    "seconds", "day", "days", "aed", "usd", "eur", "amount", "amt", "total", "count", "cnt",
    "qty", "quantity", "num", "number", "no", "value", "val", "pct", "percent", "rate", "sum",
    "avg", "mean", "id", "code", "ref",
    # temporal suffixes/qualifiers — so 'created_at' vs 'completed_at' don't match on 'at'/'date'
    "at", "on", "date", "dt", "time", "ts", "timestamp", "datetime",
}


def _name_tokens(col: str) -> set[str]:
    """Content tokens of a column name, minus generic unit/qualifier words."""
    toks = {t for t in re.split(r"[^a-z0-9]+", (col or "").lower()) if t}
    content = toks - _UNIT_TOKENS
    return content or toks  # if a name is ALL unit tokens, fall back to the raw tokens


def _names_related(col_a: str, col_b: str) -> bool:
    """Two column names are semantically related when normalised-equal OR they share at least one
    CONTENT token (unit words like 'minutes'/'aed' excluded). 'labor_minutes' vs 'labor_mins' →
    related (share 'labor'); 'labor_minutes' vs 'travel_minutes' → NOT (only 'minutes' in common);
    'sla_breached' vs 'cost_vendor_aed' → NOT."""
    a = re.sub(r"[^a-z0-9]+", "_", (col_a or "").lower()).strip("_")
    b = re.sub(r"[^a-z0-9]+", "_", (col_b or "").lower()).strip("_")
    if a == b:
        return True
    return bool(_name_tokens(col_a) & _name_tokens(col_b))


def _names_strongly_related(col_a: str, col_b: str) -> bool:
    """Stricter than :func:`_names_related`: the columns must share a NON-generic content token —
    'asset_id'↔'asset_id' (share 'asset'), 'assigned_tech_id'↔'tech_id' (share 'tech'). A generic-
    only overlap ('id'↔'id') does NOT count. Used for id_code grouping: a same-identifier name is a
    strong signal that two id columns are the SAME key even when their (sampled, in a large table)
    value sets don't overlap enough for FK→PK containment."""
    shared = (_name_tokens(col_a) & _name_tokens(col_b)) - _UNIT_TOKENS
    return bool(shared)


def column_pair_grouping(
    rows_a: Records, col_a: str, rows_b: Records, col_b: str
) -> dict[str, Any]:
    """Single source of truth for the Stage-5 Step-1 pairwise grouping decision.

    Both :func:`group_similar_columns` (B19) and the B17.1/B18.1 displayed gates
    call this, so the gate the user sees ALWAYS matches what actually groups.

    Returns a dict with the FORMAT gate (B17.1), the VALUE-PATTERN gate (B18.1)
    and the value-overlap gate that together decide grouping::

        {
          "format_a", "format_b",            # coarse class per column
          "format_score", "format_pass",     # B17.1 — coarse format agreement
          "value_score",                     # B18.1 — VALUE-SHAPE agreement
          "value_pass",                      # value_score >= value_pattern_min
          "overlap", "overlap_kind",         # jaccard | containment | None
          "group",                           # final: all gates passed
        }

    The VALUE-PATTERN gate compares value SHAPES ('A-#3' vs 'A-#2'), not coarse
    classes, so two 'id_code' columns with different shapes (S-## vs A-###) no
    longer collide. A value-overlap gate then keeps same-shape but disjoint
    domains apart (free_text → Jaccard, id/number/date → FK→PK containment).
    """
    fmt_min = THRESHOLDS["format_similarity_min"]
    val_min = THRESHOLDS["value_pattern_similarity_min"]
    fa = column_format(rows_a, col_a)
    fb = column_format(rows_b, col_b)
    fmt = format_similarity(rows_a, col_a, rows_b, col_b)
    out: dict[str, Any] = {
        "format_a": fa, "format_b": fb,
        "format_score": fmt, "format_pass": fmt >= fmt_min,
        "value_score": 0.0, "value_pass": False,
        "overlap": None, "overlap_kind": None, "group": False,
    }
    if fmt < fmt_min:
        return out
    # Variable-shape formats (categorical / free_text): the character-SHAPE distribution is a poor
    # signal — the SAME vocabulary can carry very different word lengths (assets.asset_type and
    # pm_templates.asset_type share 'AHU'/'FCU'/'Chiller' but their length mix differs, so value-shape
    # ≈ 0.47 wrongly fails an 80% gate). Skip the shape gate entirely and decide on actual value-SET
    # overlap (Jaccard); report that overlap as the value_score so the displayed % is meaningful.
    if fa in _VARIABLE_SHAPE_FORMATS and fb in _VARIABLE_SHAPE_FORMATS:
        ov = _jaccard_value_overlap(rows_a, col_a, rows_b, col_b)
        out["value_score"] = ov
        out["value_pass"] = ov >= _VALUE_OVERLAP_MIN
        out["overlap"], out["overlap_kind"] = ov, "jaccard"
        out["group"] = ov >= _VALUE_OVERLAP_MIN
        return out
    vshape = value_shape_similarity(rows_a, col_a, rows_b, col_b)
    out["value_score"] = vshape
    out["value_pass"] = vshape >= val_min
    if not out["value_pass"]:
        return out
    # Value-overlap gate — same shape is not enough across disjoint domains.
    if fa in _VALUE_OVERLAP_REQUIRED_FORMATS and fb in _VALUE_OVERLAP_REQUIRED_FORMATS:
        ov = _containment_overlap(rows_a, col_a, rows_b, col_b)
        out["overlap"], out["overlap_kind"] = ov, "containment"
        grouped = ov >= _CONTAINMENT_MIN
        if fa in _NAME_GATED_FORMATS or fb in _NAME_GATED_FORMATS:
            # Numeric measures AND dates need NAME relatedness ON TOP of value overlap — value
            # overlap alone merges unrelated numbers (sla_breached ⊂ cost_vendor_aed) and unrelated
            # events (created_at vs completed_at overlap in time).
            grouped = grouped and _names_related(col_a, col_b)
        else:
            # id_code: FK→PK containment OR a shared strong (non-generic) name. The name path
            # rescues LARGE tables whose SAMPLED value sets don't overlap even though the columns
            # are the same identifier (assets.asset_id ↔ work_order.asset_id). Generic 'id'↔'id'
            # still needs containment, so disjoint-domain surrogate keys stay apart.
            grouped = grouped or _names_strongly_related(col_a, col_b)
        out["group"] = grouped
    else:
        out["group"] = True
    return out


def group_similar_columns(tables: dict[str, dict]) -> list[list[tuple[str, str]]]:
    """Group columns across ALL tables by format-then-value-pattern similarity.

    ``tables`` = {table_name: {"rows": Records, "columns": [..]}}.
    Returns a list of groups; each group is a list of (table, column). Grouping
    is name-agnostic (7.6 AC3). Singletons are returned as 1-element groups.

    The pairwise decision is delegated to :func:`column_pair_grouping` so the
    displayed B17.1/B18.1 gates are guaranteed to agree with these groups.
    """
    nodes: list[tuple[str, str]] = []
    for t, meta in tables.items():
        for c in meta.get("columns", []):
            nodes.append((t, c))

    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for (ta, ca), (tb, cb) in combinations(nodes, 2):
        if ta == tb:
            continue  # compare against OTHER tables (7.6 AC1)
        ra, rb = tables[ta]["rows"], tables[tb]["rows"]
        if column_pair_grouping(ra, ca, rb, cb)["group"]:
            union((ta, ca), (tb, cb))

    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    return list(groups.values())


#: A redundant-column merge needs at least this many non-empty rows. Below it, two columns
#: agreeing row-for-row is far more likely to be a coincidence of a tiny sample than a genuine
#: duplicate, and merging would silently drop a real column.
_MIN_REDUNDANT_ROWS = 3


def redundant_column_groups(
    rows: Records,
    cols: list[str],
    min_rows: int = _MIN_REDUNDANT_ROWS,
) -> list[list[str]]:
    """Group columns inside ONE table whose values are row-for-row **identical**.

    This is a strict-identity check, deliberately stricter than
    :func:`group_similar_columns_within_table`: every row must agree (case- and
    whitespace-insensitively), so ``works.tagnum`` and ``works.id`` holding the same
    ``A-001…A-010`` collapse to a single column, while merely *similar* columns do not.

    Rows where both values are empty are ignored (they carry no evidence); a row where one
    side is empty and the other is not counts as a mismatch. Returns multi-member groups
    only, each in source column order.
    """
    cols = [c for c in dict.fromkeys(cols or []) if c]
    if len(cols) < 2:
        return []

    vals = {c: [_norm(v).casefold() for v in column_values(rows, c)] for c in cols}
    order = {c: i for i, c in enumerate(cols)}
    parent = {c: c for c in cols}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def identical(a: str, b: str) -> bool:
        va, vb = vals[a], vals[b]
        n = min(len(va), len(vb))
        if n == 0 or len(va) != len(vb):
            return False  # ragged columns are not comparable row-for-row
        evidence = 0
        for x, y in zip(va, vb):
            if not x and not y:
                continue  # both blank — no evidence either way
            if x != y:
                return False
            evidence += 1
        return evidence >= min_rows

    for ca, cb in combinations(cols, 2):
        if find(ca) != find(cb) and identical(ca, cb):
            parent[find(ca)] = find(cb)

    buckets: dict[str, list[str]] = {}
    for c in cols:
        buckets.setdefault(find(c), []).append(c)
    return [sorted(g, key=order.get) for g in buckets.values() if len(g) > 1]


def group_similar_columns_within_table(tables: dict[str, dict]) -> list[list[tuple[str, str]]]:
    """Group columns **within each table** by format-then-value-pattern similarity.

    Same pairwise decision as :func:`group_similar_columns`, but only unions pairs
    that share a table (redundant / duplicate columns inside one sheet).
    Returns multi-member groups only (singletons omitted).
    """
    multi: list[list[tuple[str, str]]] = []
    for t, meta in (tables or {}).items():
        cols = list(dict.fromkeys(c for c in (meta.get("columns") or []) if c))
        rows = meta.get("rows") or []
        if len(cols) < 2:
            continue
        nodes = [(t, c) for c in cols]
        parent = {n: n for n in nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for ca, cb in combinations(cols, 2):
            if column_pair_grouping(rows, ca, rows, cb)["group"]:
                union((t, ca), (t, cb))

        buckets: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for n in nodes:
            buckets.setdefault(find(n), []).append(n)
        multi.extend(g for g in buckets.values() if len(g) > 1)
    return multi


def classify_columns(
    tables: dict[str, dict],
    pk_by_table: dict[str, list[str]],
) -> dict[tuple[str, str], dict]:
    """Stage 5 Step 1 (7.6 AC3-5). Classify every column PK / FK / Shared.

    - PK   : the column is a primary key of its table.
    - FK   : column is grouped with a PK column AND referential integrity ≥95%
             against that PK (7.6 AC4) — i.e. it points at a real PK.
    - SHARED_ATTRIBUTE : similar group with no qualifying PK link (7.6 AC5).

    Returns {(table, column): {classification, group_size, references?, ri?}}.
    """
    ri_min = THRESHOLDS["referential_integrity_min"]
    pk_set = {(t, c) for t, cols in pk_by_table.items() for c in cols}
    groups = group_similar_columns(tables)

    result: dict[tuple[str, str], dict] = {}
    for group in groups:
        pk_members = [m for m in group if m in pk_set]
        for member in group:
            t, c = member
            if member in pk_set:
                result[member] = {
                    "classification": "PK",
                    "group_size": len(group),
                }
                continue

            best_ref = None
            best_ri = 0.0
            for (pt, pc) in pk_members:
                if pt == t:
                    continue
                ri = referential_integrity(tables[t]["rows"], c, tables[pt]["rows"], pc)
                if ri > best_ri:
                    best_ri, best_ref = ri, (pt, pc)

            if best_ref is not None and best_ri >= ri_min:
                result[member] = {
                    "classification": "FK",
                    "group_size": len(group),
                    "references": {"table": best_ref[0], "column": best_ref[1]},
                    "referential_integrity": round(best_ri, 4),
                }
            else:
                result[member] = {
                    "classification": "SHARED_ATTRIBUTE",
                    "group_size": len(group),
                    **({"best_referential_integrity": round(best_ri, 4)} if pk_members else {}),
                }

    # 7.6 AC6 — EVERY column must carry a classification, including singletons that are
    # not similar to any other column (so never entered a group): a primary key stays PK;
    # any other standalone column is a Shared Attribute (it has no FK relationship).
    for t, meta in tables.items():
        for c in meta.get("columns", []) or []:
            member = (t, c)
            if member in result:
                continue
            result[member] = {
                "classification": "PK" if member in pk_set else "SHARED_ATTRIBUTE",
                "group_size": 1,
            }
    return result


# ── Canonical metadata builders (final table/column metadata) ───────────────────
def build_table_metadata(name: str, rows: Records, columns: list[str], pk: dict | None = None) -> dict:
    """Stage 2 table metadata (FKs added later in Stage 5/10).

    {table, primary_key, primary_key_kind, column_names, column_count,
     samples_by_column (3 each)}.
    """
    pk = pk or detect_primary_key(rows, columns)
    samples = {c: _nonnull(column_values(rows, c))[:3] for c in columns}
    return {
        "table": name,
        "primary_key": pk["columns"],
        "primary_key_kind": pk["kind"],
        "column_names": list(columns),
        "column_count": len(columns),
        "samples_by_column": samples,
        "row_count": len(rows or []),
    }


def build_column_metadata(
    table: str,
    col: str,
    rows: Records,
    classification: str | None = None,
    dest_table: str | None = None,
) -> dict:
    """Stage 4a / final column metadata (7.11 AC4).

    {source_table, column, dest_udr_table, classification, cell_format,
     sample_values (5)}.
    """
    return {
        "source_table": table,
        "column": col,
        "dest_udr_table": dest_table,
        "classification": classification,
        "cell_format": column_format(rows, col),
        "sample_values": _nonnull(column_values(rows, col))[:5],
    }
