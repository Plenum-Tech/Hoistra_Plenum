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


# ── A rejected model key is not an outage, and its text is not for the user ──────────────
# Both provider keys were refused on the local stack:
#
#   POST https://api.anthropic.com/v1/messages -> 401  "API key is invalid."
#   POST https://api.openai.com/v1/chat/completions -> 401
#     "You do not have access to the organization tied to the API key."  invalid_organization
#
# The orchestrator had no model, /run-stateful answered 500, and the provider's own sentence
# was copied into the response body and stored on the report card a user reads.

from src.provider_errors import provider_refusal as _provider_refusal, refusal_detail  # noqa: E402

ANTHROPIC_401 = ("Error code: 401 - {'type': 'error', 'error': {'type': "
                 "'authentication_error', 'message': 'API key is invalid.'}}")
OPENAI_401 = ("Error code: 401 - {'error': {'message': 'You do not have access to the "
              "organization tied to the API key.', 'type': 'invalid_request_error', "
              "'code': 'invalid_organization'}}")


class TestAProviderRefusalIsRecognised:

    def test_an_invalid_anthropic_key_is_named_as_such(self):
        assert _provider_refusal(ANTHROPIC_401) == ("anthropic", "rejected")

    def test_an_openai_organization_mismatch_is_named_as_such(self):
        assert _provider_refusal(OPENAI_401) == ("openai", "rejected")

    def test_running_out_of_credit_is_a_different_answer_from_a_bad_key(self):
        """Both stop the model answering; only one is fixed by changing a key."""
        out = _provider_refusal("Error code: 400 - {'error': {'type': 'invalid_request_error', "
                                "'code': 'insufficient_quota', 'message': 'Your credit balance "
                                "is too low'}}")
        assert out is not None and out[1] == "out of credit"

    def test_an_ordinary_failure_is_not_blamed_on_the_provider(self):
        """A timeout, a tool error or a bad question must not be reported as a bad API key —
        that sends somebody to rotate a credential that was never the problem."""
        for other in ("ReadTimeout", "tool 'x' failed", "", "connection refused",
                      "Error code: 500 - internal server error"):
            assert _provider_refusal(other) is None, other

    def test_matching_is_on_the_providers_codes_not_its_prose(self):
        """Wording changes; 'invalid_organization' and 'authentication_error' do not."""
        assert _provider_refusal("code: invalid_organization") == ("openai", "rejected")
        assert _provider_refusal("type: authentication_error") == ("anthropic", "rejected")


class TestTheAnswerCarriesNoUpstreamText:

    def test_it_names_the_provider_and_the_variable_to_check(self):
        d = refusal_detail("openai", "rejected")
        assert d["provider"] == "openai"
        assert "OPENAI_API_KEY" in d["error"]
        assert d["reason"] == "model_provider_rejected"

    def test_the_providers_own_sentence_is_not_in_it(self):
        """That sentence is stored on a report card a user reads. It belongs in the log."""
        d = refusal_detail("openai", "rejected")
        blob = " ".join(str(v) for v in d.values()).lower()
        assert "organization tied to the api key" not in blob
        assert "error code: 401" not in blob

    def test_out_of_credit_reads_differently_from_a_bad_key(self):
        assert "out of credit" in refusal_detail("anthropic", "out of credit")["error"]
        assert refusal_detail("anthropic", "out of credit")["reason"] ==             "model_provider_out_of_credit"
