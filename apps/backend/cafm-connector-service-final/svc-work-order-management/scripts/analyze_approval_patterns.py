"""Weekly drift analysis for wo_approval_suggestions — run via cron."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from src.db import AsyncSessionLocal


async def find_drift_patterns():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT
                    fingerprint,
                    COUNT(*) AS total,
                    SUM(CASE WHEN accepted = FALSE THEN 1 ELSE 0 END) AS overridden,
                    ROUND(
                      100.0 * SUM(CASE WHEN accepted = FALSE THEN 1 ELSE 0 END)
                      / NULLIF(COUNT(*), 0),
                      1
                    ) AS override_pct
                FROM plenum_cafm.wo_approval_suggestions
                WHERE created_at > NOW() - INTERVAL '30 days'
                GROUP BY fingerprint
                HAVING COUNT(*) >= 5
                   AND SUM(CASE WHEN accepted = FALSE THEN 1 ELSE 0 END) >= 3
                ORDER BY override_pct DESC
            """)
        )
        return [dict(r._mapping) for r in result.fetchall()]


async def main():
    rows = await find_drift_patterns()
    if not rows:
        print("No drift patterns above threshold.")
        return
    print("Fingerprint drift (override rate):")
    for row in rows:
        print(
            f"  {row['fingerprint'][:16]}… "
            f"total={row['total']} overridden={row['overridden']} "
            f"pct={row['override_pct']}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
