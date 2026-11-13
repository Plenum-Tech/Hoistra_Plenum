"""A caller held to some pages is kept off the document index.

The document index mounted in this service answers about any document and any table, and has
no notion of who is asking. A caller with an access role may add a document and watch it being
indexed — that is ingesting, which their own switch governs — and nothing else there. Everyone
else reaches it exactly as before.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.services import run_access as R
from src.services.principal import Principal


def caller(pages=None, role="user"):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=uuid4(), role=role,
                     can_ingest=True, pages=pages)


HELD = caller(pages=frozenset({"compliance", "buildings"}))
DOC = "/doc-rag/documents/" + str(uuid4())


@pytest.mark.parametrize("method,path", [
    ("GET", "/doc-rag/row-index/tables"),
    ("GET", "/doc-rag/row-index/tables/compliance_certificates/rows"),
    ("GET", "/doc-rag/row-index/db-tables"),
    ("POST", "/doc-rag/row-index/import-db-table"),
    ("POST", "/doc-rag/rag/query"),
    ("GET", "/doc-rag/rows/x"),
    ("GET", "/doc-rag/documents"),
    ("POST", DOC + "/match-rows"),
    ("DELETE", DOC),
    ("GET", "/doc-rag/images/abc/page1.png"),
])
def test_a_role_user_is_refused_the_document_index(method, path):
    code, error, reason = R.doc_rag_refused(HELD, method, path)
    assert (code, reason) == (403, "page_not_allowed")
    assert error == "That is not part of the pages you have access to."


@pytest.mark.parametrize("method,path", [
    ("POST", "/doc-rag/documents/upload"),
    ("POST", "/doc-rag/documents/upload-batch"),
    ("GET", DOC),
])
def test_a_role_user_may_still_add_a_document_and_watch_it_index(method, path):
    assert R.doc_rag_refused(HELD, method, path) is None


@pytest.mark.parametrize("who", [caller(), caller(role="admin"), caller(role="superadmin")])
def test_everyone_else_reaches_it_as_before(who):
    for method, path in (("GET", "/doc-rag/row-index/tables"), ("POST", "/doc-rag/rag/query"),
                         ("DELETE", DOC), ("GET", "/doc-rag/documents")):
        assert R.doc_rag_refused(who, method, path) is None


def test_the_rule_is_about_the_document_index_only():
    for path in ("/api/migration", "/api/migration/" + str(uuid4()) + "/status", "/health"):
        assert R.doc_rag_refused(HELD, "GET", path) is None


def test_even_a_holder_of_every_page_is_kept_off_it():
    every = caller(pages=frozenset({"buildings", "compliance", "energy", "vendors", "ops", "assets"}))
    assert R.doc_rag_refused(every, "GET", "/doc-rag/row-index/tables/assets/rows") is not None
