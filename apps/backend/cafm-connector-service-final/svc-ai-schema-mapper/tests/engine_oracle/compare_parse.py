"""Compare ingest's Python parse with `hoist-engine parse`, file by file.

    python -m tests.engine_oracle.compare_parse <files…>

prints, for every file, each place the two disagree (nothing for a file they agree on) and exits
non-zero when any file differs. The Python side is ingest_node.parse_source_tables followed by the
node's own NaN scan and duplicate-column merge.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path



def python_parse(path: Path) -> dict:
    from src.graph.nodes.column_merge import apply_merge_decisions, merge_duplicate_columns
    from src.graph.nodes.ingest_node import SourceParseError, parse_source_tables
    from src.graph.nodes.nan_scan import scan_nan_values

    try:
        ps = parse_source_tables(Path(path).read_bytes())
    except SourceParseError as e:
        return {"ok": False, "error": f"Could not parse file: {e}"}
    full, parsed = ps.full_tables, ps.parsed_tables
    raw_full = {name: [dict(r) for r in rows] for name, rows in full.items()}
    nan_report = scan_nan_values(full)
    merged, dup = merge_duplicate_columns(full)
    if dup.get("total_merges"):
        full, parsed = merged, apply_merge_decisions(parsed, dup)
    return {"ok": True, "detected_file_format": ps.detected_file_format, "source_delimiter": ps.source_delimiter,
            "parsed_tables": parsed, "full_tables": full, "raw_full_tables": raw_full, "nan_report": nan_report,
            "duplicate_column_report": dup, "set_aside_sheets": ps.set_aside_sheets}


def read_all_columns(dirpath: Path) -> dict:
    """The engine's tables with every column it parsed, merged-away ones included (the manifest
    lists only the kept ones, which is all the pipeline reads)."""
    import json

    import pyarrow as pa

    meta = json.loads((Path(dirpath) / "manifest.json").read_text(encoding="utf-8"))
    out = {}
    for entry in meta.get("tables") or []:
        if not entry.get("file"):
            out[entry["name"]] = []
            continue
        with pa.OSFile(str(Path(dirpath) / entry["file"]), "rb") as src:
            out[entry["name"]] = pa.ipc.open_file(src).read_all().to_pylist()
    return out


def engine_parse(path: Path, workdir: Path) -> dict:
    from src.engine import store
    from src.engine.client import EngineError, run_engine
    from src.engine.steps import parse_source
    from src.graph.nodes.column_merge import KNOWN_DESTINATION_COLUMNS, apply_merge_decisions
    from src.graph.nodes.ingest_node import POST_WRITE_SHEETS

    out = workdir / "full"
    source, encoding = parse_source(Path(path).read_bytes(), workdir / "source")
    job = {"source": str(source), "out_dir": str(out), "encoding": encoding,
           "known_destination_columns": sorted(KNOWN_DESTINATION_COLUMNS),
           "post_write_sheets": sorted(POST_WRITE_SHEETS), "preview_rows": 10, "nan_sample_rows": 20}
    try:
        res = asyncio.run(run_engine("parse", job, workdir=workdir))
    except EngineError as e:
        return {"ok": False, "error": e.message, "code": e.code}
    preview = {t["name"]: t["preview"] for t in res["tables"]}
    dup = res["duplicate_column_report"]
    if dup.get("total_merges"):  # as ingest_node does with the engine's pre-merge preview
        preview = apply_merge_decisions(preview, dup)
    return {"ok": True, "detected_file_format": res["detected_file_format"],
            "source_delimiter": res["source_delimiter"],
            "parsed_tables": preview,
            "full_tables": store.read_tables(out), "raw_full_tables": read_all_columns(out),
            "nan_report": res["nan_report"],
            "duplicate_column_report": res["duplicate_column_report"],
            "set_aside_sheets": res.get("set_aside_sheets", [])}


def _records_diff(where: str, py: dict, go: dict) -> list[str]:
    out = []
    if list(py) != list(go):
        return [f"{where}: tables {list(py)} vs {list(go)}"]
    for name in py:
        a, b = py[name], go[name]
        if len(a) != len(b):
            out.append(f"{where}[{name!r}]: {len(a)} rows vs {len(b)}")
            continue
        for i, (ra, rb) in enumerate(zip(a, b)):
            if list(ra.items()) != list(rb.items()):
                cols = [c for c in dict.fromkeys(list(ra) + list(rb)) if ra.get(c, "<absent>") != rb.get(c, "<absent>")]
                if list(ra) != list(rb):
                    out.append(f"{where}[{name!r}] row {i}: columns {list(ra)} vs {list(rb)}")
                else:
                    out.append(f"{where}[{name!r}] row {i}: " + ", ".join(
                        f"{c}={ra.get(c)!r} vs {rb.get(c)!r}" for c in cols[:4]))
                break
    return out


def differences(py: dict, go: dict, *, csv: bool) -> list[str]:
    if not py["ok"] or not go["ok"]:
        if py["ok"] != go["ok"]:
            return [f"python {'parsed' if py['ok'] else 'failed: ' + py['error']}; "
                    f"engine {'parsed' if go['ok'] else 'failed: ' + go['error']}"]
        return []
    out = []
    for key in ("detected_file_format", "set_aside_sheets", "nan_report", "duplicate_column_report"):
        if py[key] != go[key]:
            out.append(f"{key}: {py[key]!r} vs {go[key]!r}")
    if csv and py["source_delimiter"] != go["source_delimiter"]:
        out.append(f"source_delimiter: {py['source_delimiter']!r} vs {go['source_delimiter']!r}")
    out += _records_diff("parsed_tables", py["parsed_tables"], go["parsed_tables"])
    out += _records_diff("full_tables", py["full_tables"], go["full_tables"])
    out += _records_diff("before the duplicate merge", py["raw_full_tables"], go["raw_full_tables"])
    return out


def compare(path: Path) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        go = engine_parse(path, Path(tmp))
    py = python_parse(path)
    return differences(py, go, csv=py.get("detected_file_format") == "csv")


def main(argv: list[str]) -> int:
    bad = 0
    for arg in argv:
        diffs = compare(Path(arg))
        print(f"{'DIFF' if diffs else 'same'}  {arg}")
        for d in diffs:
            print(f"    {d}")
        bad += bool(diffs)
    print(f"{bad} of {len(argv)} file(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
