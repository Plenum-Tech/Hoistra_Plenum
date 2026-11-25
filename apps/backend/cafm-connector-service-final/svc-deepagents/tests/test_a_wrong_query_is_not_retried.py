"""A query the database refuses as written is not sent again (25 Nov 2026).

svc-udr answers a 500 when the SQL it was handed names a column the table does not have
(`column v.specialty does not exist`, a ProgrammingError). Every 5xx was retried as if the
service were briefly down, so the same wrong query went three times, about 4 s of a turn, and
could never succeed. A 5xx whose body says the query itself is wrong is answered once; a 5xx
that says nothing of the kind is still retried.
"""
from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from src import http_client

UDR_BODY = {"errors": [{"code": "error", "message": (
    "(sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) column v.specialty does not exist\n"
    "[SQL: SELECT v.vendor_name, v.specialty FROM plenum_cafm.vendors v]")}]}


def _status_error(code: int, body: dict | None) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://svc-udr/api/tables/query/select")
    resp = httpx.Response(code, request=req, json=body) if body is not None else httpx.Response(code, request=req)
    return httpx.HTTPStatusError("e", request=req, response=resp)


@pytest.mark.parametrize("kind", ["ProgrammingError", "DataError"])
def test_a_server_error_naming_a_wrong_query_is_not_temporary(kind):
    body = {"errors": [{"code": "error", "message": f"(sqlalchemy.dialects.postgresql.asyncpg.{kind}) x"}]}
    assert http_client._is_transient(_status_error(500, body)) is False


def test_a_server_error_that_names_no_wrong_query_is_still_temporary():
    assert http_client._is_transient(_status_error(500, {"detail": "Internal Server Error"})) is True
    assert http_client._is_transient(_status_error(503, None)) is True


def test_the_wrong_query_is_sent_once(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(500, json=UDR_BODY)

    monkeypatch.setattr(http_client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_client, "wait_exponential", lambda **kw: (lambda state: 0))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http_client.request("POST", "http://svc-udr", "/api/tables/query/select",
                                        service="wrongsql-" + uuid.uuid4().hex[:6],
                                        json={"sql": "SELECT v.specialty FROM plenum_cafm.vendors v"}))
    assert seen == ["/api/tables/query/select"]


def test_wrong_queries_do_not_close_the_service_to_everyone(monkeypatch):
    """The circuit breaker opens after 5 failures and refuses every udr call for 30 s. A query
    the database refuses as written says nothing about the service's health, so it must not
    count: six wrong queries in a row still reach svc-udr."""
    real = httpx.AsyncClient
    service = "wrongsql-cb-" + uuid.uuid4().hex[:6]

    def handler(request):
        return httpx.Response(500, json=UDR_BODY)

    monkeypatch.setattr(http_client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(http_client, "wait_exponential", lambda **kw: (lambda state: 0))
    for _ in range(http_client._FAILURE_THRESHOLD + 1):
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(http_client.request("POST", "http://svc-udr", "/api/tables/query/select",
                                            service=service, json={"sql": "SELECT 1"}))
