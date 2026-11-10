"""A blocked vendor comes with its open work orders (Trace Lab R41-4 / R42-1, 10 Nov 2026).

"Which vendors are not eligible to work here?" (Compliance Engine, Plenum Technologies) named the four blocked vendors and
then told the reader to "confirm no live or scheduled work orders are assigned to the four blocked vendors". The records
answer that: Apex Mechanical is blocked and still holds WO-B-301-4551 (In progress), WO-B-301-4527 and WO-B-301-4561
(Draft). A reviewer's note became failure mode deferred_verification_instead_of_performing; the instruction change
stopped the deferral but the rerun still could not name the work orders, because list_blocked_vendors read the vendor
record only and no other tool in the space lists a vendor's open work. Each blocked vendor now carries them.
"""
from __future__ import annotations

import asyncio
import uuid

from src.agents import contract_performance_agent as cp

ORG = "ad88d1ed-4376-447c-8196-e4e5b81d5af6"


def _session(monkeypatch, answers):
    from src import database

    seen: list[tuple[str, dict]] = []

    class _R:
        def __init__(self, rows):
            self.rows = rows

        def mappings(self):
            return self

        def all(self):
            return self.rows

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            s = str(sql)
            seen.append((s, dict(params or {})))
            for marker, rows in answers:
                if marker in s:
                    return _R(rows)
            return _R([])

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _S())
    return seen


BLOCKED = [{"vendor_name": "Apex Mechanical", "blocked_accreditation_type": "CHAS Premium Plus / SSIP Certificate"},
           {"vendor_name": "Northgate Electrical", "blocked_accreditation_type": "NICEIC Approved Contractor Certificate"}]
OPEN = [{"vendor_name": "Apex Mechanical", "code": "WO-B-301-4551", "status": "In progress", "due": "2026-09-30",
         "title": "FCU L4-12 not holding set point"},
        {"vendor_name": "Apex Mechanical", "code": "WO-B-301-4527", "status": "Draft", "due": "2026-09-28",
         "title": "AHU-3 supply fan bearing"}]


WO_READ = "FROM plenum_cafm.work_orders w JOIN plenum_cafm.vendors v"


def _ask(monkeypatch, principal=None):
    from src.http_client import caller_organization_id
    from src.services.principal import caller_principal

    seen = _session(monkeypatch, [(WO_READ, OPEN), ("FROM plenum_cafm.vendors v", BLOCKED)])
    tok = caller_organization_id.set(ORG)
    ptok = caller_principal.set(principal) if principal is not None else None
    try:
        out = asyncio.run(getattr(cp.list_blocked_vendors, "coroutine", cp.list_blocked_vendors)())
    finally:
        caller_organization_id.reset(tok)
        if ptok is not None:
            caller_principal.reset(ptok)
    return out, seen


def test_each_blocked_vendor_lists_its_open_work_orders(monkeypatch):
    out, _ = _ask(monkeypatch)
    by = {b["vendor"]: b for b in out["blocked"]}
    assert [w["code"] for w in by["Apex Mechanical"]["open_work_orders"]] == ["WO-B-301-4551", "WO-B-301-4527"]
    assert by["Apex Mechanical"]["open_work_orders"][0] == {
        "code": "WO-B-301-4551", "status": "In progress", "due": "2026-09-30", "title": "FCU L4-12 not holding set point"}
    assert by["Northgate Electrical"]["open_work_orders"] == [], "a blocked vendor with no open work says so"
    assert by["Apex Mechanical"]["blocked_for"] == "CHAS Premium Plus / SSIP Certificate"


def test_the_answer_is_told_to_name_them_rather_than_send_the_reader_to_check(monkeypatch):
    out, _ = _ask(monkeypatch)
    rules = out["answer_rules"].lower()
    assert "open work orders" in rules and "reassign" in rules
    assert "never tell the reader to check" in rules


def test_the_work_order_read_is_the_company_s_open_work_on_blocked_vendors(monkeypatch):
    _, seen = _ask(monkeypatch)
    sql, params = next((s, p) for s, p in seen if WO_READ in s)
    low = sql.lower()
    assert "w.organization_id::text = :org" in low and "v.organization_id::text = :org" in low
    assert "block_state" in low and "'blocked'" in low
    assert "not in ('completed', 'closed', 'cancelled', 'canceled')" in low
    assert params["org"] == ORG


def test_a_user_allocated_to_some_buildings_sees_only_the_work_on_them(monkeypatch):
    from src.services.principal import Principal

    b = uuid.uuid4()
    p = Principal(user_id=uuid.uuid4(), email="u@x", organization_id=uuid.UUID(ORG), role="user", can_ingest=False,
                  building_ids=(b,))
    _, seen = _ask(monkeypatch, principal=p)
    sql, params = next((s, p_) for s, p_ in seen if WO_READ in s)
    assert "w.building_id" in sql
    assert str(b) in [str(x) for v in params.values() if isinstance(v, (list, tuple)) for x in v]
