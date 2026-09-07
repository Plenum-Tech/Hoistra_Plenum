"""Primary-key confirmation — observability + HITL approval for the PK step.

Primary-key identification runs at the pre-semantic review gate (before hierarchy /
FK detection, which reference the PK). This module builds the per-table PK proposal
the gate shows the user — detected PK + every column's uniqueness / null-rate so the
user can CHANGE the PK before the pipeline proceeds — and applies the user's approved
choice back onto the run state.

PURE-ish: the detection maths come from ``udr.primitives``; everything here is plain
dict shuffling so it stays unit-testable.
"""

from __future__ import annotations

from typing import Any

# Candidate stats are for an interactive gate, not the authoritative report (that still
# runs over the full rows in table_resolution). Sampling keeps a 1.8M-row file from
# stalling the gate build while still giving accurate unique/null signals.
_STATS_SAMPLE_ROWS = 5000
_MAX_TABLES = 40


def build_pk_confirmation(
    tables: dict[str, Any],
    *,
    sample_rows: int = _STATS_SAMPLE_ROWS,
) -> dict[str, Any]:
    """Build the per-table PK proposal shown at the confirmation gate.

    Args:
        tables: ``{table_name: [ {col: value, ...}, ... ]}`` (full or sampled rows).

    Returns:
        ``{ table_name: {
              "detected_pk": [col, ...],     # what detection chose
              "kind": "natural"|"composite"|"surrogate",
              "confidence": float|None,
              "surrogate": bool,             # no natural PK — a surrogate key is proposed
              "columns": [                   # every column, so the user can re-pick
                 {"column": str, "uniqueness": float, "null_rate": float, "qualifies": bool},
                 ...
              ],
           }, ... }``
    """
    from ...udr.primitives import (
        is_primary_key_column,
        null_rate,
        primary_key_report,
        uniqueness,
    )

    out: dict[str, Any] = {}
    for table_name, records in list((tables or {}).items())[:_MAX_TABLES]:
        if not isinstance(records, list) or not records:
            continue
        rows = records[:sample_rows] if sample_rows and len(records) > sample_rows else records

        # Ordered unique column list (first-seen order across the sample).
        seen: set = set()
        cols: list[str] = []
        for r in rows:
            if isinstance(r, dict):
                for k in r:
                    if k not in seen:
                        seen.add(k)
                        cols.append(k)

        rep = primary_key_report(rows, cols)
        col_stats = []
        for c in cols:
            col_stats.append({
                "column": c,
                "uniqueness": round(uniqueness(rows, c), 4),
                "null_rate": round(null_rate(rows, c), 4),
                "qualifies": bool(is_primary_key_column(rows, c)),
            })

        out[table_name] = {
            "detected_pk": list(rep.get("columns") or []),
            "kind": rep.get("kind"),
            "confidence": rep.get("confidence"),
            "surrogate": bool(rep.get("surrogate")) or rep.get("kind") == "surrogate",
            "columns": col_stats,
            "sampled": len(records) > len(rows),
            "sample_rows": len(rows),
        }
    return out


def normalize_pk_overrides(
    pk_overrides: Any,
    pk_confirmation: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Coerce the frontend's approved PK selection into ``{table: [pk_col, ...]}``.

    Accepts either ``{table: "col"}`` or ``{table: ["col", ...]}``. Tables the user
    left untouched fall back to the detected PK from ``pk_confirmation`` (so the
    approved set is complete — every table has an explicit, user-signed-off PK).
    An empty / ``"_udr_id"`` / ``"__surrogate__"`` selection is kept as a surrogate
    marker (empty list) so downstream knows a surrogate key was approved.
    """
    confirmation = pk_confirmation or {}
    result: dict[str, list[str]] = {}

    def _coerce(val: Any) -> list[str]:
        if val is None:
            return []
        if isinstance(val, str):
            v = val.strip()
            if not v or v in ("_udr_id", "__surrogate__", "(surrogate)"):
                return []
            return [v]
        if isinstance(val, (list, tuple)):
            return [str(c).strip() for c in val if str(c).strip() and str(c).strip() != "_udr_id"]
        return []

    if isinstance(pk_overrides, dict):
        for table, val in pk_overrides.items():
            result[str(table)] = _coerce(val)

    # Fill in any confirmed table the user didn't explicitly change with its detected PK.
    for table, info in confirmation.items():
        if table not in result:
            detected = list((info or {}).get("detected_pk") or [])
            result[str(table)] = [c for c in detected if c and c != "_udr_id"]

    return result
