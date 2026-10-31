"""The building is the access boundary. This is the one place that says so.

Thirty routes in this service take organization_id from the client and none derives it from
the signed-in caller; no route filters by building at all. So tenancy is a convention the
client is trusted to follow, and a user allocated to one building can read every building
by asking. The spec calls this out as the important backend requirement, and it is.

Everything here resolves to one object, ``Scope``, built from the Principal that
principal_from_token already reads from the database on every request:

    organization_id   the company. A superadmin may name another; nobody else may.
    building_ids      None for an admin (the whole company), a tuple for a user — and an
                      EMPTY tuple is a real answer meaning "allocated to nothing".
    can_ingest        whether new data may be added at all.

Routes depend on ``scope`` and hand it to the engines, which call ``building_filter`` to
get the SQL fragment that narrows a building-keyed query and ``assert_building`` before
writing anything against a building. A route that does neither is a route the boundary does
not reach, and the two helpers are shaped so that using them is shorter than not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, status

from . import roles as role_engine
from . import tokens as token_engine


@dataclass(frozen=True)
class Scope:
    """What the caller may see and do, resolved once per request."""

    user_id: UUID
    role: str
    organization_id: UUID | None
    #: None = unrestricted within the organisation. () = restricted to nothing.
    building_ids: tuple[UUID, ...] | None
    can_ingest: bool

    @property
    def restricted(self) -> bool:
        return self.building_ids is not None

    @property
    def is_admin(self) -> bool:
        return role_engine.at_least(self.role, role_engine.ADMIN)

    @property
    def is_superadmin(self) -> bool:
        return role_engine.at_least(self.role, role_engine.SUPERADMIN)

    def allows_building(self, building_id: UUID | str | None) -> bool:
        """Whether this caller may read or write against one building."""
        if building_id is None:
            return False
        if not self.restricted:
            return True
        try:
            return UUID(str(building_id)) in (self.building_ids or ())
        except (ValueError, TypeError):
            return False


def _forbidden(error: str, reason: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"ok": False, "error": error, "reason": reason, **extra},
    )


def scope_for(
    principal: token_engine.Principal, requested_org: UUID | None = None
) -> Scope:
    """Build the scope, honouring an explicit organisation only from a superadmin.

    Anyone else asking for a company other than their own gets 403 rather than silently
    their own — a client that sends the wrong id has a bug worth surfacing, and silently
    substituting would hide it until it mattered.
    """
    org = principal.organization_id
    if requested_org is not None and requested_org != org:
        if not role_engine.at_least(principal.role, role_engine.SUPERADMIN):
            raise _forbidden(
                "You can only work within your own company.", "wrong_organization",
                your_organization_id=str(org) if org else None,
            )
        org = requested_org
    # A selected building narrows what the caller sees to that one building — for a plain
    # user only if it is among the ones they are allocated, for an admin only if it is in
    # their company (checked where it is set). It can never widen: a selection that is not
    # allowed is ignored, not honoured.
    building_ids = principal.building_ids
    sel = getattr(principal, "selected_building_id", None)
    if sel is not None and (building_ids is None or sel in building_ids):
        building_ids = (sel,)
    return Scope(
        user_id=principal.user_id,
        role=principal.role,
        organization_id=org,
        building_ids=building_ids,
        can_ingest=principal.can_ingest,
    )


def organization_for(scope: Scope, requested: UUID | None) -> UUID | None:
    """The company a request acts in, given the one the client named — if any.

    The route-level ``scope`` dependency already applies this rule to the query string.
    This is the same rule for a company named in a request BODY, or for a route whose
    query parameter the client left out: the caller's own company by default; a different
    one is 403 unless the caller is a superadmin, who gets the company they asked for.
    Without this, an omitted organization_id reached the engines as None, which they read
    as "every company".
    """
    if requested is None or requested == scope.organization_id:
        return scope.organization_id
    if not scope.is_superadmin:
        raise _forbidden(
            "You can only work within your own company.", "wrong_organization",
            your_organization_id=str(scope.organization_id) if scope.organization_id else None,
        )
    return requested


#: The vendors with any footprint on a set of buildings — a certificate filed for one, a
#: work order raised on one, an invoice verified against one. Vendor tables carry no
#: building of their own, so this is what "the vendors for my buildings" means wherever a
#: restricted caller asks about vendors. vendor_id is uuid on all three tables.
VENDORS_ON_BUILDINGS_SQL = (
    "(SELECT c.vendor_id FROM plenum_cafm.compliance_certificates c"
    " WHERE c.vendor_id IS NOT NULL AND c.building_id = ANY(CAST(:{key} AS uuid[]))"
    " UNION SELECT w.vendor_id FROM plenum_cafm.work_orders w"
    " WHERE w.vendor_id IS NOT NULL AND w.building_id = ANY(CAST(:{key} AS uuid[]))"
    " UNION SELECT i.vendor_id FROM plenum_cafm.invoices i"
    " WHERE i.vendor_id IS NOT NULL AND i.building_id = ANY(CAST(:{key} AS uuid[])))"
)
#: assets.id is varchar on one deployment and uuid on another; compared as text it is both.
ASSETS_ON_BUILDINGS_SQL = (
    "(SELECT a.id::text FROM plenum_cafm.assets a WHERE a.building_id = ANY(CAST(:{key} AS uuid[])))"
)
#: A document placed on a building — the link contracts and invoices reach a building by.
DOCUMENTS_ON_BUILDINGS_SQL = (
    "(SELECT d.document_id FROM plenum_cafm.documents d WHERE d.building_id = ANY(CAST(:{key} AS uuid[])))"
)


def _ids_or_none(building_ids: tuple[UUID, ...] | None) -> tuple[list[str] | None, bool]:
    """(ids, refuse_all): None = unrestricted; [] with refuse_all = allocated to nothing."""
    if building_ids is None:
        return None, False
    ids = [str(b) for b in building_ids]
    return ids, not ids


def building_predicate(
    building_ids: tuple[UUID, ...] | None, column: str = "building_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """``building_filter`` for an engine handed the ids rather than the Scope."""
    ids, refuse = _ids_or_none(building_ids)
    if ids is None:
        return "", {}
    if not _SAFE_COLUMN.match(column):
        raise ValueError(f"unsafe column reference: {column!r}")
    if refuse:
        return " AND FALSE", {}
    key = f"{prefix}_building_ids"
    return f" AND {column} = ANY(CAST(:{key} AS uuid[]))", {key: ids}


def _derived_predicate(building_ids, column, template, cast, prefix):
    ids, refuse = _ids_or_none(building_ids)
    if ids is None:
        return "", {}
    if not _SAFE_COLUMN.match(column):
        raise ValueError(f"unsafe column reference: {column!r}")
    if refuse:
        return " AND FALSE", {}
    key = f"{prefix}_building_ids"
    return f" AND {column}{cast} IN {template.format(key=key)}", {key: ids}


def vendor_predicate(
    building_ids: tuple[UUID, ...] | None, column: str = "vendor_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """Narrow a vendor-keyed query to the vendors with a footprint on these buildings."""
    sql, params = _derived_predicate(
        building_ids, column, "(SELECT v.vendor_id::text FROM " + VENDORS_ON_BUILDINGS_SQL + " v)",
        "::text", prefix,
    )
    return sql, params


def asset_predicate(
    building_ids: tuple[UUID, ...] | None, column: str = "asset_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """Narrow an asset-keyed query to the assets on these buildings."""
    return _derived_predicate(building_ids, column, ASSETS_ON_BUILDINGS_SQL, "::text", prefix)


def document_predicate(
    building_ids: tuple[UUID, ...] | None, column: str = "document_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """Narrow a document-keyed query to documents placed on these buildings."""
    return _derived_predicate(building_ids, column, DOCUMENTS_ON_BUILDINGS_SQL, "", prefix)


def orm_where(q, sql: str, params: dict[str, Any]):
    """Apply one of the predicates above to a SQLAlchemy select — the ``" AND …"`` fragment
    becomes a bound text clause, so ORM engines take the same boundary as raw-SQL ones."""
    if not sql:
        return q
    from sqlalchemy import text as _text

    clause = _text(sql[len(" AND "):])
    if params:
        clause = clause.bindparams(**params)
    return q.where(clause)


def building_filter(
    scope: Scope, column: str = "building_id", *, prefix: str = "scope"
) -> tuple[str, dict[str, Any]]:
    """A SQL fragment and its parameters that narrow a building-keyed query to the scope.

    Returns ("", {}) for an unrestricted caller, so it can be appended unconditionally:

        sql, params = building_filter(scope, "d.building_id")
        text(f"SELECT … FROM plenum_cafm.documents d WHERE d.building_id IS NOT NULL {sql}")

    ``column`` is an identifier the CALLER chose, never one from a request — the same rule
    as everywhere else in this repo. It is validated anyway, because a helper used in thirty
    places will one day be handed a variable.
    """
    if not scope.restricted:
        return "", {}
    if not _SAFE_COLUMN.match(column):
        raise ValueError(f"unsafe column reference: {column!r}")
    ids = [str(b) for b in (scope.building_ids or ())]
    if not ids:
        # Allocated to nothing. A predicate that matches no row, rather than no predicate,
        # which would match every row — the exact failure this module exists to remove.
        return " AND FALSE", {}
    key = f"{prefix}_building_ids"
    return f" AND {column} = ANY(CAST(:{key} AS uuid[]))", {key: ids}


def assert_building(scope: Scope, building_id: UUID | str | None, *, action: str = "access") -> UUID:
    """403 unless this caller may act on this building. Returns the id as a UUID."""
    if building_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "A building is required.", "reason": "no_building"},
        )
    try:
        bid = UUID(str(building_id))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "building_id is not a UUID.", "reason": "bad_building"},
        ) from None
    if not scope.allows_building(bid):
        raise _forbidden(
            f"You are not allocated to that building, so you cannot {action} its data.",
            "building_not_allocated", building_id=str(bid),
        )
    return bid


#: table -> the scope columns it has, probed once. The schema does not change between
#: requests, and re-reading information_schema per request is a needless round trip.
_row_scope_columns: dict[str, frozenset[str]] = {}


async def assert_owned(
    session: Any,
    scope: Scope,
    table: str,
    row_id: UUID | str | None,
    *,
    action: str = "read",
    id_column: str = "id",
) -> None:
    """403 unless the row this caller named belongs to them. 404 if it does not exist.

    ``building_filter`` narrows a LIST. It does nothing for a route that takes an id in the
    path, because there is no list to narrow — the caller has already named the row, and the
    only question is whether it is theirs. Twenty-three GET routes ask for a row by id and
    never asked that question, so an id from one company read a row from another:

        GET /api/energy/meters/<any meter id>/readings   -> 584,042 readings, unfiltered
        GET /api/energy/assets/<any asset id>/work-history
        GET /api/contract-performance/contracts/<any id>

    The scope columns are read from information_schema rather than declared per table,
    because the two databases disagree on which tables carry which, and a hardcoded column
    that is absent on one of them fails as "no restriction" — the wrong direction to fail.

    A row whose table carries no scope column at all cannot be checked here and is allowed
    through; that is the same decision taken elsewhere for such tables, and the ones that
    matter (report_card_runs, ingestion_documents) need a join route rather than a column.
    """
    if row_id is None or scope.is_superadmin:
        return
    if not _SAFE_COLUMN.match(table) or not _SAFE_COLUMN.match(id_column):
        raise ValueError(f"unsafe identifier: {table!r}.{id_column!r}")

    from sqlalchemy import text  # local: access.py is imported by modules with no ORM need

    columns = _row_scope_columns.get(table)
    if columns is None:
        found = await session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'plenum_cafm' AND table_name = :t "
                "AND column_name IN ('organization_id', 'building_id')"
            ),
            {"t": table},
        )
        columns = frozenset(str(r[0]) for r in found)
        _row_scope_columns[table] = columns
    if not columns:
        return

    selected = ", ".join(f'"{c}"::text AS {c}' for c in sorted(columns))
    row = (
        await session.execute(
            text(f'SELECT {selected} FROM plenum_cafm."{table}" WHERE "{id_column}"::text = :rid'),
            {"rid": str(row_id)},
        )
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"ok": False, "error": "No such record.", "reason": "not_found"},
        )

    if "organization_id" in columns and scope.organization_id is not None:
        if str(row.get("organization_id") or "") != str(scope.organization_id):
            # 404, not 403: a 403 confirms the row exists and belongs to somebody else, which
            # is a fact about another company's data and is not ours to disclose.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"ok": False, "error": "No such record.", "reason": "not_found"},
            )

    if "building_id" in columns and scope.restricted:
        if not scope.allows_building(row.get("building_id")):
            raise _forbidden(
                f"You are not allocated to that building, so you cannot {action} its data.",
                "building_not_allocated",
            )


def assert_admin(scope: Scope, *, action: str = "do that") -> None:
    """403 unless the caller administers the company. Creating a building is one such
    action: a user is allocated to buildings, they do not make them."""
    if not scope.is_admin:
        raise _forbidden(
            f"Only a company administrator can {action}.", "admin_required",
        )


def assert_can_ingest(scope: Scope) -> None:
    """403 unless the caller may add data. Read-only users are told so, not stonewalled."""
    if not scope.can_ingest:
        raise _forbidden(
            "Your account can view its buildings but cannot ingest data. "
            "Ask your company administrator to grant ingestion.",
            "cannot_ingest",
        )


import re  # noqa: E402 — placed after the definitions it guards for readability

_SAFE_COLUMN = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?$")


# ── FastAPI dependencies ─────────────────────────────────────────────────────────────
# Imported lazily inside the dependency so this module does not import the auth router
# (which imports this) at module load.

async def scope(
    organization_id: UUID | None = Query(
        None,
        description="Superadmin only: act as this company. Everyone else is scoped to "
                    "their own and gets 403 for any other value.",
    ),
    principal: token_engine.Principal = Depends(lambda: None),  # replaced below
) -> Scope:  # pragma: no cover — replaced by _bind() at import of the routes package
    raise RuntimeError("access.scope must be bound with access.bind(current_principal)")


def bind(current_principal_dep, get_session_dep=None):
    """Produce the real ``scope`` dependency given the router's current_principal.

    Done this way because current_principal lives in api/routes/auth.py, which imports this
    module; a direct import here would be circular. The routes package calls bind() once and
    exports the result.

    The enforcement switch is read ONCE, here, and decides which dependency is returned.
    Settings are fixed for the life of the process, and choosing at bind time keeps the
    signed-in path a plain ``Depends(current_principal)`` — so FastAPI's dependency
    overrides still reach it in tests, and a route cannot be accidentally half-enforced.
    """
    from ...config import settings  # local: config imports nothing from here

    if not settings.auth_enforce_scope:
        import structlog
        structlog.get_logger(__name__).warning(
            "access.scope_enforcement_disabled",
            note="every domain route is open and unscoped; AUTH_ENFORCE_SCOPE=false",
        )

        async def _unscoped(
            organization_id: UUID | None = Query(None),
        ) -> Scope:
            # The boundary is switched off. Said on every request, because a switch meant
            # for one bad afternoon becomes permanent when it is silent.
            structlog.get_logger(__name__).warning("access.scope_enforcement_disabled")
            return Scope(user_id=UUID(int=0), role=role_engine.SUPERADMIN,
                         organization_id=organization_id, building_ids=None, can_ingest=True)

        return _unscoped, _unscoped

    async def _scope(
        organization_id: UUID | None = Query(
            None,
            description="Superadmin only: act as this company. Everyone else is scoped to "
                        "their own and gets 403 for any other value.",
        ),
        principal: token_engine.Principal = Depends(current_principal_dep),
    ) -> Scope:
        return scope_for(principal, organization_id)

    async def _ingest_scope(s: Scope = Depends(_scope)) -> Scope:
        assert_can_ingest(s)
        return s

    return _scope, _ingest_scope
