"""CORS allowed only localhost, on a service whose UI is served from its own origin.

Invitation links are built on PUBLIC_APP_URL. If the browser then loads the accept screen
from that origin and calls this service, a hardcoded localhost allow-list refuses the call
before it leaves the page — a CORS failure leaves nothing in the service log, because the
request never arrives. The allow-list now follows wherever the deployment says it lives.
"""
from __future__ import annotations

import importlib


def _origins(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import src.config

    importlib.reload(src.config)
    import src.app

    importlib.reload(src.app)
    return src.app._cors_origins()


def test_the_configured_public_origin_is_allowed(monkeypatch):
    out = _origins(monkeypatch, PUBLIC_APP_URL="https://hoistra.example.net")
    assert "https://hoistra.example.net" in out


def test_a_path_is_stripped_because_an_origin_has_none(monkeypatch):
    # PUBLIC_BASE_URL-shaped values carry /backend/ops-intelligence; Starlette matches
    # origins exactly, so a path would never match and the entry would be dead weight.
    out = _origins(monkeypatch, FRONTEND_PUBLIC_URL="https://hoistra.example.net/backend/ops-intelligence")
    assert "https://hoistra.example.net" in out
    assert not any(o.endswith("/backend/ops-intelligence") for o in out)


def test_a_trailing_slash_does_not_create_a_second_origin(monkeypatch):
    out = _origins(monkeypatch, PUBLIC_APP_URL="https://hoistra.example.net/",
                   FRONTEND_PUBLIC_URL="https://hoistra.example.net")
    assert out.count("https://hoistra.example.net") == 1
    assert "https://hoistra.example.net/" not in out


def test_the_local_ports_still_work(monkeypatch):
    out = _origins(monkeypatch, PUBLIC_APP_URL="", FRONTEND_PUBLIC_URL="")
    for o in ("http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5174"):
        assert o in out


def test_nothing_unset_or_malformed_reaches_the_list(monkeypatch):
    out = _origins(monkeypatch, PUBLIC_APP_URL="not-a-url", FRONTEND_PUBLIC_URL="   ")
    assert "not-a-url" not in out, "a value with no scheme or host is not an origin"
    assert all(o.startswith("http://") or o.startswith("https://") for o in out)
    # Never the wildcard: allow_credentials=True makes "*" invalid, and a silent
    # downgrade to it would open the service to every origin on the internet.
    assert "*" not in out
