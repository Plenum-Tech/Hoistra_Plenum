"""Table/column metadata + document inventory for migration review UIs.

Requirement #9 (full table + column metadata at hierarchy review, untruncated)
and #10 (document summary + extracted-table inventory). Pure, defensive helpers
that read the LangGraph migration state — no DB calls, no side effects.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

# Cap rows scanned for per-column stats (unique / nullable / type / samples).
# row_count itself is exact (taken from the dedup counts / full record length).
_MAX_ROWS_SCAN = 20000
_MAX_SAMPLE = 5

# 7.3 AC5/AC6 — composite-PK search bounds + surrogate-key marker.
_MAX_COMPOSITE_COLS = 3       # test 2- and 3-column combinations
_MAX_COMPOSITE_CANDIDATES = 12  # cap candidate columns fed to the combination search
_MAX_COMPOSITE_COMBOS = 60    # hard cap on combinations evaluated (perf guard)
SURROGATE_PK_NAME = "udr_surrogate_id"


def _detect_composite_pk(records: list[dict], col_names: list[str]) -> list[str]:
    """7.3 AC5 — the smallest 2+ column combination that is 100% unique AND 0% null.

    Bounded search: only columns that are non-null in every scanned row and not
    constant are eligible; the number of candidates and total combinations tested
    are capped so this stays cheap on wide tables. Returns [] if none qualifies.
    """
    rows = records[:_MAX_ROWS_SCAN]
    n = len(rows)
    if n == 0:
        return []
    candidates: list[str] = []
    for col in col_names:
        vals = [r.get(col) for r in rows]
        nonnull = [v for v in vals if v is not None and not (isinstance(v, str) and not v.strip())]
        if len(nonnull) != n:  # any null disqualifies (AC requires 0% null)
            continue
        if len({str(v) for v in nonnull}) <= 1:  # constant column can't add uniqueness
            continue
        candidates.append(col)
        if len(candidates) >= _MAX_COMPOSITE_CANDIDATES:
            break
    tested = 0
    for size in range(2, _MAX_COMPOSITE_COLS + 1):
        for combo in combinations(candidates, size):
            tested += 1
            if tested > _MAX_COMPOSITE_COMBOS:
                return []
            keys = {tuple(str(r.get(c)) for c in combo) for r in rows}
            if len(keys) == n:  # 100% unique across the combination
                return list(combo)
    return []


def _records_for(state: dict, table: str) -> list[dict]:
    """Full records for a table, preferring full_tables then parsed_tables."""
    for key in ("full_tables", "cleaned_tables", "parsed_tables"):
        container = state.get(key) or {}
        val = container.get(table)
        if isinstance(val, list) and val:
            return [r for r in val if isinstance(r, dict)]
    return []


def _infer_datatype(values: list[Any]) -> str:
    kinds: set[str] = set()
    for v in values[:500]:
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        if isinstance(v, bool):
            kinds.add("boolean")
        elif isinstance(v, int):
            kinds.add("integer")
        elif isinstance(v, float):
            kinds.add("numeric")
        else:
            s = str(v).strip()
            try:
                int(s)
                kinds.add("integer")
                continue
            except ValueError:
                pass
            try:
                float(s)
                kinds.add("numeric")
                continue
            except ValueError:
                pass
            kinds.add("varchar")
        if "varchar" in kinds and len(kinds) > 1:
            break
    if not kinds:
        return "varchar"
    if kinds == {"integer"}:
        return "integer"
    if kinds <= {"integer", "numeric"}:
        return "numeric"
    if kinds == {"boolean"}:
        return "boolean"
    return "varchar"


def _fallback_filename(state: dict) -> str:
    name = (str(state.get("cmms_name") or "source")).strip() or "source"
    fmt = (str(state.get("detected_file_format") or "")).strip()
    return f"{name}.{fmt}" if fmt else name


def _fk_by_table(state: dict) -> dict[str, set]:
    out: dict[str, set] = {}
    edges = list(state.get("confirmed_hierarchies") or []) + list(state.get("fk_candidates") or [])
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        st, sc = edge.get("source_table"), edge.get("source_column")
        if st and sc:
            out.setdefault(str(st), set()).add(str(sc))
    return out


def build_tables_metadata(state: dict) -> list[dict]:
    """Per-table metadata: row_count, source_file, primary_keys, foreign_keys,
    column_count + per-column {datatype, nullable, unique, sample_values}."""
    fk_by_table = _fk_by_table(state)
    row_counts = state.get("row_count_post_dedup_by_table") or {}
    source_file = state.get("source_filename") or _fallback_filename(state)
    tables = list((state.get("full_tables") or state.get("parsed_tables") or {}).keys())

    result: list[dict] = []
    for table in tables:
        records = _records_for(state, table)
        col_names: list[str] = []
        for r in records[:50]:
            for k in r.keys():
                if k not in col_names:
                    col_names.append(k)

        columns: list[dict] = []
        for col in col_names:
            vals = [r.get(col) for r in records[:_MAX_ROWS_SCAN]]
            nonnull = [v for v in vals if v is not None and not (isinstance(v, str) and not v.strip())]
            distinct = {str(v) for v in nonnull}
            sample: list[str] = []
            for v in nonnull:
                sv = str(v)
                if sv not in sample:
                    sample.append(sv)
                if len(sample) >= _MAX_SAMPLE:
                    break
            columns.append({
                "column_name": col,
                "datatype": _infer_datatype(nonnull),
                "nullable": len(nonnull) < len(vals),
                "unique": len(nonnull) > 0 and len(distinct) == len(nonnull),
                "sample_values": sample,
            })

        # Detect PK (7.3 AC4/AC5/AC6):
        #  AC4 single column — unique + non-null, preferring an "*id*" name.
        #  AC5 composite    — if no single column qualifies, the smallest 2+ column
        #                     combination that is 100% unique + 0% null.
        #  AC6 surrogate    — if neither exists, flag a generated surrogate key so
        #                     100% of tables carry a PK before proceeding.
        single_pk = [c["column_name"] for c in columns if c["unique"] and not c["nullable"]]
        single_pk_sorted = sorted(single_pk, key=lambda n: (0 if "id" in n.lower() else 1, len(n)))
        composite_pk: list[str] = []
        surrogate_key = False
        if single_pk_sorted:
            primary_keys = single_pk_sorted[:1]
        else:
            composite_pk = _detect_composite_pk(records, col_names)
            if composite_pk:
                primary_keys = composite_pk
            else:
                primary_keys = [SURROGATE_PK_NAME]
                surrogate_key = True
        row_count = row_counts.get(table)
        if row_count is None:
            row_count = len(records)
        result.append({
            "table_name": table,
            "row_count": row_count,
            "source_file": source_file,
            "primary_keys": primary_keys,
            "composite_primary_key": bool(composite_pk),
            "surrogate_key": surrogate_key,
            "foreign_keys": sorted(fk_by_table.get(table, set())),
            "column_count": len(col_names),
            "columns": columns,
        })
    return result


def build_document_inventory(state: dict, tables_metadata: list[dict] | None = None) -> dict:
    """Uploaded file(s) → extracted tables inventory (Requirement #10)."""
    if tables_metadata is None:
        tables_metadata = build_tables_metadata(state)
    fmt = (str(state.get("detected_file_format") or "")).strip()
    file_name = state.get("source_filename") or _fallback_filename(state)
    is_excel = fmt in ("xlsx", "xls", "xlsm")
    tables = [
        {
            "table_name": t["table_name"],
            "row_count": t["row_count"],
            "column_count": t["column_count"],
        }
        for t in tables_metadata
    ]
    return {
        "files": [
            {
                "file_name": file_name,
                "file_type": fmt or "unknown",
                "sheet_count": len(tables) if is_excel else 1,
                "table_count": len(tables),
                "tables": tables,
            }
        ]
    }
