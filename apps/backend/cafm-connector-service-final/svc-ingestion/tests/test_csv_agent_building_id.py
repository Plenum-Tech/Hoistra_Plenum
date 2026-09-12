"""The asset record's building_id, and the COPY alignment it depends on."""
from __future__ import annotations

import uuid

from agents.csv_agent import (
    _RESOLVED_BUILDING_KEY,
    _build_asset_record,
    _coerce_uuid,
)

ORG = uuid.UUID("11111111-1111-1111-1111-111111111111")
BID = "a0000000-0000-0000-0000-000000000001"


def _record(row):
    values, cols = _build_asset_record(row, ORG, mapping=None)
    return dict(zip(cols, values))


def test_a_resolved_building_is_written_as_a_uuid_not_a_string():
    """assets.building_id is a uuid column and asyncpg COPY encodes by Python type — a
    string here would fail the whole batch, not just this row."""
    rec = _record({"asset_name": "AHU-1", _RESOLVED_BUILDING_KEY: BID})
    assert rec["building_id"] == uuid.UUID(BID)
    assert isinstance(rec["building_id"], uuid.UUID)


def test_a_building_id_the_source_already_carries_is_never_overwritten():
    """An id in the export is better evidence than anything inferred from a name."""
    source = "b0000000-0000-0000-0000-000000000002"
    rec = _record({"asset_name": "AHU-1", "building_id": source,
                   _RESOLVED_BUILDING_KEY: BID})
    assert rec["building_id"] == uuid.UUID(source)


def test_an_unresolved_row_carries_no_building_id_column_at_all():
    """Not NULL-in-a-column — absent, so COPY never has to encode a null uuid."""
    assert "building_id" not in _record({"asset_name": "AHU-1"})


def test_a_malformed_building_id_drops_the_link_and_keeps_the_asset():
    rec = _record({"asset_name": "AHU-1", _RESOLVED_BUILDING_KEY: "not-a-uuid"})
    assert "building_id" not in rec
    assert rec["asset_name"] == "AHU-1"


def test_coerce_uuid_accepts_what_is_uuid_shaped_and_refuses_the_rest():
    u = uuid.uuid4()
    assert _coerce_uuid(u) is u
    assert _coerce_uuid(str(u)) == u
    for junk in (None, "", "   ", "nan", "N/A", "123", 42, "not-a-uuid"):
        assert _coerce_uuid(junk) is None


# ── Why the COPY groups by column names, not column count ────────────────────


def test_rows_differing_only_in_which_optional_column_they_carry_share_a_length():
    """The precondition for the old bug: COPY positions by order, so two records of equal
    length and different columns would write asset_code's value into serial_number.
    extract_csv groups by the exact column tuple for this reason."""
    a_values, a_cols = _build_asset_record(
        {"asset_name": "AHU-1", "asset_code": "A-1"}, ORG, mapping=None
    )
    b_values, b_cols = _build_asset_record(
        {"asset_name": "AHU-2", "serial": "SN-9"}, ORG, mapping=None
    )
    assert len(a_values) == len(b_values), "same arity"
    assert a_cols != b_cols, "different meaning — must not share a COPY batch"


def test_the_building_id_column_makes_rows_in_one_file_diverge():
    """A partly-linked file is the normal case, so the two shapes must be separable."""
    _, linked = _build_asset_record(
        {"asset_name": "AHU-1", _RESOLVED_BUILDING_KEY: BID}, ORG, mapping=None
    )
    _, unlinked = _build_asset_record({"asset_name": "AHU-2"}, ORG, mapping=None)
    assert "building_id" in linked and "building_id" not in unlinked
