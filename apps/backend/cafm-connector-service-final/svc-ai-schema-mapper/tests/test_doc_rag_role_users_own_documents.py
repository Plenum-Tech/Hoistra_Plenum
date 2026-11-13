"""A caller held to some pages adds a document only with the ingest right, and reads back only a
document they added (13 Nov review).

The exception that let them upload and watch indexing let any of them, ingest right or not,
upload — and read any document's name, type and status by id. The index records no uploader, so
this service remembers which documents a role user's upload returned and lets them read those.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from starlette.responses import Response

from src.services import run_access as R
from src.services.principal import Principal


def caller(pages=frozenset({"compliance"}), can_ingest=True, role="user"):
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=uuid4(), role=role,
                     can_ingest=can_ingest, pages=pages)


def test_a_role_user_without_the_ingest_right_may_not_upload():
    p = caller(can_ingest=False)
    for path in ("/doc-rag/documents/upload", "/doc-rag/documents/upload-batch"):
        code, _error, reason = R.doc_rag_refused(p, "POST", path)
        assert (code, reason) == (403, "no_ingest_right")


def test_a_role_user_may_not_read_a_document_they_did_not_add():
    assert R.doc_rag_refused(caller(), "GET", "/doc-rag/documents/" + str(uuid4())) is not None


def _request(principal, path, method="POST"):
    return SimpleNamespace(method=method, scope={"path": path, "root_path": ""},
                           url=SimpleNamespace(path=path),
                           state=SimpleNamespace(principal=principal))


def _response(body: dict, status=200):
    raw = json.dumps(body).encode()

    async def _iter():
        yield raw
    return SimpleNamespace(status_code=status, headers={"content-type": "application/json"},
                           media_type="application/json", body_iterator=_iter())


def test_a_role_user_reads_back_the_documents_their_upload_returned():
    p = caller()
    one, two = str(uuid4()), str(uuid4())
    out = asyncio.run(R.after(_request(p, "/doc-rag/documents/upload"),
                              _response({"document_id": one, "status": "extracting"})))
    assert isinstance(out, Response) and json.loads(out.body)["document_id"] == one
    asyncio.run(R.after(_request(p, "/doc-rag/documents/upload-batch"),
                        _response({"documents": [{"document_id": two}], "count": 1})))
    for doc in (one, two):
        assert R.doc_rag_refused(p, "GET", "/doc-rag/documents/" + doc) is None
    # and nobody else reads them
    assert R.doc_rag_refused(caller(), "GET", "/doc-rag/documents/" + one) is not None


def test_a_failed_upload_records_nothing():
    p = caller()
    doc = str(uuid4())
    asyncio.run(R.after(_request(p, "/doc-rag/documents/upload"),
                        _response({"document_id": doc}, status=400)))
    assert R.doc_rag_refused(p, "GET", "/doc-rag/documents/" + doc) is not None


def test_everyone_elses_responses_pass_untouched():
    resp = _response({"document_id": "x"})
    for p in (caller(pages=None), caller(pages=None, role="admin")):
        assert asyncio.run(R.after(_request(p, "/doc-rag/documents/upload"), resp)) is resp
    assert asyncio.run(R.after(SimpleNamespace(method="POST", scope={"path": "/x"},
                                               url=SimpleNamespace(path="/x"),
                                               state=SimpleNamespace()), resp)) is resp
