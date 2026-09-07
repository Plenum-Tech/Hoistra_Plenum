"""Type-compatibility guard for column matching.

Prevents a text / categorical / date source column from being auto-matched onto a
NUMERIC (or boolean / temporal) destination column — e.g. ``frequency='Quarterly'`` →
``frequency_value INTEGER`` — which PostgreSQL rejects at write time ("'str' object
cannot be interpreted as an integer"), dropping every row of the table.

When the chosen destination column's type cannot hold the source values, the guard
retargets to a type-compatible, name-similar column on the same destination table
(``frequency`` → ``frequency_type``); if none exists it signals a NEW column so the
data is preserved instead of lost. Pure + dependency-free so it is trivially testable.
"""

from __future__ import annotations

import re
from datetime import date as _date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable


def _is_numeric_type(t: str) -> bool:
    t = (t or "").lower()
    return any(k in t for k in ("int", "serial", "numeric", "decimal", "double", "real", "money"))


def _is_bool_type(t: str) -> bool:
    return (t or "").lower() == "boolean"


def _is_temporal_type(t: str) -> bool:
    t = (t or "").lower()
    return "date" in t or "timestamp" in t


def is_strict_type(t: str) -> bool:
    """A destination type that rejects arbitrary text (numeric / boolean / temporal)."""
    return _is_numeric_type(t) or _is_bool_type(t) or _is_temporal_type(t)


_BOOL_TOKENS = {"1", "0", "true", "false", "t", "f", "yes", "no", "y", "n"}


def value_fits_pg_type(value: object, pg_type: str) -> bool:
    """True if ``value`` can be stored in a column of ``pg_type``.

    Empty / None always fit (they become NULL). Non-strict (text / uuid / json / …)
    destinations accept anything. Mirrors write_node's coercion so this pre-flights the
    exact check the DB will apply.
    """
    if value is None:
        return True
    s = str(value).strip()
    if s == "":
        return True
    t = (pg_type or "").lower()

    if _is_numeric_type(t):
        try:
            int(s)
            return True
        except Exception:
            pass
        try:
            Decimal(s)
            return True
        except (InvalidOperation, ValueError, ArithmeticError):
            return False
        except Exception:
            return False
    if _is_bool_type(t):
        return s.lower() in _BOOL_TOKENS
    if _is_temporal_type(t):
        try:
            datetime.fromisoformat(s.replace("Z", "+00:00"))
            return True
        except Exception:
            pass
        try:
            _date.fromisoformat(s[:10])
            return True
        except Exception:
            return False
    return True  # text-ish destination accepts anything


def values_fit_pg_type(values: Iterable[object], pg_type: str, *, min_fit: float = 0.6) -> bool:
    """True if a strong majority of the NON-EMPTY sample values fit ``pg_type``.

    A strict (numeric/bool/temporal) column whose sampled source values mostly do NOT fit
    is a genuine type mismatch. Non-strict destinations always pass.
    """
    if not is_strict_type(pg_type):
        return True
    non_empty = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return True  # nothing contradicts the type
    fit = sum(1 for v in non_empty if value_fits_pg_type(v, pg_type))
    return (fit / len(non_empty)) >= min_fit


def _tokens(name: str) -> set[str]:
    return {p for p in re.split(r"[^a-z0-9]+", (name or "").lower()) if p}


def _name_similarity(a: str, b: str) -> float:
    a = (a or "").lower()
    b = (b or "").lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    if ta and tb and (ta & tb):
        return len(ta & tb) / len(ta | tb)
    if a in b or b in a:
        return 0.5
    return 0.0


def choose_compatible_target(
    source_field: str,
    current_target: str,
    sample_values: Iterable[object],
    dest_types: dict[str, str],
    *,
    min_name_score: float = 0.34,
    exclude: set[str] | None = None,
) -> tuple[str, str]:
    """Decide the destination column for one mapping, given the destination table's
    ``{column (lowercased): pg_type}`` map and a sample of the source column's values.

    ``exclude`` is the set of destination columns (lowercased) already claimed by OTHER
    mappings on the same table — the guard won't retarget onto one of them, so two source
    columns never collide on the same destination column.

    Returns ``(target, action)`` where action is:
      * ``"keep"``     — current target fits (or its type is unknown / text-ish)
      * ``"retarget"`` — moved to a type-compatible, name-similar column (``target`` is it)
      * ``"new"``      — no compatible column found; the caller should make it a NEW column
    """
    values = list(sample_values)
    cur = (current_target or "").lower()
    _exclude = {c.lower() for c in (exclude or set())}
    cur_type = dest_types.get(cur)
    # Unknown column or a text-ish destination — nothing to guard against.
    if cur_type is None or not is_strict_type(cur_type):
        return current_target, "keep"
    if values_fit_pg_type(values, cur_type):
        return current_target, "keep"

    # Incompatible. Find a type-compatible column, ranked by name similarity to the source
    # field first, then to the (wrong) current target so frequency_value → frequency_type.
    # Skip columns already claimed by another mapping so we don't create a collision.
    best: str | None = None
    best_score = 0.0
    for col, t in dest_types.items():
        if col == cur or col in _exclude:
            continue
        if not values_fit_pg_type(values, t):
            continue
        score = max(_name_similarity(source_field, col), _name_similarity(current_target, col))
        if score > best_score:
            best, best_score = col, score
    if best and best_score >= min_name_score:
        return best, "retarget"
    return current_target, "new"
