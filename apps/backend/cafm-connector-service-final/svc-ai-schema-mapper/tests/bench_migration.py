"""Standalone migration-ingest benchmark — measures the known RAM/CPU ceiling with NO DB / AI / Azure.

Run:  python tests/bench_migration.py [rows] [cols]
      python tests/bench_migration.py 200000 12

The migration's wall-clock is dominated (for large files) by the INGEST node holding the whole file
in memory: parse → build a list-of-dicts (``state["full_tables"]``) → checkpoint it to Postgres. This
benchmark isolates that hot path so we can see where the seconds and megabytes actually go BEFORE
optimizing, and it needs nothing but pandas — so it runs in CI / locally / on the box.

It measures, at the requested size, the exact operations the CSV path in ``graph/nodes/ingest_node.py``
performs today:
  1. read_csv (FULL)          — parse the whole file for the row count + full_tables
  2. read_csv (preview, 10)   — the SECOND, redundant full-buffer parse the CSV path still does
                                 (the Excel path avoids this with ``df_full.head(10)``)
  3. to_dict + _sanitize_records — build the in-RAM list-of-dicts that gets checkpointed
It reports per-phase wall time, rows/s + MB/s throughput, and PEAK Python heap (tracemalloc) — the
footprint that lands in the Postgres checkpoint — then extrapolates to 1 GB vs the 30 s target.
"""

import io
import os
import sys
import time
import tracemalloc

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Measure the REAL sanitizer if the graph node imports cleanly (exact production code); otherwise a
# faithful in-file replica (copied verbatim from ingest_node._sanitize_records) so the benchmark
# still runs without the LangGraph / DB import chain.
try:
    from graph.nodes.ingest_node import _sanitize_records  # type: ignore

    _SANITIZE_SOURCE = "ingest_node (real)"
except Exception:  # pragma: no cover - fallback when heavy deps are absent
    def _sanitize_records(records: list) -> list:  # noqa: D401 - mirrors ingest_node
        def _clean(v):
            try:
                if pd.isna(v):
                    return None
            except (TypeError, ValueError):
                pass
            if isinstance(v, pd.Timestamp):
                return v.isoformat()
            if hasattr(v, "item"):
                try:
                    return v.item()
                except Exception:
                    return str(v)
            return v

        return [{k: _clean(v) for k, v in row.items()} for row in records]

    _SANITIZE_SOURCE = "in-file replica"


def _synthetic_csv(rows: int, cols: int) -> str:
    """A realistic-ish CMMS export: id + code + name + status + several free-text/number columns."""
    import random

    random.seed(1702)  # deterministic — no argless random (keeps the bench reproducible)
    headers = ["asset_code", "asset_name", "category", "location_code", "status", "priority"]
    while len(headers) < cols:
        headers.append(f"attr_{len(headers)}")
    headers = headers[:cols]
    cats = ["Air Handler", "Boiler", "Chiller", "Pump", "Generator"]
    stats = ["Open", "Closed", "In Progress"]
    prios = ["Highest", "High", "Medium", "Low"]
    buf = io.StringIO()
    buf.write(",".join(headers) + "\n")
    for i in range(rows):
        row = [
            f"MOB-{i:07d}",
            f"Asset {i} {random.choice(cats)}",
            random.choice(cats),
            f"LOC-{random.randint(1, 400):04d}",
            random.choice(stats),
            random.choice(prios),
        ][:cols]
        while len(row) < cols:
            row.append(str(random.randint(0, 100000)))
        buf.write(",".join(row) + "\n")
    return buf.getvalue()


def _fmt_mb(b: float) -> str:
    return f"{b / (1024 * 1024):.1f} MB"


def main() -> None:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    cols = int(sys.argv[2]) if len(sys.argv) > 2 else 12

    print(f"# migration ingest benchmark - {rows:,} rows x {cols} cols  (sanitizer: {_SANITIZE_SOURCE})")
    csv_str = _synthetic_csv(rows, cols)
    file_bytes = len(csv_str.encode("utf-8"))
    print(f"  synthetic file size: {_fmt_mb(file_bytes)}")

    tracemalloc.start()

    t0 = time.perf_counter()
    df_full = pd.read_csv(io.StringIO(csv_str), dtype=str, nrows=None)
    t_read_full = time.perf_counter() - t0

    t0 = time.perf_counter()
    _ = pd.read_csv(io.StringIO(csv_str), dtype=str, nrows=10)  # the redundant second parse
    t_read_preview = time.perf_counter() - t0

    t0 = time.perf_counter()
    records = _sanitize_records(df_full.to_dict(orient="records"))
    t_sanitize = time.perf_counter() - t0

    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total = t_read_full + t_read_preview + t_sanitize
    secs = total or 1e-9
    mb = file_bytes / (1024 * 1024)

    def line(name: str, t: float) -> str:
        return f"  {name:<26} {t * 1000:>9.1f} ms  {100 * t / secs:>5.1f}%"

    print("\n  PHASE breakdown (ingest hot path):")
    print(line("read_csv (full)", t_read_full))
    print(line("read_csv (preview x10)", t_read_preview) + "   <- redundant second parse")
    print(line("to_dict + sanitize", t_sanitize))
    print(f"  {'TOTAL':<26} {total * 1000:>9.1f} ms")

    print("\n  THROUGHPUT + MEMORY:")
    print(f"  rows/s               : {rows / secs:>12,.0f}")
    print(f"  MB/s                 : {mb / secs:>12.1f}")
    print(f"  peak Python heap     : {_fmt_mb(peak)}   ({peak / max(rows, 1):.0f} bytes/row)")
    print(f"  records built        : {len(records):,}")

    print("\n  EXTRAPOLATION to 1 GB (target: <= 30 s ingest):")
    mb_per_s = mb / secs
    est_1gb_s = 1024.0 / mb_per_s if mb_per_s else float("inf")
    est_heap_gb = (peak / max(file_bytes, 1)) * 1024 / 1024  # peak-heap GiB per 1 GiB file
    verdict = "OK" if est_1gb_s <= 30 else "OVER TARGET"
    print(f"  est. time for 1 GB   : {est_1gb_s:>7.1f} s   [{verdict}]")
    print(f"  est. peak heap for 1 GB file : {est_heap_gb:.1f}x file  (list-of-dicts amplification)")
    redundant_pct = 100 * t_read_preview / secs
    print(f"  redundant-parse waste: {redundant_pct:.1f}% of ingest (removable - reuse df_full.head(10))")

    print("\nDONE")


if __name__ == "__main__":
    main()
