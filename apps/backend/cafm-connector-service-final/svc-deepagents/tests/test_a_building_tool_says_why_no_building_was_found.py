"""A building tool tells "no building is visible to you" apart from "no building has that name" (25 Nov 2026).

Production traces, get_cost_savings / list_invoices / list_building_meter_readings:

* 12 Nov, several: building_name "Bishopsgate Tower" and "Ashgrove Court" came back
  {"error": "building_not_resolved", "reason": "no_buildings", "candidates": []} although both are on
  the register. "no_buildings" is svc-operations-intelligence's building resolver reading NO building
  at all for the caller (building_resolver.choose_building, an empty index): the company asked about
  holds none, or the user is allocated to none. The asker was a superadmin naming no company, so the
  read was of their own company, which holds no buildings (routes/workflow.py _resolve_acting_org).
  The tools passed it on as "No building matched that name", read as "that building does not exist",
  and went on asking the resolver five more times, each answer as empty as the first.
* 30 Oct: "Bishop Gate" -> no_match while "Bishopsgate Tower" exists. Fixed by 53029ed (the compound
  read in name_match.similarity); pinned here for get_cost_savings and list_invoices.
* 16 Nov: "Vasiliki House" -> ambiguous among four buildings of that same name. Right: four
  buildings answer to it, so all four come back and none is picked.

The fake resolver answers as choose_building did before its loose rungs: an exact normalised name,
then containment for names of five letters or more - the least it does today.
"""
from __future__ import annotations

import re

import pytest

from src.agents import contract_performance_agent as contracts
from src.agents import cost_savings_agent as savings
from src.agents import energy_intelligence_agent as energy

BISHOPSGATE = {"building_id": "b-bsg", "name": "Bishopsgate Tower"}
BISHOP_ST = {"building_id": "b-bst", "name": "Bishop Street Depot"}
ASHGROVE = {"building_id": "b-ash", "name": "Ashgrove Court"}
VASILIKI = [{"building_id": f"b-vas-{i}", "name": "Vasiliki House"} for i in range(1, 5)]


def _norm(v):
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip())
    return s[4:] if s.startswith("the ") else s


def _choose(register, params):
    if not register:
        return {"ok": True, "outcome": "unmatched", "reason": "no_buildings", "building_id": None,
                "candidates": []}

    def cand(b):
        return {"building_id": b["building_id"], "name": b["name"], "site_id": None}

    n = _norm(params.get("name"))
    if n:
        hits = [b for b in register if _norm(b["name"]) == n]
        if len(hits) == 1:
            return {"ok": True, "outcome": "resolved", "reason": "name",
                    "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
        if len(hits) > 1:
            return {"ok": True, "outcome": "review", "reason": "ambiguous_name",
                    "building_id": None, "candidates": [cand(b) for b in hits]}
        if len(n) >= 5:
            hits = [b for b in register if n in _norm(b["name"]) or _norm(b["name"]) in n]
            if len(hits) == 1:
                return {"ok": True, "outcome": "resolved", "reason": "name_contains",
                        "building_id": hits[0]["building_id"], "building": hits[0]["name"]}
            if len(hits) > 1:
                return {"ok": True, "outcome": "review", "reason": "ambiguous_name_contains",
                        "building_id": None, "candidates": [cand(b) for b in hits]}
    return {"ok": True, "outcome": "unmatched", "reason": "no_match", "building_id": None,
            "candidates": []}


class _Resp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class _Services:
    """The resolver over ``register``; the savings read, a meter summary and the invoice list."""

    def __init__(self, register, invoices=()):
        self.register, self.invoices = register, list(invoices)
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, method, base, path, **kwargs):
        params = dict(kwargs.get("params") or {})
        self.calls.append((path, params))
        if path.endswith("/buildings/resolve"):
            return _Resp(_choose(self.register, params))
        if path == "/api/value/savings":
            return _Resp({"ok": True, "open_jobs": [], "building_id": params.get("building_id")})
        if path.endswith("/meter-summary"):
            return _Resp({"ok": True, "meters": []})
        if path == "/api/contract-performance/invoices":
            rows = [r for r in self.invoices
                    if not params.get("building_id") or r["building_id"] == params["building_id"]]
            return _Resp({"ok": True, "invoices": rows})
        raise AssertionError(f"unexpected path {path}")

    def paths(self, suffix):
        return [p for p, _ in self.calls if p.endswith(suffix)]


@pytest.fixture
def services(monkeypatch):
    def make(register, invoices=()):
        svc = _Services(register, invoices)
        for mod in (energy, savings, contracts):
            monkeypatch.setattr(mod, "_request", svc)
        return svc
    return make


# ── an empty register is said to be empty ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["Bishopsgate Tower", "Ashgrove Court"])
async def test_cost_savings_on_an_empty_register_says_no_building_is_visible(services, name):
    svc = services([])
    out = await savings.get_cost_savings.ainvoke({"building_name": name, "period": "last_month"})
    assert out["error"] == "building_not_resolved" and out["reason"] == "no_buildings"
    assert "no building at all is visible" in out["guidance"].lower()
    assert "not evidence" in out["guidance"].lower()
    assert len(svc.paths("/buildings/resolve")) == 1, "an empty register was asked again and again"
    assert svc.paths("/api/value/savings") == []


async def test_invoices_on_an_empty_register_say_no_building_is_visible(services):
    svc = services([])
    out = await contracts.list_invoices.ainvoke({"building_name": "Ashgrove Court"})
    assert out["error"] == "building_not_resolved" and out["reason"] == "no_buildings"
    assert "no building at all is visible" in out["guidance"].lower()
    assert svc.paths("/api/contract-performance/invoices") == []


async def test_a_name_on_no_building_of_a_full_register_is_still_no_match(services):
    services([BISHOPSGATE, ASHGROVE])
    bid, problem = await energy._resolve_building("Moreland House", None)
    assert bid is None and problem["reason"] != "no_buildings"
    assert "no building at all is visible" not in problem["guidance"].lower()


# ── a close spelling reaches the building ───────────────────────────────────────────────────────

async def test_bishop_gate_finds_bishopsgate_tower_for_cost_savings(services):
    svc = services([BISHOPSGATE, BISHOP_ST, ASHGROVE])
    out = await savings.get_cost_savings.ainvoke({"building_name": "Bishop Gate", "period": "last_month"})
    assert out.get("error") is None, out
    assert [p for path, p in svc.calls if path == "/api/value/savings"][0]["building_id"] == "b-bsg"


async def test_bishop_gate_finds_bishopsgate_tower_for_invoices(services):
    services([BISHOPSGATE, BISHOP_ST, ASHGROVE], invoices=[
        {"invoice_ref": "INV-1", "building_id": "b-bsg", "building_name": "Bishopsgate Tower"},
        {"invoice_ref": "INV-2", "building_id": "b-bst", "building_name": "Bishop Street Depot"},
    ])
    out = await contracts.list_invoices.ainvoke({"building_name": "Bishop Gate"})
    assert out.get("error") is None, out
    assert [r["invoice_ref"] for r in out["invoices"]] == ["INV-1"]


# ── four buildings of one name ──────────────────────────────────────────────────────────────────

async def test_four_buildings_of_one_name_all_come_back_and_none_is_read(services):
    svc = services([*VASILIKI, ASHGROVE])
    out = await energy.list_building_meter_readings.ainvoke({"building_name": "Vasiliki House"})
    assert out["error"] == "building_not_resolved" and out["outcome"] == "review"
    assert {c["building_id"] for c in out["candidates"]} == {b["building_id"] for b in VASILIKI}
    assert "Do NOT substitute" in out["guidance"]
    assert svc.paths("/meter-summary") == []
