"""The parity database is a fresh copy of the template, and it is the only database these tests touch."""
from .conftest import reset_parity_run


async def test_reset_gives_a_fresh_template_copy(parity_db):
    import asyncpg

    await reset_parity_run()
    conn = await asyncpg.connect(parity_db["dsn_sync"])
    try:
        assert await conn.fetchval("SELECT current_database()") == "parity_run"
        assert await conn.fetchval("SELECT to_regclass('plenum_cafm.meter_readings') IS NOT NULL")
        assert await conn.fetchval("SELECT gen_random_uuid()::text") == "00000000-0000-4000-8000-000000000001"
    finally:
        await conn.close()
