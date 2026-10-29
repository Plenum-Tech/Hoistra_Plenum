"""A vendor-name filter on certificates finds the vendor however it is named (29 Oct 2026).

list_certificates(vendor_name=) and count_certificates(vendor_name_contains=) kept a row when the
typed text was a substring of its vendor name. "Acme contractor", "facilities acme" and "Gough &
Kelly" found nothing; "Acme" counted Acme's certificates and every "Acme ..." vendor's together
even when a vendor is called exactly "Acme". The rows are already the caller's (company and
buildings), so the whole ladder applies: exact first, then compact, phrase, every word, and a close
spelling only when it is clearly the closest.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engines.compliance import certificates

ROWS = [
    {"id": "c1", "vendor_name": "Acme Facilities Ltd"},
    {"id": "c2", "vendor_name": "Acme Facilities Ltd"},
    {"id": "c3", "vendor_name": "Gough and Kelly Ltd"},
    {"id": "c4", "vendor_name": "RT5, Crossrail Services"},
    {"id": "c5", "vendor_name": None},
]


def _ids(rows):
    return sorted(r["id"] for r in rows)


@pytest.mark.parametrize("typed, expected", [
    ("Acme", ["c1", "c2"]),
    ("Acme contractor", ["c1", "c2"]),
    ("facilities acme", ["c1", "c2"]),
    ("Gough & Kelly", ["c3"]),
    ("RT5 Crossrail", ["c4"]),
    ("Acme Facilites Ltd", ["c1", "c2"]),
    ("the vendor", []),
    ("cme", []),
])
def test_the_filter_finds_the_vendor_however_it_is_named(typed, expected):
    assert _ids(certificates.rows_naming_vendor(ROWS, typed)) == expected


def test_an_exact_vendor_name_beats_a_longer_one():
    rows = ROWS + [{"id": "c6", "vendor_name": "Acme"}]
    assert _ids(certificates.rows_naming_vendor(rows, "Acme")) == ["c6"]


@pytest.mark.asyncio
async def test_count_certificates_counts_by_the_ladder(monkeypatch):
    async def fake_list(*_a, **_k):
        return [dict(r) for r in ROWS]

    monkeypatch.setattr(certificates, "list_certificates", fake_list)
    out = await certificates.count_certificates(object(), vendor_name_contains="Acme contractor")
    assert out["count"] == 2
    out = await certificates.count_certificates(object(), vendor_name_contains="Gough & Kelly")
    assert out["count"] == 1
    out = await certificates.count_certificates(object(), vendor_name_contains="the vendor")
    assert out["count"] == 0


def test_the_per_row_filter_takes_every_rung_but_close_spelling():
    f = certificates._row_matches_count_filters
    assert f({"vendor_name": "Acme Facilities Ltd"}, vendor_name_contains="facilities acme")
    assert not f({"vendor_name": "Acme Facilities Ltd"}, vendor_name_contains="the vendor")


@pytest.mark.asyncio
async def test_list_certificates_filters_by_the_ladder(monkeypatch):
    class _Session:
        async def execute(self, q):
            class _R:
                def scalars(self_inner):
                    return self_inner

                def all(self_inner):
                    return [SimpleNamespace(raw_metadata={}, d=dict(r)) for r in ROWS]
            return _R()

    async def _enrich(session, rows):
        return rows

    monkeypatch.setattr(certificates, "cert_to_dict", lambda r: r.d)
    monkeypatch.setattr(certificates, "_enrich_certificate_rows", _enrich)
    out = await certificates.list_certificates(_Session(), vendor_name="facilities acme")
    assert _ids(out) == ["c1", "c2"]
