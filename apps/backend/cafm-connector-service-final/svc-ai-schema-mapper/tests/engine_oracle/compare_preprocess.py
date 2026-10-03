"""Compare Node 5's Python cleaning with `hoist-engine preprocess`, case by case.

    python -m tests.engine_oracle.compare_preprocess

The Python side is preprocess_node.preprocess_tables; the engine side is what preprocess_node's
Go branch runs (steps.preprocess_on_engine: the engine, then Python's _coerce_dates on any column
the engine leaves to it), read back through the Arrow bridge.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


def python_preprocess(tables: dict, mapping: dict, skips: dict):
    from src.graph.nodes.preprocess_node import preprocess_tables

    return preprocess_tables(tables, mapping, skips)


def engine_preprocess(tables: dict, mapping: dict, skips: dict, workdir: Path) -> dict:
    from src.engine import store
    from src.engine.client import EngineError
    from src.engine.steps import preprocess_on_engine

    full, cleaned, renamed = workdir / "full", workdir / "cleaned", workdir / "renamed_full"
    store.write_tables(full, tables)
    try:
        res = asyncio.run(preprocess_on_engine(full, cleaned, renamed, mapping, skips, workdir=workdir))
    except EngineError as e:
        return {"ok": False, "error": e.message, "code": e.code}
    return {"ok": True, "cleaned_tables": store.read_tables(cleaned), "renamed_full": store.read_tables(renamed),
            "row_count_post_dedup_by_table": res.row_count_post_dedup_by_table,
            "dedup_drop_count_by_table": res.dedup_drop_count_by_table, "warnings": res.warnings,
            "total_original_rows": res.total_original_rows, "total_cleaned_rows": res.total_cleaned_rows}


def _rows_diff(where: str, py: dict, go: dict) -> list[str]:
    if list(py) != list(go):
        return [f"{where}: tables {list(py)} vs {list(go)}"]
    out = []
    for name in py:
        a, b = py[name], go[name]
        if len(a) != len(b):
            out.append(f"{where}[{name!r}]: {len(a)} rows vs {len(b)}")
            continue
        for i, (ra, rb) in enumerate(zip(a, b)):
            if list(ra.items()) != list(rb.items()) or any(type(ra[k]) is not type(rb.get(k)) for k in ra):
                cols = [c for c in dict.fromkeys(list(ra) + list(rb))
                        if ra.get(c, "<absent>") != rb.get(c, "<absent>") or type(ra.get(c)) is not type(rb.get(c))]
                out.append(f"{where}[{name!r}] row {i}: " + ", ".join(
                    f"{c}={ra.get(c, '<absent>')!r} vs {rb.get(c, '<absent>')!r}" for c in cols[:4]))
                break
    return out


def as_the_next_node_reads(tables: dict) -> dict:
    """Rows as the next node receives them on a Python run: preprocess's tables are offloaded as
    JSON with default=str (bulk_tables.offload_tables), so a value pandas left as a datetime
    arrives as its str() ("2026-03-28 10:00:00+00:00"); every other value is unchanged."""
    import json

    return json.loads(json.dumps(tables, default=str))


def differences(py, go: dict) -> list[str]:
    if not go["ok"]:
        return [f"engine failed: {go['code']}: {go['error']}"]
    out = []
    for key in ("row_count_post_dedup_by_table", "dedup_drop_count_by_table", "warnings", "total_original_rows",
                "total_cleaned_rows"):
        if getattr(py, key) != go[key]:
            out.append(f"{key}: {getattr(py, key)!r} vs {go[key]!r}")
    out += _rows_diff("cleaned_tables", as_the_next_node_reads(py.cleaned_tables), go["cleaned_tables"])
    # the bridge writes no file for a table with no rows (manifest columns [] — read back as [])
    out += _rows_diff("renamed_full", as_the_next_node_reads({k: v or [] for k, v in py.renamed_full.items()}),
                      go["renamed_full"])
    return out


def compare(tables: dict, mapping: dict, skips: dict) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        go = engine_preprocess(tables, mapping, skips, Path(tmp))
    return differences(python_preprocess(tables, mapping, skips), go)


def real_case(path: Path) -> tuple[str, dict, dict, dict]:
    """A real upload as preprocess receives it (ingest's parse and duplicate merge), with a rename
    for every other column (its snake_case name, the shape tier-1 targets take) and the last
    column of each table skipped — the mapping steps need the model, which the oracle has not."""
    from src.graph.nodes.column_merge import merge_duplicate_columns
    from src.graph.nodes.ingest_node import parse_source_tables
    from src.graph.nodes.preprocess_node import _snake

    full = parse_source_tables(Path(path).read_bytes()).full_tables
    merged, dup = merge_duplicate_columns(full)
    tables = merged if dup.get("total_merges") else full
    mapping, skips = {}, {}
    for name, rows in tables.items():
        cols = list(rows[0]) if rows else []
        mapping[name] = {c: _snake(c) for c in cols[::2]}
        if len(cols) > 2:
            skips[name] = {cols[-1]}
    return Path(path).name, tables, mapping, skips


def main(argv: list[str]) -> int:
    from .preprocess_corpus import cases

    bad = 0
    for name, tables, mapping, skips in (cases() if not argv else [real_case(Path(a)) for a in argv]):
        diffs = compare(tables, mapping, skips)
        print(f"{'DIFF' if diffs else 'same'}  {name}")
        for d in diffs:
            print(f"    {d}")
        bad += bool(diffs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
