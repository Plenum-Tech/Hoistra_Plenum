"""check_requirements reads plenum_cafm with the columns and types production has (25 Nov 2026).

20 Nov 2026, check_requirements(asset_code="AHU-3") failed on its first read:

    (sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) operator does not exist: uuid = text
    [SQL: SELECT mp.sm_code, ... FROM plenum_cafm.maintenance_plans mp
          JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text ...]

assets.id, maintenance_plans.asset_id and work_orders.asset_id are all uuid in production; casting one
side to text is what broke the comparison. Behind it, the inspections read selected a bare `id` from
inspections joined to assets - both have one - which Postgres refuses as ambiguous.

No database is touched: the session factory is replaced by one that records every statement and
refuses, as Postgres would, a comparison between different types, a column the table does not have, or
a bare column two joined tables share. The types are production's (plenum_cafm, exported 25 Nov 2026).
"""
from __future__ import annotations

import asyncio
import re

import pytest

from src import database
from src.agents import compliance_agent as ca

_VC, _TEXT, _UUID = "character varying", "text", "uuid"

#: Production columns the tool touches, plus every name the joined tables share (for ambiguity).
COLUMNS: dict[str, dict[str, str]] = {
    "assets": {
        "id": _UUID, "asset_code": _VC, "asset_name": _VC, "building_id": _UUID, "site_id": _UUID,
        "status": _VC, "created_at": "timestamp without time zone", "organization_id": _TEXT,
        "building_code": _VC, "charge_department_id": _UUID, "project_id": _UUID,
        "updated_at": "timestamp without time zone", "vendor_id": _UUID, "vendor_name": _VC,
        "location_id": _UUID, "notes": _TEXT, "system_id": _UUID, "category_id": _UUID,
    },
    "asset_categories": {
        "id": _UUID, "organization_id": _UUID, "name": _VC, "description": _TEXT, "parent_id": _UUID,
    },
    "maintenance_plans": {
        "id": _UUID, "asset_id": _UUID, "asset_code": _TEXT, "building_id": _UUID, "sm_code": _VC,
        "frequency_type": _VC, "frequency_value": "integer", "next_due_date": "date", "status": _VC,
        "created_at": "timestamp without time zone", "organization_id": _TEXT, "building_code": _VC,
        "charge_department_id": _UUID, "project_id": _UUID, "updated_at": "timestamp without time zone",
        "vendor_id": _UUID, "vendor_name": _VC,
    },
    "work_orders": {
        "id": _UUID, "asset_id": _UUID, "asset_code": _TEXT, "asset_name": _TEXT, "building_id": _UUID,
        "site_id": _UUID, "wo_code": _VC, "priority": _VC, "status": _VC,
        "created_at": "timestamp without time zone", "organization_id": _TEXT, "building_code": _VC,
        "charge_department_id": _UUID, "project_id": _UUID, "updated_at": "timestamp without time zone",
        "vendor_id": _UUID, "vendor_name": _VC, "location_id": _UUID, "notes": _TEXT, "system_id": _UUID,
    },
    "inspections": {
        "id": _UUID, "asset_id": _UUID, "asset_code": _VC, "inspection_date": "date",
        "finding_type": _VC, "observations": _TEXT, "risk_level": _VC, "corrective_action": "boolean",
        "created_at": "timestamp with time zone", "organization_id": _TEXT,
    },
}
#: Types Postgres compares without an operator error.
_FAMILY = {_VC: "text", _TEXT: "text", "varchar": "text", _UUID: "uuid"}


def _family(t: str) -> str:
    return _FAMILY.get(t, t)


def problems(sql: str) -> list[str]:
    """What Postgres would refuse in `sql`, for the tables above."""
    out: list[str] = []
    aliases = {a: t for t, a in re.findall(r"plenum_cafm\.(\w+)\s+(?:AS\s+)?(\w+)", sql)
               if t in COLUMNS}
    for alias, col in re.findall(r"\b(\w+)\.(\w+)\b", sql):
        if alias in aliases and col not in COLUMNS[aliases[alias]]:
            out.append(f"column {alias}.{col} does not exist")
    ref = r"(\w+)\.(\w+)(?:::(\w+))?"
    for la, lc, lcast, ra, rc, rcast in re.findall(ref + r"\s*=\s*" + ref, sql):
        if la in aliases and ra in aliases:
            lt = lcast or COLUMNS[aliases[la]].get(lc, "?")
            rt = rcast or COLUMNS[aliases[ra]].get(rc, "?")
            if _family(lt) != _family(rt):
                out.append(f"operator does not exist: {lt} = {rt} ({la}.{lc} = {ra}.{rc})")
    if len(aliases) > 1:
        select = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.S | re.I)
        order = re.search(r"ORDER BY\s+([\w.]+)", sql, re.I)
        bare = [c.strip() for c in (select.group(1).split(",") if select else [])]
        bare += [order.group(1)] if order else []
        for name in bare:
            if re.fullmatch(r"\w+", name):
                owners = [t for t in aliases.values() if name in COLUMNS[t]]
                if len(owners) != 1:
                    out.append(f'column reference "{name}" is ambiguous or unknown ({owners})')
    return out


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s = str(sql)
        self.seen.append(s)
        if "/* resolve asset:" in s:
            return _Rows([{"asset_code": "AHU-3", "asset_name": "Air Handling Unit 3"}])
        found = problems(s)
        if found:
            raise RuntimeError("ProgrammingError: " + "; ".join(found))
        if "mp.sm_code" in s:
            return _Rows([{"sm_code": "SM-AHU-3", "frequency_type": "months", "frequency_value": 1,
                           "next_due_date": "2026-09-01", "is_overdue": True}])
        return _Rows([])


@pytest.fixture
def seen(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(seen))
    return seen


def test_the_checker_refuses_what_production_refused():
    """The 20 Nov statement, as the tool sent it, is refused for the reason Postgres gave."""
    sent = ("SELECT mp.sm_code FROM plenum_cafm.maintenance_plans mp "
            "JOIN plenum_cafm.assets a ON a.id = mp.asset_id::text WHERE a.asset_code = :asset_code")
    assert problems(sent) == ["operator does not exist: uuid = text (a.id = mp.asset_id)"]


def test_ahu_3_is_checked_without_a_type_error(seen):
    out = asyncio.run(ca.check_requirements.ainvoke({"asset_code": "AHU-3", "regulation": None}))
    assert "error" not in out, out.get("error")
    assert out["asset_code"] == "AHU-3"
    assert out["compliance_status"] == "non_compliant"          # its overdue PM was read
    reads = [s for s in seen if "/* resolve asset:" not in s]
    assert len(reads) == 3, "maintenance plans, work orders and inspections were all read"


def test_every_statement_names_production_columns_with_their_types(seen):
    asyncio.run(ca.check_requirements.ainvoke({"asset_code": "AHU-3"}))
    assert seen
    assert {s: problems(s) for s in seen if problems(s)} == {}


@pytest.mark.parametrize("scope", ["all_assets", "Air Handler"])
def test_the_portfolio_report_reads_the_same_way(seen, scope):
    """generate_compliance_report, beside it, joined the same way (and named asset_categories'
    `name` as `category_name`, which production does not have)."""
    out = asyncio.run(ca.generate_compliance_report.ainvoke({"scope": scope}))
    assert "error" not in out, out.get("error")
    assert len(seen) == 3
    assert {s: problems(s) for s in seen if problems(s)} == {}
