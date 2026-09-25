"""The Assets page said "not computable" over assets that carried everything it needed.

plenum_cafm.assets holds replacement_value, design_life_years, wear_coefficient, section_id and
vendor_id. After the Harbour Point ingest on 23 Sep 2026 all twelve assets carried the first
three and a section. The register showed "Est. asset value at risk GBP0 — 0 of 12 assets
contributing", "Not computable — needs a replacement value, a design life and an install date",
and "Open the asset to read its vendor".

Nothing was missing from the data. The ORM model never mapped those columns and AssetResponse
never returned them, so the page could not see them — and it said so, in as many words:
"assets.section_id exists on the table but AssetResponse does not return it, so the link cannot
be made in bulk". That is the whole bug, and the page had been describing it accurately.

They belong in the LIST response rather than the single-asset one. The page bands every asset at
once; a field you have to open each asset to read cannot be used for that.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.api.schemas.asset import AssetResponse
from src.models.asset import Asset

NEEDED = ("replacement_value", "design_life_years", "wear_coefficient",
          "section_id", "vendor_id")


class TestTheColumnsAreMapped:
    def test_the_orm_knows_every_column_the_page_reads(self):
        cols = {c.key for c in Asset.__table__.columns}
        missing = [n for n in NEEDED if n not in cols]
        assert not missing, f"unmapped, so the page reads them as absent: {missing}"

    def test_the_condition_trio_is_still_there(self):
        cols = {c.key for c in Asset.__table__.columns}
        for n in ("condition_score", "condition_provenance", "condition_updated_at"):
            assert n in cols

    def test_installation_date_is_still_mapped(self):
        """value_at_risk needs it alongside the other two; it was never the missing part."""
        assert "installation_date" in {c.key for c in Asset.__table__.columns}


class TestTheResponseReturnsThem:
    def test_every_field_is_on_the_response(self):
        missing = [n for n in NEEDED if n not in AssetResponse.model_fields]
        assert not missing, f"on the record but never sent: {missing}"

    def test_a_decimal_becomes_a_number_rather_than_failing(self):
        """Numeric comes back as Decimal, which is not JSON."""
        out = AssetResponse.model_validate({
            "asset_id": "a1", "asset_name": "AHU 1",
            "replacement_value": Decimal("38000.00"),
            "design_life_years": Decimal("20.00"),
            "wear_coefficient": Decimal("0.900"),
        })
        assert out.replacement_value == 38000.0
        assert out.design_life_years == 20.0
        assert out.wear_coefficient == 0.9

    def test_a_uuid_section_is_sent_as_a_string(self):
        import uuid
        sid = uuid.uuid4()
        out = AssetResponse.model_validate(
            {"asset_id": "a1", "asset_name": "AHU 1", "section_id": sid})
        assert out.section_id == str(sid)

    def test_absent_stays_absent_rather_than_becoming_zero(self):
        """A missing replacement value is not a free asset; the page must see None."""
        out = AssetResponse.model_validate({"asset_id": "a1", "asset_name": "AHU 1"})
        for n in NEEDED:
            assert getattr(out, n) is None

    def test_a_real_row_serialises_whole(self):
        out = AssetResponse.model_validate({
            "asset_id": "a1", "asset_code": "B-101-AHU-01", "asset_name": "Air handling unit 1",
            "installation_date": date(2018, 3, 12), "condition_score": 2,
            "replacement_value": Decimal("38000.00"), "design_life_years": Decimal("20.00"),
            "wear_coefficient": Decimal("0.900"),
            "section_id": "889d1f0e-ccb8-4d73-aebe-44f90a7018ba", "vendor_id": "MERI",
        })
        d = out.model_dump()
        # the three value_at_risk needs, together
        assert d["replacement_value"] and d["design_life_years"] and d["installation_date"]
        assert d["section_id"] and d["vendor_id"]
