"""The caller's bearer must reach every call this service makes on their behalf.

Every call to operations-intelligence goes out as the USER, not as a service — that is how an
answer is scoped to their company and buildings instead of to everything. The token was picked
up by hand in three handlers, and a route that forgot called the next service with no
Authorization header at all.

That is not a hypothetical. In production, `list_vendor_accreditations` reached
operations-intelligence with no header:

    GET http://127.0.0.1:8009/api/compliance/certificates -> 401
    {"ok": false, "error": "Send an Authorization: Bearer <token> header.",
     "reason": "missing_token"}

The tool returned zero rows, and the agent told the user "the compliance service could not be
reached" — an outage, when the truth was a request that was never authenticated.

Setting the contextvar in middleware is only a fix if the value survives to the endpoint.
Starlette's BaseHTTPMiddleware runs `call_next` in its own task, so that is exactly the kind
of thing to test rather than assume.
"""
import asyncio
import contextvars

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from src.http_client import caller_authorization


@pytest.fixture()
def app_with_middleware():
    app = FastAPI()

    @app.middleware("http")
    async def forward(request: Request, call_next) -> Response:
        caller_authorization.set(request.headers.get("authorization"))
        return await call_next(request)

    @app.get("/seen")
    async def seen():
        return {"token": caller_authorization.get()}

    @app.get("/seen-through-a-task")
    async def through_task():
        # Tools run inside tasks the handler creates. A token that reaches the handler but
        # not the task it spawns is no use to the thing that actually makes the HTTP call.
        async def inner():
            return caller_authorization.get()
        return {"token": await asyncio.create_task(inner())}

    return app


class TestTheTokenReachesWhereTheCallIsMade:

    def test_the_handler_sees_the_caller_token(self, app_with_middleware):
        c = TestClient(app_with_middleware)
        r = c.get("/seen", headers={"Authorization": "Bearer abc123"})
        assert r.json()["token"] == "Bearer abc123"

    def test_a_task_the_handler_spawns_sees_it_too(self, app_with_middleware):
        """asyncio.create_task copies the context at creation, so a tool running in a task
        inherits it. This is the path the orchestrator actually uses."""
        c = TestClient(app_with_middleware)
        r = c.get("/seen-through-a-task", headers={"Authorization": "Bearer abc123"})
        assert r.json()["token"] == "Bearer abc123"

    def test_a_request_with_no_header_does_not_inherit_the_previous_one(self, app_with_middleware):
        """Setting None explicitly is the point. A contextvar left alone can carry the last
        caller's token into the next request — one user's answer scoped to another's."""
        c = TestClient(app_with_middleware)
        assert c.get("/seen", headers={"Authorization": "Bearer first"}).json()["token"] == "Bearer first"
        assert c.get("/seen").json()["token"] is None


#: The real app pulls in slowapi, which is installed in the image but not necessarily in a
#: bare dev environment. Skipping is honest; asserting on an app that could not be imported
#: would be a green test that checked nothing.
_app = None
try:
    from src.api.main import app as _app
except Exception as _exc:  # noqa: BLE001
    _import_error = _exc


@pytest.mark.skipif(_app is None, reason="src.api.main could not be imported here")
class TestTheRealAppSetsItForEveryRoute:

    def test_the_middleware_is_registered_on_the_deep_agents_app(self):
        app = _app
        names = [m.cls.__name__ if hasattr(m, "cls") else str(m) for m in app.user_middleware]
        joined = " ".join(names) + " " + " ".join(
            getattr(getattr(m, "kwargs", {}).get("dispatch", None), "__name__", "")
            for m in app.user_middleware)
        assert "forward_caller_token" in joined, (
            "the token must be carried by middleware, not by each handler — "
            f"middleware present: {names}")

    def test_resume_no_longer_depends_on_a_handler_remembering(self):
        """/resume drives the same tools as /run-stateful and never set the token itself.
        With middleware it does not have to."""
        assert _app.user_middleware, "no middleware registered at all"


class TestAnUnauthenticatedCallIsNamed:

    @pytest.mark.asyncio
    async def test_it_logs_rather_than_failing_silently(self, monkeypatch):
        """A call made with no token will be refused downstream. The warning is what turns
        "the compliance service could not be reached" back into something findable."""
        import src.http_client as hc
        seen = {}

        def _warn(event, **kw):
            if event == "http_client.no_caller_token":
                seen.update(kw)
        monkeypatch.setattr(hc.log, "warning", _warn)

        class _Resp:
            status_code = 200
            def raise_for_status(self): return None
            def json(self): return {}

        class _Client:
            def __init__(self, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def request(self, method, path, **kw): return _Resp()

        monkeypatch.setattr(hc.httpx, "AsyncClient", _Client)
        ctx = contextvars.copy_context()

        async def _call():
            caller_authorization.set(None)
            await hc.request("GET", "http://x", "/api/compliance/certificates",
                             service="operations_intelligence", max_attempts=1)
        await ctx.run(lambda: asyncio.ensure_future(_call()))
        await asyncio.sleep(0)
        assert seen.get("path") == "/api/compliance/certificates"
        assert seen.get("service") == "operations_intelligence"
