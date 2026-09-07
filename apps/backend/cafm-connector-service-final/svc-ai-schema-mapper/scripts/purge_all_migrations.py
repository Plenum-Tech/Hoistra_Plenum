#!/usr/bin/env python3
"""Delete all rows from plenum_cafm.migration_jobs (CASCADE to mappings/hierarchy)."""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

# Run from svc-ai-schema-mapper with PYTHONPATH=.
from src.db import get_async_engine


async def main() -> int:
    engine = get_async_engine()
    async with engine.begin() as conn:
        before = (await conn.execute(text("SELECT COUNT(*) FROM plenum_cafm.migration_jobs"))).scalar()
        print(f"migration_jobs before: {before}")
        await conn.execute(text("DELETE FROM plenum_cafm.migration_jobs"))
        after = (await conn.execute(text("SELECT COUNT(*) FROM plenum_cafm.migration_jobs"))).scalar()
        print(f"migration_jobs after: {after}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
