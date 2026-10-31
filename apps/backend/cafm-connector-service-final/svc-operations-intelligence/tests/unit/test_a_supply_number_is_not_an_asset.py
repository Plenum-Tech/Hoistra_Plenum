"""A half-hourly export names a meter, and the building it was filed against has that meter.

On 22 Sep 2026 Ashgrove Court's two half-hourly files were held for a check with:

    None of the asset references in the document (NB-B-102-E0) are on this building.
    Can you tell me why this document should be filed against Ashgrove Court?

The same run had just created that meter on that building and written 17,520 readings against
it. Two faults met to produce a question with no true answer:

  * NB-B-102-E0 is an MPAN. It matches the hyphenated-code pattern that looks for asset
    codes, so it was claimed as an asset and checked against the asset register, where a
    supply number never appears and so can never be found.
  * Even a meter claim would have found nothing, because a building's meters were read from
    plenum_cafm.meters — an eighteen-row register that predates the energy engine — and not
    from energy_meters, which is what an ingest writes to.

The document says what the value is, by presenting it under a meter column. A regular
expression only guesses.
"""
from __future__ import annotations

import pytest

from src.engines.ingestion import claims as C
from src.engines.ingestion import validation as V
from src.engines.ingestion.ontology import BuildingOntology

ASHGROVE = "4a451a94-af44-486f-b660-8e19518cd19f"
ELEC = "NB-B-102-E0"
GAS = "NB-B-102-G1"

#: The first two lines of ashgrove_court_electricity_halfhourly.csv, as the reader sees them.
CSV_HEAD = "reading_at,consumption_kwh,period_minutes,mpan\n2025-09-22T00:00:00Z,19.297,30," + ELEC


def building(meters: list[str], assets: list[str] | None = None) -> BuildingOntology:
    o = BuildingOntology(building_id=ASHGROVE, name="Ashgrove Court")
    o.meters = list(meters)
    o.assets = list(assets or [])
    o.counts = {"meters": len(meters), "assets": len(o.assets)}
    return o


def finding(findings, kind):
    return next((f for f in findings if f.check == kind), None)


class TestWhatTheDocumentIsSaying:
    def test_an_mpan_column_names_a_meter(self):
        c = C.from_document(extracted={"mpan": ELEC})
        assert ELEC in c.meters

    def test_an_mprn_column_names_a_meter(self):
        c = C.from_document(extracted={"mprn": GAS})
        assert GAS in c.meters

    def test_a_supply_number_is_not_also_an_asset(self):
        """The whole bug in one assertion. The pattern still matches it in the body text; the
        column it was presented under says what it is."""
        c = C.from_document(text=CSV_HEAD, extracted={"mpan": ELEC})
        assert ELEC in c.meters
        assert ELEC not in c.assets, "a supply number checked against the asset register can never be found"

    def test_a_real_asset_code_in_the_same_document_still_reads_as_an_asset(self):
        c = C.from_document(text="Work on HP-CH-01 recorded against meter.",
                            extracted={"mpan": ELEC})
        assert "HP-CH-01" in c.assets
        assert ELEC not in c.assets

    def test_a_document_that_names_only_a_meter_is_not_empty(self):
        """An empty claim set means "this document says nothing about where it belongs", which
        would skip the comparison entirely."""
        assert not C.from_document(extracted={"mpan": ELEC}).is_empty()

    def test_the_meters_are_reported(self):
        assert C.from_document(extracted={"mpan": ELEC}).as_dict()["meters"] == [ELEC]


class TestTheCheckItself:
    def test_a_meter_on_this_building_supports_the_filing(self):
        c = C.from_document(text=CSV_HEAD, extracted={"mpan": ELEC})
        f = finding(V.compare(c, building(meters=[ELEC, GAS])), "meters")
        assert f is not None and f.direction == V.SUPPORTS

    def test_the_asset_check_no_longer_fires_on_a_supply_number(self):
        """It fired CONFLICTS, which is what held the document."""
        c = C.from_document(text=CSV_HEAD, extracted={"mpan": ELEC})
        out = V.compare(c, building(meters=[ELEC], assets=["AC-BLR-01", "AC-GEN-01"]))
        assert finding(out, "assets") is None

    def test_a_meter_that_belongs_to_another_building_still_conflicts(self):
        """The check has to keep working. A genuinely misfiled export must still be caught."""
        c = C.from_document(text=CSV_HEAD, extracted={"mpan": ELEC})
        f = finding(V.compare(c, building(meters=["NB-B-101-E0"])), "meters")
        assert f is not None and f.direction == V.CONFLICTS

    def test_a_building_with_no_meters_yet_is_unknown_rather_than_wrong(self):
        """Before the first upload a building has no meters. That is not evidence against the
        upload; it is the absence of evidence, and the two read very differently to whoever
        has to answer the question."""
        c = C.from_document(text=CSV_HEAD, extracted={"mpan": ELEC})
        f = finding(V.compare(c, building(meters=[])), "meters")
        assert f is not None and f.direction == V.UNKNOWN

    @pytest.mark.parametrize("mpan,mprn", [(ELEC, None), (None, GAS)])
    def test_both_fuels_behave_the_same(self, mpan, mprn):
        extracted = {k: v for k, v in (("mpan", mpan), ("mprn", mprn)) if v}
        ref = mpan or mprn
        c = C.from_document(text=f"supply {ref}", extracted=extracted)
        f = finding(V.compare(c, building(meters=[ref])), "meters")
        assert f is not None and f.direction == V.SUPPORTS


class TestWhereABuildingsMetersComeFrom:
    def test_the_gatherer_reads_energy_meters(self):
        """plenum_cafm.meters is not where an ingest puts a meter. Reading only that table is
        what made a building look like it had never heard of the meter it had just been given.
        """
        import inspect
        from src.engines.ingestion import ontology
        src = inspect.getsource(ontology.load)
        assert "plenum_cafm.energy_meters" in src
        assert "mpan" in src and "mprn" in src
