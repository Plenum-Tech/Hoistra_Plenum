"""Which page owns each route's data, and the gate that holds a caller to their pages.

An administrator may give a user an access role and tick the pages they may open; the identity
service returns those pages with the caller (services/principal.py). Data owned by a page the
caller does not hold is refused here, the same way and with the same answer as in every other
service.

One row per route. The rule a row follows:

    A route is tagged with the page whose OWN register it serves. A caller needs one of the
    route's pages. Being shown on another page does not add a tag.

Work orders, maintenance, PPM, journeys, the mailbox intake and the work-order chat are the
maintenance page's (``ops``). The asset register is the assets page's. Locations are where work
happens and where assets stand, so either page reads them.

Tags that are not a page: ``shared`` (every signed-in caller), ``admin``, ``public`` (answers
without the caller's pages mattering). ``NEEDS_ALL`` is for a route that cannot be given in
part: every page listed is needed.

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


def _own(tags: tuple[str, ...], prefix: str, *routes: str) -> None:
    for route in routes:
        method, path = route.split(" ", 1)
        key = (method, prefix + path)
        assert key not in PAGE_MAP, f"page_access: {key} listed twice"
        PAGE_MAP[key] = tags


O, A = "ops", "assets"

_own((O,), "/api/work-orders",
     "POST /", "GET /", "GET /filter/active", "GET /filter/pending-approval",
     "GET /{work_order_id}/history", "PATCH /bulk/status", "POST /suggest-approval",
     "GET /{work_order_id}/status-track", "GET /{work_order_id}/approval-chain",
     "PATCH /{work_order_id}/customize-chain", "POST /{work_order_id}/request-approval",
     "POST /{work_order_id}/send-approval-email", "GET /{work_order_id}",
     "PATCH /{work_order_id}", "PATCH /{work_order_id}/status",
     "POST /{work_order_id}/approve", "POST /{work_order_id}/close",
     "POST /{work_order_id}/prepare")
# Who may approve what. It has no administrator check of its own, so it follows the page.
_own((O,), "/api/admin", "GET /approval-rules", "PATCH /approval-rules/{rule_id}")
_own((O,), "/api/email",
     "GET /status", "GET /inbox", "POST /process", "POST /process/sample/stream",
     "POST /process/sample/missing-info/stream", "POST /process/sample",
     "POST /process/sample/missing-info", "POST /poll", "GET /watch/{wo_id}")
_own((O,), "/api/ppm", "GET /due", "POST /run")
_own((O,), "/api/maintenance",
     "GET /decisions", "GET /inspections", "GET /ppm", "GET /summary", "GET /next-ppm",
     "GET /inspection-intelligence", "GET /ppm/contracts", "GET /overview",
     "POST /inspection-intelligence/read", "GET /inspection-intelligence/last-read",
     "POST /ask", "GET /ask/suggestions", "GET /inspection-intelligence/{card}")
_own((O,), "/api/journeys",
     "GET /analytics/summary", "GET /by-work-order/{work_order_id}", "GET /",
     "GET /{jlog_id}", "GET /{jlog_id}/health", "PATCH /{jlog_id}/milestone")
_own((O,), "/api/chat", "POST /", "POST /email", "POST /ppm", "GET /{session_id}/history")
_own((A,), "/api", "GET /assets", "GET /asset-categories", "GET /assets/{asset_id}")
_own((A, O), "/api", "GET /locations")
# Work-order counts and asset counts in one answer.
_own((O,), "/api/dashboard", "GET /stats")
# An approver answers by the link they were sent; the request id in it is what admits them.
_own((PUBLIC,), "/api",
     "POST /work-orders/approvals/{approval_request_id}/respond",
     "POST /approvals/{approval_request_id}/respond")
_own((PUBLIC,), "", "GET /health")

NEEDS_ALL: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/api/dashboard/stats"): (O, A),
}


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
