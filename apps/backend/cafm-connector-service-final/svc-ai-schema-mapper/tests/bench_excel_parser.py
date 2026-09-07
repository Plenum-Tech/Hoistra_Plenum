"""Dev-only benchmark: openpyxl vs python-calamine Excel reading.

Run: python tests/bench_excel_parser.py [rows]   (default 50000)

Not a unit test — a throwaway measurement tool. Generates a workbook with mixed types (str / int /
float / bool / date) and times the read the migration pipeline actually does (ExcelFile open + full
parse, dtype=str), reporting load time, rows, rows/sec, and the calamine speedup. Memory delta is
reported if psutil is available.
"""
import datetime
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd  # noqa: E402

try:
    import psutil  # type: ignore

    def _rss_mb() -> float:
        return psutil.Process().memory_info().rss / (1024 * 1024)
except Exception:
    def _rss_mb() -> float:
        return float("nan")


def build(rows: int) -> bytes:
    buf = io.BytesIO()
    df = pd.DataFrame({
        "code": [f"V{i:06d}" for i in range(rows)],
        "qty": range(rows),
        "rate": [12.5 + (i % 100) / 7 for i in range(rows)],
        "active": [i % 2 == 0 for i in range(rows)],
        "joined": [datetime.datetime(2026, 1, 1) + datetime.timedelta(days=i % 900) for i in range(rows)],
        "note": ["lorem ipsum dolor sit amet"] * rows,
    })
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="data", index=False)
    return buf.getvalue()


def bench(engine: str, data: bytes) -> tuple[float, int]:
    m0 = _rss_mb()
    t = time.perf_counter()
    xls = pd.ExcelFile(io.BytesIO(data), engine=engine)
    df = xls.parse("data", header=0, dtype=str)
    dt = time.perf_counter() - t
    rows = len(df)
    dm = _rss_mb() - m0
    rps = f"{rows / dt:,.0f}" if dt > 0 else "—"
    print(f"  {engine:9s}: {dt * 1000:7.0f} ms  |  {rps:>12s} rows/s  |  rss delta {dm:6.1f} MB")
    return dt, rows


def main() -> None:
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 50_000
    print(f"Generating a {rows:,}-row workbook (str/int/float/bool/date)…")
    data = build(rows)
    print(f"Workbook size: {len(data) / 1024:,.0f} KB\n")
    print("ExcelFile open + full parse (dtype=str) — the pipeline's read:")
    t_op, _ = bench("openpyxl", data)
    t_ca, _ = bench("calamine", data)
    if t_ca > 0:
        print(f"\n  -> python-calamine is {t_op / t_ca:.1f}x faster than openpyxl on {rows:,} rows")


if __name__ == "__main__":
    main()
