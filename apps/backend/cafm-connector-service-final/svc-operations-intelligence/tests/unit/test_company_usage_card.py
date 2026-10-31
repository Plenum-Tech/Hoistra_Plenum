"""The Super Admin console's company usage card counts what the company really holds.

Hussain, 29 Sep 2026 ("check if all the data coming here is correct and from udr"):

- Compliance certificates were counted only through the company's buildings, so every vendor
  certificate — they carry no building — and every certificate filed without one was missing.
  They are counted by the certificate's own company, as the Compliance page counts them.
- "UDR data" summed the extracted text of documents only, so a company with 289,604 migrated
  rows read 0 MB. His decision: rows in the UDR — every row the company holds across the
  unified store's data tables, each table reached by whichever link it has to a company.
- "API requests" counted events nothing ever writes, so it could only read 0. It now says it
  is not recorded, until something records one.

No database: a fake session answers by SQL text.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.engines.auth import usage

ORG = UUID("11111111-1111-1111-1111-111111111111")

COLUMNS = {
    "buildings": ["building_id", "organization_id", "name"],
    "assets": ["id", "organization_id", "building_id", "vendor_id"],
    "energy_meters": ["id", "building_id"],
    "meter_readings": ["id", "meter_id", "reading_at"],
    "work_orders": ["id", "organization_id", "asset_id"],
    "documents": ["document_id", "building_id"],
    "vendors": ["id", "organization_id"],
    "technicians": ["id", "name"],  # no link to a company
    "sites": ["id", "organization_id"],  # an integer organization_id — cannot name a uuid company
}
TYPES = {("sites", "organization_id"): "integer"}


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeDB:
    def __init__(self, api_events=0, udr_fails=False, union=None):
        self.sql: list[str] = []
        self.api_events, self.udr_fails = api_events, udr_fails
        self.union = union if union is not None else [
            {"t": "assets", "n": 61}, {"t": "meter_readings", "n": 283064},
            {"t": "work_orders", "n": 4683}, {"t": "buildings", "n": 1}, {"t": "vendors", "n": 15},
            {"t": "energy_meters", "n": 58}, {"t": "documents", "n": 0}]

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.sql.append(sql)
        if "FROM plenum_cafm.organizations WHERE id" in sql:
            return _Result([{"id": str(ORG), "name": "Plenum Technologies", "country_code": "UK"}])
        if "information_schema.columns" in sql:
            return _Result([{"table_name": t, "column_name": c, "data_type": TYPES.get((t, c), "uuid")}
                            for t, cs in COLUMNS.items() for c in cs])
        if "UNION ALL" in sql or "AS t, count(*)" in sql:
            if self.udr_fails:
                raise RuntimeError("canceling statement due to statement timeout")
            return _Result(self.union)
        if "FROM plenum_cafm.compliance_certificates" in sql:
            return _Result([{"n": 55, "countries": ["UK"]}])
        if "FROM plenum_cafm.users" in sql:
            return _Result([{"active": 3, "invited": 1, "can_ingest": 2, "total": 4}])
        if "FROM plenum_cafm.platform_usage_events" in sql:
            return _Result([{"credits_month": 12, "credits_total": 40, "api_30d": self.api_events,
                             "api_all": self.api_events, "queries": 12, "ingests": 0,
                             "last_activity": datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)}])
        if "count(*) FROM plenum_cafm.buildings" in sql or "count(DISTINCT b.building_id)" in sql:
            return _Result(scalar=1)
        if "greatest(" in sql:
            return _Result(scalar=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc))
        return _Result([])


async def test_certificates_are_counted_by_their_own_company_not_through_buildings():
    db = FakeDB()
    card = await usage.company_usage(db, ORG)
    assert card["compliance_certificates"] == 55 and card["certificate_countries"] == ["UK"]
    [q] = [s for s in db.sql if "FROM plenum_cafm.compliance_certificates" in s]
    assert "c.organization_id = :o OR c.org_id = :o" in q
    assert "JOIN plenum_cafm.buildings" not in q


async def test_udr_data_is_the_rows_the_company_holds_across_the_unified_store():
    db = FakeDB()
    card = await usage.company_usage(db, ORG)
    assert card["udr_rows"] == 61 + 283064 + 4683 + 1 + 15 + 58
    assert card["udr_tables"]["meter_readings"] == 283064
    assert "documents" not in card["udr_tables"], "a table with no rows is not listed"
    assert card["udr_unlinked"] == ["sites", "technicians"], "a table with no link to a company is named"


async def test_each_table_is_reached_by_whichever_link_it_has():
    db = FakeDB()
    await usage.company_usage(db, ORG)
    [q] = [s for s in db.sql if "AS t, count(*)" in s]
    # meter readings reach the company only through their meter's building
    assert "x.meter_id::text IN (SELECT id FROM m)" in q
    # an asset counts by its own company; only an asset carrying NO company is placed by its
    # building (re-review, 29 Sep 2026: OR-ing the two counted another company's asset that
    # pointed at this company's building)
    assert ("(x.organization_id::text = :o) OR (x.organization_id IS NULL AND "
            "(x.building_id::text IN (SELECT id FROM b) OR x.vendor_id::text IN (SELECT id FROM v)))") in q
    assert "technicians" not in q


async def test_a_udr_count_that_cannot_be_made_says_so_rather_than_reading_zero():
    db = FakeDB(udr_fails=True)
    card = await usage.company_usage(db, ORG)
    assert card["udr_rows"] is None
    assert "rows in the UDR" in card["unreadable"]


async def test_api_requests_say_they_are_not_recorded_until_one_is():
    card = await usage.company_usage(FakeDB(api_events=0), ORG)
    assert card["api_requests_30d"] is None and card["api_requests_recorded"] is False
    card = await usage.company_usage(FakeDB(api_events=7), ORG)
    assert card["api_requests_30d"] == 7 and card["api_requests_recorded"] is True



async def test_the_company_admin_card_does_not_pay_for_a_count_it_does_not_show():
    db = FakeDB()
    card = await usage.company_usage(db, ORG, with_udr=False)
    assert card["udr_rows"] is None
    assert not any("AS t, count(*)" in q or "information_schema" in q for q in db.sql)
