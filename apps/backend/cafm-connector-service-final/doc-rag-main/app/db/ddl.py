"""Startup DDL that cannot queue the service behind a lock.

Measured 17 Sep 2026: two sessions from an outside machine sat idle in a transaction holding a
share lock on ``plenum_cafm.ingestion_documents``. Every replica start then ran this service's
``ALTER TABLE ingestion_documents ADD COLUMN IF NOT EXISTS …`` — which needs an exclusive lock —
and waited. A waiting exclusive lock blocks every NEW share lock behind it, so each waiting
ALTER took every reader of the table with it: 47 sessions queued within forty minutes, and the
identity service went to 502 for the duration of a rollout.

Two rules, both here:

* **A statement that is already satisfied takes no lock.** ``ADD COLUMN IF NOT EXISTS`` still
  acquires ACCESS EXCLUSIVE before it discovers the column exists. Ask ``information_schema``
  first; on a steady-state database that is every statement, every start.
* **A statement that cannot get its lock in five seconds gives up**, retries a few times, and
  is then skipped with a warning. These statements are idempotent and run on every start;
  waiting is never worth taking the service down for.

No imports from the rest of the app, so this can be tested without standing the app up.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable

LOCK_TIMEOUT = "5s"
LOCK_RETRIES = 4
LOCK_RETRY_SLEEP_S = 3.0

APPLIED = "applied"
ALREADY_APPLIED = "already_applied"
LOCK_SKIPPED = "lock_skipped"
FAILED = "failed"

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ADD_COLUMN = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?:(?P<schema>{_IDENT})\.)?(?P<table>{_IDENT})\s+"
    rf"ADD\s+(?:COLUMN\s+)?IF\s+NOT\s+EXISTS\s+(?P<column>{_IDENT})\b",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_EXISTS_SQL = (
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = COALESCE(:s, current_schema()) AND table_name = :t AND column_name = :c "
    "LIMIT 1"
)

_default_log = logging.getLogger(__name__)


def parse_add_column(stmt: str) -> tuple[str | None, str, str] | None:
    """(schema, table, column) for a plain ``ALTER TABLE … ADD COLUMN IF NOT EXISTS …``; else None.

    Only the plain form. A ``DO $$ … $$`` block or an ``ALTER COLUMN … SET DEFAULT`` is not
    pre-checkable and simply runs under the lock timeout.
    """
    m = _ADD_COLUMN.match(stmt or "")
    if not m:
        return None
    return m.group("schema"), m.group("table"), m.group("column")


def is_lock_timeout(exc: BaseException) -> bool:
    text_ = str(exc).lower()
    return "lock timeout" in text_ or "lock_timeout" in text_ or "locknotavailable" in text_


def column_exists(engine: Any, schema: str | None, table: str, column: str) -> bool:
    """True when the column is already there. Any failure to ask counts as "not known to exist"."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            row = conn.execute(text(_COLUMN_EXISTS_SQL), {"s": schema, "t": table, "c": column}).scalar()
        return row is not None
    except Exception:  # noqa: BLE001 — the ALTER itself is the authority; this is only a shortcut
        return False


def run_ddl(engine: Any, stmt: str, *, log: Any = None, sleep: Callable[[float], None] = time.sleep) -> str:
    """Run one startup DDL statement under the two rules. Returns one of the outcome constants."""
    from sqlalchemy import text

    log = log or _default_log
    short = " ".join(stmt.split())[:110]
    parsed = parse_add_column(stmt)
    if parsed and column_exists(engine, *parsed):
        log.info(f"Migration already applied, no lock taken: {short}")
        return ALREADY_APPLIED
    for attempt in range(1, LOCK_RETRIES + 1):
        try:
            with engine.begin() as conn:
                conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
                conn.execute(text(stmt))
            log.info(f"Migration applied: {short}")
            return APPLIED
        except Exception as exc:  # noqa: BLE001
            if is_lock_timeout(exc):
                if attempt < LOCK_RETRIES:
                    log.warning(
                        f"Migration lock wait (attempt {attempt}/{LOCK_RETRIES}), a reader holds the "
                        f"table; backing off {LOCK_RETRY_SLEEP_S:.0f}s rather than queueing the service "
                        f"behind it: {short}"
                    )
                    sleep(LOCK_RETRY_SLEEP_S)
                    continue
                log.warning(f"Migration skipped after {LOCK_RETRIES} lock timeouts (next start retries): {short}")
                return LOCK_SKIPPED
            log.warning(f"Migration skipped ({type(exc).__name__}): {short}")
            return FAILED
    return LOCK_SKIPPED  # unreachable; keeps the return type honest
