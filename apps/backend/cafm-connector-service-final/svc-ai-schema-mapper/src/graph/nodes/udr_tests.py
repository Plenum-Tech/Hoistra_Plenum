"""UDR Test 1 & Test 2 gate logic (PRD Feature 7.9 / 7.10).

Test 1 — every unstructured vector-chunk association must reference a PRIMARY KEY
         (never a foreign key or shared attribute). Fail rate > 1% BLOCKS the UDR.
Test 2 — every cross-table column value overlap >= 30% must be explained by a
         defined foreign-key relationship. Unexplained rate >= 1% BLOCKS the UDR.

The ``run_test_1`` / ``run_test_2`` functions are PURE (no DB, no state) and
unit-tested offline. ``test_1_node`` / ``test_2_node`` adapt a MigrationState to
them; they are additive and non-fatal (skip cleanly when the required data is
absent) and are NOT yet wired into migration_graph.py — see module docstring of
that file / the consolidated plan for the wiring step.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

from cafm_shared.logging import get_logger

logger = get_logger(__name__)


def tests_enabled() -> bool:
    """Feature flag for wiring the Test 1/Test 2 gate nodes into the graph.
    Off by default → the migration graph is unchanged."""
    return (os.environ.get("UDR_TESTS_ENABLED", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


TEST1_BLOCK_THRESHOLD = 0.01   # >1% chunk failures blocks
TEST2_OVERLAP_THRESHOLD = 0.30  # >=30% value overlap is a similarity hit
TEST2_BLOCK_THRESHOLD = 0.01   # >=1% unexplained pairs blocks


# ── Test 1 — chunk anchor must be a primary key ──────────────────────────────
def run_test_1(
    chunks: list[dict],
    pk_values_by_type: Optional[dict[str, set]] = None,
    pk_values: Optional[set] = None,
    block_threshold: float = TEST1_BLOCK_THRESHOLD,
) -> dict:
    """Verify each chunk's anchor value exists as a PK in a structured table.

    chunks: [{chunk_id, source_entity_type, source_entity_id}]
    pk_values_by_type: {entity_type: {pk values}} (type-scoped check, preferred)
    pk_values: flat {pk values} fallback when type is unknown.
    """
    pk_values = pk_values or set()
    total = len(chunks)
    failing: list[dict] = []
    for c in chunks:
        val = c.get("source_entity_id")
        etype = c.get("source_entity_type")
        ok = False
        if val is not None:
            if pk_values_by_type and etype in pk_values_by_type:
                ok = val in pk_values_by_type[etype]
            else:
                ok = val in pk_values or any(val in s for s in (pk_values_by_type or {}).values())
        if not ok:
            failing.append({"chunk_id": c.get("chunk_id"), "value": val, "entity_type": etype})
    failed = len(failing)
    passed = total - failed
    fail_rate = (failed / total) if total else 0.0
    return {
        "test_name": "test_1",
        "total_checked": total,
        "passed": passed,
        "failed": failed,
        "fail_rate": round(fail_rate, 4),
        "blocked": fail_rate > block_threshold,
        "failing": failing[:200],
    }


# ── Test 2 — column similarity must be explained by a foreign key ────────────
def _fk_key(t1: str, c1: str, t2: str, c2: str) -> frozenset:
    return frozenset({(t1, c1), (t2, c2)})


def _normalise_fks(defined_fks: Iterable) -> set:
    out: set = set()
    for fk in defined_fks or []:
        if isinstance(fk, (set, frozenset)):
            out.add(frozenset(fk))
        elif isinstance(fk, dict):
            ft, fc = fk.get("from_table"), fk.get("from_column")
            tt, tc = fk.get("to_table"), fk.get("to_column")
            if ft and tt:
                out.add(_fk_key(ft, fc, tt, tc))
        elif isinstance(fk, (list, tuple)) and len(fk) == 4:
            out.add(_fk_key(*fk))
    return out


def run_test_2(
    column_values: dict[tuple, set],
    defined_fks: Iterable = (),
    coincidental: Iterable = (),
    overlap_threshold: float = TEST2_OVERLAP_THRESHOLD,
    block_threshold: float = TEST2_BLOCK_THRESHOLD,
) -> dict:
    """Compare every ordered column pair; >=overlap_threshold value overlap must
    be explained by a defined FK (or documented coincidental).

    column_values: {(table, column): {distinct values}}
    defined_fks / coincidental: iterables of (t1,c1,t2,c2) | {(t,c),(t,c)} | dict.
    """
    fk_set = _normalise_fks(defined_fks)
    coincidental_set = _normalise_fks(coincidental)
    cols = list(column_values.keys())

    total_pairs = 0
    hits = 0
    explained = 0
    flagged_detail: list[dict] = []
    for a in cols:
        va = column_values.get(a) or set()
        if not va:
            continue
        for b in cols:
            if a == b:
                continue
            vb = column_values.get(b) or set()
            if not vb:
                continue
            total_pairs += 1
            overlap = len(va & vb) / len(va)
            if overlap < overlap_threshold:
                continue
            hits += 1
            key = _fk_key(a[0], a[1], b[0], b[1])
            if key in fk_set or key in coincidental_set:
                explained += 1
            else:
                flagged_detail.append({
                    "column_a": f"{a[0]}.{a[1]}", "column_b": f"{b[0]}.{b[1]}",
                    "overlap": round(overlap, 3),
                })
    flagged = len(flagged_detail)
    fail_rate = (flagged / total_pairs) if total_pairs else 0.0
    return {
        "test_name": "test_2",
        "total_pairs": total_pairs,
        "hits": hits,
        "explained": explained,
        "flagged": flagged,
        "fail_rate": round(fail_rate, 4),
        "blocked": fail_rate >= block_threshold,
        "flagged_detail": flagged_detail[:200],
    }


# ── Node wrappers (additive, non-fatal) ──────────────────────────────────────
def _column_values_from_cleaned(cleaned_tables: dict, cap: int = 5000) -> dict[tuple, set]:
    out: dict[tuple, set] = {}
    for table, records in (cleaned_tables or {}).items():
        if not isinstance(records, list) or not records:
            continue
        cols = records[0].keys() if isinstance(records[0], dict) else []
        for col in cols:
            vals: set = set()
            for r in records[:cap]:
                v = r.get(col) if isinstance(r, dict) else None
                if v not in (None, ""):
                    vals.add(str(v))
            if vals:
                out[(table, col)] = vals
    return out


def _fks_from_state(state: dict) -> list[dict]:
    fks: list[dict] = []
    for h in (state.get("confirmed_hierarchies") or []):
        if isinstance(h, dict) and h.get("source_table") and h.get("target_table"):
            fks.append({
                "from_table": h.get("source_table"), "from_column": h.get("source_column"),
                "to_table": h.get("target_table"), "to_column": h.get("target_column"),
            })
    return fks


def test_1_node(state: MigrationState) -> MigrationState:  # type: ignore[name-defined]
    """Test 1 over any vector chunks present in state. Skips cleanly if absent
    (the structured migration pipeline has no Layer-2 chunks; this becomes
    active once chunk data is threaded in from doc-rag/Layer 2)."""
    try:
        chunks = state.get("document_chunks") or state.get("vector_chunks") or []
        if not chunks:
            state.setdefault("udr_test_results", {})["test_1"] = {
                "test_name": "test_1", "skipped": True, "reason": "no chunks in state",
            }
            return state
        pk_by_type = state.get("pk_values_by_type") or {}
        flat_pks = state.get("pk_values") or set()
        result = run_test_1(chunks, pk_by_type or None, flat_pks or None)
        state.setdefault("udr_test_results", {})["test_1"] = result
        if result["blocked"]:
            logger.warning(f"[Test 1] BLOCKED — fail_rate {result['fail_rate']:.2%}")
        return state
    except Exception as exc:  # pragma: no cover - never fatal
        logger.warning(f"[Test 1] non-fatal error: {exc}")
        return state


def test_2_node(state: MigrationState) -> MigrationState:  # type: ignore[name-defined]
    """Test 2 over the cleaned structured tables + confirmed FKs."""
    try:
        col_values = _column_values_from_cleaned(state.get("cleaned_tables") or {})
        if not col_values:
            state.setdefault("udr_test_results", {})["test_2"] = {
                "test_name": "test_2", "skipped": True, "reason": "no cleaned tables in state",
            }
            return state
        fks = _fks_from_state(state)
        coincidental = state.get("coincidental_overlaps") or ()
        result = run_test_2(col_values, fks, coincidental)
        state.setdefault("udr_test_results", {})["test_2"] = result
        if result["blocked"]:
            logger.warning(f"[Test 2] BLOCKED — unexplained {result['fail_rate']:.2%}")
        return state
    except Exception as exc:  # pragma: no cover - never fatal
        logger.warning(f"[Test 2] non-fatal error: {exc}")
        return state


# MigrationState import kept at the bottom to avoid hard coupling for unit tests
try:  # pragma: no cover
    from ..state import MigrationState  # noqa: E402,F811
except Exception:  # pragma: no cover
    MigrationState = dict  # type: ignore
