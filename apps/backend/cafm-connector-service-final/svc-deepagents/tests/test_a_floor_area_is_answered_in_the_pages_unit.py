"""A floor area is answered in square metres, the unit the Buildings page shows (29 Oct 2026).

"What is the floor area of LPS?" answered "95,669.64 sq ft" while the Buildings page showed 8,888 m²
for the same building — the same figure: the form takes m², and building_create stores it converted in
buildings.gross_area_sqft (95,669.64 / 10.7639 = 8,888). The answer read the stored column as it is,
and listed the separate, empty gross_internal_area_m2 as "Not recorded in m²", which read as a
contradiction. Every *_sqft area now also carries its m² figure, and the answer is told to give m².
"""
from src.agents import ontology_qa as oq


def test_a_square_foot_area_also_carries_its_square_metres():
    attrs, converted = oq.with_area_in_m2({"name": "lps", "gross_area_sqft": 95669.64, "floors": 77})
    assert converted is True
    assert attrs["gross_area_m2"] == 8888.0 and attrs["gross_area_sqft"] == 95669.64


def test_an_area_already_in_square_metres_is_left_alone():
    attrs, converted = oq.with_area_in_m2({"name": "x", "gross_area_sqft": 1000.0, "gross_area_m2": 92.9})
    assert attrs["gross_area_m2"] == 92.9 and converted is False


def test_a_missing_or_text_area_is_left_alone():
    assert oq.with_area_in_m2({"gross_area_sqft": None}) == ({"gross_area_sqft": None}, False)
    assert oq.with_area_in_m2({"gross_area_sqft": "n/a"}) == ({"gross_area_sqft": "n/a"}, False)


def test_the_evidence_gives_square_metres_and_says_why(monkeypatch):
    monkeypatch.setattr(oq, "interpretation", lambda onto, plan, notes: list(notes))
    result = {"kind": "list", "focus": "Building", "total": 1,
              "records": [{"attributes": {"name": "lps", "gross_area_sqft": 95669.64, "gross_internal_area_m2": None},
                           "context": {}, "related": {}}]}
    out = oq.evidence(None, "What is the floor area of LPS?", {}, result, [])
    assert '"gross_area_m2": 8888.0' in out["records"]
    assert any("m²" in n and "gross_area_sqft" in n for n in out["interpretation"])
