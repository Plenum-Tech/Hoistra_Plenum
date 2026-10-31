from contextlib import asynccontextmanager

import pytest

from src.api.homework_identity import TestPrincipal
from src.services.ingestion_hw1.service import IngestionService


class MemoryRepo:
    def __init__(self):
        class Session:
            async def execute(self,*args,**kwargs): pass
        self.session=Session()
        self.tables = {name: {} for name in ("records", "sources", "proposals", "history")}
        self.tables["records"]["asset-1"] = {
            "id": "asset-1", "kind": "asset_summary", "org": "org-1", "buildings": ["b-1"],
            "data": {"fields": {"asset_id": "chiller-1", "condition_score": 2,
                                  "condition_observation": "corrosion"},
                     "adapter_key": "chiller-1",
                     "source_id": "source-original", "version": 1, "accepted": True},
        }

    async def list(self, table, kind=None):
        rows = list(self.tables[table].values())
        return [row for row in rows if kind is None or row["kind"] == kind]

    async def scoped(self,table,scope,kinds,identifier=None):
        from fastapi import HTTPException
        rows=[r for r in self.tables[table].values() if r["kind"] in kinds
              and r["buildings"] and set(r["buildings"])<=set(scope)
              and (identifier is None or r["id"]==identifier)]
        if identifier is not None and not rows: raise HTTPException(404,"Record unavailable")
        return rows

    async def get(self, table, identifier, lock=False):
        from fastapi import HTTPException
        if identifier not in self.tables[table]:
            raise HTTPException(404, "Record unavailable")
        return self.tables[table][identifier]

    async def insert(self, table, kind, buildings, data, identifier=None):
        identifier = identifier or f"{table}-{len(self.tables[table])}"
        self.tables[table][identifier] = {"id": identifier, "kind": kind, "org": "org-1",
                                          "buildings": list(buildings), "data": data}
        return identifier

    async def update(self, table, row, data):
        self.tables[table][row["id"]]["data"] = data

    async def audit(self, *args, **kwargs):
        return None


class MemoryStore:
    def __init__(self):
        self.repo = MemoryRepo()

    @asynccontextmanager
    async def transaction(self, *_args, **_kwargs):
        yield self.repo


@pytest.mark.asyncio
async def test_derived_correction_retains_prior_version_and_retry_is_idempotent():
    store = MemoryStore()
    service = IngestionService(store)
    principal = TestPrincipal("pm-1", "property_manager", "org-1", ("b-1",))
    preview = await service.preview_correction(principal, "b-1", "asset-1",
        "Fictional independent inspection evidence.", {"condition_score": 3})
    result = await service.confirm_correction(principal, preview["proposal_id"], preview["preview_hash"],
        "Correct score from the retained inspection evidence.")
    assert result["outcome"] == "applied"
    assert result["version"] == 2
    assert store.repo.tables["records"]["asset-1"]["data"]["superseded"] is True
    current = store.repo.tables["records"][result["record_id"]]
    assert current["data"]["fields"]["condition_score"] == 3
    assert current["data"]["original_source_id"] == "source-original"
    history = store.repo.tables["history"]["correction-history-" + preview["proposal_id"]]["data"]
    assert history["before"]["condition_score"] == 2
    assert history["applied"]["condition_score"] == 3
    retry = await service.confirm_correction(principal, preview["proposal_id"], preview["preview_hash"],
        "Correct score from the retained inspection evidence.")
    assert retry["outcome"] == "already_applied"
    assert len(store.repo.tables["sources"]) == 1


@pytest.mark.asyncio
async def test_conflict_resolution_accepts_new_version_and_preserves_both_sources():
    store = MemoryStore()
    service = IngestionService(store)
    principal = TestPrincipal("pm-1", "property_manager", "org-1", ("b-1",))
    preview = await service.preview(principal, "b-1", "asset_summary",
        "New fictional inspection evidence.", {"asset_id": "chiller-1", "condition_score": 3,
        "condition_observation": "new inspection evidence"})

    result = await service.resolve_conflict(principal, preview["proposal_id"], preview["preview_hash"],
        "accept_incoming", "Newer signed inspection source supersedes the old summary.")

    assert result["outcome"] == "accepted_as_new_version"
    assert result["version"] == 2
    assert store.repo.tables["records"]["asset-1"]["data"]["superseded"] is True
    current = store.repo.tables["records"][result["record_id"]]
    assert current["data"]["fields"]["condition_score"] == 3
    assert current["data"]["supersedes_record_id"] == "asset-1"
    incoming = store.repo.tables["sources"][result["source_id"]]
    assert incoming["data"]["original"] == "New fictional inspection evidence."
    assert store.repo.tables["history"]["ingestion-conflict-history-"+preview["proposal_id"]]["data"]["previous_source_id"] == "source-original"
    assert store.repo.tables["proposals"][preview["proposal_id"]]["data"]["status"] == "conflict_resolved"
    retry = await service.resolve_conflict(principal, preview["proposal_id"], preview["preview_hash"],
        "accept_incoming", "Newer signed inspection source supersedes the old summary.")
    assert retry["outcome"] == "already_resolved"
    assert len(store.repo.tables["sources"]) == 1


@pytest.mark.asyncio
async def test_conflict_resolution_can_keep_current_while_retaining_candidate_evidence():
    store = MemoryStore()
    service = IngestionService(store)
    principal = TestPrincipal("pm-1", "property_manager", "org-1", ("b-1",))
    preview = await service.preview(principal, "b-1", "asset_summary",
        "Unselected fictional inspection evidence.", {"asset_id": "chiller-1", "condition_score": 1,
        "condition_observation": "unverified"})

    result = await service.resolve_conflict(principal, preview["proposal_id"], preview["preview_hash"],
        "keep_current", "Incoming source is not sufficiently verified.")

    assert result["outcome"] == "kept_current"
    assert store.repo.tables["records"]["asset-1"]["data"].get("superseded") is None
    assert store.repo.tables["sources"][result["source_id"]]["data"]["original"] == "Unselected fictional inspection evidence."
    assert result["source_id"] != store.repo.tables["records"]["asset-1"]["data"]["source_id"]
    assert store.repo.tables["proposals"][preview["proposal_id"]]["data"]["decision"] == "keep_current"
