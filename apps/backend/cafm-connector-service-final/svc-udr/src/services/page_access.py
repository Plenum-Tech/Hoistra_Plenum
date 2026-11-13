"""Who may reach each route, and the gate that holds a caller to their pages.

An administrator may give a user an access role and tick the pages they may open; the identity
service returns those pages with the caller (services/principal.py). Data owned by a page the
caller does not hold is refused, the same way and with the same answer as in every other
service.

This service reads whatever table it is asked for, so most of it is not any one page's:

  * the catalogue, the agent and the table routes are administrator-only already, and a
    caller with an access role is always a plain user;
  * the saved spaces are names and kinds — no rows of anybody's register;
  * the saved run history keeps a snapshot of the rows each run returned, from whichever
    table that was. It cannot be given in part, so a caller held to some pages needs every
    page to read or add to it.

tests/unit/test_page_gate.py walks the application and fails, naming the route, for any route
with no row here.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from .principal import Principal, current_principal

PAGES = ("buildings", "compliance", "energy", "vendors", "ops", "assets")
SHARED, ADMIN, PUBLIC = "shared", "admin", "public"

#: What a caller is told when they ask for data owned by a page they do not hold. The same
#: sentence every service uses, naming nothing internal.
PAGE_NOT_ALLOWED = "That is not part of the pages you have access to."

PAGE_MAP: dict[tuple[str, str], tuple[str, ...]] = {}
NEEDS_ALL: dict[tuple[str, str], tuple[str, ...]] = {}


def _own(tags: tuple[str, ...], prefix: str, *routes: str, every: bool = False) -> None:
    for route in routes:
        method, path = route.split(" ", 1)
        key = (method, prefix + (path if path != "/" else ""))
        assert key not in PAGE_MAP, f"page_access: {key} listed twice"
        PAGE_MAP[key] = tags
        if every:
            NEEDS_ALL[key] = tags


_own((ADMIN,), "/api/catalog", "GET /", "GET /search", "GET /{table}", "POST /rebuild")
_own((ADMIN,), "/api/agent", "POST /query")
_own((ADMIN,), "", "GET /api/tables/")
_own((ADMIN,), "/api/tables",
     "GET /{table}/schema", "GET /{table}/records", "POST /{table}/records/search",
     "GET /{table}/records/{record_id}", "POST /{table}/records",
     "PATCH /{table}/records/{record_id}", "DELETE /{table}/records/{record_id}",
     "POST /query/select")
_own(PAGES, "/api/udr",
     "POST /runs", "GET /runs", "GET /scripts", "PATCH /runs/{run_id}", every=True)
_own((SHARED,), "/api/spaces", "GET /", "POST /", "PATCH /{space_id}", "DELETE /{space_id}")
_own((PUBLIC,), "", "GET /health")


def tags_for(method: str, path: str | None) -> tuple[str, ...] | None:
    """The tags of one route, by its method and path TEMPLATE, or None if it has no row."""
    if not path:
        return None
    method = "GET" if method.upper() == "HEAD" else method.upper()
    return PAGE_MAP.get((method, path))


class PageNotAllowed(HTTPException):
    """The caller asked for data owned by a page they do not hold."""


def refusal(page: str | None) -> PageNotAllowed:
    return PageNotAllowed(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"ok": False, "reason": "page_not_allowed", "page": page,
                "error": PAGE_NOT_ALLOWED},
    )


async def page_not_allowed_handler(_request: Request, exc: PageNotAllowed) -> JSONResponse:
    """Answer with the same body every service gives for an unheld page.

    This service wraps its errors in its own envelope, which turns a structured detail into
    one string. A client has to recognise this refusal by its reason — it re-reads the
    caller's pages when it sees one — so it goes out in the shape the other services use.
    """
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


async def page_gate(request: Request, principal: Principal = Depends(current_principal)) -> None:
    """Hold the caller to the pages they were given. Attached where the routers are mounted.

    A caller with every page (``pages is None``: a normal user, an admin, a superadmin) is
    not looked at, so their path is unchanged. A route with no row is refused to a restricted
    caller: a forgotten row must fail closed for the people it would expose data to.
    """
    pages = principal.pages
    if pages is None:
        return
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    tags = tags_for(request.method, path)
    if tags is None:
        raise refusal(None)
    owners = [t for t in tags if t in PAGES]
    if len(owners) == len(tags) and not any(p in pages for p in owners):
        raise refusal(owners[0] if owners else None)
    method = "GET" if request.method.upper() == "HEAD" else request.method.upper()
    for page in NEEDS_ALL.get((method, path), ()):
        if page not in pages:
            raise refusal(page)
