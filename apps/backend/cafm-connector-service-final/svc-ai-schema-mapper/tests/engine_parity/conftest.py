"""Fixtures for the throwaway parity database (engine/scripts/parity_db.sh). Never the stack DB."""
import os

import pytest

DSN = os.environ.get("HOIST_PARITY_DSN")          # postgresql://parity:parity@hoist-parity-pg:5432/parity_run
ADMIN_DSN = (DSN or "").replace("/parity_run", "/parity")


@pytest.fixture
def parity_db():
    if not DSN:
        pytest.skip("parity database not available (engine/scripts/parity_db.sh up; HOIST_PARITY=1)")
    # The Python writer under test writes through DB_URL, not HOIST_PARITY_DSN: both must name the
    # throwaway parity server, never a stack's database.
    from urllib.parse import urlparse

    from src.config import get_settings

    hosts = {urlparse(DSN).hostname, urlparse(get_settings().db_url.replace("+asyncpg", "")).hostname}
    if hosts != {"hoist-parity-pg"}:
        pytest.fail(f"parity tests must run against hoist-parity-pg only (got {sorted(h or '' for h in hosts)})")
    return {"dsn_sync": DSN, "dsn_async": DSN.replace("postgresql://", "postgresql+asyncpg://")}


async def reset_parity_run() -> None:
    """A fresh parity_run from the template, with the writer's pool released first."""
    import asyncpg

    from src.db import get_async_engine

    try:
        await get_async_engine().dispose()
    except Exception:  # noqa: BLE001 — no pool yet
        pass
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute("DROP DATABASE IF EXISTS parity_run WITH (FORCE)")
        await conn.execute("CREATE DATABASE parity_run TEMPLATE parity_template")
    finally:
        await conn.close()
