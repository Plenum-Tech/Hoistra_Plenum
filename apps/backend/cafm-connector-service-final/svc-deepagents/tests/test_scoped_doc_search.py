"""The chat's document search goes through the company-scoped register search, never doc-rag.

query_docs / semantic_search used to POST doc-rag /rag/query - no login, no company filter, so one
company's question could be answered from another's contracts (2 Oct 2026). And a document link is
now signed and short-lived: the bare /download URL in an old chat answer is refused on its own."""
from __future__ import annotations

import asyncio
import time

from src.agents import doc_rag_agent as dra
from src.api.routes import documents as docs


class _Resp:
    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


BODY = {"ok": True, "searched": [{"file": "AM-2024-HVAC-07.pdf"}], "passages": [
    {"document": "Apex HVAC contract", "file": "AM-2024-HVAC-07.pdf", "document_id": "d-1", "page": 4,
     "heading": "Service credits", "text": "A P2 breach earns a 5% service credit.", "score": 0.4}]}


def _spy(monkeypatch, body=BODY):
    calls = []

    async def req(method, base, path, **kw):
        calls.append((method, base, path, kw))
        return _Resp(body)

    monkeypatch.setattr(dra, "_http_request", req)
    return calls


def test_query_docs_asks_the_scoped_search_not_doc_rag(monkeypatch):
    calls = _spy(monkeypatch)
    out = asyncio.run(dra.query_docs.ainvoke({"query": "service credit for a P2 breach", "top_k": 5}))
    method, base, path, kw = calls[0]
    assert (method, path) == ("GET", "/api/documents/search") and "rag" not in path
    assert base.rstrip("/") == dra.settings.operations_intelligence_base_url.rstrip("/")
    assert kw["params"] == {"q": "service credit for a P2 breach"}
    assert out["sources"][0]["document_id"] == "d-1" and out["sources"][0]["page"] == 4
    assert out["scope"] == "your company's documents"


def test_semantic_search_returns_passages_and_reports_a_refusal(monkeypatch):
    _spy(monkeypatch)
    rows = asyncio.run(dra.semantic_search.ainvoke({"query": "service credit"}))
    assert rows[0]["file_name"] == "AM-2024-HVAC-07.pdf"
    _spy(monkeypatch, {"ok": False, "error": "Not signed in"})
    assert asyncio.run(dra.semantic_search.ainvoke({"query": "service credit"}))[0]["error"] == "Not signed in"
    out = asyncio.run(dra.query_docs.ainvoke({"query": "service credit"}))
    assert out["error"] == "Not signed in" and out["sources"] == []


def test_a_signed_link_opens_once_signed_and_not_after_it_expires():
    exp = int(time.time()) + 60
    sig = docs.sign_link("doc-1", exp, "user-1")
    assert docs.verify_link("doc-1", exp, "user-1", sig)
    assert not docs.verify_link("doc-2", exp, "user-1", sig)          # another document
    assert not docs.verify_link("doc-1", exp, "user-2", sig)          # another user's link
    assert not docs.verify_link("doc-1", exp + 1, "user-1", sig)      # stretched expiry
    assert not docs.verify_link("doc-1", exp, "user-1", sig, now=exp + 1)  # expired
    assert not docs.verify_link("doc-1", exp, "user-1", "")           # an old, unsigned link


def test_the_download_route_refuses_a_link_without_a_signature():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(docs.router)
    r = TestClient(app).get("/api/documents/3f2b9a10-1c2d-4e5f-8a9b-0c1d2e3f4a5b/download")
    assert r.status_code == 403 and "expired or is not valid" in r.json()["detail"]
