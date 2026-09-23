"""A hyphenated code is not automatically a plant item.

_ASSET_RE matches a SHAPE — letters-dash-alphanumerics — so an invoice's own number, the
work orders it bills and the part codes it lists all look like asset references to it. The
asset check then told the reader:

    "None of the asset references in the document (MERI-2026-09-0051, WO-B-101-35,
     PRT-BOILER-SVC, WO-B-101-38) are on this building."

Not one of those four is an asset. Two of them are work orders that WERE on that building —
that is how the invoice matched them and priced its flagged line. So the reader was asked to
override a conflict that did not exist, which is how people learn to click through warnings.

Same reasoning as the meter filter already in claims.py: what a document presents a code AS
beats a pattern guessing from its shape.
"""
from src.engines.ingestion.claims import _looks_like_an_asset_code as ok


class TestThingsThatAreNotAssets:
    def test_a_work_order(self):
        assert ok("WO-B-101-35") is False

    def test_a_part_code(self):
        assert ok("PRT-BOILER-SVC") is False
        assert ok("PART-AHU-BELT") is False

    def test_an_invoice_number_carrying_a_year(self):
        assert ok("MERI-2026-09-0051") is False

    def test_a_purchase_order(self):
        assert ok("PO-2026-0044") is False

    def test_case_does_not_matter(self):
        assert ok("wo-b-101-35") is False


class TestThingsThatAre:
    def test_a_plant_code(self):
        assert ok("B-101-AHU-01") is True
        assert ok("B-102-BOILER-03") is True

    def test_a_lift(self):
        assert ok("B-101-LIFT-01") is True

    def test_a_short_code(self):
        assert ok("AHU-01-A") is True
