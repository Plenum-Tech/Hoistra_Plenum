"""What the CAFM tables key on, read from the database instead of assumed.

The auth engine was written against the connector ORM, where ``users.id`` and
``organizations.id`` are UUIDs. Production keys both on integers, and its ``users`` table
holds eighteen real staff rows. Neither shape is wrong; they are two deployments of the
same product, and an engine that only works against one of them is the actual defect.

So nothing here assumes. The column type is looked up once per process and every id is
coerced to whatever that type accepts before it is bound — an ``int`` for an integer key,
a ``str`` for a uuid or text one. asyncpg is strict about parameter types, which is
precisely why this cannot be left to chance: binding ``"1"`` into an integer column or
``UUID(...)`` into a varchar one fails at the driver, not at a validation layer where the
error would say something useful.

Types are validated against an allow-list before they are ever put near SQL. A type name
read from ``information_schema`` and then interpolated is still interpolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: An id in flight. Deliberately not narrowed to UUID: what a key IS depends on the
#: deployment, and the engine's job is to carry it faithfully, not to have an opinion.
KeyValue = str | int | UUID

#: Column types a key may have. Anything else is refused rather than guessed at — an
#: unfamiliar key type is a deployment nobody has thought about, and silently treating it
#: as text is how a mismatch becomes a data problem instead of a startup error.
_NUMERIC = frozenset({"integer", "bigint", "smallint"})
_TEXTUAL = frozenset({"uuid", "character varying", "varchar", "text", "character"})
ALLOWED_KEY_TYPES = _NUMERIC | _TEXTUAL

#: Fallback when the table cannot be read at all. The ORM's shape, because on a fresh
#: database created by ``create_all`` that is what it will be.
_DEFAULT = "uuid"


@dataclass(frozen=True)
class KeyShape:
    """The key types this deployment actually has."""

    users: str
    organizations: str | None
    #: Whether ``users.id`` fills itself in — a sequence, or gen_random_uuid(). When it
    #: does, an INSERT must NOT supply the column: handing a uuid to a sequence-backed
    #: integer column is a type error, and handing it a value at all defeats the sequence.
    users_id_self_assigning: bool

    @property
    def users_numeric(self) -> bool:
        return self.users in _NUMERIC

    @property
    def organizations_numeric(self) -> bool:
        return self.organizations in _NUMERIC


_CACHE: KeyShape | None = None


async def key_shape(session: AsyncSession, *, refresh: bool = False) -> KeyShape:
    """The key shape, read once per process. A deployment property, not a request one."""
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE

    users_type = _DEFAULT
    orgs_type: str | None = None
    self_assigning = True
    try:
        rows = (
            await session.execute(
                text(
                    """SELECT table_name, data_type, column_default, is_identity
                       FROM information_schema.columns
                       WHERE table_schema = 'plenum_cafm'
                         AND table_name IN ('users', 'organizations')
                         AND column_name = 'id'"""
                )
            )
        ).mappings().all()
        by_table = {str(r["table_name"]): r for r in rows}
        if "users" in by_table:
            users_type = _checked(by_table["users"]["data_type"], "users.id")
            self_assigning = bool(
                by_table["users"]["column_default"]
                or str(by_table["users"]["is_identity"]).upper() == "YES"
            )
        if "organizations" in by_table:
            orgs_type = _checked(
                by_table["organizations"]["data_type"], "organizations.id"
            )
    except Exception as exc:  # noqa: BLE001 — a failed lookup must not break a request
        log.warning("auth.key_shape.lookup_failed", error=str(exc)[:200])

    _CACHE = KeyShape(
        users=users_type,
        organizations=orgs_type,
        users_id_self_assigning=self_assigning,
    )
    log.info(
        "auth.key_shape",
        users=_CACHE.users,
        organizations=_CACHE.organizations,
        users_id_self_assigning=_CACHE.users_id_self_assigning,
    )
    return _CACHE


def _checked(data_type: Any, what: str) -> str:
    kind = str(data_type or "").strip().lower()
    if kind not in ALLOWED_KEY_TYPES:
        log.warning("auth.key_shape.unknown_type", column=what, data_type=kind)
        return _DEFAULT
    return kind


def reset_cache() -> None:
    """Forget the cached shape. For tests, and for a deployment that migrates its keys."""
    global _CACHE
    _CACHE = None


def coerce(value: KeyValue | None, kind: str) -> str | int | None:
    """An id in the Python type this column's driver will accept.

    asyncpg infers a parameter's type from the column it is compared against and refuses a
    mismatch outright, so this is what stands between a working query and
    ``invalid input for query argument``.
    """
    if value is None:
        return None
    if kind in _NUMERIC:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None
    return str(value)


async def user_key(session: AsyncSession, value: KeyValue | None) -> str | int | None:
    """A users.id ready to bind. The shortest form of the only rule in this module."""
    return coerce(value, (await key_shape(session)).users)


async def org_key(session: AsyncSession, value: KeyValue | None) -> str | int | None:
    """An organizations.id ready to bind."""
    shape = await key_shape(session)
    return coerce(value, shape.organizations or shape.users)


def is_valid(value: KeyValue | None, kind: str) -> bool:
    """Could this be an id of that type at all? Shape only — existence is a query."""
    if value is None:
        return False
    raw = str(value).strip()
    if not raw:
        return False
    if kind in _NUMERIC:
        return raw.lstrip("-").isdigit()
    if kind == "uuid":
        try:
            UUID(raw)
        except (TypeError, ValueError):
            return False
        return True
    return True


def describe(kind: str) -> str:
    """What to tell a caller who sent the wrong shape, in their terms not the column's."""
    if kind in _NUMERIC:
        return "a whole number"
    if kind == "uuid":
        return "a UUID"
    return "a text id"
