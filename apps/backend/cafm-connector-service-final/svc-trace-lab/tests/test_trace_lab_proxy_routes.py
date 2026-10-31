"""The proxy routes (G0 addendum 2.5): loopback callers only, allowed reads forwarded to the real service
with the caller's headers and streamed back, everything else refused with 403 refused_by_trace_lab and
recorded against the running golden case. The upstream is a fake (httpx.MockTransport); requests reach the
app through httpx.ASGITransport, so nothing listens on a port and no database is touched."""
from __future__ import annotations

import json
import types

import httpx
import pytest

from trace_lab import app as app_module
from trace_lab import proxy
from trace_lab.settings import Settings

UPSTREAMS = {"udr": "http://svc-udr.test:8006", "oi": "http://oi.test:8009", "wom": "http://wom.test:8007",
             "docrag": ""}
BEARER = "Bearer starter-token"


class FakeUpstream:
    """Records every request and answers from a table of (method, path) -> response."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []
        self.answers: dict[tuple[str, str], httpx.Response] = {}
        self.fail: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.fail is not None:
            raise self.fail
        hit = self.answers.get((request.method, request.url.path))
        return hit if hit is not None else httpx.Response(200, json={"ok": True, "path": request.url.path})


@pytest.fixture
def upstream(monkeypatch):
    proxy.reset_for_tests()
    fake = FakeUpstream()
    monkeypatch.setattr(proxy, "_http", httpx.AsyncClient(transport=httpx.MockTransport(fake)))
    yield fake
    proxy.reset_for_tests()


def _client(peer: str = "127.0.0.1", *, root_path: str = "", host: str = "127.0.0.1:8010") -> httpx.AsyncClient:
    app = app_module.create_app(Settings(upstreams=dict(UPSTREAMS)))
    transport = httpx.ASGITransport(app=app, client=(peer, 40123), root_path=root_path)
    return httpx.AsyncClient(transport=transport, base_url=f"http://{host}")


async def test_a_read_is_forwarded_with_the_callers_headers_and_streamed_back(upstream):
    upstream.answers[("GET", "/api/work-orders/")] = httpx.Response(
        200, json={"items": [1, 2]}, headers={"x-upstream": "yes", "connection": "close"})
    async with _client() as c:
        r = await c.get("/__proxy/wom/api/work-orders/", params={"organization_id": "o1", "limit": 5},
                        headers={"Authorization": BEARER, "X-Acting-Organization-Id": "o1"})
    assert r.status_code == 200 and r.json() == {"items": [1, 2]} and r.headers["x-upstream"] == "yes"
    (sent,) = upstream.seen
    assert str(sent.url) == "http://wom.test:8007/api/work-orders/?organization_id=o1&limit=5"
    assert sent.headers["authorization"] == BEARER and sent.headers["x-acting-organization-id"] == "o1"
    assert sent.headers["host"] == "wom.test:8007"


async def test_an_allowed_post_keeps_its_body(upstream):
    body = {"search_term": "B-301", "search_columns": ["name"], "limit": 5, "offset": 0}
    async with _client() as c:
        r = await c.post("/__proxy/udr/api/tables/buildings/records/search", json=body,
                         headers={"Authorization": BEARER})
    assert r.status_code == 200
    (sent,) = upstream.seen
    assert sent.method == "POST" and json.loads(sent.content) == body


async def test_a_write_is_refused_logged_against_the_case_and_never_sent(upstream):
    proxy.refusals.begin("golden-1a2b3c4d-21-r1:turn-g1", authorization=BEARER)
    try:
        async with _client() as c:
            r = await c.patch("/__proxy/udr/api/tables/work_orders/records/7", json={"status": "Closed"},
                              headers={"Authorization": BEARER})
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["reason"] == "refused_by_trace_lab" and detail["method"] == "PATCH"
        assert detail["path"] == "/api/tables/work_orders/records/7" and "read-only" in detail["why"]
        (entry,) = proxy.refusals.current()
        assert entry["case"] == "golden-1a2b3c4d-21-r1:turn-g1" and entry["service"] == "udr"
        assert entry["label"] == proxy.label_for("udr")
    finally:
        assert len(proxy.refusals.end()) == 1
    assert upstream.seen == []


async def test_a_refusal_outside_a_case_is_not_kept(upstream):
    async with _client() as c:
        r = await c.post("/__proxy/oi/api/compliance/scan", json={})
    assert r.status_code == 403 and proxy.refusals.current() == [] and upstream.seen == []


@pytest.mark.parametrize("peer,headers,host", [
    ("10.0.3.7", {}, "127.0.0.1:8010"),                               # another container (the gateway)
    ("testclient", {}, "127.0.0.1:8010"),
    ("127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}, "127.0.0.1:8010"),  # all-in-one nginx connects from loopback
    ("127.0.0.1", {"X-Real-IP": "203.0.113.9"}, "127.0.0.1:8010"),
    ("127.0.0.1", {}, "hoistra.example.com"),
])
async def test_only_this_process_may_use_the_proxy(upstream, peer, headers, host):
    async with _client(peer, host=host) as c:
        r = await c.get("/__proxy/udr/api/tables", headers=headers)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "loopback_only"
    assert upstream.seen == []


async def test_upstream_failures_are_kept_for_the_case_bearer_only(upstream):
    upstream.answers[("GET", "/api/catalog/search")] = httpx.Response(503, json={"detail": "down"})
    proxy.refusals.begin("case-1", authorization=BEARER)
    try:
        async with _client() as c:
            r1 = await c.get("/__proxy/udr/api/catalog/search", headers={"Authorization": BEARER})
            r2 = await c.get("/__proxy/udr/api/catalog/search", headers={"Authorization": "Bearer someone-else"})
        assert (r1.status_code, r2.status_code) == (503, 503)
        (failure,) = proxy.refusals.upstream_failures()
        assert (failure["status"], failure["service"], failure["path"]) == (503, "udr", "/api/catalog/search")
    finally:
        proxy.refusals.end()


async def test_an_unreachable_upstream_is_a_502_and_is_kept(upstream):
    upstream.fail = httpx.ConnectError("refused")
    proxy.refusals.begin("case-2", authorization=BEARER)
    try:
        async with _client() as c:
            r = await c.get("/__proxy/oi/api/auth/me", headers={"Authorization": BEARER})
        assert r.status_code == 502 and r.json()["detail"]["reason"] == "upstream_unreachable"
        assert proxy.refusals.upstream_failures()[0]["error"] == "ConnectError"
    finally:
        proxy.refusals.end()


async def test_a_service_with_no_address_is_a_502(upstream):
    async with _client() as c:
        r = await c.get("/__proxy/docrag/doc-rag/documents/search", params={"q": "x"})
    assert r.status_code == 502 and r.json()["detail"]["reason"] == "upstream_unset" and upstream.seen == []


async def test_a_redirect_comes_back_through_the_proxy(upstream):
    upstream.answers[("GET", "/api/assets")] = httpx.Response(307, headers={"location": "http://wom.test:8007/api/assets/"})
    upstream.answers[("GET", "/api/locations")] = httpx.Response(307, headers={"location": "/api/locations/"})
    async with _client() as c:
        r1 = await c.get("/__proxy/wom/api/assets")
        r2 = await c.get("/__proxy/wom/api/locations")
    assert r1.headers["location"] == "/__proxy/wom/api/assets/"
    assert r2.headers["location"] == "/__proxy/wom/api/locations/"


async def test_a_body_too_large_to_check_is_refused(upstream, monkeypatch):
    monkeypatch.setattr(proxy, "MAX_BODY_BYTES", 100)
    async with _client() as c:
        r = await c.post("/__proxy/udr/api/tables/query/select", content=b"x" * 500,
                         headers={"content-type": "application/json"})
    assert r.status_code == 403 and "too large" in r.json()["detail"]["why"] and upstream.seen == []


async def test_the_proxy_works_under_the_services_root_path_and_outside_the_api_prefix(upstream):
    async with _client(root_path="/backend/trace-lab") as c:
        r = await c.get("/__proxy/udr/api/tables")
        missing = await c.get("/api/trace-lab/__proxy/udr/api/tables")
    assert r.status_code == 200 and missing.status_code == 404


def test_the_refusal_log_holds_one_case_at_a_time_and_caps_its_entries():
    log_ = proxy.RefusalLog()
    log_.begin("a")
    with pytest.raises(RuntimeError, match="already running"):
        log_.begin("b")
    for i in range(proxy.MAX_LOG + 7):
        log_.note({"i": i})
    assert len(log_.current()) == proxy.MAX_LOG and log_.active() == "a"
    assert len(log_.end()) == proxy.MAX_LOG and log_.active() is None and log_.current() == []
    log_.note({"i": 0})
    assert log_.current() == []


async def test_svc_deepagents_http_client_goes_through_the_proxy(upstream, monkeypatch):
    """The chat's own client, pointed at the proxy as in the trace-lab container: an allowed read POST
    reaches the fake service with the caller's bearer; a write comes back 403 and opens no circuit."""
    from src import http_client

    app = app_module.create_app(Settings(upstreams=dict(UPSTREAMS)))
    real = httpx.AsyncClient

    class _ViaApp(real):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.ASGITransport(app=app, client=("127.0.0.1", 40124))
            super().__init__(*a, **kw)

    monkeypatch.setattr(http_client, "httpx", types.SimpleNamespace(**{**vars(httpx), "AsyncClient": _ViaApp}))
    monkeypatch.setattr(http_client, "_breakers", {})
    token = http_client.caller_authorization.set(BEARER)
    proxy.refusals.begin("case-3", authorization=BEARER)
    try:
        r = await http_client.request("POST", "http://127.0.0.1:8010/__proxy/udr", "/api/tables/assets/records/search",
                                      service="udr", json={"search_term": "boiler", "search_columns": ["name"],
                                                           "limit": 5, "offset": 0})
        assert r.status_code == 200
        with pytest.raises(httpx.HTTPStatusError) as err:
            await http_client.request("PATCH", "http://127.0.0.1:8010/__proxy/udr", "/api/tables/assets/records/1",
                                      service="udr", json={"name": "x"}, max_attempts=1)
        assert err.value.response.status_code == 403
        assert [e["method"] for e in proxy.refusals.current()] == ["PATCH"]
    finally:
        proxy.refusals.end()
        http_client.caller_authorization.reset(token)
    (sent,) = upstream.seen
    assert sent.url.path == "/api/tables/assets/records/search" and sent.headers["authorization"] == BEARER
    assert http_client._breakers["udr"]._failures == 0
