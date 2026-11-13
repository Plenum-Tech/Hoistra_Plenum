"""Which page owns each record kind, and the gate that holds a caller to their pages.

An administrator may give a user an access role and tick the pages they may open; the identity
service returns those pages with the caller (api/security.py). Data owned by a page the caller
does not hold is refused here, the same way and with the same answer as in every other service.

The CRUD surface is one REST resource per record kind, so ownership is one row per resource:
every route under ``/assets`` is the assets register, every route under ``/vendors`` the vendor
master. The rule a row follows:

    A resource is tagged with the page whose OWN register it is. A caller needs one of its
    pages. Being shown on another page does not add a tag.

Locations are where work happens and where assets stand, so either page reads them. Tags that
are not a page: ``shared`` (every signed-in caller) and ``admin`` (organisation, users and the
permission tables, which keep whatever check they already had).

tests/unit/test_page_gate.py walks the router and fails, naming the resource, for any resource
with no row here.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from cafm_connector.api.security import Principal, current_principal

PAGES = ("buildings", "compliance", "energy", "vendors", "ops", "assets")
SHARED, ADMIN = "shared", "admin"

#: What a caller is told when they ask for data owned by a page they do not hold. The same
#: sentence every service uses, naming nothing internal.
PAGE_NOT_ALLOWED = "That is not part of the pages you have access to."

_ASSETS, _VENDORS, _OPS = ("assets",), ("vendors",), ("ops",)

RESOURCE_PAGES: dict[str, tuple[str, ...]] = {
    # the asset register, and the stock kept for it
    "assets": _ASSETS, "asset-categories": _ASSETS, "asset-documents": _ASSETS,
    "asset-readings": _ASSETS, "spare-parts": _ASSETS, "inventory-transactions": _ASSETS,
    "locations": ("assets", "ops"),
    # the vendor master and what is agreed with each vendor
    "vendors": _VENDORS, "vendor-contacts": _VENDORS, "vendor-contracts": _VENDORS,
    "sla-policies": _VENDORS,
    # work orders, planned maintenance and the people who do it
    "work-orders": _OPS, "work-order-tasks": _OPS, "work-order-comments": _OPS,
    "work-order-attachments": _OPS, "work-order-history": _OPS, "work-order-parts": _OPS,
    "maintenance-history": _OPS, "maintenance-plans": _OPS, "technicians": _OPS,
    "technician-skills": _OPS,
    # a person's own notifications
    "notifications": (SHARED,),
    # the organisation, its users and the permission tables
    "organizations": (ADMIN,), "users": (ADMIN,), "roles": (ADMIN,), "permissions": (ADMIN,),
    "user-roles": (ADMIN,), "role-permissions": (ADMIN,), "audit-logs": (ADMIN,),
}

_MOUNT = "/plenum/"


def resource_of(path: str | None) -> str | None:
    """The resource a route path names: the first segment under the router's mount."""
    if not path:
        return None
    tail = path.split(_MOUNT, 1)[1] if _MOUNT in path else path.lstrip("/")
    return tail.split("/", 1)[0] or None


def tags_for(path: str | None) -> tuple[str, ...] | None:
    """The tags of the resource a route path names, or None if it has no row."""
    return RESOURCE_PAGES.get(resource_of(path) or "")


def refusal(page: str | None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"ok": False, "reason": "page_not_allowed", "page": page,
                "error": PAGE_NOT_ALLOWED},
    )


async def page_gate(request: Request, principal: Principal = Depends(current_principal)) -> None:
    """Hold the caller to the pages they were given. Attached to the router itself.

    A caller with every page (``pages is None``: a normal user, an admin, a superadmin) is
    not looked at, so their path is unchanged. A resource with no row is refused to a
    restricted caller: a forgotten row must fail closed for the people it would expose data to.
    """
    pages = principal.pages
    if pages is None:
        return
    route = request.scope.get("route")
    tags = tags_for(getattr(route, "path", None))
    if tags is None:
        raise refusal(None)
    owners = [t for t in tags if t in PAGES]
    if len(owners) == len(tags) and not any(p in pages for p in owners):
        raise refusal(owners[0] if owners else None)
