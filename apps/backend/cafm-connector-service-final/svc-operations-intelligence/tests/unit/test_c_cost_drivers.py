"""Cost attribution: what it ranks on, and what it refuses to fold into the ranking."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from src.engines.energy import cost_drivers as CD

BID = "a0000000-0000-0000-0000-00000000000a"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Serves each query in turn: the per-asset rank, then the unattributed total."""

    def __init__(self, *responses):
        self._responses = list(responses)

    async def execute(self, *_a, **_k):
        return _Rows(self._responses.pop(0) if self._responses else [])


def _shape(**over):
    base = {
        "assets": {"exists": True, "key": "asset_id", "code": "asset_code",
                   "name": "asset_name", "has_building": True},
        "work_orders": {"exists": True, "key": "work_order_id", "code": "wo_code",
                        "asset": "asset_id", "asset_is_key": True, "has_building": True,
                        "title": "title", "status": "status"},
        "invoice_lines": {"exists": True, "code": "wo_code", "has_delta": True,
                          "has_total": True},
        "missing": [], "can_attribute": True,
    }
    base.update(over)
    return base


def _row(code, name, wos, lines, billed, over, flagged=0, no_delta=0):
    return {"asset_id": f"id-{code}", "asset_code": code, "asset_name": name,
            "work_orders": wos, "lines": lines, "billed": Decimal(str(billed)),
            "over_contract": Decimal(str(over)), "flagged_lines": flagged,
            "lines_without_delta": no_delta}


def _run(monkeypatch, rank_rows, unattributed=None, shape=None):
    async def fake_shape(session):
        return shape or _shape()

    monkeypatch.setattr(CD, "chain_shape", fake_shape)
    s = _Session(rank_rows, [unattributed] if unattributed else [])
    return asyncio.run(CD.building_cost_drivers(s, BID))


def test_the_building_total_includes_spend_no_asset_can_be_blamed_for(monkeypatch):
    """Unattributed lines are real money. Leaving them out of the total would understate
    what the building cost."""
    out = _run(
        monkeypatch,
        [_row("LIFT-002", "Passenger lift 2", 3, 3, 3830, 1580, flagged=2)],
        unattributed={"billed": Decimal("310"), "over_contract": Decimal("40"), "lines": 1},
    )
    assert out["totals"]["billed"] == 4140.00
    assert out["totals"]["over_contract"] == 1620.00
    assert out["totals"]["lines"] == 4


def test_unattributed_spend_sits_outside_the_ranking(monkeypatch):
    """Spreading it across the assets would invent an attribution nobody recorded."""
    out = _run(
        monkeypatch,
        [_row("LIFT-002", "Passenger lift 2", 1, 1, 100, 10)],
        unattributed={"billed": Decimal("310"), "over_contract": Decimal("40"), "lines": 1},
    )
    assert [a["asset_code"] for a in out["assets"]] == ["LIFT-002"]
    assert out["assets"][0]["billed"] == 100.00, "untouched by the unattributed spend"
    assert out["unattributed"]["billed"] == 310.00


def test_the_worst_asset_is_the_one_furthest_over_not_the_biggest_spender(monkeypatch):
    """The biggest spender is usually the biggest asset, which tells you nothing. The gap
    against contract is the part somebody can act on."""
    out = _run(monkeypatch, [
        _row("LIFT-002", "Passenger lift 2", 3, 3, 3830, 1580),
        _row("CHIL-01", "Chiller 1", 1, 1, 9000, 0),
    ])
    assert out["worst"]["asset_code"] == "LIFT-002"
    assert out["worst"]["over_contract"] == 1580.00


def test_a_building_that_spends_a_lot_and_is_over_nothing_is_not_a_problem(monkeypatch):
    out = _run(monkeypatch, [_row("CHIL-01", "Chiller 1", 4, 9, 22000, 0)])
    assert out["totals"]["billed"] == 22000.00
    assert out["totals"]["over_contract"] == 0.00
    assert out["worst"]["over_contract"] == 0.00


def test_lines_never_scored_against_a_contract_are_disclosed(monkeypatch):
    """Their overrun is unknown, not zero. A rank built on the rest has to say so."""
    out = _run(monkeypatch, [
        _row("LIFT-002", "Passenger lift 2", 2, 5, 900, 120, no_delta=3),
        _row("AHU-004", "Air handler 4", 1, 2, 300, 0, no_delta=1),
    ])
    assert out["lines_without_delta"] == 4


def test_an_empty_building_answers_zero_rather_than_nothing(monkeypatch):
    out = _run(monkeypatch, [])
    assert out["ok"] is True
    assert out["assets"] == [] and out["worst"] is None
    assert out["totals"]["billed"] == 0.0


# ── what it can and cannot join ──────────────────────────────────────────────


def test_a_database_that_cannot_attribute_says_which_part_is_missing(monkeypatch):
    shape = _shape(
        work_orders={"exists": True, "key": "work_order_id", "code": "wo_code",
                     "asset": None, "asset_is_key": False, "has_building": True,
                     "title": "title", "status": "status"},
        missing=["work_orders has no asset column — spend cannot be attributed to plant"],
        can_attribute=False,
    )
    out = _run(monkeypatch, [], shape=shape)
    assert out["ok"] is False
    assert "no asset column" in out["missing"][0]
    assert out["assets"] == []


def test_chain_shape_names_every_missing_link():
    async def go():
        class _S:
            async def execute(self, *_a, **_k):
                return _Rows([])           # invoice_lines has no columns

        async def fake_graph(session, **_):
            return {
                "assets": {"exists": True, "key": "asset_id", "columns": {"asset_id"}},
                "work_orders": {"exists": True, "key": "work_order_id",
                                "columns": {"work_order_id", "wo_code"}},
            }

        import src.engines.energy.cost_drivers as M
        orig = M.graph_shape
        M.graph_shape = fake_graph
        try:
            return await M.chain_shape(_S())
        finally:
            M.graph_shape = orig

    shape = asyncio.run(go())
    joined = " | ".join(shape["missing"])
    assert "assets.building_id" in joined
    assert "work_orders.building_id" in joined
    assert "no asset column" in joined
    assert "invoice_lines is absent" in joined
    assert shape["can_attribute"] is False


def test_a_work_order_pointing_at_an_asset_by_code_joins_differently_than_by_key():
    """asset_id holds the asset's key, asset_code holds its reference. Guessing wrong
    silently matches nothing, so the shape records which it is."""
    cols = {"work_order_id", "wo_code", "asset_code", "building_id"}
    assert CD._pick(cols, CD._WO_ASSET_COLS) == "asset_code"
    cols2 = {"work_order_id", "wo_code", "asset_id", "building_id"}
    assert CD._pick(cols2, CD._WO_ASSET_COLS) == "asset_id"


def test_money_survives_the_trip_out_of_the_database():
    assert CD._num(Decimal("1580.55")) == 1580.55
    assert CD._num(None) == 0.0
    assert CD._num("not money") == 0.0
