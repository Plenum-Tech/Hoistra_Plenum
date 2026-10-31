"""How this service compares a name someone typed with a name on the register (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing: the building is named "JASMEET", and every
place that compared a typed name with a stored one did it its own way — the whole phrase in
order, ILIKE '%phrase%', = equality, a Python substring. ``name_match`` (a byte-identical copy
of svc-deepagents' agents/name_match.py, kept identical by a drift test) holds the one ladder:

    0 exact     the name as typed, or with the words for the kind of record taken out
                ("JASMEET BUILDING" is "JASMEET")
    1 compact   letters and digits only ("B090765" is "B-090765",
                "RT5 Crossrail" is "RT5, Crossrail")
    2 phrase    the words in order from the start of a word ("Acme" in "Acme Facilities Ltd"),
                a code or number whole ("Building 7" is not "Building 70")
    3 words     every word, any order ("tower hussain" is "Hussain Tower")
    4 close     one letter wrong ("Jasmet"), never in a code or number, only when clearly closest

This module adds what the copy cannot (it must stay identical): the kind words of each record
type, a ceiling on the rung (a path that LINKS a record stops at 1), and one guard. A phrase
whose remaining words cannot name anything on their own — nothing ("the building"), or only a
number or a one- or two-letter word ("Building 7" leaves "7") — is compared only as typed: it
must not find "Block 7", or every building.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import name_match

#: Words naming the KIND of record, never part of the name when it is used to find one.
BUILDING_KIND_WORDS = frozenset({"building", "buildings", "site", "sites", "property",
                                 "properties", "premises"})
#: Vendors. Not company suffixes (ltd, limited): those are part of the name, and "Acme" still
#: finds "Acme Facilities Ltd" by the phrase rung.
VENDOR_KIND_WORDS = frozenset({"vendor", "vendors", "contractor", "contractors", "supplier",
                               "suppliers"})
ASSET_KIND_WORDS = frozenset({"asset", "assets", "equipment"})
METER_KIND_WORDS = frozenset({"meter", "meters"})

EXACT, COMPACT, PHRASE, WORDS, CLOSE = 0, 1, 2, 3, 4
#: The highest rung a path that creates or links a record may use: the same name, never part of
#: one or a close spelling.
LINK_MAX_RUNG = COMPACT


def core_words(query: Any, kind_words: Iterable[str]) -> list[str]:
    return name_match.meaningful(query, kind_words)


def stands_alone(query: Any, kind_words: Iterable[str], *, min_len: int = 3) -> bool:
    """Whether the words left once the kind words and fillers are out can name a record on their
    own: a word of three letters or more, or a code mixing letters and digits. "JASMEET BUILDING"
    leaves "jasmeet" (yes); "Building 7" leaves "7" and "the building" nothing (no). A link path
    asks for a longer core (``min_len``): see building_resolver.choose_building."""
    ck = name_match.compact(" ".join(core_words(query, kind_words)))
    return bool(ck) and not ck.isdigit() and len(ck) >= min_len


def names_something(query: Any, kind_words: Iterable[str]) -> bool:
    """Whether the query has any word that is not a filler or a kind word."""
    return bool(core_words(query, kind_words))


def _phrase_at_word_start(q: list[str], s: list[str]) -> bool:
    n = len(q)
    if not n:
        return False
    for i in range(len(s) - n + 1):
        window = s[i:i + n]
        if all(window[j] == q[j] for j in range(n - 1)):
            last, got = q[-1], window[-1]
            if (got == last) if name_match.exact_token(last) else got.startswith(last):
                return True
    return False


def rung(query: Any, stored: Any, kind_words: Iterable[str] = (), *, max_rung: int = CLOSE,
         min_core_len: int = 3) -> int | None:
    """The rung (0 best) at which ``query`` names ``stored``, or None — name_match.rung, except
    that a query that does not stand alone is compared only as typed (exact, compact, and its
    phrase from the start of a word when it has any name word at all)."""
    if stands_alone(query, kind_words, min_len=min_core_len):
        r = name_match.rung(query, stored, kind_words)
    else:
        s = name_match.clean(stored)
        if not s or not name_match.words(query):
            return None
        if name_match.clean(query) == s:
            r = EXACT
        elif name_match.compact(query) and name_match.compact(query) == name_match.compact(stored):
            r = COMPACT
        elif names_something(query, kind_words) and _phrase_at_word_start(
                name_match.words(query), name_match.words(stored)):
            r = PHRASE
        else:
            r = None
    return r if r is not None and r <= max_rung else None


def best(query: Any, rows: Iterable[tuple[Any, Any]], kind_words: Iterable[str] = (), *,
         max_rung: int = CLOSE, min_core_len: int = 3) -> tuple[int | None, list[Any]]:
    """``rows`` of (key, stored text) — a record may come several times, once per text it is known
    by. Returns (the best rung any row reaches, the distinct keys matched at it): several when the
    name is shared (all of them, never one picked), the clearly closest when it is a close
    spelling."""
    scored: list[tuple[int, Any, Any]] = []
    for key, text_ in rows:
        if text_ is None or str(text_).strip() == "":
            continue
        r = rung(query, text_, kind_words, max_rung=max_rung, min_core_len=min_core_len)
        if r is not None:
            scored.append((r, key, text_))
    if not scored:
        return None, []
    top = min(r for r, _, _ in scored)
    hits = [(key, text_) for r, key, text_ in scored if r == top]
    if top == CLOSE:
        core = " ".join(core_words(query, kind_words))
        sims: dict[Any, float] = {}
        for key, text_ in hits:
            sims[key] = max(sims.get(key, 0.0), name_match.similarity(core, text_))
        top_sim = max(sims.values())
        hits = [(key, text_) for key, text_ in hits if sims[key] >= top_sim - name_match.FUZZY_TIE]
    keys = list(dict.fromkeys(key for key, _ in hits))
    return top, keys


def names_match(query: Any, stored: Any, kind_words: Iterable[str] = (), *,
                max_rung: int = WORDS) -> bool:
    """Whether ``query`` names ``stored`` — for a filter over one row at a time. Close spelling is
    off by default: one row alone cannot tell whether it is the CLEARLY closest."""
    return rung(query, stored, kind_words, max_rung=max_rung) is not None


# ── the SQL side: one exact/compact predicate, the forms rungs 0 and 1 compare ──────────

def exact_forms(query: Any, kind_words: Iterable[str]) -> tuple[list[str], list[str]]:
    """(lower-case texts, compact texts) that rungs 0 and 1 compare a stored name with: the name
    as typed, and its core when the core stands alone."""
    raw = re.sub(r"\s+", " ", str(query or "").strip())
    texts = [raw.lower()] if raw else []
    compacts = [name_match.compact(raw)] if name_match.compact(raw) else []
    if stands_alone(raw, kind_words):
        core = " ".join(core_words(raw, kind_words))
        if core and core not in texts:
            texts.append(core)
        ck = name_match.compact(core)
        if ck and ck not in compacts:
            compacts.append(ck)
    return texts, compacts


def exact_sql(columns: Iterable[str], texts_param: str, compacts_param: str) -> str:
    """WHERE over ``columns`` for rungs 0-1: lower(btrim(col)) = ANY(texts) OR the column's
    letters and digits = ANY(compacts). Column names come from the caller's own constants."""
    parts = []
    for c in columns:
        parts.append(f"lower(btrim({c}::text)) = ANY(:{texts_param})")
        parts.append(f"regexp_replace(lower({c}::text), '[^a-z0-9]+', '', 'g')"
                     f" = ANY(:{compacts_param})")
    return "(" + " OR ".join(parts) + ")"
