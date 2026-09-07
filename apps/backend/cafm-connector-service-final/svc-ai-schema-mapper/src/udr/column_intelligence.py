"""Feature 7 — Column-intelligence report (B13.1 → B21.1), PURE.

Orchestrates the existing value-centric primitives into the step-by-step "Column Intelligence
Pipeline" the UI shows BEFORE table-routing confirmation:

  B13.1 Table prefixing          — every column carried as a (source_table, column) key tagged
                                    with its destination UDR table (prevents name collisions).
  B14.1 Column metadata          — the 4 dimensions: dest table · PK/FK/Shared · format · samples.
  B15.1 FK-candidate detection   — same-values-different-table columns whose group holds a PK and
                                    referential integrity ≥ 0.95 → confirmed FK.
  B17.1 Format-similarity gate    — every cross-table column pair scored on cell-value FORMAT.
  B18.1 Value-pattern gate        — the format survivors re-checked on value pattern.
  B19.1 Similar-column grouping   — union-find over the survivors (name-agnostic).
  B20.1 PK/FK/Shared classification — each group classified; RI verified.
  (canonical) Unified names       — one canonical name per group (PK → consensus → FM → format).
  B21.1 Destination column mapping — each column matched to a destination UDR column.

Pure (stdlib only, operates on the same in-memory ``tables`` dicts the pipeline builds) so it is
unit-testable and reusable by both the migration gate and the post-write UDR pass. FM-ontology
canonical-field resolution is INJECTED (``field_resolver``) so this module never imports the
matcher layer; with no resolver the FM tiers gracefully fall back to name/format heuristics.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from itertools import combinations
from typing import Callable

from .mapping_decision import entity_prefix_conflict, score_column_mapping
from .primitives import (
    THRESHOLDS,
    classify_columns,
    column_format,
    column_pair_grouping,
    column_values,
    dominant_value_shape,
    format_similarity,
    group_similar_columns,
    group_similar_columns_within_table,
    referential_integrity,
)

FORMAT_MIN = THRESHOLDS["format_similarity_min"]
VALUE_MIN = THRESHOLDS["value_pattern_similarity_min"]
RI_MIN = THRESHOLDS["referential_integrity_min"]

# Bump when the canonical-naming / grouping logic changes — surfaced in the report so a
# deployed payload can be checked against the source (e.g. confirm a rebuild actually took).
ENGINE_VERSION = "ci-2026-07-15-b22-fk-uses-source-table"

_CLS_LABEL = {"PK": "PK", "FK": "FK", "SHARED_ATTRIBUTE": "Shared"}
_MAX_META_ROWS = 500       # show every column's metadata (O(N), one row/col — not the O(N²) pairs);
                           # a generous cap that still guards a pathologically wide (500+ col) sheet
# Cap the pairwise format/value rows PERSISTED in the report (the gate payload rides in every
# /status poll + the checkpoint). The pairs are O(N²); storing them all bloated /status to >1 MB
# and exhausted the DB pool. The UI shows the top ~80, so 200 is plenty. Counts stay accurate.
_MAX_PAIR_ROWS = 200


def _norm_col_name(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")


def _samples(rows, col, n=5) -> list[str]:
    out: list[str] = []
    for v in column_values(rows, col):
        s = "" if v is None else str(v).strip()
        if s:
            out.append(s)
        if len(out) >= n:
            break
    return out


def _pct(score: float | None) -> int:
    return round((score or 0) * 100)


def _pair_gate_rows(
    rows_a,
    col_a: str,
    rows_b,
    col_b: str,
    *,
    table_a: str,
    table_b: str,
    same_table: bool,
) -> tuple[dict, dict | None, dict]:
    """Score one column pair for B17.1 (format) and B18.1 (value-pattern). Returns
    (format_row, value_row_or_none, raw_pair)."""
    pair = column_pair_grouping(rows_a, col_a, rows_b, col_b)
    fmt_row = {
        "scope": "within_table" if same_table else "cross_table",
        "table": table_a if same_table else None,
        "col_a": _prefixed(table_a, col_a),
        "col_b": _prefixed(table_b, col_b),
        "format_a": pair["format_a"],
        "format_b": pair["format_b"],
        "score": round(pair["format_score"], 4),
        "score_pct": _pct(pair["format_score"]),
        "pass": pair["format_pass"],
        "threshold_pct": _pct(FORMAT_MIN),
    }
    val_row: dict | None = None
    if pair["format_pass"]:
        # Inline the value-pattern result on the FORMAT row too. The format_gate / value_pattern
        # lists are each capped to _MAX_PAIR_ROWS independently (different orderings), so a UI that
        # joins them loses the value result for most pairs. Carrying it here means every displayed
        # format-passing pair shows its own value score / decision (AC2) without a cross-list join.
        fmt_row["value_score_pct"] = _pct(pair["value_score"])
        fmt_row["value_decision"] = "group together" if pair["group"] else "separate"
        val_row = {
            "scope": "within_table" if same_table else "cross_table",
            "table": table_a if same_table else None,
            "col_a": _prefixed(table_a, col_a),
            "col_b": _prefixed(table_b, col_b),
            "score": round(pair["value_score"], 4),
            "score_pct": _pct(pair["value_score"]),
            "decision": "group together" if pair["group"] else "separate",
            "threshold_pct": _pct(VALUE_MIN),
        }
        if pair["overlap"] is not None:
            val_row["overlap"] = round(pair["overlap"], 4)
            val_row["overlap_pct"] = _pct(pair["overlap"])
            val_row["overlap_kind"] = pair["overlap_kind"]
    return fmt_row, val_row, pair


_SAFE_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$", re.IGNORECASE)


def _q(name: str) -> str:
    """Quote a SQL identifier only when it isn't a plain safe identifier (DDL emission, B22.1)."""
    s = str(name)
    return s if _SAFE_IDENT_RE.match(s) else '"' + s.replace('"', '""') + '"'


def _prefixed(table: str, col: str) -> str:
    """B13.1 prefixed key — lowercase table, dot-separated (e.g. 'resources.id').

    The lowercase form lets downstream stages / the FE compare prefixed keys
    case-insensitively without separate normalisation, and matches the
    destination-table convention used elsewhere in the pipeline.
    """
    return f"{table.lower()}.{col}"


# Generic PK column names that mean nothing on their own — they need to be
# qualified with the table name to become a meaningful canonical (e.g.
# Sites.id → site_id, not just 'id').
_GENERIC_PK_NAMES = {"id", "code", "key", "no", "number", "pk"}


def _singular(table: str) -> str:
    """Best-effort singular snake_case form of a source-table name.

    Used to qualify generic PK column names when promoting them to a group
    canonical: Sites → 'site', Vendors → 'vendor', WorkOrders → 'work_order'.
    Splits CamelCase to snake_case and trims a trailing 's' (but not 'ss').
    """
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", table).lower()
    if snake.endswith("s") and not snake.endswith("ss"):
        snake = snake[:-1]
    return snake


# ── canonical / unified name per group (B19/B20 closing line) ───────────────────
def canonical_name_for_group(
    group: list[tuple[str, str]],
    classification: dict[tuple, dict],
    *,
    dest_mapping: dict[tuple, str] | None = None,
    dest_table_by_source: dict[str, str] | None = None,
    tables: dict[str, dict] | None = None,
    field_resolver: Callable[[str], tuple] | None = None,
    group_id: str | None = None,
) -> str:
    """One canonical name covering every (table, column) in ``group`` — deterministic, 5 tiers:
    PK name → common destination column → name consensus → FM ontology → neutral synthetic.

    Crucially, when a multi-member group has NO PK, NO destination consensus,
    NO naming consensus and NO FM hit, we no longer pick an arbitrary member
    name (which produced misleading results like ``canonical='phone'`` for a
    group containing ``site_name``, ``postcode``, ``fault_description``, …).
    A neutral ``{format}_attr[_{group_id}]`` name is used instead.
    """
    members = list(group)

    def fm(name: str) -> str | None:
        if not field_resolver or not name:
            return None
        try:
            canon, _ = field_resolver(name)
            return canon or None
        except Exception:  # pragma: no cover — resolver hiccup must never break the report
            return None

    def _fm_pref(name: str) -> str:
        """Canonical = the SOURCE column name, verbatim.

        The FM ontology crosswalk maps source names to internal standard LABELS (wo_id/wonum/
        wr_id → 'wo_code', vendor_name → 'supplier') that are NOT necessarily real destination
        columns — the Plenum work_orders table has no 'wo_code' column, so renaming the user's
        'wo_id' to 'wo_code' shows a name that is neither their source nor an actual target. The
        source→destination mapping is surfaced separately (matched_column); the canonical keeps the
        user's own column identity so B13.1/B14.1/B19.1 read exactly as their spreadsheet."""
        return name

    # Tier 1 — a PK in the group names it. A single PK table is unambiguous. When
    # the shared value is the PK of MORE THAN ONE table (e.g. an asset id A001 that
    # is also reused as a vendor id via coincidental value overlap), the group is
    # named after the PARENT — the PK whose distinct values CONTAIN every other
    # member's values, i.e. the entity the shared value actually identifies. If no
    # single parent covers the group, it stays ambiguous and falls through.
    pk_members = [(t, c) for (t, c) in members
                  if (classification.get((t, c)) or {}).get("classification") == "PK"]
    pk_tables = {t for (t, _c) in pk_members}
    chosen_table: str | None = None
    chosen_pks: list[tuple[str, str]] = []
    if pk_members and len(pk_tables) == 1:
        chosen_table = next(iter(pk_tables))
        chosen_pks = pk_members
    elif len(pk_members) > 1 and tables:
        def _distinct(t: str, c: str) -> set[str]:
            return {str(v).strip().lower() for v in column_values(tables[t]["rows"], c) if str(v).strip()}
        union_vals: set[str] = set()
        for (t, c) in members:
            if t in tables:
                union_vals |= _distinct(t, c)
        # parent PK = one whose distinct values cover the whole group's value space
        parents = [(t, c) for (t, c) in pk_members
                   if t in tables and union_vals and _distinct(t, c) >= union_vals]
        parent_tables = {t for (t, _c) in parents}
        if len(parent_tables) == 1:
            chosen_table = next(iter(parent_tables))
            chosen_pks = parents
    if chosen_table and chosen_pks:
        cnt = Counter(_norm_col_name(c) for (_t, c) in chosen_pks)
        best = sorted(cnt.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        # When the PK column name is generic ('id', 'code', 'key', 'no', 'number') we need a
        # descriptive canonical. Order of preference:
        #   1. A descriptive name the group ALREADY shares (≥2 members) — e.g. sites.asset_id
        #      + workorders.asset_id make 'asset_id' the obvious canonical, and it does NOT
        #      depend on the destination table being resolved (at the pre-semantic gate the
        #      source 'works' may not yet be mapped to 'assets', which would otherwise yield
        #      the weaker 'work_id').
        #   2. Else qualify with the DESTINATION entity when the source table is mapped
        #      (works→assets ⇒ asset_id), falling back to the source table name.
        if best in _GENERIC_PK_NAMES:
            _ng = Counter(_norm_col_name(c) for (_t, c) in members
                          if _norm_col_name(c) not in _GENERIC_PK_NAMES)
            _shared = next((nm for nm, ct in _ng.most_common() if ct > 1), None)
            if _shared:
                return _fm_pref(_shared)
            qual_table = (dest_table_by_source or {}).get(chosen_table) or chosen_table
            best = f"{_singular(qual_table)}_{best}"
        return _fm_pref(best)

    # Tier 2 — when MULTIPLE grouped members all resolve to the SAME destination column,
    # that shared destination is the natural unified name. NOT applied to a single column:
    # a lone source column must keep its OWN name as the canonical, never be renamed to its
    # destination. Otherwise a mapped source like 'product_name' → 'asset_name' would display
    # as 'asset_name (was product_name)', which reads as if the SOURCE was edited/hardcoded —
    # and a column whose destination column name differs from a real column on its routed table
    # could then resolve to a non-existent column. The source→destination mapping is shown
    # separately (matched_column); the canonical is the source's identity.
    if dest_mapping and len(members) > 1:
        dcols = {dest_mapping.get((t, c)) for (t, c) in members if dest_mapping.get((t, c))}
        dcols = {d for d in dcols if d}
        if len(dcols) == 1:
            _only = next(iter(dcols))
            # Don't let a GENERIC shared dest column ('id'/'code'/…) become the canonical when
            # the group ALSO carries a descriptive shared SOURCE name (e.g. 'asset_id' across ≥2
            # members). This happens for a multi-PK value group whose PK-parent is ambiguous
            # (two PK tables, none covering the value union — e.g. vendors.id + works.id both
            # hold A-### but neither is a superset): Tier 1 is skipped and the only common dest
            # is the generic 'id', which would erase the entity and defeat the B21 cross-entity
            # guard (vendors.id would then look like a clean id→id match). In that case fall
            # through to the descriptive-consensus tier so the canonical becomes 'asset_id' and
            # the guard can flag vendors.id (asset codes ≠ vendor PK) as a new column. If there
            # is NO descriptive shared name to fall through to, the generic shared dest is still
            # the best available canonical — keep it (don't synthesise a neutral name).
            _is_generic = _norm_col_name(_only) in _GENERIC_PK_NAMES
            _has_shared_desc = any(
                ct > 1 for ct in Counter(
                    _norm_col_name(c) for (_t, c) in members
                    if _norm_col_name(c) not in _GENERIC_PK_NAMES
                ).values()
            )
            if not (_is_generic and _has_shared_desc):
                return _only

    # Tier 3 — naming consensus across the group, EXCLUDING generic names so
    # 'id' / 'code' / 'key' don't beat a descriptive name like 'asset_id' just
    # because they happen to be short. If the only consensus is on a generic
    # name, we let Tier 4/5 produce a more useful synthetic instead.
    non_generic_cnt = Counter(
        _norm_col_name(c) for (_t, c) in members
        if _norm_col_name(c) not in _GENERIC_PK_NAMES
    )
    if non_generic_cnt:
        best_ng = sorted(non_generic_cnt.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        if non_generic_cnt[best_ng] > 1:
            return _fm_pref(best_ng)
    cnt = Counter(_norm_col_name(c) for (_t, c) in members)
    best = sorted(cnt.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0] if cnt else ""
    if cnt and cnt[best] > 1:
        return best  # source-name consensus — keep it verbatim (no FM rename to a phantom label)

    # (FM-ontology renaming intentionally NOT applied here — see _fm_pref: the canonical always
    # keeps the user's source column name; the destination is surfaced separately as matched_column.)

    # Singleton — no real consensus needed, keep the member's own name.
    if len(members) == 1:
        return best or _norm_col_name(members[0][1])

    # Tier 5 — heterogeneous multi-member group with no PK, no dest consensus,
    # no naming consensus, no FM hit. Pick a NEUTRAL synthetic name keyed by the
    # dominant format so we never mislead with an arbitrary member's name.
    if tables:
        fmts = Counter(column_format(tables[t]["rows"], c) for (t, c) in members if t in tables)
        if fmts:
            dom = fmts.most_common(1)[0][0]
            return f"{dom}_attr_{group_id}" if group_id else f"{dom}_attr"
    h = hashlib.md5("|".join(sorted(f"{t}.{c}" for t, c in members)).encode()).hexdigest()
    return f"group_{h[:6]}"


def synthesize_shared_attribute_table(
    tables: dict,
    *,
    canonical_name: str,
    group_id: str,
    member_pairs: list,
    members_display: list | None = None,
) -> dict | None:
    """B22.1 — synthesize the lookup table + DDL for one SHARED-ATTRIBUTE group.

    The shared attribute becomes the PRIMARY KEY of a new lookup table whose rows are the
    attribute's distinct values (union across ``member_pairs`` = ``[(table, column), ...]``);
    each source column is rewritten to a FOREIGN KEY referencing the new PK. Descriptor
    carry-over is deliberately skipped — a small sample can imply spurious functional
    dependencies. Returns the ``shared_attribute_tables`` entry, or ``None`` when the group
    yields no distinct values (nothing to normalise).

    Reused by :func:`build_column_intelligence` (auto classification) and by the B20.1
    classification gate when a human re-classifies an FK group as a shared attribute.
    """
    from .reference_tables import build_reference_table

    pk = canonical_name                                   # the shared attribute becomes the PK
    tname = pk if pk not in tables else f"{pk}_ref"       # no-clobber vs an existing table
    ref = build_reference_table(
        tables, tname, source_table=None, value_columns=[(t, c) for (t, c) in member_pairs]
    )
    if not ref.get("rows"):
        return None
    # rows carry the PK under key `tname`; normalise to the canonical value list.
    values = [str(r.get(tname)) for r in ref["rows"]]
    create_ddl = (
        f"CREATE TABLE IF NOT EXISTS plenum_cafm.{_q(tname)} (\n"
        f"    {_q(pk)} TEXT PRIMARY KEY\n"
        f");"
    )
    # FK ALTER targets the SOURCE table (assets, pm_templates) so the DDL matches the FK-rewrite
    # display and the B8.1 (2.2) primary keys — NOT the routed destination (which would surprise
    # the user with unrelated table names like maintenance_plans).
    fk_rewrites = [{"src": t, "column": c} for (t, c) in member_pairs]
    fk_ddls = [
        f"ALTER TABLE plenum_cafm.{_q(rw['src'])} "
        f"ADD CONSTRAINT {_q('fk_' + rw['src'] + '_' + rw['column'])} "
        f"FOREIGN KEY ({_q(rw['column'])}) "
        f"REFERENCES plenum_cafm.{_q(tname)} ({_q(pk)});"
        for rw in fk_rewrites
    ]
    # Full block for the LIVE output SQL (applied to the DB): idempotent CREATE + INSERT, and
    # each FK wrapped in a DO block that swallows errors so a constraint that can't apply (missing
    # column, already exists) logs a NOTICE instead of aborting the whole migration transaction.
    insert_ddls = [
        f"INSERT INTO plenum_cafm.{_q(tname)} ({_q(pk)}) "
        f"VALUES ('{str(v).replace(chr(39), chr(39) * 2)}') ON CONFLICT DO NOTHING;"
        for v in sorted(set(values))
    ]
    safe_fk_ddls = [
        "DO $$ BEGIN\n  " + d + "\nEXCEPTION WHEN others THEN "
        "RAISE NOTICE 'skip FK (needs review): %', SQLERRM; END $$;"
        for d in fk_ddls
    ]
    ddl_block = "\n".join([create_ddl, *insert_ddls, *safe_fk_ddls])
    return {
        "table_name": tname,
        "pk_column": pk,
        "from_group": group_id,
        "source_columns": list(members_display) if members_display
        else [f"{t}.{c}" for (t, c) in member_pairs],
        "distinct_count": len(values),
        "sample_values": sorted(values)[:15],
        "fk_rewrites": [
            {"source": f"{rw['src']}.{rw['column']}", "references": f"{tname}.{pk}"}
            for rw in fk_rewrites
        ],
        "create_ddl": create_ddl,
        "fk_ddl": fk_ddls,
        # Complete, DB-safe SQL section consumed by Node 9 (output_generator_node).
        "ddl_block": ddl_block,
    }


def build_column_intelligence(
    tables: dict[str, dict],
    *,
    dest_table_by_source: dict[str, str] | None = None,
    dest_col_by_source_col: dict[tuple[str, str], str] | None = None,
    conf_by_source_col: dict[tuple[str, str], float] | None = None,
    field_resolver: Callable[[str], tuple] | None = None,
    canonical_overrides: dict[str, str] | None = None,
    dest_samples_by_table: dict[str, dict[str, list]] | None = None,
    dest_columns_by_table: dict[str, set] | None = None,
    unresolved_suggest_by_source_col: dict[tuple[str, str], str] | None = None,
) -> dict:
    """Build the B13.1→B21.1 column-intelligence report.

    ``tables`` = ``{table: {"rows": Records, "columns": [...], "pk": [...]}}`` (the pipeline's
    normalised tables). ``dest_table_by_source`` routes each source table → destination UDR table;
    ``dest_col_by_source_col`` / ``conf_by_source_col`` carry the per-column destination match +
    confidence the gate already produced (source (table,col) → dest column / 0-1). ``field_resolver``
    is the FM-ontology ``fm_field_lookup`` (optional).

    ``canonical_overrides`` lets the user pin a canonical name per group (HITL): when a key
    matches a ``group_id`` (e.g. ``"G2"``) the auto-derived name is replaced with the user's
    choice, and the override propagates through B14/B17/B18/B19/B20/B21 + the column_canonical
    map so every downstream node sees the same identifier.
    """
    tables = tables or {}
    dest_table_by_source = dest_table_by_source or {}
    dest_col_by_source_col = dest_col_by_source_col or {}
    conf_by_source_col = conf_by_source_col or {}
    unresolved_suggest_by_source_col = unresolved_suggest_by_source_col or {}

    pk_by_table = {t: (meta.get("pk") or []) for t, meta in tables.items()}
    nodes: list[tuple[str, str]] = [(t, c) for t, meta in tables.items() for c in meta.get("columns", [])]
    classification = classify_columns(tables, pk_by_table)
    groups = group_similar_columns(tables)
    multi_groups = [g for g in groups if len(g) > 1]

    def dest_table(t: str) -> str | None:
        return dest_table_by_source.get(t)

    # ── Data-over-header reclassification (referential integrity beats the column name) ──────────
    # A column can be UNIQUE within its own table and so get detected as that table's PK — even when
    # its VALUES actually belong to a different entity. e.g. Vendors.id holds asset codes (A-001…):
    # unique in Vendors, so classified PK, when it is really a foreign reference to the asset entity.
    # classify_columns can't catch this (it stops at "in pk_set → PK" before any RI check). Here we
    # look at the DATA: if a PK-classified column's values are strongly contained in ANOTHER source
    # table's PK — and that table routes to a DIFFERENT destination entity — reclassify it as an FK
    # to that entity so its canonical becomes <entity>_id (Vendors.id → asset_id), matching the rule
    # "the values decide the concept, not the header". Threshold kept high so only a genuine
    # reference is demoted, never a coincidental partial overlap. Additive + best-effort.
    _RI_OWNER_MIN = 0.90

    def _pk_valueset(_t: str) -> frozenset:
        _pks = pk_by_table.get(_t) or []
        if not _pks:
            return frozenset()
        _rows = tables[_t]["rows"]
        # composite PK → tuple key; single PK → the value.
        _out = set()
        for _r in _rows:
            _vals = tuple(str(_r.get(_p)).strip() for _p in _pks)
            if all(v and v != "None" for v in _vals):
                _out.add(_vals if len(_vals) > 1 else _vals[0])
        return frozenset(_out)

    _pk_sets = {_t: _pk_valueset(_t) for _t in tables}

    def _entity_id_name(_dest: str | None, _src: str) -> str:
        """<entity>_id for the referenced table — singularised dest (assets→asset_id)."""
        _base = (str(_dest) if _dest else _src).strip().lower()
        if _base.endswith("ies"):
            _base = _base[:-3] + "y"
        elif _base.endswith("ses"):
            _base = _base[:-2]
        elif _base.endswith("s"):
            _base = _base[:-1]
        return f"{_base}_id"

    def _entity_singular(_dest: str | None, _src: str) -> str:
        _base = (str(_dest) if _dest else _src).strip().lower()
        if _base.endswith("ies"):
            return _base[:-3] + "y"
        if _base.endswith("ses"):
            return _base[:-2]
        return _base[:-1] if _base.endswith("s") else _base

    # Value namespace of a table's PK, used to test whether a NAMED reference to that entity
    # exists anywhere (the tie-breaker below). Cached.
    def _has_named_reference(_entity: str, _namespace: frozenset) -> bool:
        """True if SOME column whose header names ``_entity`` (asset_id / asset_no / asset_ref …)
        holds values from ``_namespace``. This is the corroboration that breaks a symmetric
        value tie: Vendors.id and quatty.tagnum share the SAME values, but only the asset entity
        has columns literally named 'asset_*' pointing at them, so the asset table is the owner."""
        if not _entity or not _namespace:
            return False
        _suffixes = ("_id", "_no", "_ref", "_code", "_num", "_key")
        for _xt, _meta in tables.items():
            for _xc in _meta.get("columns", []) or []:
                _n = _norm_col_name(_xc)
                if _n == _entity or _n.startswith(_entity) and any(_n == _entity + s for s in _suffixes):
                    _xvals = {str(r.get(_xc)).strip() for r in _meta["rows"] if r.get(_xc) is not None}
                    _xvals.discard("None"); _xvals.discard("")
                    if _xvals and len(_xvals & _namespace) / len(_xvals) >= _RI_OWNER_MIN:
                        return True
        return False

    _ri_canonical: dict[tuple[str, str], str] = {}  # (t,c) -> forced canonical from RI ownership
    try:
        for (_t, _c), _info in list(classification.items()):
            if _info.get("classification") != "PK":
                continue
            _dt = dest_table(_t)
            _col_vals = _pk_sets.get(_t) or frozenset()
            if not _col_vals:
                continue
            _best_owner = None
            _best_frac = 0.0
            for _ot, _oset in _pk_sets.items():
                if _ot == _t or not _oset:
                    continue
                if dest_table(_ot) and dest_table(_ot) == _dt:
                    continue  # same destination entity — not a cross-entity reference
                _frac = len(_col_vals & _oset) / len(_col_vals)
                if _frac < _RI_OWNER_MIN:
                    continue
                # Tie-breaker: only treat _ot as the OWNER of this value namespace if the entity is
                # corroborated by a NAMED reference (some column called <entity>_id/_no/… holds
                # these values). Symmetric equal-set case: quatty.tagnum ↔ Vendors.id both 100%,
                # but only 'asset' has asset_id/asset_no columns → quatty owns it, Vendors.id is the FK.
                _owner_entity = _entity_singular(dest_table(_ot), _ot)
                if not _has_named_reference(_owner_entity, _col_vals):
                    continue
                if _frac > _best_frac:
                    _best_frac, _best_owner = _frac, _ot
            if _best_owner is not None:
                _owner_pks = pk_by_table.get(_best_owner) or []
                classification[(_t, _c)] = {
                    "classification": "FK",
                    "group_size": _info.get("group_size", 1),
                    "references": {"table": _best_owner,
                                   "column": _owner_pks[0] if _owner_pks else "id"},
                    "referential_integrity": round(_best_frac, 4),
                    "reclassified_from_pk": True,
                }
                _ri_canonical[(_t, _c)] = _entity_id_name(dest_table(_best_owner), _best_owner)
    except Exception:  # pragma: no cover — additive, never break the report
        _ri_canonical = {}

    def cls_of(t: str, c: str) -> str:
        return (classification.get((t, c)) or {}).get("classification", "SHARED_ATTRIBUTE")

    def ri_pct_of(t: str, c: str) -> int | None:
        """Match % for a group member: the PK is the parent (100%); an FK is the share of its
        values found in that PK (referential integrity). None when not an FK/PK (no link %)."""
        info = classification.get((t, c)) or {}
        if info.get("classification") == "PK":
            return 100
        r = info.get("referential_integrity")
        return _pct(r) if isinstance(r, (int, float)) else None

    # ── B13.1 — table prefixing (canonical name attached below, once groups exist) ──
    # The user has CONFIRMED the source→destination routing, so prefix with the confirmed
    # destination table (fall back to the source only when a table wasn't routed — e.g. a new
    # table). ``dest_prefixed_key`` is what B13.1 shows; ``prefixed_key`` (source-based) stays as
    # the internal key the value-matching / grouping stages compare on.
    # Duplicate source tables routed to the SAME destination (WorkOrders + WorkOrders_2 ->
    # work_orders) would otherwise emit one row per source, so `work_orders.status` appears twice
    # with nothing to tell the rows apart. Key on the destination column — the thing B13.1 shows —
    # and keep the co-routed source tables on the row instead of repeating it.
    _prefix_by_dest: dict[str, dict] = {}
    for (t, c) in nodes:
        _dest_key = _prefixed(dest_table(t) or t, c)
        _existing = _prefix_by_dest.get(_dest_key)
        if _existing is not None:
            if t not in _existing["source_tables"]:
                _existing["source_tables"].append(t)
            continue
        _prefix_by_dest[_dest_key] = {
            "source_table": t,
            # Every source table feeding this destination column (1 for the normal case).
            "source_tables": [t],
            "column": c,
            "prefixed_key": _prefixed(t, c),
            "dest_table": dest_table(t),
            "dest_prefixed_key": _dest_key,
        }
    prefixing = list(_prefix_by_dest.values())

    # ── B14.1 — column metadata (4 dimensions + 5 samples) ───────────────────
    # Same collapse as B13.1: duplicate sheets routed to one destination must not produce two
    # identical metadata rows. Deduped BEFORE the row cap so the cap isn't spent on duplicates.
    _meta_seen: set[str] = set()
    _meta_nodes: list[tuple[str, str]] = []
    for (t, c) in nodes:
        _k = _prefixed(dest_table(t) or t, c)
        if _k in _meta_seen:
            continue
        _meta_seen.add(_k)
        _meta_nodes.append((t, c))

    # Format + sample detection are O(rows) PER COLUMN, and the same (table, column) is needed by
    # BOTH this metadata list and the B21 dest_mapping rows below. Memoise so each column's rows are
    # scanned ONCE per report instead of twice — on a wide export (80+ columns × the 10k row cap)
    # the duplicate scan measurably delayed the gate opening.
    _dim_cache: dict[tuple, tuple] = {}

    def _dims_for(t: str, c: str) -> tuple:
        """(format, samples) for one column — computed once, reused everywhere."""
        _k = (t, c)
        _v = _dim_cache.get(_k)
        if _v is None:
            _rows = tables[t]["rows"]
            _v = (column_format(_rows, c), _samples(_rows, c, 5))
            _dim_cache[_k] = _v
        return _v

    metadata = []
    for (t, c) in _meta_nodes[:_MAX_META_ROWS]:
        _fmt, _smp = _dims_for(t, c)
        metadata.append({
            "prefixed_key": _prefixed(t, c),
            "source_table": t,
            "column": c,
            "source_column": c,
            "dest_table": dest_table(t),
            "classification": _CLS_LABEL.get(cls_of(t, c), cls_of(t, c)),
            "format": _fmt,
            "samples": _smp,
        })

    # ── B15.1 — FK-candidate detection (from the classification's FK links) ──
    fk_candidates = []
    for (t, c), info in classification.items():
        if info.get("classification") != "FK":
            continue
        ref = info.get("references") or {}
        ri = info.get("referential_integrity")
        fk_candidates.append({
            "src_table": t, "src_column": c,
            "dst_table": ref.get("table"), "dst_column": ref.get("column"),
            "ri": ri,
            "confirmed": isinstance(ri, (int, float)) and ri >= RI_MIN,
            "reason": (
                f"grouped with PK {ref.get('table')}.{ref.get('column')} · referential integrity "
                f"{round((ri or 0) * 100)}% ≥ {round(RI_MIN * 100)}%"
            ),
        })

    # ── B17.1 / B18.1 — pairwise FORMAT then VALUE-PATTERN gates ─────────────
    # Cross-table (7.6 AC1) and within-table (pre-processing overlap / duplicate cols).
    format_pairs: list[dict] = []
    value_pairs: list[dict] = []
    within_format_pairs: list[dict] = []
    within_value_pairs: list[dict] = []
    fmt_pass = 0
    within_fmt_pass = 0
    # Per source column → its cross-table candidates that PASSED the format gate (≥80%). Both
    # directions are recorded so each column sees all its partners; we keep the raw (table, col,
    # scores, group) and pick the Top-3 after duplicate collapse below.
    _col_matches: dict[tuple, list[tuple]] = {}
    for (ta, ca), (tb, cb) in combinations(nodes, 2):
        same_table = ta == tb
        ra, rb = tables[ta]["rows"], tables[tb]["rows"]
        fmt_row, val_row, pair = _pair_gate_rows(
            ra, ca, rb, cb, table_a=ta, table_b=tb, same_table=same_table,
        )
        if same_table:
            within_format_pairs.append(fmt_row)
            if fmt_row["pass"]:
                within_fmt_pass += 1
            if val_row:
                within_value_pairs.append(val_row)
        else:
            format_pairs.append(fmt_row)
            if fmt_row["pass"]:
                fmt_pass += 1
                # format gate passed → candidate for BOTH columns' Top-3 lists
                _col_matches.setdefault((ta, ca), []).append(
                    (tb, cb, pair["format_score"], pair["value_score"], pair["group"])
                )
                _col_matches.setdefault((tb, cb), []).append(
                    (ta, ca, pair["format_score"], pair["value_score"], pair["group"])
                )
            if val_row:
                value_pairs.append(val_row)
    format_pairs.sort(key=lambda p: p["score"], reverse=True)
    value_pairs.sort(key=lambda p: p["score"], reverse=True)
    within_format_pairs.sort(key=lambda p: p["score"], reverse=True)
    within_value_pairs.sort(key=lambda p: p["score"], reverse=True)
    value_survivors = sum(1 for p in value_pairs if p["decision"] == "group together")
    within_value_survivors = sum(
        1 for p in within_value_pairs if p["decision"] == "group together"
    )

    # The pairwise gates are O(N²) — for a wide workbook this is thousands of rows. The gate payload
    # rides in every /status poll (and the checkpoint), so storing them ALL bloats the response to
    # >1 MB and exhausts the DB connection pool under polling. The UI only shows the top ~80, so cap
    # the STORED rows (highest score first) while keeping the true scored/survivor COUNTS for the
    # summary. (The full computation still runs; only the persisted list is trimmed.)
    _total_format_pairs = len(format_pairs)
    _total_within_format_pairs = len(within_format_pairs)
    format_pairs = format_pairs[:_MAX_PAIR_ROWS]
    value_pairs = value_pairs[:_MAX_PAIR_ROWS]
    within_format_pairs = within_format_pairs[:_MAX_PAIR_ROWS]
    within_value_pairs = within_value_pairs[:_MAX_PAIR_ROWS]

    # Within-table groups (same-table redundant columns).
    within_table_groups_raw = group_similar_columns_within_table(tables)
    within_table_group_records: list[dict] = []
    for idx, g in enumerate(within_table_groups_raw, 1):
        t = g[0][0]
        fmt = Counter(column_format(tables[t]["rows"], c) for (_, c) in g).most_common(1)[0][0]
        within_table_group_records.append({
            "group_id": f"WT-G{idx}",
            "table": t,
            "format": fmt,
            "members": [_prefixed(t, c) for (_, c) in g],
            "member_keys": [{"table": t, "column": c} for (_, c) in g],
        })

    # Duplicate tables (e.g. work_order + workorders — same columns, same wo_id PK) are collapsed
    # to ONE entry here so the B17.1/B18.1 grouping matches B7.1–B12.1 (`work_order ×2`) instead of
    # listing both. Detected by identical column SET; the alphabetically-first name is the
    # representative and carries the group's columns, and the duplicate copies are skipped so their
    # redundant `workorders.*` keys don't clutter the cross-table lists.
    _by_colset: dict[frozenset, list[str]] = {}
    for _t, _meta in tables.items():
        _by_colset.setdefault(frozenset(_meta.get("columns", [])), []).append(_t)
    _dup_rep: dict[str, str] = {}      # table -> representative table
    _dup_count: dict[str, int] = {}    # representative -> number of duplicate members
    for _cols, _ts in _by_colset.items():
        if len(_ts) >= 2 and _cols:
            _r = sorted(_ts)[0]
            for _t in _ts:
                _dup_rep[_t] = _r
            _dup_count[_r] = len(_ts)

    def _is_dup_copy(t: str) -> bool:
        """True for a duplicate table that is NOT the representative (skip it in grouping)."""
        return t in _dup_rep and _dup_rep[t] != t

    def _rep(t: str) -> str:
        return _dup_rep.get(t, t)

    def _rep_label(t: str) -> str:
        r = _rep(t)
        n = _dup_count.get(r)
        return f"{r} ×{n}" if n else r

    # Duplicate-table groups exposed to the UI so consumers of the RAW pairwise gate (format_gate,
    # which is NOT collapsed) can fold workorders → work_order themselves.
    _dup_members: dict[str, list[str]] = {}
    for _t, _r in _dup_rep.items():
        _dup_members.setdefault(_r, []).append(_t)
    duplicate_groups = [
        {"tables": sorted(members), "representative": r, "label": _rep_label(r), "count": _dup_count.get(r, len(members))}
        for r, members in _dup_members.items()
    ]

    # ── B17.1 — Top-3 format matches per source column (+ value pattern) ──────
    # For each column, of the OTHER-table columns that passed the ≥80% format gate, keep the 3
    # strongest (duplicate tables collapsed, best kept per target). Each match carries its format %
    # AND value-pattern %, and a `both` flag = passed BOTH gates (this is what actually groups).
    top_format_matches: list[dict] = []
    for (t, c) in nodes:
        if _is_dup_copy(t):
            continue
        best: dict[tuple, dict] = {}    # (rep_table, col) -> best match record
        for (t2, c2, fscore, vscore, grp) in _col_matches.get((t, c), []):
            if _is_dup_copy(t2):
                continue  # fold work_order_2 into work_order_1 — don't show the duplicate twice
            key = (_rep(t2), c2)
            rec = {
                "target": _prefixed(_rep(t2), c2),
                "format_pct": _pct(fscore),
                "value_pct": _pct(vscore),
                "both": bool(grp),
            }
            prev = best.get(key)
            if prev is None or (rec["both"], rec["format_pct"], rec["value_pct"]) > (
                prev["both"], prev["format_pct"], prev["value_pct"]
            ):
                best[key] = rec
        matches = sorted(
            best.values(),
            key=lambda m: (m["both"], m["format_pct"], m["value_pct"]),
            reverse=True,
        )[:3]
        if matches:
            top_format_matches.append({
                "source": _prefixed(_rep(t), c),
                "format": column_format(tables[t]["rows"], c),
                "matches": matches,
            })

    # ── B17.1 format grouping for the UI (built server-side so the FE just renders) ──
    # Instead of one row per matched column pair, columns that share a cell-value FORMAT are
    # grouped ONCE: per table (within-table) and across all tables (cross-table). Only formats
    # with ≥2 columns are "matched". _col_fmt is computed once per column (N, not the N² pairs).
    _col_fmt = {(t, c): _dims_for(t, c)[0] for (t, c) in nodes}
    _within_fmt: dict[str, dict[str, list[str]]] = {}   # keyed by REP table (dup copies skipped)
    _cross_fmt: dict[str, list[str]] = {}
    for (t, c) in nodes:
        if _is_dup_copy(t):
            continue  # representative carries the identical columns for the whole duplicate group
        fmt = _col_fmt[(t, c)]
        _within_fmt.setdefault(t, {}).setdefault(fmt, []).append(c)
        _cross_fmt.setdefault(fmt, []).append(_prefixed(_rep(t), c))

    def _within_accuracy_pct(rep_table: str, cols: list[str]) -> int:
        """Similarity % for a within-table group: mean pairwise format_similarity across the
        group's columns (they already share a format class, so this scores how close their cell
        formats actually are — the 'x% accuracy' for B17.1 same-table similarity)."""
        rows = tables[rep_table]["rows"]
        scores = [format_similarity(rows, a, rows, b) for a, b in combinations(cols, 2)]
        return round(100 * sum(scores) / len(scores)) if scores else 100

    within_table_format_groups = sorted(
        (
            {
                "table": _rep_label(t),
                "format": fmt,
                "columns": cols,
                "accuracy_pct": _within_accuracy_pct(t, cols),
            }
            for t, by in _within_fmt.items()
            for fmt, cols in by.items()
            if len(cols) >= 2
        ),
        key=lambda r: (r["table"], -len(r["columns"])),
    )
    cross_table_format_groups = sorted(
        (
            {"format": fmt, "columns": keys}
            for fmt, keys in _cross_fmt.items()
            if len(keys) >= 2
        ),
        key=lambda r: -len(r["columns"]),
    )

    # ── B18.1 value-shape grouping for the UI (mirrors format_groups above) ──
    # Columns sharing the same dominant value-shape skeleton (e.g. 'A-#3', 'Aa6') are
    # grouped ONCE per table and across all tables — no reverse pairwise duplicates.
    _col_shape = {(t, c): dominant_value_shape(tables[t]["rows"], c) for (t, c) in nodes}
    _within_shape: dict[str, dict[str, list[str]]] = {}
    _cross_shape: dict[str, list[str]] = {}
    for (t, c) in nodes:
        if _is_dup_copy(t):
            continue  # collapse duplicate tables (work_order ×2) — same as the format grouping above
        shape = _col_shape[(t, c)]
        if not shape:
            continue
        _within_shape.setdefault(_rep_label(t), {}).setdefault(shape, []).append(c)
        _cross_shape.setdefault(shape, []).append(_prefixed(_rep(t), c))
    within_table_value_groups = sorted(
        (
            {"table": t, "value_shape": shape, "columns": cols}
            for t, by in _within_shape.items()
            for shape, cols in by.items()
            if len(cols) >= 2
        ),
        key=lambda r: (r["table"], -len(r["columns"])),
    )
    cross_table_value_groups = sorted(
        (
            {"value_shape": shape, "columns": keys}
            for shape, keys in _cross_shape.items()
            if len(keys) >= 2
        ),
        key=lambda r: -len(r["columns"]),
    )

    # ── unified canonical names (per group + per column) ─────────────────────
    column_canonical: dict[str, str] = {}      # "table.col" -> canonical
    group_records: list[dict] = []
    display_multi_groups: list[list] = []      # raw groups kept for display, aligned with group_records
    for idx, g in enumerate(multi_groups, 1):
        gid = f"G{idx}"
        name = canonical_name_for_group(
            g, classification, dest_mapping=dest_col_by_source_col,
            dest_table_by_source=dest_table_by_source, tables=tables,
            field_resolver=field_resolver, group_id=gid,
        )
        fmt = Counter(column_format(tables[t]["rows"], c) for (t, c) in g).most_common(1)[0][0]
        # Stamp the group canonical on every member. A member whose VALUES belong to this group
        # but whose destination is a different entity (e.g. vendors.id holds A-### asset codes
        # → grouped as 'asset_id', but the dest is the vendor PK) keeps this canonical so the
        # B21 entity-prefix guard can flag it as a NEW column (asset codes don't belong in the
        # vendor's id) rather than silently merging into it. Stamped over EVERY raw member
        # (incl. duplicate-table copies) so downstream mapping for workorders.* is unaffected.
        for (t, c) in g:
            column_canonical[f"{t}.{c}"] = name
        canon_norm = _norm_col_name(name)
        # Collapse duplicate tables for DISPLAY (work_order + workorders → one member). A group
        # that then has a single distinct column existed ONLY because of the duplicate table
        # (e.g. work_order.wo_code ↔ workorders.wo_code) — it maps across no real tables, so drop
        # it from the B19.1 "columns that map together" panel rather than showing a group of one.
        _seen: set[tuple[str, str]] = set()
        _disp: list[tuple[str, str]] = []      # (original_table, column) — first copy of each rep wins
        for (t, c) in g:
            k = (_rep(t), c)
            if k in _seen:
                continue
            _seen.add(k)
            _disp.append((t, c))
        if len(_disp) < 2:
            continue
        group_records.append({
            "group_id": gid,
            "canonical_name": name,
            "format": fmt,
            "basis": f"{fmt} format + value pattern",
            "members": [_prefixed(_rep(t), c) for (t, c) in _disp],
            "member_keys": [{"table": _rep(t), "column": c} for (t, c) in _disp],
            # Per-member classification + which signal pulled this member into the
            # group: 'name' when the member's normalised column name matches the
            # group's canonical, 'value' otherwise (joined via cell value / format
            # similarity alone).
            "member_classes": [
                {
                    "prefixed_key": _prefixed(_rep(t), c),
                    "classification": _CLS_LABEL.get(cls_of(t, c), cls_of(t, c)),
                    "joined_by": "name" if _norm_col_name(c) == canon_norm else "value",
                    # Match % — PK parent = 100; FK = referential integrity (values found in the PK).
                    "match_pct": ri_pct_of(t, c),
                }
                for (t, c) in _disp
            ],
        })
        display_multi_groups.append(g)
    # singletons get a canonical name too (Tiers 3/4)
    for (t, c) in nodes:
        if f"{t}.{c}" not in column_canonical:
            column_canonical[f"{t}.{c}"] = canonical_name_for_group(
                [(t, c)], classification, dest_mapping=dest_col_by_source_col,
                dest_table_by_source=dest_table_by_source, tables=tables,
                field_resolver=field_resolver,
            )

    # ── Data-over-header canonical: RI ownership beats the auto-derived name ─────────────────────
    # A column whose values are a foreign reference to another entity (computed above) takes that
    # entity's <entity>_id as its canonical (Vendors.id → asset_id), overriding whatever name the
    # group/singleton tiers produced from its header. Applied BEFORE user pins so an explicit HITL
    # override still wins.
    for (_t, _c), _forced in _ri_canonical.items():
        column_canonical[f"{_t}.{_c}"] = _forced

    # ── HITL: apply user-pinned canonical overrides ──────────────────────────
    # Override format: {group_id: canonical_name} OR {table.col: canonical_name}.
    # Group-keyed entries rename every member of that group; column-keyed entries
    # pin just one member (useful to split a group's behaviour). Both feed
    # column_canonical so the rest of the build (collision guard, metadata
    # stamping, B17/B18/B19/B20/B21 propagation) picks up the new name.
    applied_overrides: dict[str, str] = {}
    # Columns whose canonical was set by an explicit user PIN. The B21 display shows the
    # source column name by default (we do NOT auto-rename a user's columns), but a column
    # the user deliberately pinned keeps the pinned name.
    _pinned_cols: set[str] = set()
    if canonical_overrides:
        # Index group_records by id for quick rewrite
        _grp_by_id = {rec["group_id"]: rec for rec in group_records}
        for k, new_name in canonical_overrides.items():
            if not new_name or not isinstance(new_name, str):
                continue
            new_name = new_name.strip()
            if not new_name:
                continue
            # Case 1 — group-level override (e.g. {"G2": "asset_id"})
            if k in _grp_by_id:
                rec = _grp_by_id[k]
                rec["canonical_name"] = new_name
                # Re-stamp every member with the new canonical. A member whose dest is a
                # different entity (vendors.id under an 'asset_id' pin) keeps the name too —
                # the B21 entity guard then flags it as a NEW column rather than a merge.
                for mk in rec.get("member_keys", []):
                    column_canonical[f"{mk['table']}.{mk['column']}"] = new_name
                    _pinned_cols.add(f"{mk['table']}.{mk['column']}")
                # member_classes joined_by: now compare against the override
                _new_norm = _norm_col_name(new_name)
                for mc, mk in zip(rec.get("member_classes", []), rec.get("member_keys", [])):
                    mc["joined_by"] = "name" if _norm_col_name(mk["column"]) == _new_norm else "value"
                applied_overrides[k] = new_name
            # Case 2 — column-level override (e.g. {"vendors.id": "asset_id"})
            elif "." in k and k in column_canonical:
                column_canonical[k] = new_name
                _pinned_cols.add(k)
                applied_overrides[k] = new_name
            else:
                # Try case-insensitive table match for column-level overrides
                lc = k.lower()
                _hit = next((kk for kk in column_canonical if kk.lower() == lc), None)
                if _hit:
                    column_canonical[_hit] = new_name
                    _pinned_cols.add(_hit)
                    applied_overrides[k] = new_name

    # ── Within-table canonical collision guard ───────────────────────────────
    # Two ways the override can produce duplicate (table, canonical) keys:
    #   (a) The canonical happens to match another column ALREADY in that table
    #       (e.g. Sites has a real `id` column AND Sites.asset_id's group has
    #       canonical 'id').
    #   (b) Two members of the SAME table land on the same canonical because
    #       they're both in the same multi-member group (e.g. works.id +
    #       works.tagnum both renamed to 'asset_id'). Only one can keep it.
    #
    # Priority for who keeps the canonical when there's a tie:
    #   1. A column literally named the canonical (it's already there).
    #   2. A PK column.
    #   3. Otherwise the first one by source-column name (deterministic).
    # Every loser falls back to its source column name.
    table_columns: dict[str, set[str]] = {t: set(meta.get("columns") or []) for t, meta in tables.items()}

    # Bucket every node by (table, canonical) so we can see the conflicts.
    canon_owners: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for (t, c) in nodes:
        canon = column_canonical.get(f"{t}.{c}")
        if not canon:
            continue
        canon_owners.setdefault((t, canon), []).append((t, c))

    def _priority(member: tuple[str, str], canon_name: str) -> tuple:
        t_, c_ = member
        is_native = 0 if c_ == canon_name else 1
        is_pk = 0 if (classification.get((t_, c_)) or {}).get("classification") == "PK" else 1
        return (is_native, is_pk, c_)

    suppressed: set[str] = set()
    for (t, canon), owners in canon_owners.items():
        # Also count any sibling column whose name LITERALLY matches the canonical
        # but isn't being renamed (Case a): it's an implicit owner the override
        # would step on.
        native = (t, canon) if canon in table_columns.get(t, ()) and (t, canon) not in owners else None
        contenders = owners + ([native] if native else [])
        if len(contenders) <= 1:
            continue
        winner = sorted(contenders, key=lambda m: _priority(m, canon))[0]
        for m in contenders:
            if m == winner or m == native:
                continue
            suppressed.add(f"{m[0]}.{m[1]}")
    for key in suppressed:
        # Restore the source column name as canonical for the colliding member,
        # so it surfaces under its original (table, column) key.
        t, c = key.split(".", 1)
        column_canonical[key] = c

    # B14/B15 — stamp the canonical (unified) name onto every metadata row as the 5th dimension.
    # The source UDR view re-keys each row by `{table}.{canonical}` so that columns sharing a
    # group surface under the same unified identifier (e.g. WorkOrders.asset_no, qatty.tag_id →
    # WorkOrders.asset_id, qatty.asset_id). The original header stays as `source_column`.
    for _m in metadata:
        _key = f"{_m['source_table']}.{_m['column']}"
        _canon = column_canonical.get(_key)
        _m["canonical_name"] = _canon
        _m["canonical_prefixed_key"] = _prefixed(_m["source_table"], _canon) if _canon else _m["prefixed_key"]
        _m["canonical_suppressed"] = _key in suppressed

    # B13.1 — re-key prefixing rows with the canonical name too (source UDR override).
    # Also stamp the PK/FK/Shared classification so the prefixing card can show
    # the PK badge alongside the unified name (otherwise PK is only visible in
    # B14.1 metadata, which surprised users reading the B13.1 panel directly).
    for _p in prefixing:
        _t, _c = _p["source_table"], _p["column"]
        _key = f"{_t}.{_c}"
        _canon = column_canonical.get(_key)
        _p["source_column"] = _c
        _p["canonical_name"] = _canon
        _p["canonical_prefixed_key"] = _prefixed(_t, _canon) if _canon else _p["prefixed_key"]
        _p["classification"] = _CLS_LABEL.get(cls_of(_t, _c), cls_of(_t, _c))
        _p["canonical_suppressed"] = _key in suppressed

    # ── Propagate canonical override to B17.1 / B18.1 pair rows ────────────────
    # Pair rows were emitted with raw "table.col" before column_canonical existed.
    # Re-key now so every downstream panel reads the same canonical identifier
    # used in B13/B14/B19 (e.g. vendors.id rendered as vendors.asset_id when its
    # group's canonical resolved to asset_id).
    # column_canonical keys use original-case table names (e.g. 'Vendors.id'); the
    # prefixed_key form is lowercase ('vendors.id'). Build a lowercase-keyed index
    # so _canonical_key can resolve either case without losing entries.
    column_canonical_lc: dict[str, str] = {
        f"{k.split('.', 1)[0].lower()}.{k.split('.', 1)[1]}": v
        for k, v in column_canonical.items() if "." in k
    }

    def _canonical_key(k: str) -> str:
        canon = column_canonical_lc.get(k) or column_canonical.get(k)
        if not canon:
            return k
        return f"{k.split('.', 1)[0]}.{canon}"

    for _p in format_pairs:
        _p["canonical_col_a"] = _canonical_key(_p["col_a"])
        _p["canonical_col_b"] = _canonical_key(_p["col_b"])
    for _p in value_pairs:
        _p["canonical_col_a"] = _canonical_key(_p["col_a"])
        _p["canonical_col_b"] = _canonical_key(_p["col_b"])
    for _p in within_format_pairs:
        _p["canonical_col_a"] = _canonical_key(_p["col_a"])
        _p["canonical_col_b"] = _canonical_key(_p["col_b"])
    for _p in within_value_pairs:
        _p["canonical_col_a"] = _canonical_key(_p["col_a"])
        _p["canonical_col_b"] = _canonical_key(_p["col_b"])
    for _g in within_table_group_records:
        _g["canonical_members"] = [_canonical_key(_m) for _m in _g.get("members", [])]

    # B19.1 — stamp each group's members + member_classes with their canonical key
    # so the B17.1·B18.1 "Columns that map together" panel can render them under
    # the unified identifier (vendors.asset_id) instead of the original (vendors.id).
    for _g in group_records:
        _g["canonical_members"] = [_canonical_key(_m) for _m in _g.get("members", [])]
        for _mc in _g.get("member_classes", []):
            _mc["canonical_prefixed_key"] = _canonical_key(_mc["prefixed_key"])

    # ── B20.1 — classify each group PK / FK / Shared ─────────────────────────
    pk_groups = fk_groups = shared_groups = 0
    classification_rows: list[dict] = []
    for rec, g in zip(group_records, display_multi_groups):
        pk_member = next(((t, c) for (t, c) in g if cls_of(t, c) == "PK"), None)
        fk_member = next(((t, c) for (t, c) in g if cls_of(t, c) == "FK"), None)
        ri_vals = [
            (classification.get((t, c)) or {}).get("referential_integrity")
            for (t, c) in g
            if (classification.get((t, c)) or {}).get("classification") == "FK"
        ]
        ri = max([r for r in ri_vals if isinstance(r, (int, float))], default=None)
        if pk_member and fk_member:
            verdict = "Foreign Key relationship"; fk_groups += 1
        elif pk_member:
            verdict = "Primary Key group"; pk_groups += 1
        else:
            verdict = "Shared Attribute"; shared_groups += 1
        classification_rows.append({
            "group_id": rec["group_id"],
            "canonical_name": rec["canonical_name"],
            "members": rec["members"],
            "has_pk": _prefixed(*pk_member) if pk_member else None,
            "fk_column": _prefixed(*fk_member) if fk_member else None,
            # Canonical-overridden form of has_pk / fk_column so B20.1 reads with
            # the same unified identifier as B13.1 / B14.1 / B19.1.
            "canonical_has_pk": _canonical_key(_prefixed(*pk_member)) if pk_member else None,
            "canonical_fk_column": _canonical_key(_prefixed(*fk_member)) if fk_member else None,
            "ri": ri,
            "verdict": verdict,
        })

    # ── B22.1 — synthesize a lookup table per SHARED-ATTRIBUTE group + DDL + FK rewrites ──
    # A shared attribute that is NOT any table's PK/FK (e.g. asset_type shared by assets +
    # pm_templates) is NORMALISED into a new lookup table whose PRIMARY KEY *is* that attribute; its
    # rows are the attribute's distinct values. We reuse the tested promote_shared_attribute() (which
    # also carries functionally-dependent descriptors) and additionally emit the CREATE TABLE DDL and
    # the ALTER … FOREIGN KEY rewrites (each source column → the new lookup PK). FK groups already
    # reference an existing entity table, so they are skipped.
    shared_attribute_tables: list[dict] = []
    for rec, g in zip(classification_rows, display_multi_groups):
        if rec["verdict"] != "Shared Attribute":
            continue
        entry = synthesize_shared_attribute_table(
            tables,
            canonical_name=rec["canonical_name"],
            group_id=rec["group_id"],
            member_pairs=[(t, c) for (t, c) in g],
            members_display=rec["members"],
        )
        if entry:
            shared_attribute_tables.append(entry)

    # ── B20.1 completeness — add EVERY table's primary key as its own "Primary Key group" row ──
    # The group-based classification above only surfaces a PK when another table references it (it
    # forms a cross-table group). A leaf table's PK (pm_template_id, wo_id) references nothing, so it
    # never appeared — leaving B20.1 (2.9) showing fewer PKs than B8.1 (2.2). Append the missing ones
    # so every table's primary key is represented, matching the 2.2 primary-key list. Duplicate-copy
    # tables are skipped (their rep already carries the PK).
    _pk_shown = {rec.get("has_pk") for rec in classification_rows if rec.get("has_pk")}
    for _t, _pk_cols in pk_by_table.items():
        if _is_dup_copy(_t) or not _pk_cols:
            continue
        _key = _prefixed(_rep(_t), _pk_cols[0])
        if _key in _pk_shown:
            continue
        _pk_shown.add(_key)
        pk_groups += 1
        classification_rows.append({
            "group_id": f"PK-{_rep(_t)}",
            "canonical_name": _pk_cols[0],
            "members": [_key],
            "has_pk": _key,
            "fk_column": None,
            "canonical_has_pk": _key,
            "canonical_fk_column": None,
            "ri": None,
            "verdict": "Primary Key group",
        })

    # ── B21.1 — destination column mapping (one row per column) ──────────────
    _dest_samples_by_table = dest_samples_by_table or {}
    _dest_cols_by_table = dest_columns_by_table or {}

    def _dest_has_column(dest_tbl, dcol):
        """Whether the destination column actually EXISTS on the routed table.

        Returns True/False when the table's column list is known, else None (unknown —
        don't second-guess). Guards against a name-only / LLM match onto a column the table
        doesn't have (e.g. 'asset id' → 'asset_code' on 'sites', which has no asset_code) —
        such a mapping is a NEW column, never an auto-resolve.
        """
        if not dest_tbl or not dcol:
            return None
        cols = _dest_cols_by_table.get(str(dest_tbl).lower())
        if not cols:
            return None
        return _norm_col_name(dcol) in {_norm_col_name(x) for x in cols}

    def _dest_sample_values(dest_tbl, dcol):
        """Existing values of the destination column (case-insensitive), or None when the
        target table/column has no sampled data — then the value dimension is 'unknown'."""
        if not dest_tbl or not dcol:
            return None
        tvals = _dest_samples_by_table.get(str(dest_tbl).lower())
        if not tvals:
            return None
        if dcol in tvals:
            return tvals[dcol]
        return {k.lower(): v for k, v in tvals.items()}.get(str(dcol).lower())

    _BAND = {"auto_resolved": "auto-resolved", "suggested": "suggested — confirm",
             "new_column": "new column"}
    dest_mapping_rows: list[dict] = []
    mapped = 0
    for (t, c) in nodes:
        # B14.1 four-dimension metadata carried on EACH row so the column-mapping gate can show
        # Dest UDR table · PK? · cell-value format · sample values next to every source column
        # (not just source → matched column). Reuses the memo above — no second O(rows) scan.
        _dim_format, _dim_samples = _dims_for(t, c)
        _dim_cls = cls_of(t, c)
        _dim_is_pk = _dim_cls == "PK"
        _dim_cls_label = _CLS_LABEL.get(_dim_cls, _dim_cls)
        dcol = dest_col_by_source_col.get((t, c))
        conf = conf_by_source_col.get((t, c))
        # `_canon` is the UNIFIED canonical (group/FM) — kept for internal resolution + grouping.
        # `_disp` is what the user SEES: their own source column name, unless they explicitly
        # pinned a canonical for this column. We do NOT auto-rename a customer's source columns
        # in the display (no vendor_name→supplier, property_ref→site_id, vendors.id→asset_id);
        # the destination it maps to is shown separately via matched_column.
        _canon = column_canonical.get(f"{t}.{c}")
        _disp = _canon if f"{t}.{c}" in _pinned_cols else c
        _disp_key = _prefixed(t, _disp)
        _scores = None
        # Canonical-driven resolution: the deterministic mapper may leave a column unresolved
        # even though the cross-table grouping gave it a canonical name that IS a real column
        # on the routed table — e.g. works.property_ref grouped to canonical 'site_id', and the
        # 'assets' table HAS a site_id column. Resolve to it so the grouping intelligence isn't
        # discarded as a false "new column". Exact (normalised) canonical → real column only;
        # truly semantic gaps (product_name→asset_name, condition→status) stay for Node 4.
        if not dcol and _canon and dest_table(t):
            _dcols = _dest_cols_by_table.get(str(dest_table(t)).lower())
            if _dcols:
                _hit = {_norm_col_name(x): x for x in _dcols}.get(_norm_col_name(_canon))
                if _hit:
                    dcol = _hit
        # Unresolved-suggestion fallback (LOWER priority than canonical-driven): the deterministic
        # mapper left this column unresolved (→ semantic), but the gate already shows a best-guess
        # suggestion to a REAL destination column (wo_status → status, date_raised → updated_at).
        # Reflect that same suggestion here so B21 and Step 2 agree instead of B21 flatly showing
        # 'new column'. Runs only AFTER canonical-driven resolution so a strong canonical match
        # (property_ref → site_id) still beats a weak fuzzy suggestion (property_ref → document_ids).
        if not dcol:
            _sg = unresolved_suggest_by_source_col.get((t, c))
            if _sg and _dest_has_column(dest_table(t), _sg):
                dcol = _sg
        if dcol:
            mapped += 1
            # Guard 0 — the matched column must actually EXIST on the routed table. A
            # name-only / LLM match onto a column the table doesn't have (e.g. 'asset id'
            # → 'asset_code' on 'sites') is a NEW column, not an auto-resolve.
            if _dest_has_column(dest_table(t), dcol) is False:
                mapped -= 1
                dest_mapping_rows.append({
                    "source": _prefixed(t, c),
                    "canonical_source": _disp_key,
                    "canonical_name": _disp,
                    "dest_table": dest_table(t),
                    "matched_column": None,
                    "confidence": conf,
                    "scores": None,
                    "outcome": "new column",
                    "source_column": c,
                    "source_tables": [t],
                    "is_primary_key": _dim_is_pk,
                    "classification": _dim_cls_label,
                    "format": _dim_format,
                    "samples": _dim_samples,
                })
                continue
            # Combined gate (name AND value AND ontology AND context). Always score so the
            # name-based entity-prefix guard (asset_id → vendors.id) fires even for an EMPTY
            # destination table; the value dimension is simply "unknown" when there are no
            # destination samples. Use the CANONICAL as the name dimension so the entity check
            # can see a qualified-PK alias — the DISPLAY still shows the source column (_disp).
            _dvals = _dest_sample_values(dest_table(t), dcol)
            _sc = score_column_mapping(
                source_name=_canon or c,
                source_values=column_values(tables[t]["rows"], c),
                dest_name=dcol,
                dest_values=_dvals,            # may be None → value dimension is inconclusive
                same_table=True,
                field_resolver=field_resolver,
                # Threading dest_table lets score_column_mapping detect entity mismatch on
                # qualified-PK containment (asset_id → vendors.id).
                dest_table=dest_table(t),
            )
            # Demote to a NEW column on a CONFIRMED mismatch only: a value/namespace mismatch
            # (A-### vs V-###) or an entity-prefix conflict (asset_id → vendors.id even when
            # values coincidentally overlap). A weak NAME alone must NOT undo a mapping the
            # deterministic mapper already made — curated aliases legitimately have low name
            # similarity (product code→asset_code, condition→status). Otherwise trust the band.
            if _sc.get("value_match") is False or _sc.get("entity_conflict"):
                band = "new column"
                mapped -= 1
                if _sc.get("entity_conflict"):
                    dcol = None  # drop the misleading target so B21 shows '—'
                    # This column's values belong to a DIFFERENT entity than its destination
                    # (vendors.id holds asset codes). The new column it creates is named after
                    # what the values actually are — show the canonical (e.g. vendors.asset_id),
                    # not the misleading source 'id'.
                    if _canon:
                        _disp = _canon
                        _disp_key = _prefixed(t, _canon)
            else:
                band = (
                    "auto-resolved" if isinstance(conf, (int, float)) and conf >= 0.95
                    else "suggested — confirm" if isinstance(conf, (int, float)) and conf >= 0.70
                    else "matched"
                )
            # Surface the per-dimension breakdown only when destination data actually informed
            # it (keeps the B21 table readable for empty-table migrations).
            if _dvals is not None:
                _scores = {
                    "name": _sc["name_score"], "value": _sc["value_score"],
                    "ontology": _sc["ontology_score"], "context": _sc["context_score"],
                    "final": _sc["final_score"],
                    "entity_conflict": _sc.get("entity_conflict"),
                }
        else:
            band = "new column" if dest_table(t) else "review"
        _src_key = _prefixed(t, c)
        dest_mapping_rows.append({
            "source": _src_key,
            # The DISPLAYED identity = the user's source column (or a name they explicitly
            # pinned). We don't auto-rename source columns to a unified canonical here; the
            # destination column the data lands in is shown via matched_column.
            "canonical_source": _disp_key,
            "canonical_name": _disp,
            "dest_table": dest_table(t),
            "matched_column": dcol if band != "new column" else None,
            "confidence": conf,
            "scores": _scores,
            "outcome": band,
            "source_column": c,
            "source_tables": [t],
            "is_primary_key": _dim_is_pk,
            "classification": _dim_cls_label,
            "format": _dim_format,
            "samples": _dim_samples,
        })

    # ── Collapse co-routed duplicate sheets (work_order + workorders → work_orders) ──────────────
    # Two source sheets that route to the SAME destination and share a column produce two identical
    # dest-mapping rows — the B14.1 gate then shows duplicate cards. Keep ONE row per
    # (dest_table, source_column) and aggregate every contributing source table into ``source_tables``
    # so a re-target/exclude decision at the gate fans out to ALL of them (matching the B14.1 metadata
    # dedup above). Ordering is preserved.
    _dm_seen: dict[tuple, dict] = {}
    _dm_deduped: list[dict] = []
    for _row in dest_mapping_rows:
        _dt = str(_row.get("dest_table") or "").strip().lower()
        if not _dt:
            # Unrouted / new-table column — never collapse (its "dest" is its own source name).
            _dm_deduped.append(_row)
            continue
        _key = (_dt, str(_row.get("source_column") or "").lower())
        _prev = _dm_seen.get(_key)
        if _prev is None:
            _dm_seen[_key] = _row
            _dm_deduped.append(_row)
        else:
            for _st in _row.get("source_tables") or []:
                if _st not in _prev["source_tables"]:
                    _prev["source_tables"].append(_st)
    dest_mapping_rows = _dm_deduped

    # ── summary ──────────────────────────────────────────────────────────────
    confs = [v for v in conf_by_source_col.values() if isinstance(v, (int, float))]
    # Per-COLUMN PK/FK/Shared counts (the "5 Primary Keys / 3 Foreign Keys / 22 Shared
    # Attributes" chips) — distinct from the per-GROUP pk_groups/fk_groups/shared_groups.
    # Exposed here so the pre-semantic gate's column_intelligence carries them and the live
    # Activity-Log run row can show them progressively, not only at completion.
    _per_col = {"PK": 0, "FK": 0, "SHARED_ATTRIBUTE": 0}
    for _v in classification.values():
        _k = (_v or {}).get("classification")
        if _k in _per_col:
            _per_col[_k] += 1
    summary = {
        "columns_analyzed": len(nodes),
        "pk": _per_col["PK"],
        "fk": _per_col["FK"],
        "shared": _per_col["SHARED_ATTRIBUTE"],
        "pk_groups": pk_groups,
        "fk_groups": fk_groups,
        "shared_groups": shared_groups,
        "columns_mapped": mapped,
        "groups": len(group_records),
        "confidence": round(sum(confs) / len(confs), 4) if confs else None,
        "format_threshold_pct": _pct(FORMAT_MIN),
        "value_threshold_pct": _pct(VALUE_MIN),
        "format_pairs_scored": _total_format_pairs,
        "format_survivors": fmt_pass,
        "value_survivors": value_survivors,
        "within_table_pairs_scored": _total_within_format_pairs,
        "within_table_format_survivors": within_fmt_pass,
        "within_table_value_survivors": within_value_survivors,
        "within_table_groups": len(within_table_group_records),
        "cross_table_groups": len(group_records),
    }

    return {
        "engine_version": ENGINE_VERSION,
        "prefixing": prefixing,
        "metadata": metadata,
        "metadata_total": len(nodes),
        "fk_candidates": fk_candidates,
        "format_gate": format_pairs,
        "value_pattern": value_pairs,
        # B17.1 — per-column Top-3 cross-table format matches (≥80%) with value-pattern % + `both`.
        "top_format_matches": top_format_matches,
        # Duplicate-table groups (work_order + workorders) so the UI can collapse the raw format_gate.
        "duplicate_groups": duplicate_groups,
        # B22.1 — lookup tables synthesized from shared attributes (attribute → new PK table).
        "shared_attribute_tables": shared_attribute_tables,
        # B17.1 format grouping (server-built) — columns sharing a format, grouped once.
        "format_groups": {
            "within_table": within_table_format_groups,
            "cross_table": cross_table_format_groups,
        },
        "value_pattern_groups": {
            "within_table": within_table_value_groups,
            "cross_table": cross_table_value_groups,
        },
        "within_table_similarity": {
            "format_gate": within_format_pairs,
            "value_pattern": within_value_pairs,
            "groups": within_table_group_records,
            "summary": {
                "pairs_scored": _total_within_format_pairs,
                "format_survivors": within_fmt_pass,
                "value_survivors": within_value_survivors,
                "groups": len(within_table_group_records),
                "format_threshold_pct": _pct(FORMAT_MIN),
                "value_threshold_pct": _pct(VALUE_MIN),
            },
        },
        "groups": group_records,
        "classification": classification_rows,
        "column_canonical": column_canonical,
        "dest_mapping": dest_mapping_rows,
        # Real destination-table columns per table (JSON-safe lists) so the B14.1 column-mapping
        # gate can offer a re-target dropdown of valid columns.
        "dest_columns_by_table": {
            str(_t): sorted(str(_c) for _c in (_cols or []))
            for _t, _cols in _dest_cols_by_table.items()
        },
        "summary": summary,
        # Echo of which overrides were accepted (so the FE can confirm pin status).
        "applied_canonical_overrides": applied_overrides,
    }


def apply_canonical_overrides(ci: dict, overrides: dict) -> dict:
    """Re-stamp an already-built column-intelligence report with user canonical pins.

    ``overrides`` is the same ``{group_id | "table.col": canonical_name}`` map
    build_column_intelligence consumes. This refreshes a CI that was built BEFORE the latest
    pins were known: the pre-semantic gate builds the CI from ``state.canonical_overrides``
    BEFORE the resume delivers THIS submit's pins (the build runs before ``interrupt()``; the
    new pins arrive via the interrupt), so without this re-stamp a pin made in the current
    submit (e.g. vendors.id → asset_id) never reaches ``state.column_intelligence`` and the
    Tier-2 gate / state-canonicals endpoint keep showing the stale source name.

    Group-level pins rename the group + every member's ``column_canonical`` entry; column-level
    pins (``"table.col"``) set just that entry. Mutates + returns ``ci``. Pure."""
    if not isinstance(ci, dict) or not isinstance(overrides, dict) or not overrides:
        return ci
    groups = ci.get("groups") if isinstance(ci.get("groups"), list) else []
    grp_by_id = {g.get("group_id"): g for g in groups if isinstance(g, dict)}
    col_canon = ci.get("column_canonical")
    if not isinstance(col_canon, dict):
        col_canon = {}
        ci["column_canonical"] = col_canon
    for k, v in overrides.items():
        if not k or not isinstance(v, str) or not v.strip():
            continue
        name = v.strip()
        if k in grp_by_id:  # group-level pin → rename the group + every member
            g = grp_by_id[k]
            g["canonical_name"] = name
            for mk in (g.get("member_keys") or []):
                if isinstance(mk, dict) and mk.get("table") and mk.get("column"):
                    col_canon[f"{mk['table']}.{mk['column']}"] = name
        elif "." in str(k):  # column-level pin
            col_canon[str(k)] = name
    return ci


def _normalize_verdict_target(value) -> str | None:
    """Coerce a gate override value to its TARGET verdict, 'fk' | 'shared'.

    Tolerates label-ish inputs and the applied-record forms persisted on state
    ('fk_to_shared' → target 'shared', 'shared_to_fk' → target 'fk') so the post-write
    re-apply consumes ``state.classification_overrides`` directly."""
    v = str(value or "").strip().lower()
    if v in ("fk", "foreign_key", "foreign key", "foreign key relationship", "shared_to_fk"):
        return "fk"
    if v in ("shared", "shared_attribute", "shared attribute", "fk_to_shared"):
        return "shared"
    return None


def apply_classification_decisions(
    ci: dict,
    tables: dict | None = None,
    *,
    rejected=None,
    overrides=None,
) -> dict:
    """Apply the B20.1 gate's human decisions onto a column-intelligence report, in place.

    ``rejected`` — group_ids the user DE-selected: dropped from the classification display,
    their B22.1 lookup tables are not emitted, and fk_candidates on their member columns are
    removed (nothing of the group is applied downstream).

    ``overrides`` — ``{group_id: "fk" | "shared"}`` human re-classifications:
      • FK → Shared Attribute: the FK is NOT enforced; the group instead synthesises its
        B22.1 lookup table + FK-rewrite DDL (needs ``tables`` = ``{name: {"rows", "columns"}}``;
        the verdict change still applies when data is unavailable, just without a lookup).
      • Shared Attribute → FK: the lookup table is NOT created; the group is recorded as a
        Foreign Key relationship. Enforcement (fk_candidates) is only added when the group
        carries a PK member to reference — otherwise the re-classification is display/audit
        only (there is no PK to enforce against).

    The row keeps a ``human_override`` marker so every downstream view (Activity-Log Section 2,
    the post-write UDR report) can show the decision was human, not model. Group counts in
    ``ci["summary"]`` are recomputed. Pure (no DB / LLM); mutates + returns a summary dict:
    ``{rejected_applied, overrides_applied, lookup_tables_added, fk_candidates_added,
    fk_candidates_removed}``.
    """
    result = {
        "rejected_applied": [],
        "overrides_applied": {},
        "lookup_tables_added": [],
        "fk_candidates_added": 0,
        "fk_candidates_removed": 0,
    }
    if not isinstance(ci, dict):
        return result
    classification = ci.get("classification")
    if not isinstance(classification, list) or not classification:
        return result

    rejected_set = {str(g) for g in rejected} if isinstance(rejected, (list, tuple, set)) else set()
    override_map: dict[str, str] = {}
    if isinstance(overrides, dict):
        for gid, target in overrides.items():
            t = _normalize_verdict_target(target)
            if t and str(gid) not in rejected_set:  # rejection wins over a re-classification
                override_map[str(gid)] = t

    if not rejected_set and not override_map:
        return result

    # Case-insensitive table-name resolution for member keys ('assets.asset_type' → real key).
    tbl_by_lower = {str(k).lower(): k for k in (tables or {})}

    def _member_pairs(row) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for m in (row.get("members") or []):
            key = str(m)
            if "." not in key:
                continue
            t, c = key.split(".", 1)
            real = tbl_by_lower.get(t.lower())
            if real is not None:
                pairs.append((real, c))
        return pairs

    def _member_keys(row) -> set[str]:
        return {str(m).lower() for m in (row.get("members") or [])}

    fk_candidates = ci.get("fk_candidates") if isinstance(ci.get("fk_candidates"), list) else []
    shared_tables = (
        ci.get("shared_attribute_tables")
        if isinstance(ci.get("shared_attribute_tables"), list) else []
    )

    def _drop_fk_candidates(keys: set[str]) -> None:
        before = len(fk_candidates)
        fk_candidates[:] = [
            f for f in fk_candidates
            if f"{f.get('src_table')}.{f.get('src_column')}".lower() not in keys
        ]
        result["fk_candidates_removed"] += before - len(fk_candidates)

    # ── 1. Human re-classifications (on rows that survive) ─────────────────────
    existing_lookup_groups = {str(t.get("from_group")) for t in shared_tables}
    for row in classification:
        gid = str(row.get("group_id"))
        target = override_map.get(gid)
        if not target:
            continue
        verdict = str(row.get("verdict") or "").lower()
        if target == "shared" and "foreign" in verdict:
            row["verdict"] = "Shared Attribute"
            row["human_override"] = "fk_to_shared"
            _drop_fk_candidates(_member_keys(row))
            if tables and gid not in existing_lookup_groups:
                pairs = _member_pairs(row)
                canonical = str(row.get("canonical_name") or (pairs[0][1] if pairs else "")).strip()
                if pairs and canonical:
                    entry = synthesize_shared_attribute_table(
                        tables,
                        canonical_name=canonical,
                        group_id=gid,
                        member_pairs=pairs,
                        members_display=row.get("members"),
                    )
                    if entry:
                        shared_tables.append(entry)
                        existing_lookup_groups.add(gid)
                        result["lookup_tables_added"].append(entry["table_name"])
            result["overrides_applied"][gid] = "fk_to_shared"
        elif target == "fk" and "shared" in verdict:
            row["verdict"] = "Foreign Key relationship"
            row["human_override"] = "shared_to_fk"
            shared_tables[:] = [t for t in shared_tables if str(t.get("from_group")) != gid]
            pk_key = row.get("has_pk") or row.get("canonical_has_pk")
            if pk_key and "." in str(pk_key):
                dt, dc = str(pk_key).split(".", 1)
                for (st, sc) in _member_pairs(row):
                    if f"{st}.{sc}".lower() == str(pk_key).lower():
                        continue
                    fk_candidates.append({
                        "src_table": st, "src_column": sc,
                        "dst_table": tbl_by_lower.get(dt.lower(), dt), "dst_column": dc,
                        "ri": row.get("ri"), "confirmed": True,
                        "reason": "human override at classification gate (B20.1)",
                    })
                    result["fk_candidates_added"] += 1
            result["overrides_applied"][gid] = "shared_to_fk"

    # ── 2. De-selected groups — nothing of theirs is applied ───────────────────
    if rejected_set:
        rej_keys: set[str] = set()
        for row in classification:
            if str(row.get("group_id")) in rejected_set:
                rej_keys |= _member_keys(row)
        shared_tables[:] = [
            t for t in shared_tables if str(t.get("from_group")) not in rejected_set
        ]
        classification[:] = [
            c for c in classification if str(c.get("group_id")) not in rejected_set
        ]
        _drop_fk_candidates(rej_keys)
        result["rejected_applied"] = sorted(rejected_set)

    ci["classification"] = classification
    ci["fk_candidates"] = fk_candidates
    ci["shared_attribute_tables"] = shared_tables

    # Keep the per-GROUP summary chips consistent with the post-decision verdicts.
    summary = ci.get("summary")
    if isinstance(summary, dict):
        verdicts = [str(c.get("verdict") or "").lower() for c in classification]
        summary["pk_groups"] = sum("primary" in v for v in verdicts)
        summary["fk_groups"] = sum("foreign" in v for v in verdicts)
        summary["shared_groups"] = sum("shared" in v for v in verdicts)

    return result
