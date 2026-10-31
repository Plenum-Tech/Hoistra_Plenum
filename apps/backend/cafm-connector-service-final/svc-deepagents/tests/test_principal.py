"""deep-agents learns who is calling, from the service that owns identity.

It has no users table and no signing key, and should have neither. The caller is resolved by
forwarding their Authorization header to operations-intelligence /api/auth/me, which reads
role, ingestion right and building allocation from the database on every call.

These pin the mapping from /me to the local Principal, the building rule, the 401 on a
missing token, and the short per-token cache — without a network.
"""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException

from src.services import principal as P

ORG = UUID("11111111-1111-5111-8111-111111111111")
B1, B2 = uuid4(), uuid4()


def me(role="user", building_ids=None, can_ingest=False, org=ORG):
    return {"ok": True, "user": {
        "id": str(uuid4()), "email": "x@example.com", "organization_id": str(org),
        "role": role, "can_ingest": can_ingest,
        "building_ids": None if building_ids is None else [str(b) for b in building_ids],
        "buildings": [] if not building_ids else [{"id": str(b), "name": "B"} for b in building_ids],
    }}


def test_an_admin_arrives_unrestricted():
    p = P._from_me(me(role="admin", building_ids=None, can_ingest=True))
    assert p.building_ids is None and p.is_admin and p.can_ingest
    assert p.allows_building(uuid4())


def test_a_user_arrives_with_exactly_their_buildings():
    p = P._from_me(me(building_ids=[B1, B2]))
    assert p.building_ids == (B1, B2)
    assert p.allows_building(B1) and p.allows_building(str(B2))
    assert not p.allows_building(uuid4())


def test_allocated_to_nothing_is_a_real_answer():
    # [] from /me becomes (), not None. The difference is "sees nothing" vs "sees all".
    p = P._from_me(me(building_ids=[]))
    assert p.building_ids == ()
    assert not p.allows_building(B1)


@pytest.mark.parametrize("bad", [None, "", "nope"])
def test_a_malformed_building_is_never_allowed(bad):
    assert not P._from_me(me(building_ids=[B1])).allows_building(bad)


def test_read_only_is_the_default():
    assert not P._from_me(me()).can_ingest


def test_missing_token_is_401_before_any_call(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("no network call should be made without a token")
    monkeypatch.setattr(P, "_request", boom)
    for header in (None, "", "Basic abc", "Bearer ", "bearer"):
        with pytest.raises(HTTPException) as e:
            asyncio.run(P.resolve(header))
        assert e.value.status_code == 401
        assert e.value.detail["reason"] == "missing_token"


class FakeResp:
    def __init__(self, body): self._b = body
    def json(self): return self._b


def test_the_token_is_forwarded_and_the_answer_cached(monkeypatch):
    calls = []
    async def fake(method, base, path, **kw):
        calls.append((path, kw.get("headers")))
        return FakeResp(me(role="admin"))
    monkeypatch.setattr(P, "_request", fake)
    P._cache.clear()
    a = asyncio.run(P.resolve("Bearer tok-1"))
    b = asyncio.run(P.resolve("Bearer tok-1"))
    assert a == b
    assert len(calls) == 1, "second resolve within the TTL must not call /me again"
    assert calls[0][0] == "/api/auth/me"
    assert calls[0][1] == {"Authorization": "Bearer tok-1"}


def test_different_tokens_are_never_confused(monkeypatch):
    async def fake(method, base, path, **kw):
        tok = kw["headers"]["Authorization"]
        return FakeResp(me(role="admin" if "admin" in tok else "user", building_ids=[B1]))
    monkeypatch.setattr(P, "_request", fake)
    P._cache.clear()
    a = asyncio.run(P.resolve("Bearer admin-token"))
    u = asyncio.run(P.resolve("Bearer user-token"))
    assert a.is_admin and not u.is_admin


def test_forget_drops_only_that_token(monkeypatch):
    async def fake(method, base, path, **kw):
        return FakeResp(me())
    monkeypatch.setattr(P, "_request", fake)
    P._cache.clear()
    asyncio.run(P.resolve("Bearer t1")); asyncio.run(P.resolve("Bearer t2"))
    assert len(P._cache) == 2
    P.forget("Bearer t1")
    assert len(P._cache) == 1


def test_an_upstream_401_is_a_401_with_its_reason(monkeypatch):
    async def fake(method, base, path, **kw):
        raise httpx.HTTPStatusError(
            "unauthorized", request=httpx.Request("GET", "http://x/api/auth/me"),
            response=httpx.Response(401, json={"detail": {"error": "Your session has expired.",
                                                         "reason": "expired"}}),
        )
    monkeypatch.setattr(P, "_request", fake)
    P._cache.clear()
    with pytest.raises(HTTPException) as e:
        asyncio.run(P.resolve("Bearer stale"))
    assert e.value.status_code == 401 and e.value.detail["reason"] == "expired"


def test_an_unreachable_identity_service_is_503_not_401(monkeypatch):
    # "Sign in again" would be a lie; the token may be perfectly good.
    async def fake(method, base, path, **kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(P, "_request", fake)
    P._cache.clear()
    with pytest.raises(HTTPException) as e:
        asyncio.run(P.resolve("Bearer fine"))
    assert e.value.status_code == 503 and e.value.detail["reason"] == "identity_unavailable"


def test_the_tariff_matches_operations_intelligence():
    # Two copies of one number. This is the test that keeps them one.
    from pathlib import Path
    import re
    from src.services import usage_events
    ops = (Path(__file__).resolve().parents[2] / "svc-operations-intelligence" / "src"
           / "engines" / "auth" / "usage.py").read_text(encoding="utf-8")
    m = re.search(r"TARIFF: dict\[str, float\] = (\{[^}]+\})", ops)
    assert m, "ops-intelligence TARIFF not found"
    assert eval(m.group(1)) == usage_events.TARIFF  # noqa: S307 — a literal dict from our own file
