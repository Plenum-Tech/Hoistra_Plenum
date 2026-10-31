"""How a name typed by a user, or put in a plan by the planner, is compared with a name on the register.

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing (29 Oct 2026): the planner's match was
"JASMEET BUILDING", the building is named "JASMEET", and every comparison took the whole phrase, in
order. A record that is on the register must be found however it is referred to — with the kind of
record named beside it, in another word order, with a possessive or a stray comma, a code typed
without its hyphen, or one letter wrong — and a phrase that names nothing ("the building") must find
nothing rather than everything.

These are the pure pieces. ontology_qa.resolve_match runs them as a ladder against the scoped views,
stopping at the first rung that finds a record.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

#: Words that sit around a name without being part of it.
FILLERS = frozenset({
    "the", "of", "for", "at", "in", "on", "to", "from", "about", "called", "named", "name",
    "record", "records", "detail", "details", "info", "information", "data", "all", "our", "my", "this",
    "that", "please", "show", "me", "list",
})
#: Articles that are fillers only where an article stands — first, or after a filler ("show me a
#: building"). After a name word they are the name: "Tower A" is not every tower.
ARTICLES = frozenset({"a", "an"})

#: A close spelling counts at this similarity (0-1). One wrong, missing or swapped letter in a word of
#: six or more clears it; a different word does not.
FUZZY_MIN = 0.85
#: Candidates this close to the best are as good as the best: more than one is ambiguous, not a pick.
FUZZY_TIE = 0.02
#: A close spelling of PART of a longer name counts a little less than one of the whole name.
WINDOW_WEIGHT = 0.97
#: Below this many letters a name is too short to read as a split compound: "a b" must not fuzzily
#: become any two-letter name, and short codes are compared exactly anyway.
_COMPOUND_MIN_LETTERS = 6

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                         "“": '"', "”": '"', "„": '"', "″": '"',
                         "–": "-", "—": "-", "‒": "-", "−": "-", " ": " "})


def clean(text) -> str:
    """Lower case, accents folded, curly quotes and long dashes made plain, surrounding quotes and
    possessives dropped, runs of space collapsed. Commas and hyphens stay: they are text, not noise,
    until a comparison decides otherwise."""
    s = unicodedata.normalize("NFKD", str(text or "")).translate(_QUOTES)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"(\w)'s\b", r"\1", s)                 # jasmeet's -> jasmeet
    s = re.sub(r"(\w)s'(?=\s|$)", r"\1s", s)          # buildings' -> buildings
    s = re.sub(r"\b(?:[a-z]\.){2,}(?:[a-z]\b)?", lambda m: m.group(0).replace(".", ""), s)   # r.d.m. -> rdm
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" '\"`")


def words(text) -> list[str]:
    return re.findall(r"[a-z0-9]+", clean(text))


def compact(text) -> str:
    """Letters and digits only: B-090765, b 090765 and B090765 are the same code."""
    return "".join(words(text))


def meaningful(text, kind_words) -> list[str]:
    """The words that name the record: fillers and the words for the kind of record taken out."""
    kind = {str(k).lower() for k in (kind_words or ())}
    out: list[str] = []
    prev_is_name = False
    for w in words(text):
        if w in ARTICLES and not prev_is_name:
            continue
        if w in FILLERS or w in kind:
            prev_is_name = False
            continue
        out.append(w)
        prev_is_name = True
    return out


def phrase_variants(text, kind_words) -> list[str]:
    """The text as typed, then its meaningful words alone when they differ. Never an empty search."""
    raw = str(text or "").strip()
    out = [raw] if raw else []
    core = " ".join(meaningful(raw, kind_words))
    if core and core != clean(raw):
        out.append(core)
    return out


def exact_token(w: str) -> bool:
    """A word compared exactly, never by closeness: any word with a digit (a code, a number) and any
    word of one or two letters — "Tower A" is not "Tower B", "B-301" is not "B-302"."""
    return len(w) <= 2 or any(ch.isdigit() for ch in w)


_exact_token = exact_token


def similarity(query, stored) -> float:
    """How close two names are, 0-1, for one-letter mistakes in words. Every exact-only word of the
    query (codes, numbers, one- and two-letter words) must be in the stored name, or the answer is 0."""
    q, s = words(query), words(stored)
    if not q or not s:
        return 0.0
    q_exact = {w for w in q if _exact_token(w)}
    if not q_exact <= set(s):
        return 0.0
    q_rest = [w for w in q if not _exact_token(w)]
    s_rest = [w for w in s if w not in q_exact]
    if not q_rest:
        return 1.0 if compact(query) == compact(stored) else 0.0
    if not s_rest:
        return 0.0
    a, b = " ".join(q_rest), " ".join(s_rest)
    plain = SequenceMatcher(None, a, b).ratio()
    ordered = SequenceMatcher(None, " ".join(sorted(q_rest)), " ".join(sorted(s_rest))).ratio()
    # The typed words against the same number of the name's words, in order — "Acme Facilites" is
    # one letter from "Acme Facilities" even though the name goes on with "Ltd" — a little below a
    # whole-name match, so a close whole name still wins over part of a longer one.
    n = len(q_rest)
    window = max((SequenceMatcher(None, a, " ".join(s_rest[i:i + n])).ratio()
                  for i in range(len(s_rest) - n + 1)), default=0.0) * WINDOW_WEIGHT if len(s_rest) > n else 0.0
    # The same name with the compound split, or joined: "Bishop Gate Tower" beside "Bishopsgate
    # Tower", "saint pauls" beside "StPauls". Every comparison above comparesWORDS, and no word of
    # "bishop gate" equals or prefixes "bishopsgate", so the pair scored 0.71 and the building was
    # not found at all — not picked wrongly, not asked about, simply absent. Comparing the letters
    # with the spaces taken out reads the two as one name again, against each run of the stored
    # words so a dropped kind word ("Tower") costs nothing.
    compacted = 0.0
    qc = compact(a)
    if len(qc) >= _COMPOUND_MIN_LETTERS:
        for i in range(len(s_rest)):
            for j in range(i + 1, len(s_rest) + 1):
                sc_win = compact(" ".join(s_rest[i:j]))
                if not sc_win:
                    continue
                r = SequenceMatcher(None, qc, sc_win).ratio()
                if j - i != len(s_rest):
                    r *= WINDOW_WEIGHT        # part of the name is worth a little less than all of it
                compacted = max(compacted, r)
    return max(plain, ordered, window, compacted)


def pick_closest(query, rows) -> list[tuple]:
    """``rows`` of (key, label): the one clearly closest above FUZZY_MIN, or every one tied with it."""
    scored = sorted(((similarity(query, label), key, label) for key, label in rows), reverse=True)
    scored = [x for x in scored if x[0] >= FUZZY_MIN]
    if not scored:
        return []
    best = scored[0][0]
    return [(key, label) for sc, key, label in scored if sc >= best - FUZZY_TIE]


def fragments(text) -> list[str]:
    """Three-letter pieces from both ends of each word that can be misspelt, to fetch candidates for
    the close-spelling rung without reading a whole table."""
    out: list[str] = []
    for w in words(text):
        if w in FILLERS or _exact_token(w) or len(w) < 4:
            continue
        for frag in (w[:3], w[-3:]):
            if frag not in out:
                out.append(frag)
    return out


# ── the same ladder for names compared in Python ──────────────────────────────────────────────
# Tools that filter rows a service returned (a vendor_name, a building_name argument) used their own
# substring tests. They rank by the same rungs as ontology_qa's SQL ladder, so a name finds the same
# record whichever path reads it.

def _word_hit(w: str, stored_words: list[str]) -> bool:
    return any((s == w) if exact_token(w) else s.startswith(w) for s in stored_words)


def _phrase_at_word_start(variant: list[str], stored_words: list[str]) -> bool:
    """The words in order from the start of a stored word; inner words whole, the last one a prefix
    (whole when it is a code, a number or a one-/two-letter word) — the SQL search rung's pattern."""
    n = len(variant)
    for i in range(len(stored_words) - n + 1):
        window = stored_words[i:i + n]
        inner_ok = all(window[j] == variant[j] for j in range(n - 1))
        last, got = variant[-1], window[-1]
        if inner_ok and ((got == last) if exact_token(last) else got.startswith(last)):
            return True
    return False


def rung(query, stored, kind_words=()) -> int | None:
    """The rung (0 best) at which ``query`` names ``stored``, or None: 0 exact, 1 compact, 2 phrase,
    3 every word in any order, 4 close spelling."""
    s = clean(stored)
    q_words = words(query)
    if not s or not q_words:
        return None
    core_words = meaningful(query, kind_words)
    core = " ".join(core_words)
    if clean(query) == s or (core and core == s):
        return 0
    sc = compact(stored)
    if compact(query) == sc or (core and compact(core) == sc):
        return 1
    if not core_words:
        return None          # kind words alone ("the building") name only a record named exactly that
    sw = words(stored)
    if _phrase_at_word_start(q_words, sw) or (core_words and _phrase_at_word_start(core_words, sw)):
        return 2
    if len(core_words) >= 2 and all(_word_hit(w, sw) for w in core_words):
        return 3
    if core and similarity(core, stored) >= FUZZY_MIN:
        return 4
    return None


def best_matches(query, rows, kind_words=()) -> list[tuple]:
    """``rows`` of (key, text): the rows matched at the best rung any row reaches — several when the
    name is shared (all of them, never one picked), the clearly closest when it is a close spelling."""
    scored = [(rung(query, text, kind_words), key, text) for key, text in rows]
    scored = [x for x in scored if x[0] is not None]
    if not scored:
        return []
    top = min(x[0] for x in scored)
    hits = [(key, text) for r, key, text in scored if r == top]
    if top == 4:
        core = " ".join(meaningful(query, kind_words))
        sims = {key: similarity(core, text) for key, text in hits}
        best = max(sims.values())
        hits = [(key, text) for key, text in hits if sims[key] >= best - FUZZY_TIE]
    return hits


def names_match(query, stored, kind_words=()) -> bool:
    """Whether ``query`` names ``stored`` at any rung — for a filter over one row at a time. Prefer
    best_matches when the candidates are all in hand: an exact name should beat a partial one."""
    return rung(query, stored, kind_words) is not None
