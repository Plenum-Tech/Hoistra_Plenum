"""Which asset or location a typed name or code means, by the same ladder svc-deepagents uses.

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing (29 Oct 2026): the building is named "JASMEET",
and every service compared the whole phrase its own way. This service read assets with
``asset_name ILIKE '%q%' ... LIMIT 10`` and locations with ``name ILIKE '%q%'`` — the phrase in
order, ten rows at most, cut silently — and the email intake then picked the closest of twenty by
spelling, so "Jasmet Chiller" could raise a work order on whichever chiller came nearest.

The rungs are name_match's (copied byte-identical from svc-deepagents; a drift test holds it
there): 0 exact, 1 compact (letters and digits only), 2 the phrase at the start of a word, 3 every
word in any order. The close-spelling rung (4) is never used here: these lookups choose the asset
or location a NEW work order is raised against, and a wrong pick raises it on the wrong asset.
Every record at the best rung is returned — several when the name is shared, never one picked.

SQL fetches the candidates (a row holding every meaningful word, or equal once both sides are
reduced to letters and digits); Python ranks them. Compact-equal rows are read first, so a cut can
never drop an exact or compact match, and a cut is always reported, never silent.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import and_, case, func, or_, select

from . import name_match

#: Words for the kind of record. "JASMEET BUILDING" names JASMEET; "boiler equipment" a boiler.
KIND_WORDS = ("asset", "assets", "equipment", "location", "locations",
              "building", "buildings", "site", "sites")

#: The loosest rung allowed: 3, every word in any order. 4 (close spelling) never.
MAX_RUNG = 3
RUNG_NAMES = ("exact", "compact", "phrase", "all_words")

#: Candidates read for one lookup (one more is asked for, to know whether there were more).
CANDIDATE_LIMIT = 200
#: Matches listed in a tool's output; ``total`` always says how many there were.
SHOW_LIMIT = 50


def compact_sql(col):
    """``col`` as name_match.compact spells it: lower case, letters and digits only."""
    return func.regexp_replace(func.lower(col), "[^a-z0-9]+", "", "g")


def compact_keys(query, kind_words: Iterable[str] = KIND_WORDS) -> list[str]:
    """The query and its meaningful words, each reduced to letters and digits."""
    core = " ".join(name_match.meaningful(query, kind_words))
    return [k for k in dict.fromkeys([name_match.compact(query), name_match.compact(core)]) if k]


def compact_clause(cols: Sequence, query, kind_words: Iterable[str] = KIND_WORDS):
    keys = compact_keys(query, kind_words)
    if not keys or not cols:
        return None
    return or_(*(compact_sql(c).in_(keys) for c in cols))


def candidate_clause(cols: Sequence, query, kind_words: Iterable[str] = KIND_WORDS):
    """The WHERE for every row a rung up to MAX_RUNG can match, or None when the text has no
    words at all. A text with no meaningful word ("the building") reads only rows that ARE that
    text — never every row holding the word "building"."""
    parts = []
    cc = compact_clause(cols, query, kind_words)
    if cc is not None:
        parts.append(cc)
    core = name_match.meaningful(query, kind_words)
    if core and cols:
        # words() yields [a-z0-9]+ only, so no word carries a LIKE wildcard.
        parts.append(and_(*(or_(*(c.ilike(f"%{w}%") for c in cols)) for w in core)))
    return or_(*parts) if parts else None


def rank(query, rows: Iterable[Any], texts: Callable[[Any], Iterable[Any]],
         kind_words: Iterable[str] = KIND_WORDS, max_rung: int = MAX_RUNG) -> tuple[int | None, list]:
    """(best rung, every row at it) in the order given; (None, []) when no row matches.

    A text with no meaningful word matches only at the exact and compact rungs."""
    kind_words = tuple(kind_words)
    if not name_match.meaningful(query, kind_words):
        max_rung = min(max_rung, 1)
    best: int | None = None
    hits: list = []
    for row in rows:
        rungs = [name_match.rung(query, t, kind_words) for t in texts(row) if t]
        rungs = [r for r in rungs if r is not None and r <= max_rung]
        if not rungs:
            continue
        r = min(rungs)
        if best is None or r < best:
            best, hits = r, [row]
        elif r == best:
            hits.append(row)
    return best, hits


async def lookup(session, model, cols: Sequence, query, texts: Callable[[Any], Iterable[Any]], *,
                 order_by: Sequence = (), kind_words: Iterable[str] = KIND_WORDS,
                 scope: Callable | None = None) -> dict:
    """Read the candidates for ``query`` from ``model`` and rank them.

    Returns {"rung", "match", "matches", "truncated"}: ``matches`` every row at the best rung,
    ``truncated`` True when more than CANDIDATE_LIMIT rows were candidates (only the first were
    compared — exact and compact ones always among them)."""
    kind_words = tuple(kind_words)
    where = candidate_clause(cols, query, kind_words)
    if where is None:
        return {"rung": None, "match": None, "matches": [], "truncated": False}
    stmt = select(model).where(where)
    if scope is not None:
        stmt = scope(stmt)
    first = compact_clause(cols, query, kind_words)
    stmt = stmt.order_by(case((first, 0), else_=1), *order_by).limit(CANDIDATE_LIMIT + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    truncated = len(rows) > CANDIDATE_LIMIT
    best, hits = rank(query, rows[:CANDIDATE_LIMIT], texts, kind_words)
    return {"rung": best, "match": RUNG_NAMES[best] if best is not None else None,
            "matches": hits, "truncated": truncated}


def unique(found: dict):
    """The one record ``found`` names, or None when it names none or several — or when a cut
    may have hidden an equal one (a phrase or word match from a truncated read)."""
    hits = found.get("matches") or []
    if len(hits) != 1:
        return None
    if found.get("truncated") and (found.get("rung") or 0) > 1:
        return None
    return hits[0]


def describe(found: dict, query, noun: str, shown: int) -> str | None:
    """What a tool's caller must be told about a lookup: several equal matches, a list cut to
    SHOW_LIMIT, a candidate read cut at CANDIDATE_LIMIT. None when there is nothing to say."""
    total = len(found.get("matches") or [])
    notes: list[str] = []
    if total > 1:
        notes.append(f"{total} {noun}s match '{query}' equally ({found.get('match')}). Ask the user "
                     f"which one before raising a work order; never pick one.")
    if shown < total:
        notes.append(f"Showing {shown} of {total}.")
    if found.get("truncated"):
        notes.append(f"More than {CANDIDATE_LIMIT} {noun}s contain these words, so only the first "
                     f"{CANDIDATE_LIMIT} were compared; ask for the code or more of the name.")
    return " ".join(notes) or None
