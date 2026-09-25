"""A migrated asset finds its building, and a re-imported one is not a twin.

On 21 Sep 2026 migration d44fe166 wrote cmms_clean_test.xlsx into plenum_agent. The workbook
said A-001 sits at S-01, the Sites sheet said S-01 is Bishopsgate Tower, and plenum_cafm had a
building of that name — yet the writer dropped the site reference on purpose and wrote ten
assets with no building. Ten assets from a June import already existed under the same codes
(stored in `id`, no asset_code), each linked to a building, so every join that matched on
code returned both copies: one with its building and one "not recorded".

These tests hold the resolution to what the data actually looked like. The module under test
is pure; the database is a dict here. Loaded by path so no service import chain is needed.
"""
import asyncio
import importlib.util
import os

_HERE = os.path.dirname(__file__)
_SPEC = importlib.util.spec_from_file_location(
    "building_link", os.path.join(_HERE, "..", "src", "graph", "nodes", "building_link.py")
)
bl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bl)

ORG = "00000000-0000-0000-0000-000000000001"
BISHOPSGATE = "a9c0c91b-51bc-430f-9430-02a3023ccd82"
RIVERSIDE = "927a30f6-9314-4e01-8887-af783209a50d"
GARDEN = "1111aaaa-2222-4bbb-8ccc-dddddddddddd"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakeDb:
    """buildings + sites as the resolver queries them; records every read."""

    def __init__(self, buildings, sites):
        self.buildings = buildings  # [(id, name, code, site_id)]
        self.sites = sites          # [(site_id, site_code, site_name, name, building_name)]
        self.calls = []

    async def fetch(self, sql, params):
        self.calls.append(sql)
        if ".buildings" in sql:
            names = set(params["names"])
            return [(b[0],) for b in self.buildings
                    if b[1].lower() in names or (b[2] or "").lower() in names or (b[3] or "").lower() in names]
        if ".sites" in sql:
            k = params["k"]
            out = []
            for s in self.sites:
                if k in {(s[0] or "").lower(), (s[1] or "").lower(), (s[2] or "").lower(), (s[3] or "").lower()}:
                    out.append((s[2] or "", s[4] or "", s[3] or ""))
            return out
        return []


DB = FakeDb(
    buildings=[(BISHOPSGATE, "Bishopsgate Tower", "B-01", None),
               (RIVERSIDE, "Riverside Campus", "B-02", None),
               (GARDEN, "Garden Square", "B-10", "S0010")],
    sites=[("S-01", None, "Bishopsgate Tower", None, None),
           ("S-10", None, "Garden Square", None, None),
           ("B-006", None, "Riverside Court", None, None)],
)


# ── what in a row names a building ──────────────────────────────────────────────────────

def test_the_hint_is_the_site_reference_the_workbook_carried():
    assert bl.building_hint({"asset_code": "A-001", "site_ref": "S-01"}) == "S-01"
    # after the T2 mapping renamed it, the reference sits under site_id — still a hint
    assert bl.building_hint({"asset_code": "A-001", "site_id": "S-01"}) == "S-01"
    assert bl.building_hint({"building_name": "Garden Square"}) == "Garden Square"


def test_a_row_that_already_has_a_real_building_id_needs_no_resolution():
    assert bl.building_hint({"building_id": GARDEN, "site_ref": "S-01"}) is None
    assert bl.building_hint({"asset_code": "A-001"}) is None
    assert bl.building_hint(None) is None


# ── the run's own Sites sheet ───────────────────────────────────────────────────────────

def test_the_sites_sheet_of_the_same_run_names_its_references():
    cleaned = {
        "Sites": [{"site_id": "S-02", "site_name": "Riverside Campus", "city": "Manchester"}],
        "Assets": [{"asset_code": "A-003", "site_ref": "S-02"}],
    }
    names = bl.site_names_from_run(cleaned, {"Sites": "sites", "Assets": "assets"})
    assert names == {"s-02": "Riverside Campus", "riverside campus": "Riverside Campus"}
    # an unrouted sheet called Sites counts too; anything else does not
    assert bl.site_names_from_run({"sites": [{"id": "X1", "name": "Tech Park"}]}, {}) == {"x1": "Tech Park", "tech park": "Tech Park"}
    assert bl.site_names_from_run({"Vendors": [{"site_id": "S-02", "site_name": "no"}]}, {"Vendors": "vendors"}) == {}


# ── resolution ──────────────────────────────────────────────────────────────────────────

def test_a_site_reference_resolves_through_the_run_sheet_to_the_building_of_that_name():
    r = bl.BuildingResolver(DB.fetch, ORG, site_names={"s-01": "Bishopsgate Tower"})
    assert run(r.resolve("S-01")) == BISHOPSGATE


def test_a_reference_the_run_does_not_explain_resolves_through_the_sites_table():
    r = bl.BuildingResolver(DB.fetch, ORG)
    assert run(r.resolve("S-10")) == GARDEN
    # a building's own code or name resolves directly
    assert run(r.resolve("B-02")) == RIVERSIDE
    assert run(r.resolve("bishopsgate tower")) == BISHOPSGATE


def test_nothing_or_more_than_one_thing_is_no_link_and_the_ambiguity_is_kept():
    db = FakeDb(buildings=[("id-a", "Tower", "T1", None), ("id-b", "Tower", "T2", None)], sites=[])
    r = bl.BuildingResolver(db.fetch, ORG)
    assert run(r.resolve("Tower")) is None
    assert r.ambiguous == ["tower"]
    assert run(r.resolve("S-99")) is None
    assert run(r.resolve("")) is None and run(r.resolve(None)) is None


def test_each_distinct_hint_is_read_at_most_twice_and_then_remembered():
    db = FakeDb(DB.buildings, DB.sites)
    r = bl.BuildingResolver(db.fetch, ORG)

    async def go():
        for _ in range(50):
            await r.resolve("S-10")
            await r.resolve("s-10 ")
            await r.resolve("Nowhere")
    run(go())
    # S-10: buildings miss, sites hit, buildings hit = 3 reads; Nowhere: buildings miss, sites miss = 2
    assert r.reads == 5
    assert len(db.calls) == 5


# ── merging instead of duplicating ──────────────────────────────────────────────────────

def test_the_match_code_is_asset_code_or_a_code_stored_in_id():
    assert bl.asset_match_code({"asset_code": "A-001", "id": "4c59e90f-6050-4aba-b1d5-56fc0882fd17"}) == "A-001"
    assert bl.asset_match_code({"id": "A-001"}) == "A-001"
    assert bl.asset_match_code({"id": "4c59e90f-6050-4aba-b1d5-56fc0882fd17"}) is None
    assert bl.asset_match_code({}) is None


def test_the_merge_writes_the_import_over_the_existing_row_but_never_its_identity_or_building():
    filtered = {"id": "fresh-uuid", "organization_id": ORG, "asset_code": "A-001", "asset_name": "A-001",
                "model": "AHU-9000", "building_id": BISHOPSGATE, "created_at": "2026-09-21", "notes": None}
    sql, params = bl.build_asset_merge_update("plenum_cafm", filtered, "A-001")
    assert sql.startswith("UPDATE plenum_cafm.assets SET ")
    assert "id = :id" not in sql and "organization_id" not in sql and "created_at" not in sql
    assert "asset_code = :asset_code" in sql and "model = :model" in sql
    assert "building_id = COALESCE(building_id, :building_id)" in sql
    assert "notes" not in sql, "a null in the import does not blank a value the row has"
    assert sql.endswith("WHERE id::text = :_existing_id") and "updated_at = now()" in sql
    assert params["_existing_id"] == "A-001" and params["building_id"] == BISHOPSGATE
    assert "id" not in params and "created_at" not in params


def test_the_lookup_sql_matches_either_way_the_code_was_stored():
    sql = bl.ASSET_LOOKUP_SQL.format(schema="plenum_cafm")
    assert "asset_code = :code OR id::text = :code" in sql and "organization_id::text = :org" in sql
    sql2 = bl.ASSET_BUILDING_SQL.format(schema="plenum_cafm")
    assert "building_id IS NOT NULL" in sql2 and "id::text = :ref OR asset_code = :ref" in sql2
