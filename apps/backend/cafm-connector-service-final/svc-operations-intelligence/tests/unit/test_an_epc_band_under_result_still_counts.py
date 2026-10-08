"""An EPC whose band arrived under `result` is still a rated EPC.

mees_summary read the band from energy_rating alone. On 23 Sep 2026 the only EPC on file carried
its band, C, in `result`, so it had no band here at all and the tiles counted a band-C building as
neither below E nor below B: zero and zero, over a certificate that plainly said C.

The band is now read from energy_rating, and from result when that is empty and result is one
letter A to G. The regex is the whole safeguard: result also carries Satisfactory, Pass and
Unsatisfactory on other certificate types, and none of those may be mistaken for a band.
"""
from __future__ import annotations

import re

from src.engines.compliance import epc_rating

SRC = open(epc_rating.__file__, encoding="utf-8").read()
_START = SRC.index("SELECT DISTINCT ON (c.building_id)")
QUERY = SRC[_START:_START + 1800]


class TestTheBandHasAFallback:
    def test_result_is_read_when_energy_rating_is_empty(self):
        assert "coalesce(c.energy_rating," in QUERY
        assert "c.result" in QUERY

    def test_only_a_single_letter_a_to_g_counts_as_a_band(self):
        """Satisfactory, Pass and Unsatisfactory live in result too, and are not bands."""
        assert re.search(r"c\.result ~ '\^\[A-Ga-g\]\$'", QUERY), "the regex is the safeguard"

    def test_the_fallback_is_upper_cased(self):
        assert "upper(c.result)" in QUERY

    def test_a_rated_row_still_outranks_an_unrated_one(self):
        """DISTINCT ON keeps the first row per building; a band from either column must sort
        ahead of no band, or a newer unrated certificate hides an older rated one."""
        order = QUERY[QUERY.index("ORDER BY"):]
        assert "IS NULL)" in order and "c.result" in order, \
            "the ORDER BY must rank on the same fallback the SELECT uses"


class TestMeesPositionIsUnchanged:
    def test_c_is_not_below_e(self):
        assert not epc_rating.mees_position("C", gia_m2=14200)["below_minimum_now"]

    def test_c_is_below_b(self):
        pos = epc_rating.mees_position("C", gia_m2=14200)
        assert pos["below_proposed_2030"] and pos["large_building"]

    def test_f_is_below_e(self):
        assert epc_rating.mees_position("F", gia_m2=500)["below_minimum_now"]
