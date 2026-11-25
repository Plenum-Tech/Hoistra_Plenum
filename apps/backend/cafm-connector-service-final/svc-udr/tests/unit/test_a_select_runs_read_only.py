"""A caller's SELECT runs in a read-only transaction, so a WITH query can be allowed (25 Nov 2026).

execute_select let through only text that starts with SELECT. The orchestrator's database agent
writes `WITH ... SELECT` for anything with a sub-total, and every one was refused ("Only SELECT
statements are permitted") - the Aire Mill Court vendors question lost its first query to it.
The prefix check was also the only thing standing between the route and a write: a WITH can
carry a DELETE, and a SELECT can call a function that writes. So the transaction is now marked
READ ONLY before the caller's SQL runs, and Postgres refuses any write in it; WITH is accepted.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/d")

from src.services import principal as P  # noqa: E402
from src.services.database_service import DatabaseService, UnsafeQueryError  # noqa: E402

ORG = UUID("11111111-1111-1111-1111-111111111111")
WITH_QUERY = ("WITH v AS (SELECT vendor_id, count(*) AS n FROM plenum_cafm.work_orders GROUP BY 1) "
              "SELECT vendor_id, n FROM v")


class _Rows:
    def fetchmany(self, n):
        return []


class _Session:
    def __init__(self):
        self.sent: list[str] = []

    async def execute(self, stmt, params=None):
        self.sent.append(str(stmt))
        return _Rows()


def _service(monkeypatch):
    who = P.Principal(user_id=uuid4(), email="admin@example.com", organization_id=ORG,
                      role="admin", building_ids=None)
    session = _Session()
    svc = DatabaseService(session, who)

    async def views_exist(self):
        return True

    monkeypatch.setattr(DatabaseService, "_scoped_views_exist", views_exist)
    return svc, session


def test_a_with_query_is_run(monkeypatch):
    svc, session = _service(monkeypatch)
    asyncio.run(svc.execute_select(WITH_QUERY))
    assert any(s.lstrip().upper().startswith("WITH") for s in session.sent)


@pytest.mark.parametrize("sql", ["SELECT 1", WITH_QUERY])
def test_the_transaction_is_read_only_before_the_callers_sql_runs(monkeypatch, sql):
    svc, session = _service(monkeypatch)
    asyncio.run(svc.execute_select(sql))
    ro = next(i for i, s in enumerate(session.sent) if "READ ONLY" in s.upper())
    callers = next(i for i, s in enumerate(session.sent)
                   if s.lstrip().upper().startswith(("SELECT 1", "WITH")))
    assert ro < callers


@pytest.mark.parametrize("sql", ["UPDATE plenum_cafm.vendors SET status = 'x'",
                                 "DELETE FROM plenum_cafm.vendors",
                                 "WITH v AS (SELECT 1) SELECT 1; DELETE FROM plenum_cafm.vendors"])
def test_a_write_or_a_second_statement_is_still_refused(monkeypatch, sql):
    svc, session = _service(monkeypatch)
    with pytest.raises(UnsafeQueryError):
        asyncio.run(svc.execute_select(sql))
    assert session.sent == []
