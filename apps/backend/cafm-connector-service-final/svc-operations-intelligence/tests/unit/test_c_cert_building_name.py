"""A certificate that knows its building, and a register that said it did not.

Asked what compliance certificates Riverside Court holds, the deployed agent answered that
Riverside Court "is not present in the building compliance register" and listed the three
buildings it could see. Nine Building-scope certificates exist, all nine carry a correct
building_id, and the building graph draws Riverside Court's from that column.

The enrichment resolved the display name from site_id, falling back to the building_name
TEXT column captured at ingestion, and never consulted building_id. Six of the nine rows
have a null text copy, so the name filter every agent uses could match only the three where
somebody had filled it in — which is exactly the list the model recited.

Nothing errored. The filter matched nothing and reported the values it had found, and those
looked like the whole register.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.engines.compliance import certificates as cert

BUILDING = "f12e9629-95ed-5722-80cc-a1397f627850"


class _Rows:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self._rows = rows or []

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return None


class FakeSession:
    """Answers only the buildings lookup; everything else comes back empty.

    The enrichment's other loaders run through _safe_exec, which turns a failure into the
    default, so an empty answer is the same as a deployment with no vendors or assets
    attached — which is the case this test is about anyway.
    """

    def __init__(self, known: dict[str, tuple[str, str]]):
        self.known = known
        self.saw_buildings_query = False

    def begin_nested(self):
        # _safe_exec runs every optional loader inside a SAVEPOINT so a failure cannot
        # leave the transaction unusable for the queries after it.
        session = self

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "plenum_cafm.buildings" in sql:
            self.saw_buildings_query = True
            ids = {str(i) for i in (params or {}).get("ids", [])}
            return _Rows([
                {"building_id": b, "name": n, "building_code": c}
                for b, (n, c) in self.known.items()
                if b in ids
            ])
        return _Rows([])


def enrich(rows: list[dict[str, Any]], known=None, monkeypatch=None) -> list[dict[str, Any]]:
    session = FakeSession(known if known is not None else {BUILDING: ("Riverside Court", "B-006")})
    out = asyncio.run(cert._enrich_certificate_rows(session, rows))
    return out


def row(**kw: Any) -> dict[str, Any]:
    base = {
        "certificate_type_code": "US_FIRE_ALARM_NFPA72",
        "cert_scope": "Building",
        "building_id": BUILDING,
        "site_id": None,
        "building_name": None,
        "building_reference": None,
        "expiry_date": None,
        "country_code": "US",
        "raw_metadata": {},
    }
    base.update(kw)
    return base


def test_the_building_id_alone_names_the_building():
    # The regression. This row is Riverside Court's certificate exactly as it is stored.
    out = enrich([row()])
    assert out[0]["building_name"] == "Riverside Court"


def test_the_building_code_becomes_the_reference():
    assert enrich([row()])[0]["building_reference"] == "B-006"


def test_a_name_read_off_the_document_still_wins():
    # A stored name that disagrees with the FK is a mis-filing worth seeing. Overwriting it
    # from the FK would make the two agree and hide which one is wrong.
    out = enrich([row(building_name="Bishopsgate Tower")])
    assert out[0]["building_name"] == "Bishopsgate Tower"


def test_a_vendor_certificate_is_left_alone():
    # Vendor accreditations name no property and must not acquire one.
    out = enrich([row(cert_scope="Vendor", building_id=None)])
    assert out[0]["building_name"] is None


def test_a_building_id_pointing_nowhere_names_nothing():
    # A dangling FK is not a building. Better to say nothing than to invent a name.
    out = enrich([row()], known={})
    assert out[0]["building_name"] is None


def test_rows_without_a_building_id_do_not_trigger_the_lookup():
    session = FakeSession({BUILDING: ("Riverside Court", "B-006")})
    asyncio.run(cert._enrich_certificate_rows(session, [row(building_id=None)]))
    assert not session.saw_buildings_query


def test_every_row_that_has_the_fk_gets_named():
    # The shape of the bug: a partially-populated text column made the register look like
    # it held three buildings. With the FK read, all of them resolve.
    known = {
        BUILDING: ("Riverside Court", "B-006"),
        "11111111-1111-5111-8111-111111111111": ("Town Hall", "B-003"),
        "22222222-2222-5222-8222-222222222222": ("Northgate Mall", "B-008"),
    }
    rows = [row(building_id=b) for b in known]
    out = enrich(rows, known=known)
    assert [r["building_name"] for r in out] == [
        "Riverside Court", "Town Hall", "Northgate Mall"
    ]


def test_an_empty_batch_asks_nothing():
    assert enrich([]) == []


@pytest.mark.parametrize("bad", ["", "not-a-uuid", None])
def test_a_malformed_building_id_does_not_take_the_listing_down(bad):
    # This runs on every certificate listing, including the console's first paint. A bad
    # value in one row must not cost the other rows their page.
    out = enrich([row(building_id=bad)])
    assert out and out[0]["building_name"] is None
