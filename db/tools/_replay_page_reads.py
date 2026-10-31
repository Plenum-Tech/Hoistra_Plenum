"""Run inside a service container: replay GETs through the app in-process, READ-ONLY.

check_data_visibility.py copies this into svc-operations-intelligence and the work-order
service and runs it there, so a read is exercised through the very route, engine and SQL the
page gets, against the database the container is configured for. Two things make it safe:

  - every connection is opened with default_transaction_read_only=on, and the run refuses to
    start unless Postgres confirms it. A route that tries to INSERT, UPDATE, DELETE or run DDL
    fails in Postgres instead of writing — and that failure is itself a finding;
  - the ASGI transport sends no lifespan events, so the app's startup (migrations, seeding,
    table creation) never runs.

Authentication is replaced by a fixed principal for the company being checked, so no token is
minted and no sign-in is needed. Output: one JSON object per line on stdout.

    python _replay_page_reads.py <organization_id> <paths-file> [<home_org> <role>]
"""
import asyncio
import json
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

import sqlalchemy.ext.asyncio as sa_async

_real_create = sa_async.create_async_engine


def _read_only_engine(url, **kw):
    connect_args = dict(kw.pop("connect_args", {}) or {})
    settings = dict(connect_args.get("server_settings", {}) or {})
    settings["default_transaction_read_only"] = "on"
    settings["application_name"] = "hoistra-visibility-check"
    connect_args["server_settings"] = settings
    kw["connect_args"] = connect_args
    return _real_create(url, **kw)


# Before anything imports the app's db module, which builds its engine at import time.
sa_async.create_async_engine = _read_only_engine

sys.path.insert(0, "/app")
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src import db as app_db  # noqa: E402
from src.app import app  # noqa: E402

ORG = uuid.UUID(sys.argv[1])
PATHS = [line.strip() for line in open(sys.argv[2]) if line.strip() and not line.startswith("#")]
HOME_ORG = uuid.UUID(sys.argv[3]) if len(sys.argv) > 3 else ORG
ROLE = sys.argv[4] if len(sys.argv) > 4 else "admin"
USER = uuid.UUID("00000000-0000-4000-8000-00000000a0d1")

try:  # svc-operations-intelligence
    from src.api.routes.auth import current_principal
    from src.engines.auth import tokens as _tokens

    principal = _tokens.Principal(
        user_id=USER, email="visibility-check@local", organization_id=HOME_ORG, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=ROLE,
        can_ingest=False, building_ids=None,
    )
except ImportError:  # the work-order service
    from src.services.principal import Principal, current_principal

    principal = Principal(user_id=USER, email="visibility-check@local",
                          organization_id=HOME_ORG, role=ROLE, building_ids=None)
app.dependency_overrides[current_principal] = lambda: principal


def shape(body):
    """Each top-level list's length, the paging fields, and item_type counts where present."""
    if isinstance(body, list):
        return {"<list>": len(body)}
    if not isinstance(body, dict):
        return {"<scalar>": str(body)[:80]}
    out = {}
    for key, value in body.items():
        if isinstance(value, list):
            out[key] = len(value)
            types = Counter(str(v.get("item_type")) for v in value if isinstance(v, dict) and "item_type" in v)
            if types:
                out[key + ".item_type"] = dict(types)
        elif key in ("count", "total", "truncated", "limit", "ok", "error", "reason"):
            out[key] = value
    return out


async def main():
    async with app_db.engine.connect() as conn:
        guard = (await conn.execute(text("SELECT current_setting('default_transaction_read_only')"))).scalar()
    print(json.dumps({"guard": guard}), flush=True)
    if guard != "on":
        raise SystemExit("the read-only guard is not active; refusing to run")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://visibility-check", timeout=180) as client:
        for p in PATHS:
            try:
                r = await client.get(p.replace("ORG", str(ORG)))
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                rec = {"path": p, "status": r.status_code, "shape": shape(body)}
                if r.status_code >= 400:
                    rec["detail"] = str(body)[:300]
            except Exception as exc:  # noqa: BLE001 — a crashing route is a finding, not a stop
                rec = {"path": p, "status": "EXC", "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
            print(json.dumps(rec, default=str), flush=True)


asyncio.run(main())
