"""A meter file filed against a building places its meter there.

The register (plenum_cafm.meters) answers for every MPAN the portfolio already knows. A
building hoisted five minutes ago knows none of them, so the first half-hourly CSV for it
created a meter with no building at all: the readings landed, counted towards no building
and no EUI, and reported "ingested" either way. Nothing said they were stranded.

So the building the person chose in the composer travels with the upload and is used where
the register cannot answer — and only there. A file filed against the wrong building must
never move a meter the register has already placed, which is the second test here.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from src.engines.energy import meters

REGISTERED = UUID("f12e9629-95ed-5722-80cc-a1397f627850")
CHOSEN = UUID("5fdf539b-3aef-422b-9063-ee089e547e3c")

CSV = (
    "reading_at,consumption_kwh,period_minutes,mpan\n"
    "2025-09-01T00:00:00Z,12.5,30,NB-B-101-E0\n"
    "2025-09-01T00:30:00Z,11.9,30,NB-B-101-E0\n"
)


@pytest.fixture()
def spy(monkeypatch):
    """Capture what building each created meter is given, without a database."""
    seen: dict = {"register_answer": None, "created": []}

    async def fake_register(session, *, mpan=None, mprn=None):
        return seen["register_answer"]

    async def fake_upsert(session, data):
        seen["created"].append(dict(data))
        return {"meter": {"id": "11111111-1111-1111-1111-111111111111"}}

    monkeypatch.setattr(meters, "building_for_meter", fake_register)
    monkeypatch.setattr(meters, "upsert_meter", fake_upsert)
    return seen


class _NoMeterSession:
    """Every lookup for an existing energy_meters row comes back empty."""

    async def execute(self, *a, **k):
        class _R:
            def scalar_one_or_none(self_inner):
                return None

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return []

        return _R()

    async def commit(self):
        return None

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_an_unregistered_mpan_takes_the_building_it_was_filed_against(spy, monkeypatch):
    spy["register_answer"] = None
    captured = {}

    async def fake_bulk(session, *, meter_id, readings, **k):
        captured["meter_id"] = meter_id
        return {"inserted": len(readings), "gaps_flagged": []}

    monkeypatch.setattr(meters, "ingest_readings", fake_bulk)
    await meters.ingest_readings_csv(
        _NoMeterSession(), csv_text=CSV, building_id=CHOSEN, detect_gaps=False
    )
    assert spy["created"], "a meter should have been created for an MPAN nobody knows"
    assert spy["created"][0]["building_id"] == CHOSEN, (
        "without this the readings land on a meter attached to nothing"
    )


@pytest.mark.asyncio
async def test_the_register_wins_so_a_wrong_upload_cannot_move_a_placed_meter(spy, monkeypatch):
    spy["register_answer"] = REGISTERED

    async def fake_bulk(session, *, meter_id, readings, **k):
        return {"inserted": len(readings), "gaps_flagged": []}

    monkeypatch.setattr(meters, "ingest_readings", fake_bulk)
    await meters.ingest_readings_csv(
        _NoMeterSession(), csv_text=CSV, building_id=CHOSEN, detect_gaps=False
    )
    assert spy["created"][0]["building_id"] == REGISTERED, (
        "the portfolio's own record of where this meter is outranks the upload"
    )


@pytest.mark.asyncio
async def test_with_no_building_named_anywhere_the_meter_is_still_created(spy, monkeypatch):
    """Readings are not thrown away for want of a building. The meter is created unplaced
    and the service logs that it could not be placed, which is the honest outcome."""
    spy["register_answer"] = None

    async def fake_bulk(session, *, meter_id, readings, **k):
        return {"inserted": len(readings), "gaps_flagged": []}

    monkeypatch.setattr(meters, "ingest_readings", fake_bulk)
    await meters.ingest_readings_csv(_NoMeterSession(), csv_text=CSV, detect_gaps=False)
    assert spy["created"][0]["building_id"] is None
