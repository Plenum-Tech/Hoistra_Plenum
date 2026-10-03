"""Per-column statistics computed once per UDR pass instead of once per column pair.

Every pairwise test in the UDR pipeline (format similarity, value-shape similarity, Jaccard and
containment overlap, referential integrity) recomputes the same per-column facts — the non-null
values, the distinct set, the format and shape distributions — for both columns of every pair:
combinations(181 columns, 2) on the 280k-row run of 1 Oct 2026, 797 s. Inside a memo_scope each
is computed once per (rows, column). The functions are pure in their rows, so the report is
identical; the scope pins the row lists it has seen so an id() cannot be reused while it lives.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

T = TypeVar("T")
_MISS = object()


class _Scope:
    __slots__ = ("cache", "pins")

    def __init__(self) -> None:
        self.cache: dict = {}
        self.pins: dict = {}


_SCOPE: "contextvars.ContextVar[_Scope | None]" = contextvars.ContextVar("udr_memo", default=None)


def enabled() -> bool:
    return True


@contextmanager
def memo_scope():
    """Cache per-column statistics until the outermost scope ends. Rows must not change inside."""
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set(_Scope())
    try:
        yield
    finally:
        _SCOPE.reset(token)


def memoized(kind: Any, rows: Any, col: Any, compute: Callable[[], T]) -> T:
    scope = _SCOPE.get()
    if scope is None or not enabled() or not isinstance(rows, list):
        return compute()
    key = (kind, id(rows), col)
    hit = scope.cache.get(key, _MISS)
    if hit is _MISS:
        hit = compute()
        scope.cache[key] = hit
        scope.pins[id(rows)] = rows
    return hit  # type: ignore[return-value]


def memo_scoped(fn: Callable[..., T]) -> Callable[..., T]:
    """Run ``fn`` inside a memo scope (reusing an outer one)."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        with memo_scope():
            return fn(*args, **kwargs)

    return wrapper
