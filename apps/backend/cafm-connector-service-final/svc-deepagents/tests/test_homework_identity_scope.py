from contextlib import asynccontextmanager

import pytest

from src.api.homework_identity import TestPrincipal, expand_portfolio_scope


class Repo:
    def __init__(self):
        self.portfolio = {"id": "portfolio-a", "kind": "portfolio", "org": "org-a",
            "buildings": ["a1", "a2"], "data": {"portfolio_id": "portfolio-a",
            "building_ids": ["a1", "a2", "a3"]}}
        self.buildings = [
            {"id": "a1", "kind": "building", "org": "org-a"},
            {"id": "a2", "kind": "building", "org": "org-a"},
            {"id": "a3", "kind": "building", "org": "org-a"},
        ]

    async def list(self, table, kind=None):
        rows = [self.portfolio] if kind == "portfolio" else self.buildings if kind == "building" else []
        return rows


class Store:
    def __init__(self):
        self.repo = Repo()

    @asynccontextmanager
    async def transaction(self, *_args, **_kwargs):
        yield self.repo


@pytest.mark.asyncio
async def test_portfolio_scope_expands_only_to_current_trusted_portfolio_sites(monkeypatch):
    monkeypatch.setenv("HW1_COMPLIANCE_ENABLED", "true")
    principal = TestPrincipal("pm-a", "portfolio_manager", "org-a", ("a1", "a2"), "portfolio-a")
    result = await expand_portfolio_scope(principal, Store)
    assert result.building_ids == ("a1", "a2", "a3")
    assert result.portfolio_id == "portfolio-a"


@pytest.mark.asyncio
async def test_portfolio_scope_fails_closed_on_foreign_or_dangling_membership(monkeypatch):
    monkeypatch.setenv("HW1_COMPLIANCE_ENABLED", "true")
    store = Store()
    store.repo.portfolio["data"]["building_ids"].append("foreign-building")
    principal = TestPrincipal("pm-a", "portfolio_manager", "org-a", ("a1", "a2"), "portfolio-a")
    with pytest.raises(ValueError, match="inconsistent"):
        await expand_portfolio_scope(principal, lambda: store)


@pytest.mark.asyncio
async def test_compliance_scope_refreshes_to_all_organization_buildings(monkeypatch):
    monkeypatch.setenv("HW1_COMPLIANCE_ENABLED", "true")
    principal = TestPrincipal("co-a", "compliance_officer", "org-a", ("a1", "a2"))
    result = await expand_portfolio_scope(principal, Store)
    assert result.building_ids == ("a1", "a2", "a3")
