"""The thread's working set - what "those" means - and the hard filter it puts on a follow-up.

On 2 Oct 2026 a thread went: "how many work orders at Bishopsgate Tower last month?" -> "out of
those, which are blocked by vendor compliance?" -> "out of those, which save cost?" -> "out of
those, which breach SLA?". The first three follow-ups held the scope; the fourth answered for the
whole estate and recommended claiming against a vendor at another company's building. The scope
had only ever been prose in the model's context, and three turns in it let go.

So the scope is now a record. After every answer `derive()` works out, without a model, what the
thread is about - the building, the period, the work orders named - and the thread keeps it as its
working set. When the next question is a follow-up (`is_followup`: "those", "these", "them",
"out of those"...) the orchestrator:

  1. puts the working set in the message as a hard filter the model is told to pass to every tool;
  2. sets it in `active_scope`, so a tool the model calls WITHOUT a building or period fills them
     from the working set instead of reading the whole estate.

A question that names its own scope ("across all buildings", "at Harbour Point") is not a
follow-up: nothing is defaulted and the working set is replaced by what that answer establishes.
"""
from __future__ import annotations

import calendar
import re
from contextvars import ContextVar
from datetime import date, timedelta
from typing import Any

import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request
from . import name_match
from .energy_intelligence_agent import BUILDING_KIND_WORDS

log = structlog.get_logger(__name__)

active_scope: ContextVar[dict[str, Any] | None] = ContextVar("thread_active_scope", default=None)

_FOLLOWUP = re.compile(r"\b(those|these|them|out of (?:those|these|them)|of (?:those|these|them)|the same ones?|"
                       r"that list|the above|from (?:that|the) list|the ones?)\b", re.I)
_NEW_SCOPE = re.compile(r"\b(all buildings|whole (?:estate|portfolio)|across the (?:estate|portfolio|company)|every building)\b", re.I)
_WO = re.compile(r"\bWO-[A-Z0-9][A-Z0-9-]*\d\b")
# A building code of any length, hyphen or not: B-301, B-090765, B090765 (29 Oct 2026 - only three
# digits after a hyphen were read, so JASMEET's B-090765 never reached the working set).
_BCODE = re.compile(r"\bB-?\d{3,}\b", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
MAX_WO = 60


def is_followup(question: str) -> bool:
    q = question or ""
    return bool(_FOLLOWUP.search(q)) and not _NEW_SCOPE.search(q)


#: A short question that leans on the last answer without a pronoun: "what about the vendors?",
#: "and for Kingsway?", "which of them are blocked?".
_ELLIPTICAL = re.compile(r"^\s*(?:and\s+|so\s+)?(?:what about|how about|same for|and for|what of|of which|which of (?:them|those|these)|"
                         r"for (?:each|every) (?:one|of them)|(?:now|then) (?:show|list|tell))\b", re.I)
MAX_ELLIPTICAL_WORDS = 14


def continues(question: str) -> bool:
    """Whether a question continues the thread, so it reads inside the thread's working set without
    asking again (Workstream A4, 11 Nov 2026): "those"/"these" (is_followup), a reference to the last
    answer ("for each of them", "the previous list" - domain_chooser.refers_back), or a short
    elliptical follow-up ("what about the vendors?"). A question that names its own scope ("across all
    buildings") never does.

    Until now the working set was applied on is_followup alone, which was kept narrow on purpose;
    "what about the vendors?" after a Bishopsgate answer read the whole estate."""
    q = question or ""
    if _NEW_SCOPE.search(q):
        return False
    if is_followup(q):
        return True
    from .domain_chooser import refers_back

    if refers_back(q):
        return True
    return bool(_ELLIPTICAL.search(q)) and len(q.split()) <= MAX_ELLIPTICAL_WORDS


def parse_period(text: str, today: date | None = None) -> dict[str, Any] | None:
    """'last month', 'this month', 'September 2026', 'last 90 days' -> {from, to (exclusive), label, key}."""
    t = today or date.today()
    s = (text or "").lower()
    m0 = date(t.year, t.month, 1)
    # "this mont" / "last mont" are the month (2 Nov 2026: a typo dropped the period, and the answer
    # still said "this month").
    if re.search(r"\blast mon(?:th|t)\b|\bprevious month\b", s):
        start = (m0 - timedelta(days=1)).replace(day=1)
        return {"from": start.isoformat(), "to": m0.isoformat(), "label": start.strftime("%B %Y"), "key": "last_month"}
    if re.search(r"\bthis mon(?:th|t)\b|\bcurrent month\b|\bmonth to date\b", s):
        return {"from": m0.isoformat(), "to": (t + timedelta(days=1)).isoformat(), "label": m0.strftime("%B %Y") + " to date", "key": "this_month"}
    if re.search(r"\bthis year\b|\byear to date\b|\bytd\b", s):
        return {"from": date(t.year, 1, 1).isoformat(), "to": (t + timedelta(days=1)).isoformat(), "label": f"{t.year} to date", "key": "this_year"}
    if re.search(r"\blast (?:90 days|quarter|3 months|three months)\b", s):
        return {"from": (t - timedelta(days=90)).isoformat(), "to": (t + timedelta(days=1)).isoformat(), "label": "the last 90 days", "key": "last_90_days"}
    m = re.search(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(20\d\d)\b", s)
    if m:
        mon, yr = _MONTHS[m.group(1)], int(m.group(2))
        start = date(yr, mon, 1)
        end = date(yr + (mon == 12), mon % 12 + 1, 1)
        key = "last_month" if start == (m0 - timedelta(days=1)).replace(day=1) else "this_month" if start == m0 else None
        return {"from": start.isoformat(), "to": end.isoformat(), "label": start.strftime("%B %Y"), "key": key}
    return None


#: A question that sets one period against another names no single period for its tools.
_COMPARES_PERIODS = re.compile(
    r"\b(vs\.?|versus|compared?(?:\s+(?:with|to))?|against|all[\s-]+time|overall|in\s+total|ever)\b", re.I)


def question_scope(question: str, today: date | None = None) -> dict[str, Any] | None:
    """The scope a question names for itself - the one building it names, and its period - for the
    tools of a question that is not a follow-up (2 Nov 2026). A tool called without a building read
    the whole portfolio even when the question named one ("Decisions owed 38" under a Bishopsgate
    answer); the thread's building reached the tools only on a follow-up. A question that names
    several buildings, or none, sets no building: every one of them stays in scope."""
    name, bid, _several = _question_buildings(question or "", [])
    period = parse_period(question or "", today)
    if period and _COMPARES_PERIODS.search(question or ""):
        # "this month vs all time": the second read must not inherit the first period
        # (pre-push review, 3 Nov 2026).
        period = None
    out: dict[str, Any] = {}
    if name:
        out["building_name"] = name
        if bid:
            out["building_id"] = bid
    if period:
        out["period"] = period
    return out or None


def _tool_inputs(tools: list | None) -> list[dict[str, Any]]:
    out = []
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("input"), dict):
            out.append(t["input"])
    return out


def derive(previous: dict[str, Any] | None, question: str, answer: str, tools: list | None,
           today: date | None = None) -> dict[str, Any] | None:
    """The working set after this turn.

    A follow-up keeps the anchor (building, period, the work orders the anchor answer named) and
    records what this answer named as `last_mentioned`. A question with its own scope starts a
    new working set from what its tools were asked and its answer said."""
    inputs = _tool_inputs(tools)
    # The building is the one the QUESTION names (29 Oct 2026): "HOW MANY FLOORS IN JASMEET BUILDING"
    # after a Bishopsgate thread kept Bishopsgate, because the first tool input with a building won,
    # and a building id from one tool was kept beside another tool's building name.
    q_name, q_id, several = _question_buildings(question, inputs)
    q_code = _code(question)
    period = parse_period(question, today) or next((parse_period(str(i["period"]), today) for i in inputs if i.get("period")), None)
    named = list(dict.fromkeys(_WO.findall(answer or "")))[:MAX_WO]
    shown = entities(tools, answer)
    if continues(question) and previous and _same_building(previous, q_name, q_code, several):
        ws = dict(previous or {})
        ws["last_mentioned"] = named
        if any(shown.values()):
            # "those" now means what this answer showed (Workstream A4).
            ws["entities"] = shown
        ws["turns"] = int(ws.get("turns") or 1) + 1
        if (q_name or q_code) and not (ws.get("building_name") or ws.get("building_code") or ws.get("building_id")):
            ws.update(building_name=q_name, building_id=q_id, building_code=q_code)   # narrowed to a building
        return ws
    if q_name or several:
        building_name, building_id = q_name, q_id
    else:
        building_name, building_id = _first_building(inputs)
    code = q_code or (None if several else _code(answer))
    if not (building_name or building_id or code or period or named or several or any(shown.values())):
        return previous if previous and _NEW_SCOPE.search(question or "") is None else None
    ws = {"building_name": building_name, "building_id": building_id, "building_code": code,
          "period": period, "work_orders": named, "last_mentioned": named, "anchor_question": (question or "")[:300],
          "turns": 1}
    if any(shown.values()):
        ws["entities"] = shown
    if several:
        ws["buildings"] = several
    return ws


def carry(ws: dict[str, Any] | None, question: str, named: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The working set as a continuing question reads it. "And for Kingsway?" after a Bishopsgate
    answer continues the thread but names its own building: the building is the question's, the
    period stays, and the records the last answer showed (Bishopsgate's) go. `named` is the scope the
    question names for itself as the caller resolved it (the company's buildings register knows names
    this module cannot read off the text)."""
    if not ws:
        return ws
    name, bid, several = _question_buildings(question or "", [])
    if not name and named and named.get("building_name"):
        name, bid = str(named["building_name"]), (str(named["building_id"]) if named.get("building_id") else None)
    code = _code(question)
    if not (name or code or several) or _same_building(ws, name, code, several):
        return ws
    out = {k: v for k, v in ws.items() if k not in ("entities", "work_orders", "last_mentioned", "buildings")}
    out.update(building_name=name, building_id=bid, building_code=code)
    if several:
        out["buildings"] = several
    return out


def _code(text: str | None) -> str | None:
    return next((c.upper() for c in _BCODE.findall(text or "")), None)


def _core(name: str) -> list[str]:
    return name_match.meaningful(name, BUILDING_KIND_WORDS)


def _first_building(inputs: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """The first tool input's building when the question names none - its name with ITS id only."""
    for i in inputs:
        name = next((str(i[k]) for k in ("building_name", "building") if i.get(k)), None)
        if name:
            return name, (str(i["building_id"]) if i.get("building_id") else None)
    return None, next((str(i["building_id"]) for i in inputs if i.get("building_id")), None)


def _named_tier(question: str, name: str) -> int | None:
    """How the question names ``name``: 0 by its words (any order, a code without its hyphen),
    1 by a close spelling of each word, None not at all."""
    core = _core(name)
    if not core:
        return None
    r = name_match.rung(name, question, BUILDING_KIND_WORDS)
    if r is not None and r <= 3:
        return 0
    qw = name_match.words(question)
    target = name_match.compact(" ".join(core))
    if any("".join(qw[i:i + n]) == target for n in range(1, len(core) + 2) for i in range(len(qw) - n + 1)):
        return 0
    if all(any((q == w) if name_match.exact_token(w) else name_match.similarity(w, q) >= name_match.FUZZY_MIN
               for q in qw) for w in core):
        return 1
    return None


def _spans(question: str, name: str) -> set[tuple[int, int]]:
    """Where the name's words stand in the question, in order (the last may be a prefix)."""
    core, qw = _core(name), name_match.words(question)
    n, out = len(core), set()
    for i in range(len(qw) - n + 1):
        win = qw[i:i + n]
        last_ok = win[-1] == core[-1] if name_match.exact_token(core[-1]) else win[-1].startswith(core[-1])
        if n and last_ok and all(win[j] == core[j] for j in range(n - 1)):
            out.add((i, i + n))
    return out


# A phrase the question itself calls a building: "in JASMEET BUILDING", "at the Kew site".
_PLACE_WORDS = frozenset({"at", "in", "for", "of", "on", "about"})
_PLACE_END = frozenset({"last", "this", "next", "past", "since", "during", "between", "before", "after", "today",
                        "yesterday", "and", "or", "but", "with", "that", "which", "who", "where", "when", "is", "are",
                        "was", "were", "has", "have", "had", "do", "does", "did", "vs", "versus", "from", "to", "by",
                        "per", *_PLACE_WORDS, *_MONTHS})
_PLACE_BACK = frozenset({"those", "these", "them", "it", "its", "they", "each", "every", "all", "any", "my", "our",
                         "their", "which", "what"})


def _is_place(phrase: str) -> bool:
    """A name with the kind word after it ("JASMEET BUILDING", "Harbour Point site"), or the kind word
    and a number or code ("building 7", "building B-090765") - not "building services", "site office"."""
    ws = name_match.words(phrase)
    kinds = [i for i, w in enumerate(ws) if w in BUILDING_KIND_WORDS]
    return bool(kinds and _core(phrase)) and (
        ws[-1] in BUILDING_KIND_WORDS or all(name_match.exact_token(w) for w in ws[kinds[0] + 1:]))


def _places(question: str) -> list[str]:
    """Phrases after at/in/for/of/on/about that say they are a building ("JASMEET BUILDING") and
    name one beside the kind word - never "the building" alone, never "those"."""
    return _place_phrases(question, require_kind=True)


def place_candidates(question: str) -> list[str]:
    """Every phrase after at/in/for/of/on/about that could name a place, kind word or not ("at
    Bishopsgate Tower"). Not a building until the buildings register says so (orchestrator
    _named_building, 2 Nov 2026)."""
    return [p for p in _place_phrases(question, require_kind=False) if _core(p)]


def _place_phrases(question: str, require_kind: bool) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    open_ = False
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9'&-]*|[^\sA-Za-z0-9]", question or "") + ["."]:
        low = tok.lower()
        if not tok[0].isalnum() or low in _PLACE_END or re.fullmatch(r"20\d\d", low):
            phrase = " ".join(cur)
            if cur and (_is_place(phrase) or not require_kind):
                out.append(phrase)
            cur, open_ = [], low in _PLACE_WORDS
            continue
        if open_ and not cur and low in ("the", "a", "an"):
            continue
        if open_ and not cur and low in _PLACE_BACK:
            open_ = False
            continue
        if open_:
            cur.append(tok)
    return out


def _question_buildings(question: str, inputs: list[dict[str, Any]]) -> tuple[str | None, str | None, list[str]]:
    """The building the question names: (name, its id when the same tool input carried it, []), or
    (None, None, [names]) when it names several - all of them, none picked.

    A tool input counts when the question names it (agents/name_match.py: any word order, a code
    without its hyphen, a close spelling); a phrase the question calls a building counts on its own."""
    pairs = []
    for i in inputs:
        name = next((str(i[k]) for k in ("building_name", "building") if i.get(k)), None)
        if name and _core(name):
            pairs.append((name, str(i["building_id"]) if i.get("building_id") else None, _named_tier(question, name)))
    tiers = [t for _, _, t in pairs if t is not None]
    groups: list[list[tuple[str, str | None]]] = []
    for name, bid, t in pairs:
        if t is None or t != min(tiers):
            continue
        key = name_match.compact(" ".join(_core(name)))
        g = next((g for g in groups if name_match.compact(" ".join(_core(g[0][0]))) == key), None)
        if g is None:
            groups.append([(name, bid)])
        else:
            g.append((name, bid))
    # A short form inside the longer name it stands in ("Bishopsgate" in "... at Bishopsgate Tower") is
    # that building; a short form the question also names on its own ("Building 7 and Building 7 East") is not.
    spans = [set().union(*(_spans(question, n) for n, _ in g)) for g in groups]
    keep = [g for k, g in enumerate(groups)
            if not (spans[k] and any(j != k and all(any(a >= c and b <= d and (a, b) != (c, d) for c, d in spans[j])
                                                    for a, b in spans[k]) for j in range(len(groups))))]
    for place in _places(question):
        if not any(name_match.names_match(place, n, BUILDING_KIND_WORDS) or name_match.names_match(n, place, BUILDING_KIND_WORDS)
                   for g in keep for n, _ in g):
            keep.append([(place, None)])
    if not keep:
        return None, None, []
    if len(keep) > 1:
        return None, None, [max((n for n, _ in g), key=len) for g in keep]
    g = keep[0]
    name = max((n for n, _ in g), key=len)
    return name, next((b for n, b in g if b and n == name), None) or next((b for _, b in g if b), None), []


def _same_building(ws: dict[str, Any], name: str | None, code: str | None, several: list[str]) -> bool:
    """Whether a follow-up's own building is the working set's (or it names none): the anchor is
    kept unless a building the question names can be compared with it and differs."""
    if several:
        return False
    if not (name or code):
        return True
    ws_name, ws_code = ws.get("building_name"), ws.get("building_code")
    verdicts = []
    if name and ws_name:
        verdicts.append(name_match.names_match(name, ws_name, BUILDING_KIND_WORDS)
                        or name_match.names_match(ws_name, name, BUILDING_KIND_WORDS))
    if code and ws_code:
        verdicts.append(name_match.compact(code) == name_match.compact(ws_code))
    if name and not ws_name and ws.get("buildings"):
        verdicts.append(any(name_match.names_match(name, b, BUILDING_KIND_WORDS)
                            or name_match.names_match(b, name, BUILDING_KIND_WORDS) for b in ws["buildings"]))
    return not verdicts or any(verdicts)


#: Where a row names each kind of record: (name keys, code keys, id keys).
_ENTITY_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "buildings": (("building_name", "building"), ("building_code",), ("building_id",)),
    "vendors": (("vendor_name", "vendor", "trading_name"), ("vendor_code",), ("vendor_id",)),
    "assets": (("asset_name", "asset"), ("asset_code", "asset_tag"), ("asset_id",)),
}
MAX_ENTITIES = 30


def entities(tools: list | None, answer: str) -> dict[str, list[dict[str, str]]]:
    """The records this answer showed, verified (Workstream A4, 11 Nov 2026): each building, vendor and
    asset that a tool returned AND the answer names, with its code and id from the row - never a name
    read off the answer alone. What "those vendors" means on the next turn."""
    import json as _json

    from .evidence import _rows

    a = (answer or "").lower()
    out: dict[str, list[dict[str, str]]] = {k: [] for k in _ENTITY_FIELDS}
    seen: set[tuple[str, str]] = set()
    for tc in tools or []:
        if not isinstance(tc, dict):
            continue
        output = tc.get("output")
        if isinstance(output, str) and output.strip().startswith(("{", "[")):
            try:
                output = _json.loads(output)
            except ValueError:
                continue
        for row in (_rows(output) or [])[:2000]:
            for kind, (names, codes, ids) in _ENTITY_FIELDS.items():
                if len(out[kind]) >= MAX_ENTITIES:
                    continue
                name = next((str(row[k]).strip() for k in names if isinstance(row.get(k), str) and row[k].strip()), "")
                code = next((str(row[k]).strip() for k in codes if row.get(k) not in (None, "")), "")
                if not (name or code):
                    continue
                if not ((code and code.lower() in a) or (len(name) >= 3 and name.lower() in a)):
                    continue
                key = (kind, (code or name).lower())
                if key in seen:
                    continue
                seen.add(key)
                ent = {k: v for k, v in (("name", name), ("code", code),
                                          ("id", next((str(row[k]) for k in ids if row.get(k) not in (None, "")), ""))) if v}
                out[kind].append(ent)
    return out


def describe(ws: dict[str, Any] | None) -> str:
    if not ws:
        return ""
    parts = []
    b = ws.get("building_name") or ws.get("building_code")
    if b:
        parts.append("building " + b + (f" ({ws['building_code']})" if ws.get("building_code") and ws.get("building_name") else ""))
    elif ws.get("buildings"):
        parts.append("buildings " + ", ".join(str(x) for x in ws["buildings"]))
    if ws.get("period"):
        parts.append("period " + ws["period"]["label"] + f" ({ws['period']['from']} to {ws['period']['to']}, end exclusive)")
    if ws.get("work_orders"):
        wos = ws["work_orders"]
        parts.append(f"work orders named so far: {', '.join(wos[:20])}" + (f" and {len(wos) - 20} more" if len(wos) > 20 else ""))
    ents = ws.get("entities") or {}
    for kind in ("vendors", "assets"):
        shown = [e.get("name") or e.get("code") for e in ents.get(kind) or [] if e.get("name") or e.get("code")]
        if shown:
            parts.append(f"{kind} the last answer showed: {', '.join(shown[:20])}" + (f" and {len(shown) - 20} more" if len(shown) > 20 else ""))
    if not b and not ws.get("buildings") and ents.get("buildings"):
        names = [e.get("name") or e.get("code") for e in ents["buildings"] if e.get("name") or e.get("code")]
        parts.append("buildings the last answer showed: " + ", ".join(names[:20]))
    return "; ".join(parts)


def scope_block(ws: dict[str, Any] | None) -> str:
    """What the model is told on a follow-up."""
    d = describe(ws)
    if not d:
        return ""
    return ("## Working set (hard filter - this is what the user's \"those\"/\"these\"/\"them\" means)\n"
            f"{d}.\n"
            "Pass this building and period to every tool you call (building_name / building_id, period); never widen "
            "to other buildings or periods. If a tool cannot filter, keep only the results inside this set and say so. "
            "A work order outside this building is not part of the answer.")


# ── defaults for tools, read from the ContextVar ───────────────────────────────────────

def scope_building() -> tuple[str | None, str | None]:
    ws = active_scope.get()
    if not ws:
        return None, None
    return ws.get("building_name") or ws.get("building_code"), ws.get("building_id")


def scope_period_key() -> str | None:
    ws = active_scope.get()
    return (ws or {}).get("period", {}).get("key") if ws and ws.get("period") else None


def scope_period_bounds() -> tuple[str | None, str | None]:
    ws = active_scope.get()
    p = (ws or {}).get("period") or {}
    return p.get("from"), p.get("to")


def scope_hint() -> str:
    """A clause appended to a free-text question so a record read resolves the same scope."""
    ws = active_scope.get()
    if not ws:
        return ""
    b, _ = scope_building()
    p = ws.get("period") or {}
    bits = [x for x in ((f"at {b}" if b else ""), (f"in {p['label']}" if p.get("label") else "")) if x]
    return (" (" + " ".join(bits) + ")") if bits else ""


_SERVICE = "operations_intelligence"
_TIMEOUT = 60.0


@tool
async def replacement_candidates(building_name: str | None = None, building_id: str | None = None,
                                 period: str | None = None) -> dict:
    """What should be bought again - PARTS to reorder and ASSETS at the end of their life - in one read.

    USE THIS for "repurchase", "reorder", "restock", "replace", "replacement", "end of life", "beyond economic
    repair", "write off", "which assets should we replace / buy new", "what is below reorder level". The
    question has two readings and this answers both from the product's own definitions: `parts_to_reorder`
    (spare_parts at or below reorder level, company stock), `end_of_life` (assets at condition grade >= 4,
    the Assets page's remediate-or-replace grade), `repeat_failures` (3+ reactive jobs in the period, reactive
    cost against replacement value), `inspection_recommendations` that say replace and whether they were
    actioned, `replacement_work_orders`, and what `purchase_orders` actually holds. Returns `answer_rules`.

    Name the building; `period` (last_month, this_year, last_90_days, 'September 2026') bounds the repeat
    failures - a year back by default. On a follow-up the thread's working set fills these in. Never look for
    a replacement register elsewhere (energy_recommendations is not one).
    """
    from .energy_intelligence_agent import _resolve_building

    if not (building_name or building_id):
        building_name, building_id = scope_building()
    params: dict[str, Any] = {}
    if building_name or building_id:
        bid, problem = await _resolve_building(building_name, building_id)
        if problem:
            return problem
        params["building_id"] = bid
    bounds = parse_period(period or "") if period else None
    if bounds:
        params["period_from"], params["period_to"] = bounds["from"], bounds["to"]
    elif not period:
        f, to = scope_period_bounds()
        if f and to:
            params["period_from"], params["period_to"] = f, to
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/assets/replacement-candidates",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise into the agent loop
        log.error("replacement_candidates.failed", error=str(exc)[:300])
        return {"ok": False, "error": "The replacement read failed: " + str(exc).splitlines()[0][:300]}
    if isinstance(body, dict):
        body["scope"] = {"building": building_name or building_id or "all buildings",
                         "period": period or ((active_scope.get() or {}).get("period") or {}).get("label") or "the last 12 months"}
    return body


@tool
async def work_order_blockers(building_name: str | None = None, building_id: str | None = None,
                              period: str | None = None, work_orders: list[str] | None = None) -> dict:
    """Which OPEN work orders cannot proceed because of their vendor - and why - in one read.

    USE THIS for "which work orders are blocked / can't proceed / are held because of vendor compliance,
    a lapsed accreditation, insurance, a contract that has expired or is up for renewal". It joins each
    open work order to its vendor's block state, lapsed or expiring accreditation certificates, contracts
    (expired, ending within 60 days, not active) and unconfirmed SLA terms.

    Returns `blocked` (cannot proceed today, with the blocker), `at_risk` (can proceed, vendor risk to
    call out), `clear`, and `answer_rules`. Name the building; `period` (last_month, this_month,
    this_year, last_90_days or 'September 2026') narrows to work orders raised then; `work_orders`
    narrows to those codes. On a follow-up ("out of those") the thread's working set fills these in.
    """
    from .energy_intelligence_agent import _resolve_building

    if not (building_name or building_id):
        building_name, building_id = scope_building()
    params: dict[str, Any] = {}
    if building_name or building_id:
        bid, problem = await _resolve_building(building_name, building_id)
        if problem:
            return problem
        params["building_id"] = bid
    bounds = parse_period(period or "") if period else None
    if bounds:
        params["period_from"], params["period_to"] = bounds["from"], bounds["to"]
    elif not period:
        f, to = scope_period_bounds()
        if f and to:
            params["period_from"], params["period_to"] = f, to
    codes = list(work_orders or []) or (active_scope.get() or {}).get("work_orders") or []
    if codes:
        params["wo_code"] = [str(c) for c in codes][:60]
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/work-orders/blockers",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise into the agent loop
        log.error("work_order_blockers.failed", error=str(exc)[:300])
        return {"ok": False, "error": "The blocker read failed: " + str(exc).splitlines()[0][:300]}
    if isinstance(body, dict):
        body["scope"] = {"building": building_name or building_id or "all buildings", "period": period or
                         ((active_scope.get() or {}).get("period") or {}).get("label") or "all open",
                         "work_orders": len(codes) or "all open"}
    return body
