"""Which pages a person may open: the second boundary, after buildings.

The building says *where* a plain user may look. This says *what*. An administrator may give
a plain user an access role — a label — together with the pages that user may open, and from
then on data owned by any other page is withheld from them everywhere: the screens, the chat
and the API.

    normal user   platform role ``user``, no access role   → every page (``pages is None``)
    role user     platform role ``user``, an access role   → only the ticked pages
    admin / superadmin                                      → every page, whatever the row says

Two rules run through everything here, and each exists because of how this goes wrong:

  **None means unrestricted, an empty set means nothing.** They are different facts, the same
  way ``building_ids`` keeps them apart. Collapsing them turns "allocated to no page" into
  "sees everything", which is the opposite of what was asked for.

  **Reading fails closed.** A row holding a page this code has never heard of loses that page;
  a row holding a role with no valid page behind it is restricted to nothing. A restricted row
  is never read as an unrestricted one.

Pure apart from ``columns_ready``, so every branch is testable without a database.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text

from . import roles as role_engine

#: The pages an administrator can tick, in the order they are shown and stored. These are the
#: report ids the frontend (logic/instances.js) and the chat (agents/spaces.py) already use.
PAGES: tuple[str, ...] = ("buildings", "compliance", "energy", "vendors", "ops", "assets")

#: The access roles, and what each is called. A role is a label: it ticks no page by itself.
ACCESS_ROLES: dict[str, str] = {
    "portfolio_management": "Portfolio Management",
    "property_manager": "Property Manager",
    "general_user": "General User",
    "compliance_officer": "Compliance Officer",
}

#: Tags a route can carry that are not a page. ``shared`` is open to every signed-in caller,
#: ``ingest`` is governed by the can-ingest switch, ``admin`` is already administrator-only,
#: ``public`` takes no token at all.
SHARED, INGEST, ADMIN, PUBLIC = "shared", "ingest", "admin", "public"

_PAGE_SET = frozenset(PAGES)


class AccessRoleError(Exception):
    """An assignment an administrator may not make. ``reason`` is for a client to switch on,
    ``message`` is a sentence a person can read."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def clean_pages(raw: Any) -> frozenset[str]:
    """The known pages in ``raw``. Anything else — a string, a number, an unknown key — is
    dropped, so junk in the column can only ever narrow what a person sees."""
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(str(p).strip().lower() for p in raw if isinstance(p, str)) & _PAGE_SET


def resolve(
    platform_role: str | None, access_role: str | None, allowed_pages: Any
) -> tuple[str | None, frozenset[str] | None]:
    """What a stored row means: ``(access_role, pages)``, or ``(None, None)`` for unrestricted.

    Only an administrator escapes the pages. A row whose platform role is unknown ranks below
    everything (roles.rank), so it is read as a plain user and stays restricted.
    """
    if role_engine.is_admin(platform_role):
        return None, None
    role = str(access_role or "").strip().lower()
    if not role:
        # Pages on a row with no role - a row somebody edited by hand - still restrict. Only
        # a row with neither is a normal user: a restriction is never lost to a missing label.
        return (None, clean_pages(allowed_pages)) if allowed_pages is not None else (None, None)
    return role, clean_pages(allowed_pages)


def label(access_role: str | None) -> str | None:
    """What a role is called on screen, or None for no role or one this code does not know."""
    return ACCESS_ROLES.get(str(access_role or "").strip().lower())


def validate_assignment(
    *, platform_role: str, access_role: str | None, pages: list[str] | None
) -> tuple[str | None, list[str] | None]:
    """Check what an administrator asked for and return what to store.

    ``(None, None)`` is a normal user. Otherwise the role key and its pages, de-duplicated and
    in ``PAGES`` order so two saves of the same ticks store the same value.
    """
    role = str(access_role or "").strip().lower() or None
    wanted = [str(p).strip().lower() for p in (pages or [])]

    if role is None:
        if wanted:
            raise AccessRoleError(
                "pages_without_role", "Choose a role before choosing the pages for it.")
        return None, None

    if role not in ACCESS_ROLES:
        raise AccessRoleError(
            "unknown_access_role",
            "Unknown role. Choose one of: " + ", ".join(ACCESS_ROLES.values()) + ".")
    if role_engine.is_admin(platform_role):
        raise AccessRoleError(
            "role_needs_plain_user",
            "An administrator sees every page, so a role cannot be given to one.")
    unknown = [p for p in wanted if p not in _PAGE_SET]
    if unknown:
        raise AccessRoleError(
            "unknown_page", "Unknown page: " + ", ".join(sorted(set(unknown))) + ".")
    if not wanted:
        raise AccessRoleError(
            "pages_required", "Tick at least one page for this role.")
    return role, [p for p in PAGES if p in set(wanted)]


def narrowed(before: frozenset[str] | None, after: frozenset[str] | None) -> bool:
    """Whether access got narrower: a role given to an unrestricted user, or a page taken away.

    Widening, or going back to unrestricted, is not narrowing. This decides when a person's
    earlier chat turns and report runs stop being shown to them.
    """
    if after is None:
        return False
    if before is None:
        return True
    return bool(before - after)


def public_fields(access_role: str | None, held: frozenset[str] | None) -> dict[str, Any]:
    """What a client is told about a caller's pages: the role, its label, and the pages in the
    order they are ticked (null = every page). ``pages_known`` says this is the authority."""
    return {
        "access_role": access_role,
        "access_role_label": label(access_role),
        "pages": None if held is None else [p for p in PAGES if p in held],
        "pages_known": True,
    }


async def for_user(
    session: Any, user_id: Any, platform_role: str | None
) -> tuple[str | None, frozenset[str] | None]:
    """One account's access role and pages, read from its row. ``(None, None)`` for an
    administrator, and for everybody until the columns exist — with nothing asked."""
    if role_engine.is_admin(platform_role):
        return None, None
    if not await columns_ready(session):
        if check_failed():
            # Not "the columns are absent" but "it could not be told". Saying "every page"
            # here would be a guess; the caller says nothing and the client asks again.
            raise PagesUnreadable("could not tell whether access roles are set up")
        return None, None
    from . import keys  # local: keys imports nothing from here, but keep the module light

    row = (await session.execute(
        text("SELECT access_role, allowed_pages FROM plenum_cafm.users WHERE id = :i"),
        {"i": await keys.user_key(session, user_id)},
    )).mappings().first()
    if row is None:
        return None, None
    return resolve(platform_role, row["access_role"], row["allowed_pages"])


# ── are the columns there yet? ────────────────────────────────────────────────────────
# The sign-in query runs on every request. If it named a column the database does not have,
# every request would fail and nobody could sign in — so the columns are named only once they
# are known to exist. Until the migration (migrations/users_access_role.sql) has been applied
# everybody is a normal user and a role cannot be assigned.

#: What an administrator is told when they try to give a role before the columns exist.
NOT_SET_UP = ("Roles are not available on this installation yet. Ask your platform "
              "administrator to finish the update, then try again.")

#: Seconds a "not there yet" answer is believed before the database is asked again, so a
#: migration applied while the service is running is picked up without a restart.
_RECHECK_SECONDS = 60.0

_READY = False
_CHECKED_AT: float | None = None
#: Whether the last check failed outright, as opposed to finding the columns absent.
_CHECK_FAILED = False


def _monotonic() -> float:
    return time.monotonic()


class PagesUnreadable(RuntimeError):
    """Whether the access-role columns exist could not be read. Not the same as their being
    absent: a caller that cannot tell must not be treated as holding every page."""


def check_failed() -> bool:
    """Whether the last look for the columns failed outright, rather than finding them absent."""
    return _CHECK_FAILED


def reset_cache() -> None:
    global _READY, _CHECKED_AT, _CHECK_FAILED
    _READY, _CHECKED_AT, _CHECK_FAILED = False, None, False


async def columns_ready(session: Any) -> bool:
    """Whether ``users`` has the access-role columns. Yes is remembered for the life of the
    process; no is re-asked at most once a minute. A check that fails reads as no."""
    global _READY, _CHECKED_AT, _CHECK_FAILED
    if _READY:
        return True
    now = _monotonic()
    if _CHECKED_AT is not None and now - _CHECKED_AT < _RECHECK_SECONDS:
        return False
    _CHECKED_AT = now
    try:
        found = (await session.execute(text(
            """SELECT count(*) AS n
                 FROM information_schema.columns
                WHERE table_schema = 'plenum_cafm' AND table_name = 'users'
                  AND column_name IN ('access_role', 'allowed_pages', 'access_narrowed_at')"""
        ))).scalar()
        _READY = int(found or 0) == 3
        _CHECK_FAILED = False
    except Exception:  # noqa: BLE001
        # Not believed, and not remembered: "it could not be read" is not "they are not
        # there". The next request asks again, and until one can be answered a caller is
        # refused (tokens.principal_from_token) rather than treated as holding every page.
        _READY = False
        _CHECK_FAILED = True
        _CHECKED_AT = None
    return _READY
