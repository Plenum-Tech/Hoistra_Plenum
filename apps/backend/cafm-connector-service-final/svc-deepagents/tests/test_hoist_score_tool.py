"""The chat's Hoist Score is the Home tile's Hoist Score.

7 Oct 2026: "What is the hoist score of each building" answered "Not recorded" for every
building - it read buildings.hoist_score, a recorded override that is usually empty - while
Home showed 31. get_hoist_score reads what the tile reads and works out the tile's figure the
tile's way: the mean of its four bars, maintenance not drawn.
"""
from __future__ import annotations

import asyncio

from src.agents import energy_intelligence_agent as E


class _Resp:
    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


def _bar(key, covered, of=4):
    return {"key": key, "covered": covered, "of": of,
            "pct": None if covered is None else int(round(100.0 * covered / of)),
            "missing_buildings": [], "missing_count": None if covered is None else of - covered}


def _call(monkeypatch, body):
    seen = {}

    async def fake(method, base, path, **kw):
        seen.update(path=path, params=kw.get("params"))
        return _Resp(body)
    monkeypatch.setattr(E, "_request", fake)
    return asyncio.run(E.get_hoist_score.ainvoke({})), seen


def test_the_home_figure_is_the_mean_of_the_four_drawn_bars(monkeypatch):
    body = {"ok": True, "buildings": 4, "score": 30,
            "domains": [_bar("assets", 1), _bar("compliance", 3), _bar("contracts", 0),
                        _bar("energy", 1), _bar("maintenance", 1)],
            "rows": [{"name": "B-301", "score": 60, "covered": ["assets", "compliance", "energy"],
                      "missing": ["contracts", "maintenance"]}]}
    out, seen = _call(monkeypatch, body)
    assert seen["path"] == "/api/energy/hoist-score" and seen["params"]["include_rows"] == "true"
    assert out["home_tile"] == 31                       # (0 + 25 + 25 + 75) / 4, as on Home
    assert [b["key"] for b in out["bars"]][-1] == "maintenance" and "domains" not in out
    assert out["rows"][0]["missing"] == ["contracts", "maintenance"]


def test_a_bar_with_no_source_is_left_out_not_counted_as_zero(monkeypatch):
    body = {"ok": True, "domains": [_bar("assets", 2), _bar("compliance", None),
                                    _bar("contracts", None), _bar("energy", None)]}
    out, _ = _call(monkeypatch, body)
    assert out["home_tile"] == 50


def test_nothing_counted_is_none_not_zero(monkeypatch):
    out, _ = _call(monkeypatch, {"ok": True, "domains": [_bar("assets", None)]})
    assert out["home_tile"] is None


def test_the_tool_is_on_the_energy_agent():
    assert "get_hoist_score" in [t.name for t in E.ENERGY_INTELLIGENCE_TOOLS]
