"""plenum_cafm.assets.id is varchar on this deployment, uuid on another. plant_position()'s
name lookup cast the query PARAM to uuid[] and compared it against that column — Postgres
refuses "character varying = uuid" outright, 500ing the AE ratings tile on every request.
Comparing both sides as text works on either shape, the same fix already applied elsewhere
in this codebase for the identical mismatch (engines/auth/access.py's ASSETS_ON_BUILDINGS_SQL).
"""
from __future__ import annotations

import asyncio
import inspect
from uuid import uuid4

from src.engines.energy import chiller


def test_the_name_lookup_query_casts_to_text_not_uuid():
    src = inspect.getsource(chiller.plant_position)
    assert "CAST(:ids AS text[])" in src, "must not cast the id list to uuid[] against a possibly-varchar column"
    assert "id::text = ANY" in src, "the column side must be compared as text too"
    assert "CAST(:ids AS uuid[])" not in src, "the exact cast production 500'd on must be gone"


def test_params_passed_to_the_name_lookup_are_stringified(monkeypatch):
    """End-to-end through plant_position() with the DB layer stubbed: confirms the actual
    query executed at runtime carries the text[] cast and string params, not just the
    source text (belt-and-braces against a docstring/code drifting apart)."""
    aid = str(uuid4())
    captured = {}

    class FakeMappingsResult:
        def __init__(self, rows): self._rows = rows
        def mappings(self): return self
        def all(self): return self._rows

    class FakeScalarsResult:
        def __init__(self, ids): self._ids = ids
        def all(self): return [(i,) for i in self._ids]

    class FakeSession:
        def __init__(self):
            self.n = 0
        async def execute(self, stmt, params=None):
            self.n += 1
            if self.n == 1:
                # engines/energy/chiller.py's ORM select of ChillerDesignSpec.asset_id
                return FakeScalarsResult([aid])
            # the raw-SQL name lookup this fix targets
            captured["sql"] = str(getattr(stmt, "text", stmt))
            captured["params"] = params
            return FakeMappingsResult([])

    async def fake_assess(session, *, asset_id, window_days=14):
        return {"ok": True, "asset_id": aid, "actual_kw_per_rt": 0.7, "design_kw_per_rt": 0.6,
                "deviation_pct": 16.0, "breach": True, "window_days": window_days}

    monkeypatch.setattr(chiller, "assess", fake_assess)
    out = asyncio.run(chiller.plant_position(FakeSession(), building_ids=[uuid4()]))

    assert out["ok"] is True
    assert "uuid[]" not in captured["sql"]
    assert captured["params"]["ids"] == [aid]
    assert all(isinstance(x, str) for x in captured["params"]["ids"])
