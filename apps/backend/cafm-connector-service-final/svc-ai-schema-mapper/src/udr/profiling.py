"""Migration profiling harness — measure WHERE the seconds go before optimizing anything.

Design goals:
- **Zero overhead when off.** Disabled unless the env flag ``MIGRATION_PROFILE`` is truthy, so it
  is safe to leave the ``span()`` calls in production nodes permanently. When off, ``span()`` is a
  bare ``yield`` and ``record()`` returns immediately.
- **Per-run phase accumulation.** Timings are keyed by run id (migration id) and phase name, so the
  same node running N sub-operations accumulates into one line with a call count.
- **No new deps.** Uses ``time.perf_counter`` for wall time; RSS via ``psutil`` if present, else the
  Unix ``resource`` module, else ``None`` (the benchmark uses ``tracemalloc`` for cross-platform peak
  heap, which is what actually matters for the in-RAM ``full_tables`` list-of-dicts).

Usage in a node::

    from ...udr import profiling
    with profiling.span(migration_id, "ingest.read_csv_full"):
        df_full = pd.read_csv(...)
    ...
    profiling.emit_report(migration_id, logger)   # logs the phase table when enabled

Enable with ``MIGRATION_PROFILE=1`` and read the ``[perf]`` log lines; or run
``python tests/bench_migration.py`` for an isolated, DB/AI-free throughput benchmark.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections import defaultdict
from typing import Iterator

_ENABLED = os.getenv("MIGRATION_PROFILE", "").strip().lower() in ("1", "true", "yes", "on")

# run_id -> phase -> accumulated milliseconds / call count
_phases: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))


def enabled() -> bool:
    """True when profiling is switched on (MIGRATION_PROFILE truthy)."""
    return _ENABLED


@contextlib.contextmanager
def span(run_id: str, name: str) -> Iterator[None]:
    """Time a block and add it to ``run_id``'s ``name`` phase. No-op (bare yield) when disabled."""
    if not _ENABLED:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        ms = (time.perf_counter() - t0) * 1000.0
        _phases[run_id][name] += ms
        _counts[run_id][name] += 1


def record(run_id: str, name: str, ms: float) -> None:
    """Add a pre-measured duration (ms) to a phase — for timings taken outside a ``with`` block."""
    if not _ENABLED:
        return
    _phases[run_id][name] += ms
    _counts[run_id][name] += 1


def report(run_id: str) -> dict:
    """Structured phase report for a run: total + per-phase ms / % / call count, slowest first."""
    ph = dict(_phases.get(run_id, {}))
    total = sum(ph.values())
    ordered = sorted(ph.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "run_id": run_id,
        "total_ms": round(total, 1),
        "phases": [
            {
                "name": n,
                "ms": round(ms, 1),
                "pct": round(100.0 * ms / total, 1) if total else 0.0,
                "calls": _counts[run_id][n],
            }
            for n, ms in ordered
        ],
    }


def emit_report(run_id: str, logger=None) -> dict | None:
    """Log the phase table for a run (when enabled) and return the report. Safe no-op when off."""
    if not _ENABLED:
        return None
    rep = report(run_id)
    lines = [f"[perf] run={run_id} total={rep['total_ms']}ms"]
    for p in rep["phases"]:
        lines.append(f"[perf]   {p['name']:<32} {p['ms']:>9.1f}ms  {p['pct']:>5.1f}%  x{p['calls']}")
    text = "\n".join(lines)
    if logger is not None:
        try:
            logger.info(text)
        except Exception:
            print(text)
    else:
        print(text)
    return rep


def reset(run_id: str) -> None:
    """Drop a run's accumulated phases (call once the run is fully reported)."""
    _phases.pop(run_id, None)
    _counts.pop(run_id, None)


def rss_mb() -> float | None:
    """Current process RSS in MiB — psutil if available, else Unix ``resource``, else ``None``."""
    try:
        import psutil  # type: ignore

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import resource  # Unix only
        import sys

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is bytes on macOS, KiB on Linux.
        return maxrss / (1024 * 1024) if sys.platform == "darwin" else maxrss / 1024
    except Exception:
        return None
