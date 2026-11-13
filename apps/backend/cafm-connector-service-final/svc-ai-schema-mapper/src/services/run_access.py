"""Who may see, start and act on a migration run -- enforced for the whole service in one place.

Measured 6 Nov 2026 against the deployed app, with no token at all: GET /api/migration listed 70
runs for one company and 62 for another (both companies' ids are in this public repository), and
33 of the 35 migration routes answered without asking who was calling -- the uploaded workbook,
the downloads, answering a run's gates (which writes rows), re-running nodes, retrying its DDL.
services/principal.py had recorded the same on 30 Sep and narrowed cancel alone. Narrowing route
by route is how 33 stayed open, so this is a middleware: every route is closed unless it is listed
as open here, and a route added tomorrow under /api/migration/{id} is covered without anyone
remembering to.

The rule, by role:

    superadmin             any company's runs; may name the company to act for
    admin                  every run of their own company
    user, ingestion right  the runs THEY started, and may start one
    user, no such right    may see a run they started; may not act on it
    no token               401; another company's run, or a run that does not exist: 404

404 rather than 403 for another company's run, so the answer never confirms that a run with that
id exists somewhere else. A run started before this (no starter recorded) is an admin's: nobody's
is not anybody's.

Families nothing outside this service calls -- the testing pipeline, the activity log, entity
graph, sanctity checks, registry, mappings, embedding, debug, Fiix platform checks -- are
superadmin-only. They were open to the internet and used by nobody; if one is needed, it is
opened here on purpose.
"""
from __future__ import annotations

import json
import logging
import re
import time
from uuid import UUID

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.responses import Response
from sqlalchemy import text

from .principal import Principal, resolve

logger = logging.getLogger(__name__)

#: Answered for anyone: the health check the ingress and roll scripts read, and metrics.
OPEN = {("GET", "/health"), ("HEAD", "/health"), ("GET", "/metrics")}

#: A route naming a run. kind -> the table that run lives in.
_RUN = re.compile(
    r"^/api/(?P<kind>migration|schema-mapping|fiix-ingestion)/(?P<id>[0-9a-fA-F-]{36})(?:/|$)")
_TABLE = {"migration": "plenum_cafm.migration_jobs",
          "schema-mapping": "plenum_cafm.schema_mapping_jobs",
          "fiix-ingestion": "plenum_cafm.fiix_ingestion_jobs"}

#: Families with no caller outside this service (searched 6 Nov 2026: the frontend, every other
#: backend service, deploy/). Superadmin-only.
_PLATFORM_ONLY = (
    "/api/testing/", "/api/activity-log", "/api/activity-actions", "/api/entity/",
    "/api/sanctity/", "/api/registry/", "/api/mappings", "/api/embed/", "/api/debug/",
)

#: Connecting a company's CMMS signs in to it -- with credentials the caller supplies, or the
#: platform's own. An admin's action. Called by svc-deepagents' fiix_agent for the signed-in admin.
_ADMIN_ONLY = ("/api/platforms/fiix/",)

#: Exact (method, path) pairs that are platform-only. POST /api/migration starts a run from a
#: `source_blob_url` the CALLER names, which the worker then reads with the platform's storage
#: credentials -- any company's upload, given its path. Nothing calls it (the app and the chat
#: both upload through start-with-upload), so it is closed rather than made safe.
_PLATFORM_ONLY_EXACT = {("POST", "/api/migration")}

_READS = ("GET", "HEAD")

#: The document index mounted in this service (app.py, "/doc-rag"). It answers about any
#: document and any table and has no notion of who is asking, so a caller an administrator has
#: held to some pages (services/principal.Principal.pages) is kept off it. Adding a document,
#: and reading that one document's status while it is indexed, is ingesting - governed by the
#: caller's own ingest switch - and stays.
_DOC_RAG = "/doc-rag/"
_DOC_RAG_ADD = re.compile(r"^/doc-rag/documents/(upload|upload-batch)$")
_DOC_RAG_ONE = re.compile(r"^/doc-rag/documents/(?P<id>[^/]+)$")
PAGE_NOT_ALLOWED = "That is not part of the pages you have access to."
NO_INGEST_RIGHT = ("You do not have the right to add documents. An admin can give it to you.")

#: The documents a caller held to pages added, by user id: {document_id: expiry}. The index
#: records no uploader, so this service remembers what each such caller's upload returned and
#: lets them read those back while they index. In this process only (the service runs one) and
#: for a day: lost on a restart, which refuses the read rather than allowing it.
_ADDED: dict[str, dict[str, float]] = {}
_ADDED_TTL_S = 24 * 3600.0


def _added_by(user_id, document_id: str) -> bool:
    exp = _ADDED.get(str(user_id), {}).get(document_id)
    return exp is not None and exp > time.monotonic()


def _remember(user_id, document_ids: list[str]) -> None:
    now = time.monotonic()
    mine = {d: e for d, e in _ADDED.get(str(user_id), {}).items() if e > now}
    for d in document_ids:
        mine[str(d)] = now + _ADDED_TTL_S
    _ADDED[str(user_id)] = mine


def doc_rag_refused(principal: Principal, method: str, path: str) -> tuple[int, str, str] | None:
    """None when this caller may make this call to the document index; else the refusal.

    Only a caller held to pages is looked at: everyone else reaches the index as before."""
    if getattr(principal, "pages", None) is None or not path.startswith(_DOC_RAG):
        return None
    method = method.upper()
    if method == "POST" and _DOC_RAG_ADD.match(path):
        # Adding a document is ingesting: their own switch governs it.
        return None if principal.can_ingest else (403, NO_INGEST_RIGHT, "no_ingest_right")
    one = _DOC_RAG_ONE.match(path)
    if method in _READS and one and _added_by(principal.user_id, one.group("id")):
        return None
    return 403, PAGE_NOT_ALLOWED, "page_not_allowed"


async def after(request, response):
    """Note the documents a caller held to pages just added, so they may watch them index.

    Everyone else's response passes untouched. Only a successful upload is read; its body is
    handed back unchanged."""
    principal = getattr(getattr(request, "state", None), "principal", None)
    if (principal is None or getattr(principal, "pages", None) is None
            or request.method.upper() != "POST" or not 200 <= response.status_code < 300
            or not _DOC_RAG_ADD.match(route_path(request))):
        return response
    body = b"".join([chunk async for chunk in response.body_iterator])
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    ids = [data.get("document_id")] if isinstance(data, dict) else []
    ids += [d.get("document_id") for d in (data.get("documents") or [])
            if isinstance(d, dict)] if isinstance(data, dict) else []
    _remember(principal.user_id, [i for i in ids if i])
    headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
    return Response(content=body, status_code=response.status_code, headers=headers,
                    media_type=getattr(response, "media_type", None))


def _deny(code: int, error: str, reason: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"detail": {"ok": False, "error": error,
                                                              "reason": reason}})


def decide(principal: Principal, method: str, run_org, run_starter) -> tuple[int, str, str] | None:
    """None when this caller may do this to this run; else (status, message, reason).

    Pure, so the whole role table is testable without a database or a token."""
    if principal.is_superadmin:
        return None
    if (run_org is None or principal.organization_id is None
            or str(run_org) != str(principal.organization_id)):
        return 404, "No such migration.", "not_found"
    if principal.is_admin:
        return None
    if run_starter is None or str(run_starter) != str(principal.user_id):
        return (403, "This migration was started by someone else in your company. An admin can "
                     "open it.", "not_your_run")
    if method.upper() not in _READS and not principal.can_ingest:
        return (403, "You can see this migration but not change it: you do not have the right to "
                     "run migrations. An admin can give it to you.", "no_ingest_right")
    return None


_has_starter: dict[str, bool] = {}


async def has_starter_column(kind: str) -> bool:
    """Whether this run table records starters yet. Its startup patch skips itself when it cannot
    get its lock, so on that start the column may not exist; a list narrowed to "my runs" must
    then show nothing rather than fall back to the whole company."""
    from ..db import get_async_engine

    table = _TABLE[kind]
    if table not in _has_starter:
        schema, name = table.split(".")
        async with get_async_engine().connect() as conn:
            _has_starter[table] = (await conn.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name = :t AND column_name = 'started_by_user_id'"),
                {"s": schema, "t": name})).first() is not None
    return _has_starter[table]


def started_by(kind: str, user_id):
    """A WHERE clause narrowing a run query to one starter, for the ORM queries the list routes
    build. Plain SQL because the column is not on the model (see _run_owner)."""
    return text(f"{_TABLE[kind]}.started_by_user_id = CAST(:_starter AS uuid)").bindparams(
        _starter=str(user_id))


async def _run_owner(table: str, run_id: str) -> tuple[bool, object, object]:
    """(exists, organization_id, started_by_user_id) for one run, read with plain SQL.

    Plain SQL, not the ORM model: started_by_user_id is added by a startup patch that SKIPS
    itself when it cannot get its lock, and a mapped column that does not exist yet would break
    every load of the model on that start. Missing column reads as no starter recorded."""
    from ..db import get_async_engine

    engine = get_async_engine()
    async with engine.connect() as conn:
        if table not in _has_starter:
            schema, name = table.split(".")
            _has_starter[table] = (await conn.execute(text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = :s"
                " AND table_name = :t AND column_name = 'started_by_user_id'"),
                {"s": schema, "t": name})).first() is not None
        cols = "organization_id, started_by_user_id" if _has_starter[table] else "organization_id, NULL"
        # `table` is one of the two literals in _TABLE, never request input.
        row = (await conn.execute(text(f"SELECT {cols} FROM {table} WHERE id = :id"),
                                  {"id": run_id})).first()
    if row is None:
        return False, None, None
    return True, row[0], row[1]


def route_path(request: Request) -> str:
    """The path the app's own routes see: the request path with the mount prefix taken off.

    Deployed, this service runs behind nginx with `--root-path /backend/schema-mapper`, so the
    path a middleware reads is /backend/schema-mapper/api/migration/..., not /api/migration/....
    Matched on that, no rule here fires: /health is refused (which fails the roll's health
    check) and -- far worse -- the per-run company check never runs, so anyone signed in reaches
    any company's runs. Caught 6 Nov 2026 by probing the built container, after every test had
    passed against a client with no root path."""
    path = request.scope.get("path") or request.url.path
    root = (request.scope.get("root_path") or "").rstrip("/")
    if root and (path == root or path.startswith(root + "/")):
        path = path[len(root):] or "/"
    return path


async def authorize(request: Request) -> JSONResponse | None:
    """The middleware's decision for one request. None lets it through; a response refuses it.
    Sets request.state.principal for the handlers that need the caller (list, start)."""
    method, path = request.method.upper(), route_path(request)
    if method == "OPTIONS" or (method, path) in OPEN:
        return None
    try:
        principal = await resolve(request.headers.get("authorization"))
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    request.state.principal = principal

    if ((path.startswith(_PLATFORM_ONLY) or (method, path.rstrip("/")) in _PLATFORM_ONLY_EXACT)
            and not principal.is_superadmin):
        return _deny(403, "This is a platform tool, not part of the app.", "platform_only")
    if path.startswith(_ADMIN_ONLY) and not principal.is_admin:
        return _deny(403, "Only an admin can connect the company's CMMS.", "admin_only")
    held_out = doc_rag_refused(principal, method, path)
    if held_out:
        # The same body every service answers with for a page the caller does not hold.
        detail = {"ok": False, "reason": held_out[2], "error": held_out[1]}
        if held_out[2] == "page_not_allowed":
            detail["page"] = None
        return JSONResponse(status_code=held_out[0], content={"detail": detail})

    m = _RUN.match(path)
    if m:
        try:
            run_id = str(UUID(m.group("id")))
        except ValueError:
            return _deny(404, "No such migration.", "not_found")
        try:
            exists, org, starter = await _run_owner(_TABLE[m.group("kind")], run_id)
        except Exception as exc:  # noqa: BLE001 - never fail open
            logger.warning("run_access: could not read the run's owner: %s", str(exc)[:200])
            return _deny(503, "Could not check who this migration belongs to. Try again.",
                         "owner_unreadable")
        if not exists:
            return _deny(404, "No such migration.", "not_found")
        refused = decide(principal, method, org, starter)
        if refused:
            code, error, reason = refused
            return _deny(code, error, reason)
    return None


# -- for the handlers that list or start runs ----------------------------------------------------

def principal_of(request: Request) -> Principal:
    """FastAPI dependency: the caller the middleware resolved. Raises if the middleware did not
    run, so a route mounted without it fails closed rather than acting for nobody."""
    p = getattr(request.state, "principal", None)
    if p is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail={"ok": False, "error": "Sign in to do this.",
                                    "reason": "missing_token"})
    return p


def _uuid(value) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        try:
            return UUID(int=int(value))
        except (ValueError, TypeError):
            raise HTTPException(400, detail=f"Invalid organization_id UUID: {value}") from None


def company_for(principal: Principal, requested) -> UUID:
    """The company a list or a start is for. The caller's own -- the request only names one for a
    superadmin acting for a company. Naming another company is refused, never quietly replaced:
    a start that silently landed in a different company than the one named would be worse."""
    asked = _uuid(requested)
    if principal.is_superadmin:
        if asked is None and principal.organization_id is None:
            raise HTTPException(400, detail="Name the company: organization_id.")
        return asked or principal.organization_id
    if principal.organization_id is None:
        raise HTTPException(403, detail={"ok": False, "reason": "no_company",
                                         "error": "Your account is not part of a company."})
    if asked is not None and asked != principal.organization_id:
        raise HTTPException(403, detail={"ok": False, "reason": "other_company",
                                         "error": "You can only work with your own company's "
                                                  "migrations."})
    return principal.organization_id


async def require_run(principal: Principal, kind: str, run_id, method: str = "POST") -> UUID:
    """The same rule the middleware applies to a run named in the PATH, for a run named in a
    BODY or query -- e.g. the schema mapping a Fiix ingestion writes through. Returns the run's
    company; raises 404/403 exactly as the middleware would."""
    try:
        rid = str(UUID(str(run_id)))
    except (ValueError, TypeError):
        raise HTTPException(400, detail=f"Invalid id: {run_id}") from None
    exists, org, starter = await _run_owner(_TABLE[kind], rid)
    refused = (404, "No such migration.", "not_found") if not exists else \
        decide(principal, method, org, starter)
    if refused:
        code, error, reason = refused
        raise HTTPException(code, detail={"ok": False, "error": error, "reason": reason})
    return org


def starter_of(request: Request) -> Principal:
    """FastAPI dependency for a route that STARTS a run: the caller, refused here when they may
    not start one. Declare it before the route's database session -- dependencies resolve in
    signature order, so a refusal then costs no session and stores no upload."""
    principal = principal_of(request)
    may_start(principal)
    return principal


def may_start(principal: Principal) -> None:
    if principal.is_admin or principal.can_ingest:
        return
    raise HTTPException(403, detail={"ok": False, "reason": "no_ingest_right",
                                     "error": "You do not have the right to run migrations. An "
                                              "admin can give it to you."})


def only_mine(principal: Principal) -> UUID | None:
    """The starter a run list is narrowed to: None (no narrowing) for an admin or superadmin,
    the caller's own id for anyone else."""
    return None if principal.is_admin else principal.user_id


async def record_starter(session, kind: str, run_id, principal: Principal) -> None:
    """Stamp who started a run. Best effort: if the column is not there yet (its patch was
    skipped this start), the run is simply an admin's until it is."""
    try:
        await session.execute(text(
            f"UPDATE {_TABLE[kind]} SET started_by_user_id = :u WHERE id = :id"),
            {"u": str(principal.user_id), "id": str(run_id)})
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        logger.warning("run_access: starter not recorded for %s: %s", run_id, str(exc)[:200])
