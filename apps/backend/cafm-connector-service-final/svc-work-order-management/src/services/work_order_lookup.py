"""A work order named by its human code however the code is typed.

The code people read and quote is plenum_cafm.work_orders.wo_code ("WO-1042"). It was compared
exactly and case-sensitively, so "wo-1042" or "WO1042" was a 404 for a work order that exists.
These compare it the way name_match compares a name: 0 the same but for case and spacing, 1 the
same letters and digits. Nothing looser — "1042" is not "WO-1042" — and the code is not unique on
one database (1,728 rows set, 948 distinct), so every row at the best rung is returned and the
caller decides; a route refuses several rather than picking one.
"""
from __future__ import annotations

from typing import Callable, Iterable

from sqlalchemy import String, cast, select

from ..models.work_order import WorkOrder
from ..shared import name_match
from ..shared.record_lookup import compact_sql

#: Rows read for one code. The comparison is an equality, so this only bounds a pathological case.
CODE_CANDIDATES = 50


def code_rung(reference, code) -> int | None:
    """0 the same code but for case and spacing, 1 the same letters and digits, else None."""
    if not code or not name_match.words(reference):
        return None
    if name_match.clean(reference) == name_match.clean(code):
        return 0
    if name_match.compact(reference) == name_match.compact(code):
        return 1
    return None


def code_clause(reference):
    """wo_code reduced to letters and digits equals the reference reduced the same way; None when
    the reference has no letters or digits. Cast to text: wo_code is varchar on both databases,
    and stating it keeps the comparison from depending on that."""
    key = name_match.compact(reference)
    if not key:
        return None
    return compact_sql(cast(WorkOrder.wo_code, String)) == key


def best_by_code(reference, rows: Iterable[WorkOrder]) -> list[WorkOrder]:
    """Every row whose code matches at the best rung any row reaches."""
    scored = [(code_rung(reference, getattr(w, "wo_code", None)), w) for w in rows]
    scored = [(r, w) for r, w in scored if r is not None]
    if not scored:
        return []
    top = min(r for r, _ in scored)
    return [w for r, w in scored if r == top]


async def by_code(session, reference, scope: Callable | None = None) -> list[WorkOrder]:
    """The work orders ``reference`` names by code, narrowed by ``scope`` when given."""
    clause = code_clause(reference)
    if clause is None:
        return []
    q = select(WorkOrder).where(clause)
    if scope is not None:
        q = scope(q)
    q = q.order_by(cast(WorkOrder.work_order_id, String)).limit(CODE_CANDIDATES)
    rows = (await session.execute(q)).scalars().all()
    return best_by_code(reference, rows)


def describe_candidates(rows: Iterable[WorkOrder], limit: int = 10) -> str:
    """"WO-1042 (id 9f…), WO 1042 (id 3c…)" for an ambiguity message."""
    rows = list(rows)
    out = ", ".join(f"{w.wo_code} (id {w.work_order_id})" for w in rows[:limit])
    return out + (f" and {len(rows) - limit} more" if len(rows) > limit else "")
