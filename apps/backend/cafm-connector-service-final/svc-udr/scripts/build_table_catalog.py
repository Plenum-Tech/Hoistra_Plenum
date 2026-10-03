"""Build (or rebuild) the table catalogue against the configured database.

    python scripts/build_table_catalog.py                 # every table, model-written purposes
    python scripts/build_table_catalog.py --no-model      # heuristic purposes only (no Anthropic key needed)
    python scripts/build_table_catalog.py assets vendors  # just these tables

Reads DB_URL / DATABASE_URL, ANTHROPIC_API_KEY and OPENAI_API_KEY from the environment (or the
service's .env). Creates the catalogue table if it is missing. Writes one row per table;
prints progress and a summary. Safe to re-run: rows are upserted.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db import ensure_udr_tables, get_session  # noqa: E402
from src.services import catalog  # noqa: E402


async def main(argv: list[str]) -> int:
    with_model = "--no-model" not in argv
    tables = [a for a in argv if not a.startswith("--")] or None
    await ensure_udr_tables()
    t0 = time.perf_counter()

    def progress(i: int, n: int, name: str) -> None:
        if i % 10 == 0 or i == n:
            print(f"  {i}/{n}  {name}  ({time.perf_counter() - t0:.0f}s)", flush=True)

    async for session in get_session():
        out = await catalog.build(session, only=tables, with_model=with_model, progress=progress)
        break
    print(f"\nwritten {out['written']}/{out['tables']} tables; model purposes {out['with_model_purpose']}; "
          f"embedded={out['embedded']}; failures {len(out['failures'])} in {time.perf_counter() - t0:.0f}s")
    for f in out["failures"]:
        print("  FAILED", f)
    return 0 if not out["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
