"""The chat cannot be talked into reading, or uploading, a file it was not given.

Found 6 Nov 2026, live: POST /backend/deep-agents/api/migration/start answered with no token, and
start_migration did `Path(file_path).read_bytes()` on whatever it was handed and uploaded it as a
migration. With the schema-mapper's /source route open too, `/app/.env` -- every secret in the
image -- went in and could come straight back out. The route had existed since 7 Sep.

The model can call start_migration itself, and read_file / write_file joined its `path` onto a
session directory with os.path.join, which DISCARDS everything before an absolute path:
read_file('/app/.env') read the secrets, '../' climbed out, write_file could overwrite code. A
document that talks the model into a path is the same hole with no route involved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# -- start_migration: only a file this conversation was given ----------------------------------------

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    from src.agents import migration_agent
    from src.config import settings

    up = tmp_path / "uploads"
    up.mkdir()
    monkeypatch.setattr(settings, "deep_agents_upload_dir", str(up))
    return up, migration_agent


def test_an_uploaded_file_is_accepted(uploads):
    up, ma = uploads
    f = up / "sess_assets.csv"
    f.write_text("a,b\n1,2\n")
    assert ma._given_file(str(f)) == f.resolve()


def test_a_file_elsewhere_on_the_server_is_refused(uploads, tmp_path):
    """It EXISTS -- and the answer is the same as for one that does not."""
    _, ma = uploads
    secret = tmp_path / ".env"
    secret.write_text("DB_PASSWORD=hunter2\n")
    assert ma._given_file(str(secret)) is None
    assert ma._given_file(str(tmp_path / "no-such-file")) is None


def test_climbing_out_of_the_upload_directory_is_refused(uploads, tmp_path):
    up, ma = uploads
    (tmp_path / "outside.csv").write_text("x\n")
    assert ma._given_file(str(up / ".." / "outside.csv")) is None


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs admin rights on Windows")
def test_a_symlink_out_of_the_upload_directory_is_refused(uploads, tmp_path):
    up, ma = uploads
    target = tmp_path / ".env"
    target.write_text("SECRET=1\n")
    (up / "innocent.csv").symlink_to(target)
    assert ma._given_file(str(up / "innocent.csv")) is None


async def test_the_tool_says_the_same_thing_whether_or_not_the_file_exists(uploads, tmp_path):
    """No answer that tells a caller which files are on the server."""
    _, ma = uploads
    (tmp_path / "real.csv").write_text("a\n1\n")
    a = await ma.start_migration.ainvoke({"file_path": str(tmp_path / "real.csv")})
    b = await ma.start_migration.ainvoke({"file_path": str(tmp_path / "absent.csv")})
    assert a == b == {"error": ma._NOT_GIVEN}


async def test_the_multi_file_tool_refuses_the_same_way(uploads, tmp_path):
    up, ma = uploads
    good = up / "s_one.csv"
    good.write_text("a\n1\n")
    out = await ma.start_migration_multi.ainvoke(
        {"file_paths": [str(good), "/app/.env"], "cmms_name": "x"})
    assert out == {"error": ma._NOT_GIVEN}


# -- read_file / write_file stay inside the session ----------------------------------------------------

@pytest.mark.parametrize("path", ["/app/.env", "../../etc/passwd", "a/../../../x",
                                  os.path.abspath(__file__)])
async def test_read_file_will_not_leave_the_session(path):
    from src.agents import meta_tools
    out = json.loads(await meta_tools.read_file.ainvoke({"path": path}))
    assert out == {"error": meta_tools._OUTSIDE_SESSION}


async def test_write_file_will_not_leave_the_session(tmp_path):
    from src.agents import meta_tools
    target = tmp_path / "overwritten.py"
    out = json.loads(await meta_tools.write_file.ainvoke({"path": str(target), "content": "x"}))
    assert out == {"error": meta_tools._OUTSIDE_SESSION}
    assert not target.exists()


async def test_a_relative_path_still_round_trips():
    from src.agents import meta_tools
    await meta_tools.write_file.ainvoke({"path": "notes/step1.txt", "content": "kept"})
    assert await meta_tools.read_file.ainvoke({"path": "notes/step1.txt"}) == "kept"


# -- the route asks who is calling -----------------------------------------------------------------------

def test_the_migration_routes_refuse_an_anonymous_caller():
    """Driven through the router itself, so a route added to it later is refused too."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.migration import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    for route in router.routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            path = route.path.replace("{migration_id}", "00000000-0000-0000-0000-000000000000")
            res = c.request(method, path, json={"file_path": "/app/.env", "cmms_name": "x",
                                                "organization_id": "x"})
            assert res.status_code == 401, f"{method} {route.path} -> {res.status_code}"


# -- the person travels with the call, and only when there is one ---------------------------------------

def test_the_callers_token_goes_with_a_bare_client_call():
    from src.http_client import caller_authorization, caller_headers, caller_organization_id
    t1 = caller_authorization.set("Bearer abc")
    t2 = caller_organization_id.set("org-1")
    try:
        assert caller_headers() == {"Authorization": "Bearer abc",
                                    "X-Acting-Organization-Id": "org-1"}
    finally:
        caller_authorization.reset(t1)
        caller_organization_id.reset(t2)


def test_no_caller_means_no_header_at_all():
    """In a fresh context: an earlier test in the same run can leave the variables set, which is
    the reused-context leak the middleware guards against by setting them on every request."""
    import contextvars

    from src.http_client import caller_headers
    assert contextvars.Context().run(caller_headers) == {}


def test_every_schema_mapper_client_in_the_agents_carries_the_caller():
    """The bare httpx clients these agents open against the schema-mapper are the ones that
    went out with no token. A new one without the header fails here."""
    import re
    root = Path(__file__).resolve().parent.parent / "src" / "agents"
    missing = []
    for name in ("migration_agent.py", "fiix_agent.py", "schema_mapper_agent.py",
                 "single_door_flow.py"):
        src = (root / name).read_text(encoding="utf-8")
        for m in re.finditer(r"httpx\.AsyncClient\((.{0,400}?)\)\s+as\s", src, re.S):
            args = m.group(1)
            if ("_BASE" in args or "migration_base_url" in args) and "caller_headers()" not in args:
                missing.append(f"{name}:{src.count(chr(10), 0, m.start()) + 1}")
    assert not missing, missing
