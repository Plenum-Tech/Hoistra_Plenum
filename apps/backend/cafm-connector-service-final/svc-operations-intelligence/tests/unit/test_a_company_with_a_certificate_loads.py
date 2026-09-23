"""The company detail page asked for a column two of its tables have.

GET /api/superadmin/companies/{id} returned 500 for any company whose buildings carry a
compliance certificate. The query counts certificates and lists the countries they cover, and
asked for `country_code` unqualified across a join of compliance_certificates and buildings —
both of which have that column, so PostgreSQL refused the statement outright.

Qualifying it was not the whole decision. buildings.country_code exists but is NULL on every row
in both databases, because a building records its country on plenum_cafm.locations; three other
queries in this service were corrected for exactly that on 23 Sep 2026. So `b.country_code`
would have fixed the crash and answered wrongly — Northbridge has five certificates and would
have reported an empty country list beside the count, which reads as uninformative rather than
broken and would have survived much longer.

The certificate's own country is also the right figure on its own terms: it names the regime the
certificate was issued under, which is what a count of certificates is worth knowing by. Measured
on hoistra_test: b. gives countries=[], c. gives ['UK'].
"""
from __future__ import annotations

import re

from src.engines.auth import usage


def _certs_sql() -> str:
    """The certificate query as the module will send it."""
    src = open(usage.__file__, encoding="utf-8").read()
    body = src.split("certs = (await q(text(", 1)[1]
    return body.split('"""', 2)[1]


class TestTheCertificateCountQuery:
    def test_every_country_reference_is_qualified(self):
        """Unqualified is what PostgreSQL refused; the column is on both sides of the join."""
        sql = _certs_sql()
        for m in re.finditer(r"(\w+\.)?country_code", sql):
            assert m.group(1), f"unqualified country_code in: {m.group(0)}"

    def test_the_country_is_the_certificates_own(self):
        assert "c.country_code" in _certs_sql()

    def test_it_is_not_the_buildings(self):
        """buildings.country_code is null on every row; a building's country lives on locations."""
        assert "b.country_code" not in _certs_sql()

    def test_the_join_and_the_scope_are_unchanged(self):
        sql = _certs_sql()
        assert "JOIN plenum_cafm.buildings b ON b.building_id = c.building_id" in sql
        assert "WHERE b.organization_id = :o" in sql, "still scoped to the one company"

    def test_a_certificate_naming_no_country_is_dropped_not_counted_as_one(self):
        assert "array_remove(array_agg(DISTINCT c.country_code), NULL)" in _certs_sql()


class TestTheSameMistakeIsNotElsewhereInThisModule:
    def test_no_other_query_joins_buildings_and_asks_for_a_bare_country_code(self):
        src = open(usage.__file__, encoding="utf-8").read()
        for block in re.findall(r'text\("""(.*?)"""', src, re.S):
            if "plenum_cafm.buildings" not in block:
                continue
            for m in re.finditer(r"(\w+\.)?country_code", block):
                assert m.group(1), f"unqualified country_code beside a buildings join:\n{block}"
