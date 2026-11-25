"""An asset's documents are found through asset_documents, the table production links them by
(25 Nov 2026).

get_asset_documents took an asset's documents from `assets.document_ids`. Production's assets
table has no such column (information_schema, read-only): documents hang off the asset through
`asset_documents(asset_id, document_id)`. So every "what do the documents say about AHU-3"
answered "This asset has no linked documents (document_ids is empty)".

The SQLite stand-in is test_udr_lookups_find_a_name_however_it_is_typed's, with the assets table
shaped as production has it (no document_ids) and the link table added.
"""
from __future__ import annotations

import pytest

import test_udr_lookups_find_a_name_however_it_is_typed as H
from src.agents import ontology_qa as oq
from src.agents import udr_agent
from src.services import principal as P


@pytest.fixture
def db(monkeypatch):
    cut = H.ASSET_COLS.index("document_ids")
    tables = dict(H.TABLES)
    tables["assets"] = ([c for i, c in enumerate(H.ASSET_COLS) if i != cut],
                        [tuple(v for i, v in enumerate(r) if i != cut) for r in H.ASSETS])
    tables["asset_documents"] = (["id", "asset_id", "document_id", "document_type"],
                                 [("AD-1", "A-1", "D-1", "service_report")])
    monkeypatch.setattr(H, "TABLES", tables)
    d = H._Db()
    monkeypatch.setattr(oq, "_session_factory", lambda: (lambda: H._Session(d)))
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: H._Session(d))
    monkeypatch.setattr(P, "_columns_cache", {})
    token = P.caller_principal.set(H._admin())
    yield d
    P.caller_principal.reset(token)


def test_a_linked_document_is_read(db):
    out = H._run(udr_agent.get_asset_documents, identifier="chiller 1")
    assert out["asset"]["id"] == "A-1", out
    assert [d["id"] for d in out["documents"]] == ["D-1"]
    assert out["asset"]["linked_document_count"] == 1
    assert out["chunks"] and "PT-B-301-CH-01" in out["chunks"][0]["text"]


def test_an_asset_with_no_link_says_so_without_naming_a_missing_column(db):
    out = H._run(udr_agent.get_asset_documents, identifier="chiller 10")
    assert out["documents"] == [] and "no linked documents" in out["note"]
    assert "document_ids" not in out["note"]
