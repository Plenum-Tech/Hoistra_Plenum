"""Reads that named a column production does not have, so they failed on every call (25 Nov 2026).

Found by checking every SQL statement in svc-deepagents against production's own column list
(information_schema, read-only):

* lookup_user selected `users.department` - users has no such column (it has job_title), so
  "who is this user" always came back as a database error.
* the compliance analyst's open work orders selected `work_orders.work_order_id` - the order's
  code is `wo_code`. The read runs beside the register with return_exceptions=True, so it failed
  silently on every compliance turn since 7 Sep and the analyst never saw an open work order.

No database is touched: the session factory is replaced and every statement is recorded.
"""
from __future__ import annotations

import asyncio
import re
from uuid import UUID

import pytest

from src import database
from src.agents import activity_log
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

ORG = "00000000-0000-0000-0000-000000000001"


class _Result:
    def mappings(self):
        return self

    def all(self):
        return []

    def one_or_none(self):
        return None


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self.seen.append(str(sql))
        return _Result()


@pytest.fixture
def seen(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(seen))
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)
    return seen


def _selected(sql: str) -> str:
    return sql[sql.upper().index("SELECT"):sql.upper().index("FROM")]


def test_lookup_user_reads_the_job_title_not_a_department_column(seen):
    from src.agents.udr_agent import lookup_user

    asyncio.run(lookup_user.ainvoke({"user_id": "00000000-0000-0000-0000-000000000007"}))
    sql = seen[-1]
    assert "department" not in sql
    assert "u.job_title" in _selected(sql)


def test_open_work_orders_read_the_code_from_wo_code(seen, monkeypatch):
    from src.services import principal as pr

    async def table_columns(session, table):
        return frozenset({"organization_id", "status", "building_id"})

    monkeypatch.setattr(pr, "table_columns", table_columns)
    p = Principal(user_id=UUID(int=7), email="a@x", organization_id=UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)

    async def go():
        tp, to = caller_principal.set(p), caller_organization_id.set(None)
        try:
            return await O.__new__(O)._fetch_open_work_orders()
        finally:
            caller_principal.reset(tp)
            caller_organization_id.reset(to)

    asyncio.run(go())
    cols = _selected(seen[-1])
    assert "wo_code AS work_order_id" in cols
    assert not re.search(r"(?<!AS )\bwork_order_id\b", cols)
