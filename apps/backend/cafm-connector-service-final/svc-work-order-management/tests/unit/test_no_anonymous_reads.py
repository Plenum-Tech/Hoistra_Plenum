"""No route on this service hands data to a caller with no token.

Written after a sweep of the deployed API found six that did. The worst of them served a real
mailbox — its messages and the address it was connected to — to anybody on the internet. The
others leaked approval rules, work-order journeys, and the approval chain of any order whose id
you could guess.

The specific six are pinned below so they cannot quietly reopen, and the sweep underneath them
is the one that matters: it walks every route the app declares, so a new router added without a
caller on it fails here rather than in production.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from src.app import app

client = TestClient(app, raise_server_exceptions=False)

DEAD = "00000000-0000-0000-0000-000000000000"

#: Routes that are meant to answer without a token, and why.
PUBLIC = {
    "/health", "/healthz", "/ready", "/readiness", "/liveness", "/metrics",
    "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/",
}


def anonymous(path: str):
    return client.get(path)


class TestTheSixThatWereOpen:
    """Each of these answered 200 to an anonymous caller on the deployed service."""

    @pytest.mark.parametrize("path,what", [
        ("/api/email/inbox", "a real mailbox's contents"),
        ("/api/email/status", "the connected account's address"),
        ("/api/admin/approval-rules", "who may approve what"),
        ("/api/journeys/", "every work-order journey"),
        ("/api/journeys/analytics/summary", "journey totals across the portfolio"),
        (f"/api/work-orders/{DEAD}/approval-chain", "any order's approval chain"),
    ])
    def test_refuses_without_a_token(self, path: str, what: str):
        r = anonymous(path)
        assert r.status_code in (401, 403), (
            f"{path} served {what} to an anonymous caller (got {r.status_code})")


class TestNoOtherRouteIsOpen:
    """The sweep: every GET the app declares, called with no Authorization header."""

    def test_no_get_route_answers_an_anonymous_caller(self):
        served = []
        for route in app.routes:
            if not isinstance(route, APIRoute) or "GET" not in route.methods:
                continue
            path = route.path
            if path in PUBLIC or path.startswith("/docs") or "openapi" in path:
                continue
            probe = path
            for name in route.param_convertors:
                probe = probe.replace("{" + name + "}", DEAD)
            if "{" in probe:                      # a shape we cannot fill; skip rather than guess
                continue
            r = anonymous(probe)
            if r.status_code == 200:
                served.append((path, len(r.content)))
        assert not served, (
            "these routes answered an anonymous caller with 200: "
            + ", ".join(f"{p} ({n} bytes)" for p, n in served))

    def test_the_sweep_can_actually_see_routes(self):
        # Guards against the test passing because it examined nothing.
        gets = [r for r in app.routes
                if isinstance(r, APIRoute) and "GET" in r.methods]
        assert len(gets) > 20
