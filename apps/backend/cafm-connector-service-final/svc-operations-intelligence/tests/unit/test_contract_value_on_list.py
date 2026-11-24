"""The contract's commercial value, surfaced on the parameters list (24 Nov 2026).

The SLA parameters extracted into contract_sla_parameters hold no money of their own —
contract_value / annual_value_gbp live on the separate plenum_cafm.vendor_contracts row. So
"list the vendor contracts at Brunel Wharf House with their contract value" was answered from
the parameters list alone and reported every value blank, though the figure was on record.

The list now joins vendor_contracts: by contract_id where the document→contract link resolved
it, else by vendor_id but only when that vendor has exactly one contract (several vendors hold
more than one row under one name, and a bare vendor_id cannot say which — those stay unplaced).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.engines.contract_performance import parameters as params_mod

ORG = UUID("00000000-0000-0000-0000-000000000001")
_VENDOR_NAMES = "SELECT id::text AS id, vendor_name FROM plenum_cafm.vendors"


class _Row:
    def __init__(self, **over):
        self.id = over.get("id", uuid4())
        self.organization_id = ORG
        self.vendor_id = over.get("vendor_id")
        self.contract_id = over.get("contract_id")
        self.document_id = None
        self.contract_ref = "REF-1"
        self.signed_date = None
        self.status = "confirmed"
        self.confirmed_by = None
        self.confirmed_at = None
        self.defaults_used = []
        self.overrides_log = []
        self.field_sources = {}
        self.created_at = None
        self.updated_at = None
        for f in (
            "sla_response_p1_hours", "sla_response_p2_hours", "sla_response_p3_hours",
            "sla_response_p4_hours", "sla_completion_p1_hours", "sla_completion_p2_hours",
            "sla_completion_p3_hours", "sla_completion_p4_hours", "labour_day_rate",
            "labour_hour_rate", "overtime_rate", "call_out_rate", "payment_terms",
        ):
            setattr(self, f, None)
        for f in ("parts_pricing_json", "kpi_clauses_json", "ppm_obligations_json", "task_criticality_json"):
            setattr(self, f, {})


class _M:
    """A result row exposing ._mapping and attribute access like SQLAlchemy's."""

    def __init__(self, d):
        self._mapping = d
        for k, v in d.items():
            setattr(self, k, v)


class _Res:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def mappings(self):
        return self


def _session(vendor_contracts_sql_rows):
    """A fake session: the parameters rows come from `rows`, vendor_contracts reads return the
    mapped rows the test supplies, everything else empty."""
    seen = {"by_contract": 0, "by_vendor": 0}

    class FakeSession:
        def __init__(self, rows):
            self._rows = rows

        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if "FROM plenum_cafm.vendor_contracts" in sql and "id::text = ANY" in sql and "vendor_id::text = ANY" not in sql:
                seen["by_contract"] += 1
                return _Res([_M(r) for r in vendor_contracts_sql_rows.get("by_contract", [])])
            if "FROM plenum_cafm.vendor_contracts" in sql and "vendor_id::text = ANY" in sql:
                seen["by_vendor"] += 1
                return _Res([_M(r) for r in vendor_contracts_sql_rows.get("by_vendor", [])])
            if sql.startswith(_VENDOR_NAMES) or "FROM plenum_cafm.ingestion_documents" in sql:
                return _Res([])
            return _Res(self._rows)

    return FakeSession, seen


@pytest.mark.asyncio
async def test_value_is_read_by_contract_id_when_it_resolved():
    cid = uuid4()
    row = _Row(contract_id=cid, vendor_id=uuid4())
    FakeSession, seen = _session({"by_contract": [
        {"id": str(cid), "contract_value": Decimal("9300.00"), "annual_value_gbp": None,
         "currency": "GBP", "contract_name": "Electrical · Brunel Wharf",
         "contract_start": date(2025, 1, 1), "contract_end": date(2027, 12, 11)}]})

    class S(FakeSession):
        def __init__(s):
            super().__init__([row])

    out = await params_mod.list_contract_parameters(S(), organization_id=ORG)
    assert out[0]["contract_value"] == 9300.0
    assert out[0]["currency"] == "GBP"
    assert out[0]["contract_name"] == "Electrical · Brunel Wharf"
    assert out[0]["contract_end"] == "2027-12-11"


@pytest.mark.asyncio
async def test_value_falls_back_to_vendor_id_only_when_the_vendor_has_one_contract():
    vid = uuid4()
    row = _Row(vendor_id=vid)  # no contract_id
    FakeSession, _ = _session({"by_vendor": [
        {"vid": str(vid), "cid": str(uuid4()), "contract_value": Decimal("5300.00"),
         "annual_value_gbp": Decimal("5300.00"), "currency": "GBP",
         "contract_name": "Fire alarm maintenance · Brunel Wharf",
         "contract_start": None, "contract_end": None, "n": 1}]})

    class S(FakeSession):
        def __init__(s):
            super().__init__([row])

    out = await params_mod.list_contract_parameters(S(), organization_id=ORG)
    assert out[0]["contract_value"] == 5300.0 and out[0]["annual_value_gbp"] == 5300.0


@pytest.mark.asyncio
async def test_a_vendor_with_several_contracts_is_left_unplaced():
    vid = uuid4()
    row = _Row(vendor_id=vid)
    FakeSession, _ = _session({"by_vendor": [
        {"vid": str(vid), "cid": str(uuid4()), "contract_value": Decimal("1.00"), "annual_value_gbp": None,
         "currency": "GBP", "contract_name": "A", "contract_start": None, "contract_end": None, "n": 2},
        {"vid": str(vid), "cid": str(uuid4()), "contract_value": Decimal("2.00"), "annual_value_gbp": None,
         "currency": "GBP", "contract_name": "B", "contract_start": None, "contract_end": None, "n": 2}]})

    class S(FakeSession):
        def __init__(s):
            super().__init__([row])

    out = await params_mod.list_contract_parameters(S(), organization_id=ORG)
    assert out[0]["contract_value"] is None and out[0]["contract_name"] is None


@pytest.mark.asyncio
async def test_a_vendor_contracts_read_that_fails_does_not_break_the_list():
    row = _Row(vendor_id=uuid4(), contract_id=uuid4())

    class S:
        async def execute(self, stmt, params=None):
            sql = " ".join(str(stmt).split())
            if "FROM plenum_cafm.vendor_contracts" in sql:
                raise RuntimeError("vendor_contracts unavailable")
            if sql.startswith(_VENDOR_NAMES) or "FROM plenum_cafm.ingestion_documents" in sql:
                return _Res([])
            return _Res([row])

    out = await params_mod.list_contract_parameters(S(), organization_id=ORG)
    assert out and out[0]["contract_value"] is None
