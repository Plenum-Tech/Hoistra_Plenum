"""The building resolver reads the asking company's buildings only (29 Oct 2026).

GET /api/energy/buildings/resolve and POST /buildings/resolve-batch loaded EVERY company's
buildings (SELECT ... FROM plenum_cafm.buildings LIMIT 5000) and matched a name against them. With
names now matched however they are typed — words in any order, one letter wrong — that would hand a
caller another company's building, and list other companies' building names as candidates. The
index is read for the caller's company, and for a user allocated to buildings, those buildings.
A superadmin acting as no company keeps the platform-wide read, as everywhere else.
"""
import asyncio
from uuid import UUID

from src.engines.auth import access
from src.engines.energy import building_resolver as br

ORG = UUID("11111111-1111-4111-8111-111111111111")
B1 = UUID("00000000-0000-4000-8000-0000000b0101")


class _Res:
    def mappings(self):
        return self

    def all(self):
        return []


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self):
        self.calls = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params or {})))
        return _Res()


def _shape(monkeypatch):
    async def graph_shape(session):
        return {"buildings": {"exists": True, "key": "building_id",
                              "columns": {"building_id", "name", "building_code", "site_id", "organization_id"}},
                "sites": {"exists": False, "columns": set(), "key": "site_id"}}

    import src.engines.energy.building_rollup as rollup
    monkeypatch.setattr(rollup, "graph_shape", graph_shape)


def _scope(role="admin", org=ORG, buildings=None):
    return access.Scope(user_id=UUID(int=1), role=role, organization_id=org, building_ids=buildings, can_ingest=True)


def test_the_index_is_read_for_one_company(monkeypatch):
    _shape(monkeypatch)
    s = _Session()
    asyncio.run(br.load_building_index(s, scope=_scope()))
    sql, params = s.calls[0]
    assert "b.organization_id::text = :org" in sql and params["org"] == str(ORG)


def test_a_user_allocated_to_buildings_reads_only_those(monkeypatch):
    _shape(monkeypatch)
    s = _Session()
    asyncio.run(br.load_building_index(s, scope=_scope(role="user", buildings=(B1,))))
    sql, params = s.calls[0]
    assert "= ANY(:bids)" in sql and params["bids"] == [str(B1)]


def test_a_platform_superadmin_reads_every_company(monkeypatch):
    _shape(monkeypatch)
    s = _Session()
    asyncio.run(br.load_building_index(s, scope=_scope(role="superadmin", org=None)))
    sql, params = s.calls[0]
    assert ":org" not in sql and "bids" not in params


def test_a_caller_with_no_company_reads_nothing(monkeypatch):
    _shape(monkeypatch)
    s = _Session()
    assert asyncio.run(br.load_building_index(s, scope=_scope(role="admin", org=None))) == []
    assert s.calls == []


def test_both_routes_pass_the_callers_scope():
    import inspect

    from src.api.routes import energy

    for fn in (energy.resolve_building, energy.resolve_buildings_batch):
        assert "s" in inspect.signature(fn).parameters, fn.__name__
        assert "scope=s" in inspect.getsource(fn), fn.__name__
