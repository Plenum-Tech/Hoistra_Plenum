"""restrict_by_site() narrowed a building-allocated person's energy rows to nothing.

``site_id`` is not always a site. ``energy_meters.site_id`` and ``energy_anomalies.site_id``
carry the BUILDING on this schema — meters.py:839 says so outright, market_profiles.py joins
``buildings b ON b.building_id = m.site_id``, and detection_coverage.py selects
``site_id::text AS building_id``. The first cut of restrict_by_site mapped that id through a
site-keyed map, missed every time, and returned an empty list: every meter and every anomaly
vanished for exactly the users the scoping was written to serve.

Both callers (api/routes/energy.py's /meters and /anomalies) pass rows from those two tables,
so the building reading is the one that matters; the site reading stays as the fallback.
"""
from __future__ import annotations

import asyncio

from src.engines.energy import buildings as bld_svc

B1 = "11111111-1111-4111-8111-111111111111"
B2 = "22222222-2222-4222-8222-222222222222"
SITE_WITH_TWO = "33333333-3333-4333-8333-333333333333"


class Scope:
    """Only the two members restrict_by_site touches."""

    def __init__(self, allowed, restricted=True):
        self._allowed = {str(a) for a in allowed}
        self.restricted = restricted

    def allows_building(self, building_id):
        if building_id is None:
            return False
        if not self.restricted:
            return True
        return str(building_id) in self._allowed


def _run(rows, scope, site_map=None, **kw):
    """site_to_buildings() hits the database; stub it so this stays a pure unit test."""
    calls = {"n": 0}

    async def fake_map(_session):
        calls["n"] += 1
        return site_map if site_map is not None else {}

    original = bld_svc.site_to_buildings
    bld_svc.site_to_buildings = fake_map
    try:
        out = asyncio.run(bld_svc.restrict_by_site(None, rows, scope, **kw))
    finally:
        bld_svc.site_to_buildings = original
    return out, calls["n"]


def test_a_building_id_in_site_id_is_matched_directly():
    """The regression: these rows carry a building, and the caller is allocated it."""
    rows = [{"id": "m1", "site_id": B1}, {"id": "m2", "site_id": B2}]
    out, _ = _run(rows, Scope([B1]))
    assert [r["id"] for r in out] == ["m1"], "the allocated building's meter must survive"


def test_a_building_the_caller_does_not_hold_is_still_dropped():
    rows = [{"id": "m1", "site_id": B1}, {"id": "m2", "site_id": B2}]
    out, _ = _run(rows, Scope([B2]))
    assert [r["id"] for r in out] == ["m2"]
    assert _run(rows, Scope([]))[0] == [], "an empty allocation sees nothing"


def test_the_direct_match_does_not_even_load_the_site_map():
    """The map is a full building read (limit=5000). A row that matches as a building must
    not pay for it — and with every row matching, the query never runs at all."""
    rows = [{"id": "m1", "site_id": B1}, {"id": "m2", "site_id": B1}]
    out, map_loads = _run(rows, Scope([B1]))
    assert len(out) == 2
    assert map_loads == 0


def test_a_genuine_site_id_still_resolves_through_the_map():
    """The fallback still works for a caller whose rows really are site-keyed."""
    rows = [{"id": "a1", "site_id": "site-a"}]
    out, map_loads = _run(rows, Scope([B1]), site_map={"site-a": [B1]})
    assert [r["id"] for r in out] == ["a1"]
    assert map_loads == 1


def test_a_site_holding_more_than_one_building_is_refused_not_guessed():
    """Unchanged rule: a site that cannot be attributed to exactly one building is dropped
    rather than assigned to whichever building happens to be first."""
    rows = [{"id": "a1", "site_id": SITE_WITH_TWO}]
    out, _ = _run(rows, Scope([B1]), site_map={SITE_WITH_TWO: [B1, B2]})
    assert out == []
    # A site with no buildings at all is equally unattributable.
    assert _run(rows, Scope([B1]), site_map={SITE_WITH_TWO: []})[0] == []


def test_an_unrestricted_caller_and_empty_input_are_untouched():
    rows = [{"id": "m1", "site_id": B1}, {"id": "m2", "site_id": B2}]
    out, map_loads = _run(rows, Scope([], restricted=False))
    assert out == rows, "an admin sees every row and pays for no extra query"
    assert map_loads == 0
    assert _run([], Scope([B1]))[0] == []
    assert asyncio.run(bld_svc.restrict_by_site(None, rows, None)) == rows


def test_a_row_with_no_id_at_all_is_dropped_not_admitted():
    rows = [{"id": "m1", "site_id": ""}, {"id": "m2"}, {"id": "m3", "site_id": None}]
    out, _ = _run(rows, Scope([B1]))
    assert out == [], "a row that cannot be attributed is never shown to a restricted caller"


def test_the_site_key_is_configurable_and_respected():
    rows = [{"id": "x", "building_id": B1, "site_id": B2}]
    out, _ = _run(rows, Scope([B1]), site_key="building_id")
    assert [r["id"] for r in out] == ["x"]
