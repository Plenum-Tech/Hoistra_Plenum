"""Document search finds a vendor's (or an asset's) documents however the name is written (29 Oct 2026).

linked_documents matched a vendor with ILIKE '%typed%' — the whole phrase, in order — on the vendor
register, the certificates and the document's title and file name. "Acme contractor", "facilities
acme", "Gough & Kelly" and "RT5 Crossrail" (the vendor is "RT5, Crossrail Services") found no
document, and "Acme Facilites" (one letter wrong) found nothing at all. An asset matched only its
code exactly, so "ACSDL07" missed "ACS-DL-07".

Every document returned is still the caller's company's (the register scope is unchanged); what
the phrase may match is widened by the exact forms, the phrase without its kind word, every word
in any order — and, only when nothing at all was found, the vendor's closest spelling among the
company's own vendors.
"""
from __future__ import annotations

import re
import uuid

import pytest

from src.engines import document_search as ds

COLS = {("contract_sla_parameters", "document_id"), ("contract_sla_parameters", "organization_id"),
        ("compliance_certificates", "document_id"), ("compliance_certificates", "organization_id"),
        ("vendors", "organization_id"), ("assets", "asset_id")}


def _pg_like(pattern: str, value: str) -> bool:
    rx = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    return re.match(rx, value, re.I | re.S) is not None


def _names(vendor: str, stored: str) -> bool:
    """The predicate vendor_name_sql builds, evaluated here by its parameters."""
    p: dict = {}
    sql = ds.vendor_name_sql("v.vendor_name", vendor, p)
    if sql == "FALSE":
        return False
    if _pg_like(p["ds_vendor"], stored):
        return True
    if "ds_vendor_core" in p and _pg_like(p["ds_vendor_core"], stored):
        return True
    if re.sub(r"[^a-z0-9]+", "", stored.lower()) in p.get("ds_vendor_compacts", []):
        return True
    words = [v for k, v in sorted(p.items()) if k.startswith("ds_vendor_w")]
    return bool(words) and all(re.search(w, stored, re.I) for w in words)


@pytest.mark.parametrize("typed, stored", [
    ("Acme", "Acme Facilities Ltd"),
    ("Acme contractor", "Acme Facilities Ltd"),
    ("facilities acme", "Acme Facilities Ltd"),
    ("Gough & Kelly", "Gough and Kelly Ltd"),
    ("RT5 Crossrail", "RT5, Crossrail Services"),
    ("facilities acme", "2026_Acme_Facilities_contract.pdf"),
])
def test_the_vendor_is_matched_however_it_is_written(typed, stored):
    assert _names(typed, stored), (typed, stored)


def test_the_kind_of_record_alone_names_no_vendor():
    assert not _names("the vendor", "Acme Facilities Ltd")
    assert not _names("contractors", "Acme Contractors Ltd")


def test_what_is_added_never_cuts_a_number():
    """ILIKE '%Acme 7%' still matches "Acme 70 Ltd" as it always did (the phrase as typed is left
    as it was); nothing added here does: a number is compared whole."""
    p: dict = {}
    ds.vendor_name_sql("v.vendor_name", "Acme 7", p)
    stored = "Acme 70 Ltd"
    words = [v for k, v in sorted(p.items()) if k.startswith("ds_vendor_w")]
    assert words and not all(re.search(w, stored, re.I) for w in words)
    assert re.sub(r"[^a-z0-9]+", "", stored.lower()) not in p["ds_vendor_compacts"]


@pytest.mark.asyncio
async def test_a_close_spelling_is_tried_only_when_nothing_was_found(monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def cols(_s):
        return COLS

    async def grab(_s, sql, params):
        seen.append((sql, dict(params)))
        if "AS vendor_name" in sql:                     # the candidate read for the close spelling
            assert params.get("ds_org")                 # the company's own vendors only
            return [{"vendor_name": "Acme Facilities Ltd"}, {"vendor_name": "Apex Mechanical"}]
        if params.get("ds_vendor") == "%Acme Facilities Ltd%":
            return [{"document_id": "d1", "title": "Acme contract", "file_name": "acme.pdf",
                     "doc_type": "contract", "building_id": None, "sensitivity": None, "chunks": 0}]
        return []

    monkeypatch.setattr(ds, "_columns", cols)
    monkeypatch.setattr(ds, "_grab_rows", grab)
    org = uuid.uuid4()
    docs = await ds.linked_documents(None, organization_id=org, building_ids=None, vendor="Acme Facilites Ltd")
    assert [d["document_id"] for d in docs] == ["d1"]
    assert docs[0]["vendor_read_as"] == "Acme Facilities Ltd"

    seen.clear()

    async def grab_found(_s, sql, params):
        seen.append((sql, dict(params)))
        return [{"document_id": "d2", "title": "x", "file_name": "x.pdf", "doc_type": None,
                 "building_id": None, "sensitivity": None, "chunks": 0}]

    monkeypatch.setattr(ds, "_grab_rows", grab_found)
    await ds.linked_documents(None, organization_id=org, building_ids=None, vendor="Acme")
    assert len(seen) == 1                                # found: no close-spelling read


def test_an_asset_code_without_its_hyphens_is_the_asset(monkeypatch):
    import asyncio

    seen: list[dict] = []

    async def cols(_s):
        return COLS | {("asset_documents", "document_id"), ("asset_documents", "asset_id")}

    async def grab(_s, sql, params):
        seen.append(dict(params, _sql=sql))
        return [{"document_id": "d3"}]

    monkeypatch.setattr(ds, "_columns", cols)
    monkeypatch.setattr(ds, "_grab_rows", grab)
    asyncio.run(ds.linked_documents(None, organization_id=uuid.uuid4(), building_ids=None, asset="ACSDL07"))
    assert "acsdl07" in seen[0]["ds_asset_compacts"]
    assert "regexp_replace(lower(a.asset_code::text)" in seen[0]["_sql"]
