import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from src.db import get_async_engine


async def main() -> None:
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE plenum_cafm.migration_jobs "
                "ADD COLUMN IF NOT EXISTS field_mapping_draft JSONB"
            )
        )
    print("field_mapping_draft column ensured")


if __name__ == "__main__":
    asyncio.run(main())
