"""Which path doc-rag is actually asked for.

Asked which documents are linked to Riverside Court, the deployed agent answered that the
document retrieval service returned an error. doc-rag serves /rag/query at its own root —
confirmed against the deployment, 200 in 2.6s with real chunks — and every path in
doc_rag_agent asked for /doc-rag/rag/query, which 404s.

`/backend/doc-rag/` is an nginx location so a browser can reach the service through the one
ingress; nginx strips it before proxying to 127.0.0.1:8004. A service calling that port
directly must not send it. nginx.conf already carries a comment about the previous round of
this same mistake, and two call sites here had been given ad-hoc fallbacks — which is why
indexing worked while querying did not.

So the prefix is tried rather than assumed, and these tests pin the rule: the service's own
path first, the prefixed one only on a 404, and any other failure raised untouched.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.agents import doc_rag_agent as dr


class Recorder:
    """Stands in for http_client.request, answering each path as told."""

    def __init__(self, answers: dict[str, object]):
        self.answers = answers
        self.tried: list[str] = []

    async def __call__(self, method, base, path, **kwargs):
        self.tried.append(path)
        answer = self.answers.get(path, 404)
        if isinstance(answer, Exception):
            raise answer
        if answer == 404:
            raise httpx.HTTPStatusError(
                "not found",
                request=httpx.Request(method, base + path),
                response=httpx.Response(404),
            )
        return answer


def call(rec, path, monkeypatch):
    monkeypatch.setattr(dr, "_http_request", rec)
    return asyncio.run(dr._request("POST", "http://localhost:8004", path))


def test_the_services_own_path_is_asked_for_first(monkeypatch):
    rec = Recorder({"/rag/query": "ok"})
    assert call(rec, "/doc-rag/rag/query", monkeypatch) == "ok"
    assert rec.tried == ["/rag/query"]


def test_the_prefixed_path_is_the_fallback_not_the_default(monkeypatch):
    # A deployment whose base URL points at the nginx front door still works.
    rec = Recorder({"/doc-rag/rag/query": "ok"})
    assert call(rec, "/doc-rag/rag/query", monkeypatch) == "ok"
    assert rec.tried == ["/rag/query", "/doc-rag/rag/query"]


def test_a_path_without_the_prefix_is_asked_once(monkeypatch):
    # Nothing to strip, so nothing to retry — a 404 here is the real answer.
    rec = Recorder({"/documents/upload": "ok"})
    assert call(rec, "/documents/upload", monkeypatch) == "ok"
    assert rec.tried == ["/documents/upload"]


def test_both_missing_raises_rather_than_returning_nothing(monkeypatch):
    rec = Recorder({})
    with pytest.raises(httpx.HTTPStatusError):
        call(rec, "/doc-rag/rag/query", monkeypatch)
    assert rec.tried == ["/rag/query", "/doc-rag/rag/query"]


def test_a_server_error_is_not_retried_as_a_wrong_path(monkeypatch):
    # A 500 is the service answering the right path badly. Retrying it under a second path
    # would turn one real fault into a confusing second one and hide the first.
    boom = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("POST", "http://localhost:8004/rag/query"),
        response=httpx.Response(500, text="kaboom"),
    )
    rec = Recorder({"/rag/query": boom, "/doc-rag/rag/query": "ok"})
    with pytest.raises(httpx.HTTPStatusError) as caught:
        call(rec, "/doc-rag/rag/query", monkeypatch)
    assert caught.value.response.status_code == 500
    assert rec.tried == ["/rag/query"]


def test_a_connection_failure_is_raised_as_itself(monkeypatch):
    # The service being down is not a path problem, and must not be reported as one.
    rec = Recorder({"/rag/query": httpx.ConnectError("refused")})
    with pytest.raises(httpx.ConnectError):
        call(rec, "/doc-rag/rag/query", monkeypatch)
    assert rec.tried == ["/rag/query"]


@pytest.mark.parametrize(
    "path,expected_first",
    [
        ("/doc-rag/rag/query", "/rag/query"),
        ("/doc-rag/documents/upload", "/documents/upload"),
        ("/doc-rag/documents/abc-123", "/documents/abc-123"),
        ("/doc-rag/row-index/tables", "/row-index/tables"),
        ("/doc-rag/documents/abc/match-rows", "/documents/abc/match-rows"),
    ],
)
def test_every_endpoint_this_module_uses_is_stripped(path, expected_first, monkeypatch):
    rec = Recorder({expected_first: "ok"})
    assert call(rec, path, monkeypatch) == "ok"
    assert rec.tried[0] == expected_first


def test_a_path_merely_containing_the_word_is_not_stripped(monkeypatch):
    # Only a leading segment is the nginx prefix. "/documents/doc-rag-notes" is a document.
    rec = Recorder({"/documents/doc-rag-notes": "ok"})
    assert call(rec, "/documents/doc-rag-notes", monkeypatch) == "ok"
    assert rec.tried == ["/documents/doc-rag-notes"]
