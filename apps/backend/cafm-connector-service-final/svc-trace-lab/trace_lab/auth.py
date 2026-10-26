"""Who may use Trace Lab, and for which company (G0 addendum 2.4).

The caller is svc-deepagents' own current_principal, reused by import: it forwards the bearer to
operations-intelligence GET /api/auth/me and returns a Principal (role "user", "admin" or
"superadmin"). The helpers below are pure. They read that Principal and the organization_id the
request named, and raise the house error envelope (db.http_error).

* A company is named only by a superadmin. Anyone else naming another company gets 403
  bad_company; naming their own is the same as naming none.
* Company admins, and superadmins acting for a company, review (require_reviewer).
* The platform owner is a superadmin acting for no company (require_platform).
* Plain users never open Trace Lab (403 reviewers_only).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends

from src.services.principal import Principal, current_principal

from .db import http_error

#: The signed-in caller, as a route parameter default: `principal: Principal = auth.caller`.
caller = Depends(current_principal)

SUPERADMIN = "superadmin"
REVIEWERS_ONLY = "Only company admins and the platform team can open Trace Lab."
NAME_A_COMPANY = "Choose a company to view as first."
PLATFORM_ONLY = "Only the platform team can do this, outside any one company."


def _named(organization_id: str | None) -> str | None:
    """The company the request named, as a canonical uuid string; 400 bad_company if malformed."""
    raw = str(organization_id).strip() if organization_id is not None else ""
    if not raw:
        return None
    try:
        return str(UUID(raw))
    except ValueError:
        raise http_error(400, "bad_company", "That is not a company id.") from None


def _own(principal: Principal) -> str | None:
    return str(principal.organization_id) if principal.organization_id else None


def acting_org(principal: Principal, organization_id: str | None) -> str | None:
    """The company this request acts for. A superadmin: the company named, or None (the platform
    view). Anyone else: their own company; naming another one is 403 bad_company."""
    named = _named(organization_id)
    if principal.role == SUPERADMIN:
        return named
    own = _own(principal)
    if named is not None and named != own:
        raise http_error(403, "bad_company", "Only the platform team can open another company.")
    return own


def require_reader(principal: Principal, organization_id: str | None) -> str | None:
    """Read access: a company admin (own company) or a superadmin (the company named, or None for
    the platform view). Plain users, and admins with no company, get 403 reviewers_only."""
    if not principal.is_admin or (principal.role != SUPERADMIN and _own(principal) is None):
        raise http_error(403, "reviewers_only", REVIEWERS_ONLY)
    return acting_org(principal, organization_id)


def require_reviewer(principal: Principal, organization_id: str | None) -> str:
    """Review, golden sets and golden runs: a company admin, or a superadmin acting for a company.
    Returns the company id. 403 reviewers_only; a superadmin naming no company gets 400
    name_a_company."""
    org = require_reader(principal, organization_id)
    if org is None:
        raise http_error(400, "name_a_company", NAME_A_COMPANY)
    return org


def require_platform(principal: Principal, organization_id: str | None = None) -> None:
    """The platform owner: a superadmin acting for no company. Everyone else 403 platform_only."""
    if principal.role != SUPERADMIN or _named(organization_id) is not None:
        raise http_error(403, "platform_only", PLATFORM_ONLY)
