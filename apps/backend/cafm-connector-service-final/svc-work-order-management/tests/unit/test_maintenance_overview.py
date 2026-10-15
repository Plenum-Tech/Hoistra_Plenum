"""The four cards across the top of the Maintenance page come back, every one.

From 4 Oct 2026 (4fd3cd7) to 6 Oct the overview raised NameError on every call: the statutory
marking moved into decisions() and the lookup it used went with it, but one line still named it.
The page showed "no figure on record" on all four cards while the decisions list below them,
a different read, kept loading. Nothing ran overview() end to end, so nothing caught it.
"""
from __future__ import annotations

import asyncio

from src.services import maintenance as mx

DECISIONS = [
    {"id": "d1", "state": "To raise", "statutory": True, "statutory_certificate": {"type": "EICR"}},
    {"id": "d2", "state": "Blocked", "statutory": False},
    {"id": "d3", "state": "To raise", "statutory": False},
]


def _stub(monkeypatch, *, statutory_lookup):
    async def decisions(session, *, building_ids, limit):
        return {"decisions": DECISIONS, "total": 3, "by_state": {"To raise": 2, "Blocked": 1}}

    async def panel(session, *, building_ids):
        return {"cards": {"unconverted_recommendations": {"count": 4, "answerable": True, "reason": None}},
                "corpus": {"reports": 7, "since": "2026-01-01"}}

    async def ppm(session, *, building_ids, year_to_date):
        return {"summary": {"completion_pct": 82.5, "done": 33, "plan": 40, "missed": 2, "reports": 30}}

    async def statutory_assets(session, *, building_ids):
        return statutory_lookup

    async def last_read(session, *, building_ids):
        return None

    monkeypatch.setattr(mx, "decisions", decisions)
    monkeypatch.setattr(mx.ii, "panel", panel)
    monkeypatch.setattr(mx, "ppm_health_by_contract", ppm)
    monkeypatch.setattr(mx, "statutory_assets", statutory_assets)
    monkeypatch.setattr(mx, "last_inspection_read", last_read)


def test_the_overview_returns_all_four_cards_with_their_figures(monkeypatch):
    _stub(monkeypatch, statutory_lookup={"asset:a1": {"type": "EICR"}})
    out = asyncio.run(mx.overview(None, building_ids=None))
    cards = out["cards"]
    assert set(cards) == {"decisions_owed", "statutory", "recommendations_unconverted", "ppm_to_plan"}
    assert (cards["decisions_owed"]["value"], cards["decisions_owed"]["blocked"], cards["decisions_owed"]["to_raise"]) == (3, 1, 2)
    assert cards["statutory"]["value"] == 1 and cards["statutory"]["answerable"] is True
    assert [d["id"] for d in cards["statutory"]["decisions"]] == ["d1"]
    assert cards["recommendations_unconverted"]["value"] == 4
    assert cards["ppm_to_plan"]["value"] == 82.5


def test_statutory_is_unanswerable_only_when_no_certificate_could_be_read_and_some_were_marked(monkeypatch):
    # Marked decisions with an empty lookup is a contradiction the card must not paper over.
    _stub(monkeypatch, statutory_lookup={})
    assert asyncio.run(mx.overview(None, building_ids=None))["cards"]["statutory"]["answerable"] is False
