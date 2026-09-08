"""Placing an invoice through the work it bills, and refusing to when the work disagrees.

An invoice rarely names a building — it names the work. That work is already on the graph,
which makes it better evidence than the address block on the letterhead, since the address
on an invoice is as often the vendor's own as the property's.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.engines.energy import graph_ingest
from src.engines.energy.building_resolver import building_keys

BID_A = "a0000000-0000-0000-0000-00000000000a"
BID_B = "a0000000-0000-0000-0000-00000000000b"


def _shape(*, wo_exists=True, wo_cols=("work_order_id", "wo_code", "building_id")):
    base = {"exists": False, "key": None, "columns": set()}
    return {
        "work_orders": {
            "exists": wo_exists, "key": "work_order_id", "columns": set(wo_cols),
        },
        "documents": {"exists": True, "key": "document_id", "columns": {"document_id"}},
        "buildings": dict(base),
        "sites": dict(base),
    }


def _patch(monkeypatch, *, rows, shape=None):
    async def fake_shape(session, **_):
        return shape or _shape()

    class _Result:
        def all(self):
            return [(r,) for r in rows]

    class _Session:
        def begin_nested(self):
            class _Ctx:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return _Ctx()

        async def execute(self, *_a, **_k):
            return _Result()

    monkeypatch.setattr(graph_ingest, "graph_shape", fake_shape)
    return _Session()


def test_work_orders_on_one_building_place_the_invoice(monkeypatch):
    s = _patch(monkeypatch, rows=[BID_A])
    out = asyncio.run(graph_ingest.building_from_work_orders(s, ["WO-100", "WO-101"]))
    assert out["building_id"] == BID_A
    assert out["reason"] == "work_orders_agree"


def test_work_orders_across_two_buildings_place_nothing(monkeypatch):
    """An invoice covering a campus is a real thing. Picking one of its buildings would put
    the whole invoice value against a building that only earned part of it."""
    s = _patch(monkeypatch, rows=[BID_A, BID_B])
    out = asyncio.run(graph_ingest.building_from_work_orders(s, ["WO-100", "WO-900"]))
    assert out["building_id"] is None
    assert out["reason"] == "work_orders_span_buildings"
    assert set(out["candidates"]) == {BID_A, BID_B}


def test_work_orders_that_are_themselves_unplaced_say_so(monkeypatch):
    s = _patch(monkeypatch, rows=[])
    out = asyncio.run(graph_ingest.building_from_work_orders(s, ["WO-404"]))
    assert out["building_id"] is None and out["reason"] == "work_orders_have_no_building"


def test_no_work_orders_is_distinct_from_unplaced_ones(monkeypatch):
    """Different problems: one invoice has no lines to go on, the other's lines lead
    nowhere. The remedies are not the same, so the reasons are not either."""
    s = _patch(monkeypatch, rows=[BID_A])
    assert asyncio.run(graph_ingest.building_from_work_orders(s, []))["reason"] == "no_work_orders"
    assert asyncio.run(
        graph_ingest.building_from_work_orders(s, ["  ", None])
    )["reason"] == "no_work_orders"


def test_a_deployment_whose_work_orders_predate_the_graph_is_reported_not_crashed(monkeypatch):
    """work_orders exists in every deployment and only gains building_id via the migration."""
    s = _patch(monkeypatch, rows=[], shape=_shape(wo_cols=("work_order_id", "wo_code")))
    out = asyncio.run(graph_ingest.building_from_work_orders(s, ["WO-100"]))
    assert out["building_id"] is None
    assert out["reason"] == "work_orders_not_on_the_graph"


def test_work_orders_with_no_recognisable_code_column(monkeypatch):
    s = _patch(monkeypatch, rows=[], shape=_shape(wo_cols=("work_order_id", "building_id")))
    out = asyncio.run(graph_ingest.building_from_work_orders(s, ["WO-100"]))
    assert out["reason"] == "work_orders_have_no_code_column"


# ── how the route composes with the rest of attach_to_graph ──────────────────


def _attach(monkeypatch, *, index, wo_result, **kwargs):
    async def fake_index(session, **_):
        return index

    async def fake_wo(session, codes):
        return wo_result

    async def fake_doc(session, **kw):
        return {"document_id": kw.get("document_id") or "generated", "created": True}

    monkeypatch.setattr(graph_ingest.building_resolver, "load_building_index", fake_index)
    monkeypatch.setattr(graph_ingest, "building_from_work_orders", fake_wo)
    monkeypatch.setattr(graph_ingest, "record_document", fake_doc)
    return asyncio.run(graph_ingest.attach_to_graph(SimpleNamespace(), **kwargs))


_INDEX = [building_keys({"building_id": BID_A, "name": "Bishopsgate Tower",
                         "building_code": "BT-01", "site_id": "S-01"})]


def test_the_document_naming_its_building_wins_over_its_line_items(monkeypatch):
    """A document naming its own building is more direct than an inference from what it
    bills, so the work-order route is only ever a fallback."""
    out = _attach(
        monkeypatch, index=_INDEX,
        wo_result={"building_id": BID_B, "reason": "work_orders_agree"},
        building_name="Bishopsgate Tower", work_order_codes=["WO-900"],
    )
    assert out["building_id"] == BID_A
    assert out["building_link_reason"] == "name"


def test_the_work_order_route_runs_when_nothing_else_placed_it(monkeypatch):
    out = _attach(
        monkeypatch, index=_INDEX,
        wo_result={"building_id": BID_B, "reason": "work_orders_agree"},
        work_order_codes=["WO-900"],
    )
    assert out["building_id"] == BID_B
    assert out["building_link_reason"] == "work_orders_agree"
    assert out["building_link_outcome"] == "resolved"


def test_a_campus_invoice_reports_the_useful_refusal(monkeypatch):
    """'These work orders are in two buildings' tells somebody what to do about it.
    'Nothing matched' does not, so the more informative reason is the one kept."""
    out = _attach(
        monkeypatch, index=_INDEX,
        wo_result={"building_id": None, "reason": "work_orders_span_buildings"},
        work_order_codes=["WO-100", "WO-900"],
    )
    assert out["building_id"] is None
    assert out["building_link_reason"] == "work_orders_span_buildings"
    assert out["building_link_outcome"] == "review"


def test_an_invoice_with_no_work_orders_keeps_the_resolver_reason(monkeypatch):
    out = _attach(
        monkeypatch, index=_INDEX,
        wo_result={"building_id": None, "reason": "no_work_orders"},
        building_name="Somewhere Else", work_order_codes=[],
    )
    assert out["building_link_reason"] == "no_match", "not overwritten by the WO route"


def test_the_document_is_still_recorded_when_no_building_is_found(monkeypatch):
    """The documents row is what the contracts and invoices views join on. Without it the
    building column is NULL forever, even once the building is known."""
    out = _attach(
        monkeypatch, index=_INDEX,
        wo_result={"building_id": None, "reason": "work_orders_have_no_building"},
        document_id="d-1", work_order_codes=["WO-404"],
    )
    assert out["building_id"] is None
    assert out["document_id"] == "d-1" and out["document_created"] is True
