"""Graph ingest — how a resolution becomes a link, and what it refuses to link. No DB."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.engines.energy import graph_ingest
from src.engines.energy.building_resolver import building_keys


def _index():
    return [
        building_keys({"building_id": "u-1", "name": "Bishopsgate Tower",
                       "building_code": "BT-01", "site_id": "S-01",
                       "site_name": "Bishopsgate Estate"}),
        building_keys({"building_id": "u-2", "name": "Riverside North Wing",
                       "site_id": "S-02", "site_name": "Riverside Campus"}),
        building_keys({"building_id": "u-3", "name": "Riverside Lab Block",
                       "site_id": "S-02", "site_name": "Riverside Campus"}),
    ]


def _resolve(monkeypatch, **kwargs):
    async def fake_index(session, **_):
        return _index()

    monkeypatch.setattr(graph_ingest.building_resolver, "load_building_index", fake_index)
    return asyncio.run(graph_ingest.resolve_building_for(SimpleNamespace(), **kwargs))


def test_a_named_building_becomes_a_link_with_its_reason(monkeypatch):
    out = _resolve(monkeypatch, building_name="Bishopsgate Tower")
    assert out["building_id"] == "u-1"
    assert out["outcome"] == "resolved" and out["reason"] == "name"


def test_a_reference_resolves_on_the_code(monkeypatch):
    out = _resolve(monkeypatch, building_reference="BT-01")
    assert out["building_id"] == "u-1" and out["reason"] == "building_code"


def test_an_ambiguous_document_is_never_linked(monkeypatch):
    """The certificate still ingests, but with no building_id — a link written on an
    ambiguous match would misstate two buildings' obligations at once."""
    out = _resolve(monkeypatch, site_id="S-02")
    assert out["building_id"] is None
    assert out["outcome"] == "review" and out["reason"] == "site_has_several_buildings"


def test_an_unknown_building_is_not_linked(monkeypatch):
    out = _resolve(monkeypatch, building_name="Somewhere Else")
    assert out["building_id"] is None and out["outcome"] == "unmatched"


def test_a_site_with_one_building_resolves_through_it(monkeypatch):
    out = _resolve(monkeypatch, site_id="S-01")
    assert out["building_id"] == "u-1" and out["reason"] == "site_sole_building"


def test_an_unavailable_index_does_not_break_the_ingest(monkeypatch):
    """A failure here must leave the certificate ingestible, not raise into the pipeline."""
    async def boom(session, **_):
        raise RuntimeError("database gone")

    monkeypatch.setattr(graph_ingest.building_resolver, "load_building_index", boom)
    out = asyncio.run(
        graph_ingest.resolve_building_for(SimpleNamespace(), building_name="Bishopsgate Tower")
    )
    assert out["building_id"] is None
    assert out["reason"] == "index_unavailable" and out["outcome"] == "unmatched"


def test_record_document_is_skipped_cleanly_when_the_table_is_absent(monkeypatch):
    async def no_documents(session, **_):
        return {"documents": {"exists": False, "key": None, "columns": set()}}

    monkeypatch.setattr(graph_ingest, "graph_shape", no_documents)
    out = asyncio.run(graph_ingest.record_document(SimpleNamespace(), document_id="d-1"))
    assert out["created"] is False and "skipped" in out


def test_attach_reports_both_halves(monkeypatch):
    async def fake_index(session, **_):
        return _index()

    async def fake_doc(session, **kw):
        return {"document_id": kw.get("document_id") or "generated", "created": True}

    monkeypatch.setattr(graph_ingest.building_resolver, "load_building_index", fake_index)
    monkeypatch.setattr(graph_ingest, "record_document", fake_doc)
    out = asyncio.run(
        graph_ingest.attach_to_graph(
            SimpleNamespace(), document_id="d-9", building_name="Bishopsgate Tower",
            doc_type="compliance_certificate",
        )
    )
    assert out["building_id"] == "u-1"
    assert out["building_link_reason"] == "name"
    assert out["document_id"] == "d-9" and out["document_created"] is True
