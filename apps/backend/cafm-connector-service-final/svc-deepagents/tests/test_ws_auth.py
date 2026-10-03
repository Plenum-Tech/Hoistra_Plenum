"""A streaming socket carries the caller, the same as every other door into this service.

The three POST workflow routes authenticate the caller and stash their token, so every call
the agent then makes on their behalf is scoped to their company and buildings. The WebSocket
route did neither — a browser cannot put a header on a socket, and nothing read one out of
the handshake — which cost two things at once:

  * every tool call reached operations-intelligence with no bearer and was refused, so the
    ask bar answered "no authorization token" instead of answering the question; and
  * the route asked for no credential at all, while the reads that run SQL in-process treat
    an absent caller as "nobody is restricting this one" — every building, every company.

These pin the handshake. The token rides in Sec-WebSocket-Protocol, which is the one field
a browser can set when it dials; a socket without a good one is closed before the
orchestrator is reached; and a signed-in one runs with both context variables set, so the
agent's downstream calls carry the caller exactly as they do on the POST routes.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

# src.api.routes.workflow pulls in slowapi, which ships in the image but is not necessarily
# installed in a bare dev environment. Imported unguarded, a missing dependency is a COLLECTION
# error, and pytest abandons the whole run — so one absent package took all 551 tests in this
# service with it rather than just these. Skipping is honest; the tests below do not run.
import pytest as _pytest

workflow = _pytest.importorskip(
    "src.api.routes.workflow",
    reason="src.api.routes.workflow needs slowapi, which is not installed here",
)

from src.http_client import caller_authorization
from src.services import principal as P

# Shaped like the real thing: the access token is a JWT, so its characters are already
# legal in a subprotocol value. A token that was not would never reach the server.
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzYW0ifQ.c2lnbmF0dXJlLWdvZXMtaGVyZQ"
#: The wire contract, written out rather than imported — a test that reads the constant
#: from the code it checks would follow a rename that broke every deployed browser.
MARKER = "hoistra.auth.bearer"

B1 = uuid4()


def a_caller() -> P.Principal:
    """A plain user allocated to one building — restricted, so the scope is visible."""
    return P.Principal(
        user_id=uuid4(), email="sam@example.com", organization_id=uuid4(),
        role="user", can_ingest=False, building_ids=(B1,),
    )


class FakeOrchestrator:
    """Records what the request context looked like while the run was in flight.

    Reading the context variables afterwards would prove nothing: the question is whether
    the agent can see the caller at the moment it goes off to fetch data.
    """

    def __init__(self) -> None:
        self.ran = False
        self.saw_authorization: str | None = None
        self.saw_principal: P.Principal | None = None

    async def stream(self, user_message, session_id, extra_context=None):
        self.ran = True
        self.saw_authorization = caller_authorization.get()
        self.saw_principal = P.caller_principal.get()
        yield {"type": "workflow_completed", "answer": "ok", "session_id": session_id}


def make_client(monkeypatch, resolver=None) -> tuple[TestClient, FakeOrchestrator]:
    """An app holding just the workflow router, with identity and the orchestrator faked.

    ``resolver`` stands in for services.principal.resolve — the one call that would other-
    wise go to operations-intelligence — so these run without a network or a database. Pass
    None to keep the real one, which is hermetic for a token it can reject on sight.
    """
    if resolver is not None:
        monkeypatch.setattr(workflow, "resolve_principal", resolver)
    app = FastAPI()
    app.include_router(workflow.router)
    orchestrator = FakeOrchestrator()
    app.state.orchestrator = orchestrator
    return TestClient(app), orchestrator


@pytest.fixture(autouse=True)
def _clean_context():
    """No test inherits another's caller."""
    auth = caller_authorization.set(None)
    who = P.caller_principal.set(None)
    yield
    caller_authorization.reset(auth)
    P.caller_principal.reset(who)


# ── the handshake ────────────────────────────────────────────────────────────────────

def test_the_marker_is_the_name_the_browser_dials_with():
    assert P.WS_AUTH_SUBPROTOCOL == MARKER


def test_a_token_offered_beside_the_marker_becomes_an_authorization_header():
    assert P.bearer_from_subprotocols([MARKER, TOKEN]) == f"Bearer {TOKEN}"


@pytest.mark.parametrize("offered", [None, [], [MARKER], ["something.else", TOKEN]])
def test_a_handshake_without_our_marker_and_a_token_carries_nobody(offered):
    assert P.bearer_from_subprotocols(offered) is None


def test_surrounding_whitespace_from_the_wire_is_not_part_of_the_token():
    # A proxy may re-serialise the comma-separated header with spaces after the commas.
    assert P.bearer_from_subprotocols([f" {MARKER} ", f" {TOKEN} "]) == f"Bearer {TOKEN}"


# ── the route ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "offered",
    [None, [MARKER], ["chat"], [TOKEN]],
    ids=["nothing offered", "marker with no token", "some other protocol", "token with no marker"],
)
def test_a_socket_with_no_token_is_closed_before_the_orchestrator_is_reached(monkeypatch, offered):
    # The real identity function, not a stand-in: a handshake carrying no bearer is refused
    # on sight, without a network call, by the same code the POST routes are refused by.
    client, orchestrator = make_client(monkeypatch)

    with pytest.raises(WebSocketDisconnect) as disconnect:
        with client.websocket_connect("/api/workflow/ws/s-1", subprotocols=offered):
            pass

    assert disconnect.value.code == 1008
    assert not orchestrator.ran


def test_a_socket_whose_token_identity_rejects_is_closed(monkeypatch):
    async def rejects(authorization):
        raise P._unauthorized("Sign in again.", "invalid")

    client, orchestrator = make_client(monkeypatch, rejects)

    with pytest.raises(WebSocketDisconnect) as disconnect:
        with client.websocket_connect("/api/workflow/ws/s-1", subprotocols=[MARKER, TOKEN]):
            pass

    assert disconnect.value.code == 1008
    assert not orchestrator.ran


def test_a_socket_is_not_told_to_sign_in_again_when_identity_is_merely_down(monkeypatch):
    """A 503 from the identity service is not the caller's fault. Closing 1008 would tell a
    signed-in user to sign in again, which cannot help; 1011 says the server failed."""
    async def unavailable(authorization):
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": "Identity service unavailable.",
                    "reason": "identity_unavailable"},
        )

    client, orchestrator = make_client(monkeypatch, unavailable)

    with pytest.raises(WebSocketDisconnect) as disconnect:
        with client.websocket_connect("/api/workflow/ws/s-1", subprotocols=[MARKER, TOKEN]):
            pass

    assert disconnect.value.code == 1011
    assert not orchestrator.ran


def test_a_signed_in_socket_carries_the_caller_into_the_run(monkeypatch):
    seen: dict[str, str | None] = {}

    async def accepts(authorization):
        seen["authorization"] = authorization
        return a_caller()

    client, orchestrator = make_client(monkeypatch, accepts)

    with client.websocket_connect("/api/workflow/ws/s-1", subprotocols=[MARKER, TOKEN]) as ws:
        ws.send_text(json.dumps({"message": "which certificates are lapsed"}))
        completed = json.loads(ws.receive_text())

    # Identity was asked about this caller's own token, not a service credential.
    assert seen.get("authorization") == f"Bearer {TOKEN}"
    # And the agent could see them for the whole run — this is what the tool calls read.
    assert orchestrator.ran
    assert orchestrator.saw_authorization == f"Bearer {TOKEN}"
    assert orchestrator.saw_principal is not None
    assert orchestrator.saw_principal.building_ids == (B1,)
    assert completed["type"] == "workflow_completed"


def test_the_agreed_subprotocol_is_the_marker_and_never_the_token(monkeypatch):
    """The server echoes one subprotocol back. Echoing the token would write the
    credential into the client's handshake record and every proxy log along the way."""
    async def accepts(authorization):
        return a_caller()

    client, _ = make_client(monkeypatch, accepts)

    with client.websocket_connect("/api/workflow/ws/s-1", subprotocols=[MARKER, TOKEN]) as ws:
        assert ws.accepted_subprotocol == MARKER
