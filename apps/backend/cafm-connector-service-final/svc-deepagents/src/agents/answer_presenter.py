"""A finished answer, laid out by the model as the OpenUI program the chat draws.

Only the general loop writes its own answer as OpenUI (agents/openui_view.py). Every other route
the chat reaches (the engine sub-agents, the planner's synthesis, the compliance summary) writes
Markdown, and that Markdown has already passed its checks. Once the answer is final, this asks the
model one more time to lay it out with the chat's components: headline figures as tiles,
comparisons as charts, what to do as numbered steps (27 Oct 2026: "I want charts and better UI on
all answers").

The answer itself is untouched. `answer`, the stored thread, reports, emails and every check read
the Markdown exactly as it was. The layout rides the tool calls as the same `openui_view` card the
general loop sends, and only when it says what the answer says, as the reader sees it without a
click (no follow-up chips or buttons, nothing inside a collapsed section):

* every figure in the answer is shown, and the layout (chips included) has no figure, date, sign,
  direction, unit or currency the answer does not;
* every row of the answer's tables is shown whole, every bar and table row of the layout pairs a
  label with the figure the answer gives it, and a tile's label is the one the answer gives its figure;
* a change stays a change ("down 2.04 points"), never a level;
* every "not", "no" and caveat ("provisional", "yet", "only") is shown;
* a Finding (one issue of several) has the title and code the answer gives that issue, and every
  figure, date and code in it is one the answer says in the same paragraph as that title or code;
* every name the answer uses (a capitalised word inside a sentence: a vendor, a building, a code)
  is shown, and most of the words of each paragraph, so a fact cannot be reworded away.

27 Oct 2026 review: the check used to compare sets, so a fall drawn as a rise, a figure under
another vendor, a dropped "not" or a data gap left only in a follow-up chip all passed.

Anything else (no model, a slow model, a program that does not parse, a component the chat would
drop, a fact dropped, hidden or changed) and nothing is sent: the chat draws the Markdown as before
(apps/frontend/src/openui/fromMarkdown.js).

OPENUI_PRESENTER=0 switches this off on its own; OPENUI_ANSWERS=0 switches off every view.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from typing import Any, NamedTuple

import structlog

from . import openui_view

log = structlog.get_logger(__name__)

# An answer shorter than this is a sentence or two: the Markdown already reads as well as a layout.
MIN_CHARS = 160
# Longer than this, laying it out costs more than it is worth and is slow to come back.
MAX_CHARS = 14_000
TIMEOUT_S = 40.0
# Share of the answer's longer ordinary words the layout must keep (names and figures: all of them).
WORD_COVERAGE = 0.85
# Share of each clause's longer words the layout must show. A Metric may trim a word from the
# sentence it lifts ("SLA response component fell to 23.44 / 25" under the label "SLA response"),
# but no clause may go. (28 Oct 2026: at 0.85 overall, a layout dropped "the worst offender,
# CHILLER-101, also carrying the site's lowest condition score" and was still sent.)
CLAUSE_COVERAGE = 0.6
# Share of each paragraph's words the layout must show, so a whole caveat or data gap cannot go.
PASSAGE_COVERAGE = 0.6
# Card payloads that are drawn by their own components: an answer that has them is not laid out.
OWN_LAYOUT = {"openui_view", "compliance_response"}

Llm = Callable[[str, str, str], Awaitable[str]]


def enabled() -> bool:
    return openui_view.enabled() and os.getenv("OPENUI_PRESENTER", "1").strip().lower() not in {
        "0", "false", "off", "no"}


def wanted(answer: Any, tool_calls: list[Any] | None, *, choices: Any = None) -> bool:
    """Whether this finished answer is one to lay out."""
    if not enabled() or not isinstance(answer, str) or choices:
        return False
    text = answer.strip()
    if not MIN_CHARS <= len(text) <= MAX_CHARS or openui_view.split_answer(text)[1] is not None:
        return False
    return not any(isinstance(t, dict) and t.get("tool") in OWN_LAYOUT for t in tool_calls or [])


_HEADER = (
    "You lay out an answer that is already written and checked. Rewrite the answer below as one "
    "OpenUI Lang program built from the components described after these rules, so the chat can "
    "draw it.\n\n"
    "Rules that come before everything below:\n"
    "- The answer is your only source. Every figure, date, name, code and link in your program is "
    "in the answer, written the same way. Never add one, never work one out (no totals, counts, "
    "averages or differences of your own), never round one.\n"
    "- Keep every fact the answer states: every figure, every row of every table, every name and "
    "every caveat or limitation. Keep every sentence in the answer's own words: never shorten, "
    "summarise or drop a word; only the Markdown marks (bold, bullets, heading marks) may go. When "
    "the answer's opening paragraph is longer than two sentences, the Headline is its first "
    "sentence and a Text right after it carries the rest of that paragraph word for word.\n"
    "- Keep every heading of the answer, its title included, as the title of a Section (or a "
    "Callout's title) in the same words.\n"
    "- Lay it out so it reads at a glance: the direct answer as the Headline; the 2 to 6 figures "
    "the answer turns on as Metrics; a comparison of one measure across 3 or more items as a "
    "BarChart (a table that carries those figures stays as a Table too when it has other "
    "columns); a measure over periods as a LineChart; what to do as Steps; a warning or a gap in "
    "the data as a Callout; each headed part of the answer as a Section.\n"
    "- When the answer lists several issues, each about one asset, certificate, vendor or work "
    "order (often a bold lead, then the detail), write one Finding per issue, in the answer's order, "
    "one after another. Its title is the issue's lead and its text the rest of that issue, both in "
    "the answer's own words; its code is the record or asset code the answer gives that issue, only "
    "when it gives one. When the lead writes the issue's code in brackets, leave the brackets out "
    "of the title and give that code as code ('Fire Risk Assessment — Lapsed', code "
    "'FRA-B-301-2025'), so the code is shown once. "
    "Every figure, date and code in a Finding is one the answer says of that "
    "issue, in the same paragraph: never move one to another issue. Keep Callout for a single "
    "caveat set apart from the rest, and Text for prose that is not a list of issues.\n"
    "- A Finding's tone is risk only for something lapsed, expired, overdue, blocked or failed; warn "
    "for something due soon or expiring; ok for something in date or healthy; neutral for anything "
    "else. Its own words must say it.\n"
    "- A layout never collapses a Section: everything the answer says is shown without a click. "
    "FollowUps and Buttons are not where a fact goes: every fact is in a Headline, Metric, Table, "
    "chart, Callout, Finding, Text, Details or Step.\n"
    "- Write every figure as a string exactly as the answer writes it (\"1,200\", \"£84,000\", "
    "\"2026\", \"-2.04\", \"23.44 / 25\"), never as a bare number: the chat shows a bare number "
    "without the answer's commas, zeros or sign. Keep each figure's sign or direction word, unit and "
    "currency, and each date as the answer writes it.\n"
    "- Say each figure once: never repeat a BarChart's labels and values as a Table of only those "
    "two columns, and never title a Section with the title of the one chart inside it. A Table "
    "keeps every column of the answer's table, including the one a BarChart draws: never drop a "
    "column because a chart shows it.\n"
    "- A Metric's value and a chart's values are figures copied from the answer. A change or "
    "difference (+0.45, -2 pt) goes in a Metric's note, never as a bar.\n"
    "- A Metric's value is the thing its label names. When the answer gives only a change for "
    "something (its overall score down 2.04 points), the value says the change with its "
    "direction (\"down 2.04 points\") and the label says what changed and over what "
    "(\"Overall score, month-on-month\"): never a bare change under a label that reads as the "
    "level itself.\n"
    "- End with FollowUps of two or three short questions the user is likely to ask next about "
    "what the answer covers. A question states no fact: no figure, date or name the answer does "
    "not have. Write a Button only for a next step the answer itself offers, labelled with what "
    "it asks for in words other than its step's title.\n"
    "- Output the program only: no prose before or after it and no code fences."
)


def system_prompt() -> str:
    section = openui_view.prompt_section()
    return _HEADER + ("\n\n---\n\n" + section if section else "")


def user_prompt(question: str, answer: str) -> str:
    return ("The user asked:\n" + (question or "").strip()
            + "\n\nThe answer to lay out:\n<answer>\n" + answer.strip() + "\n</answer>")


# ── the layout says what the answer says ─────────────────────────────────────────────────────

# A list's own numbering is not a figure: "1. ", "**1.** ", and "(1) " after a colon or a comma
# (27 Oct 2026: "What to do: (1) ask …, (2) ask …" read as the figures 1 and 2).
_ORDINAL = re.compile(r"^[ \t>*_#-]*\d+[.)](?:\*\*|__)?[ \t]+|(?:^|(?<=[:;,])|(?<=[:;,]\*\*))[ \t]*\(\d{1,2}\)[ \t]+",
                      re.MULTILINE)
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’]*")
# "Bishopsgate Tower's" names Bishopsgate Tower (27 Oct 2026: it read as the name "tower's", so a
# layout that wrote the name plainly was refused).
_POSSESSIVE = re.compile(r"['’]s?$")
_SENTENCE_END = re.compile(r"(?:^|[.!?:;\n|·]|\*\*|^\s*[-*>#]+)\s*$")


def _norm(raw: str) -> str:
    s = raw.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def figures(text: str) -> set[str]:
    """Every figure written in the text, as a number reads (1,200 = 1200; 4.50 = 4.5). A numbered
    list's own numbering is not a figure: a layout numbers its steps itself."""
    return {_norm(m.group(0)) for m in _FIGURE.finditer(_ORDINAL.sub("", text or ""))}


def _bare(word: str) -> str:
    return _POSSESSIVE.sub("", word)


def names(text: str) -> set[str]:
    """Words the text capitalises inside a sentence (a vendor, a building, a code), lower-cased.
    A capital at the start of a sentence or a line says nothing, so it is not counted."""
    out: set[str] = set()
    for m in _WORD.finditer(text or ""):
        w = _bare(m.group(0))
        if not w[0].isupper() or len(w) < 2 or (len(w) < 3 and not w.isupper()):
            continue
        before = (text[max(0, m.start() - 6):m.start()]).rstrip(" \t*_[(\"'`")
        if not before or _SENTENCE_END.search(before):
            continue
        out.add(w.lower())
    return out


_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_DIVIDER = re.compile(r"^[\s|:-]+$")
_MARKS = re.compile(r"\*+|_+|`|\s+")


def _squash(s: str) -> str:
    """Text compared without its spacing and emphasis: "23.44 / 25" reads as "23.44/25", "*Expired*"
    as "expired"."""
    return _MARKS.sub("", s or "").lower()


def _split_row(inner: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", inner)]


def cells(text: str) -> list[str]:
    """Every filled cell of every Markdown table in the text. A table's rows are facts the answer
    lists one by one, so each one must survive the layout."""
    out: list[str] = []
    for line in (text or "").splitlines():
        m = _TABLE_ROW.match(line)
        if not m or _DIVIDER.match(m.group(1)):
            continue
        out.extend(c for c in _split_row(m.group(1)) if c and c not in {"—", "-", "–"})
    return out


_TITLE_LINE = re.compile(r"^\s*(?:#{1,6}\s+.+|\*\*[^*]+\*\*:?)\s*$")


def _tables(text: str) -> list[tuple[list[str], list[list[str]], str]]:
    """Every Markdown table in the text: its headings, its rows, and the title line over it (a
    heading or a bold line), if any."""
    out: list[tuple[list[str], list[list[str]], str]] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        if not _TABLE_ROW.match(lines[i]):
            i += 1
            continue
        j = i
        while j < len(lines) and _TABLE_ROW.match(lines[j]):
            j += 1
        block = [_TABLE_ROW.match(line).group(1) for line in lines[i:j]]  # type: ignore[union-attr]
        above = next((line for line in reversed(lines[:i]) if line.strip()), "")
        head, body = ([], block)
        if len(block) >= 2 and _DIVIDER.match(block[1]):
            head, body = _split_row(block[0]), block[2:]
        rows = [_split_row(b) for b in body if not _DIVIDER.match(b)]
        out.append((head, rows, above if _TITLE_LINE.match(above) else ""))
        i = j
    return out


def _prose(text: str) -> str:
    """The text without its tables."""
    return "\n".join(line for line in (text or "").splitlines() if not _TABLE_ROW.match(line))


def _sentences(text: str) -> list[str]:
    return [s for line in text.splitlines() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]


_CLAUSE = re.compile(r",\s+|;\s+|\s+(?:and|but|while|whereas)\s+|\s+[—–]\s+", re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {_bare(w).lower() for w in _WORD.findall(text or "")}


# ── figures with what is written around them ──

# A date is read whole, so 03/09/2026 and 09/03/2026 are different dates (their figures are not).
_DATE = re.compile(r"(?<![\w.])(\d{1,4})([/.-])(\d{1,2})\2(\d{1,4})(?![\w])")
_UNITS = {
    "%": "%", "°c": "°C", "m²": "m²", "pt": "pt", "pts": "pt", "point": "pt", "points": "pt", "kwh": "kWh",
    "mwh": "MWh", "kw": "kW", "day": "day", "days": "day", "hour": "hr", "hours": "hr", "hr": "hr", "hrs": "hr",
    "week": "week", "weeks": "week", "month": "month", "months": "month", "year": "yr", "years": "yr", "yr": "yr",
    "yrs": "yr", "min": "min", "mins": "min", "minute": "min", "minutes": "min",
}
# A figure with its sign, currency and unit. A sign counts only where it is not part of a code or a
# range (WO-1184, 2024-25 and Sep-26 have none).
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9.,/])(?P<sign>[-+−])?(?:(?P<cur>[£$€])[ \t]?)?(?P<num>\d[\d,]*(?:\.\d+)?)(?!\d)"
    r"(?:[ \t]?(?P<unit>%|°C|m²|(?:pts?|points?|kWh|MWh|kW|days?|hours?|hrs?|weeks?|months?|years?|yrs?|mins?|"
    r"minutes?)\b))?", re.IGNORECASE)
_DOWN = (r"fell|falls?|falling|dropped|drops?|down|declined|declines?|decreased|decreases?|lost|lower|reduced|"
         r"reduction|slipped|worsened")
_UP = r"rose|rises?|rising|up|increased|increases?|grew|growth|gained|gains?|improved|improvement|higher|climbed|jumped"
_DIRECTION = re.compile(rf"(?:{_DOWN}|{_UP})", re.IGNORECASE)
# A direction word right before a figure ("fell 0.73", "down by 2", "a rise of 12%") or a comparative
# right after it ("18% more energy").
_BEFORE = re.compile(rf"\b(?:(?P<down>{_DOWN})|(?P<up>{_UP}))(?:[ \t]+(?:by|of))?[ \t*_]*$", re.IGNORECASE)
_AFTER = re.compile(r"[ \t*_]*(?:(?P<down>less|fewer|lower|down|worse)|(?P<up>more|higher|up|better))\b", re.IGNORECASE)


class _Fig(NamedTuple):
    value: str  # as figures() reads it
    way: str  # "down", "up" or "": the direction the text gives it
    cur: str
    unit: str
    raw: str  # as written


def _figs(text: str) -> list[_Fig]:
    t = _DATE.sub(" ", _ORDINAL.sub("", text or ""))
    out: list[_Fig] = []
    for m in _TOKEN.finditer(t):
        way = {"-": "down", "−": "down", "+": "up"}.get(m.group("sign") or "", "")
        if not way:
            hit = _BEFORE.search(t, max(0, m.start() - 40), m.start()) or _AFTER.match(t, m.end())
            if hit:
                way = "down" if hit.group("down") else "up"
        unit = _UNITS.get((m.group("unit") or "").lower(), "")
        out.append(_Fig(_norm(m.group("num")), way, m.group("cur") or "", unit, m.group(0).strip()))
    return out


def _say(value: str) -> str:
    """A figure as figures() reads it, written for a reason: ".62" as "0.62"."""
    return "0" + value if value.startswith(".") else value


def dates(text: str) -> set[str]:
    """Every date written in figures (2026-09-14, 03/09/2026), its parts in the order written."""
    return {"/".join(str(int(p)) for p in (m.group(1), m.group(3), m.group(4))) for m in _DATE.finditer(text or "")}


# ── words, for the labels beside a figure ──

_STOP = {"the", "and", "for", "with", "from", "this", "that", "its", "per", "versus", "than", "into", "all", "any",
         "are", "was", "were", "has", "have", "had", "not", "but", "our", "your", "their", "out", "off"}
# Capitalised words that are not who or what a clause is about.
_NOT_SUBJECT = {"Its", "The", "This", "These", "There", "That", "Their", "What", "Which", "All", "Our", "Each",
                "Both", "And", "But", "January", "February", "March", "April", "May", "June", "July", "August",
                "September", "October", "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday",
                "Friday", "Saturday", "Sunday"}


def _stem(word: str) -> str:
    """A word cut to its root, enough to read "scored" as "score" and "anomalies" as "anomaly"."""
    w = _bare(word).lower()
    for suffix in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            w = w[:-len(suffix)]
            break
    return w[:6]


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text or "") if len(_bare(w)) >= 3 and _bare(w).lower() not in _STOP}


def _caps(text: str) -> set[str]:
    return _stems(" ".join(w for w in _WORD.findall(text or "") if w[0].isupper()))


def _has_subject(clause: str) -> bool:
    """Whether the clause names who or what it is about after its first word ("Apex Mechanical fell
    …"); "Its SLA response fell …" does not, so it is about what the sentences before name."""
    return any(w[0].isupper() and not w.isupper() and _bare(w) not in _NOT_SUBJECT for w in _WORD.findall(clause)[1:])


def _contexts(answer: str) -> dict[str, list[set[str]]]:
    """Each figure of the answer with the words it is said with: its table row, column heading and
    the table's title, or the clause it sits in (and, when that clause names no one, the names of
    its sentence and the one before)."""
    out: dict[str, list[set[str]]] = {}
    for head, rows, above in _tables(answer):
        for row in rows:
            around = _stems(" ".join(row)) | _stems(above)
            for j, cell in enumerate(row):
                words = around | (_stems(head[j]) if j < len(head) else set())
                for f in _figs(cell):
                    out.setdefault(f.value, []).append(words)
    sentences = _sentences(_prose(answer))
    for k, sentence in enumerate(sentences):
        inherited = _caps(sentence) | (_caps(sentences[k - 1]) if k else set())
        for clause in _CLAUSE.split(sentence):
            words = _stems(clause) | (set() if _has_subject(clause) else inherited)
            for f in _figs(clause):
                out.setdefault(f.value, []).append(words)
    return out


# ── the layout's tree ──

def _kids(v: Any) -> list[openui_view.Node]:
    return [x for x in (v if isinstance(v, list) else [v]) if isinstance(x, openui_view.Node)]


def _walk(v: Any) -> Iterator[openui_view.Node]:
    """Every component of the layout, closed sections and buttons included."""
    if isinstance(v, list):
        for x in v:
            yield from _walk(x)
    elif isinstance(v, openui_view.Node):
        yield v
        for p in v.props.values():
            yield from _walk(p)


def _records(v: Any, out: list[list[str]], shown: bool = True) -> list[list[str]]:
    """The rows the reader sees: table rows, bars, points, tiles, fields and steps, and any Markdown
    table written in prose. With shown, not what needs a click: chips, buttons, a closed section."""
    text = openui_view.cell_text
    for node in _kids(v):
        p, t = node.props, node.type
        if shown and t in ("Buttons", "Button", "FollowUps"):
            continue
        if t == "Table":
            out.append([text(c) for c in p.get("columns") or []])
            out.extend([text(c) for c in r] for r in p.get("rows") or [] if isinstance(r, list))
        elif t == "BarChart":
            labels, values = p.get("labels") or [], p.get("values") or []
            out.extend([text(lb), text(values[i] if i < len(values) else None)] for i, lb in enumerate(labels))
        elif t == "LineChart":
            labels = p.get("labels") or []
            series = [s.props.get("values") or [] for s in _kids(p.get("series"))]
            out.extend([text(lb)] + [text(s[i] if i < len(s) else None) for s in series] for i, lb in enumerate(labels))
        elif t in ("Metric", "Field", "Step"):
            out.append([text(p.get(k)) for k in ("label", "value", "note", "title", "detail") if k in p])
        elif t == "Finding":
            out.append([text(p.get(k)) for k in ("title", "code", "text") if k in p])
        elif t == "Details":
            fields = [f.props for f in _kids(p.get("fields"))]
            out.append([text(p.get("title")), text(p.get("subtitle"))]
                       + [text(f.get(k)) for f in fields for k in ("label", "value")])
        for prose in (p.get("text"), p.get("markdown"), p.get("summary")):
            for head, rows, _above in _tables(prose if isinstance(prose, str) else ""):
                out.extend([head, *rows])
        if t == "Section" and shown and p.get("collapsed") is True:
            continue
        for key in ("children", "items"):
            _records(p.get(key), out, shown)
    return out


def _key(cell: str) -> str:
    """A cell as compared: emphasis and spacing gone, and a figure's thousands separators too, since
    the chat shows 12400 for "12,400" (a different value, or 241 for "241.0", still differs)."""
    s = _squash(cell)
    if s in {"", "—", "-", "–"}:
        return ""
    return s.replace(",", "").replace("−", "-") if _NUMERIC_CELL.fullmatch(s) else s


_NUMERIC_CELL = re.compile(r"[-+−]?[£$€]?\d[\d,]*(?:\.\d+)?%?")


# ── the checks ──

_NEGATION = re.compile(r"\b(?:no|not|never|none|nor|neither|nothing|nobody|without|cannot|unable|\w+n['’]t)\b",
                       re.IGNORECASE)
_CAVEAT = re.compile(r"\b(?:provisional(?:ly)?|estimated?|estimates|approx(?:imately)?|roughly|(?-i:may)|might|could|"
                     r"likely|unlikely|unconfirmed|unverified|pending|preliminary|partial(?:ly)?|only|yet|"
                     r"assum(?:ed|ing)|expected|uncertain|incomplete|excluding|except|unless)\b", re.IGNORECASE)
# A label that says its figure is a change, not a level.
_CHANGE = re.compile(rf"\b(?:change[sd]?|difference|delta|movement|month-on-month|year-on-year|week-on-week|vs|"
                     rf"versus|compared|{_DOWN}|{_UP})\b", re.IGNORECASE)
# Words that join facts rather than state them; and direction words, which each figure keeps itself.
_CONNECTIVE = {"while", "whereas", "although", "also", "which", "that", "this", "these", "those", "with", "from",
               "into", "than", "then", "there", "their", "have", "been", "being", "were", "will", "would", "should",
               "what", "when"}


def _plain(text: str, shortest: int) -> set[str]:
    return {w for w in _words(text)
            if len(w) >= shortest and w not in _CONNECTIVE and not _DIRECTION.fullmatch(w)}


def _figures_kept(answer: str, rendition: str, shown: str) -> str | None:
    a, r, seen = figures(answer), figures(rendition), figures(shown)
    if a - r:
        return "dropped figures " + ", ".join(map(_say, sorted(a - r)[:6]))
    if a - seen:
        hidden = ", ".join(map(_say, sorted(a - seen)[:6]))
        return "figures only behind a click (a closed section or a button) " + hidden
    if r - a:
        return "added figures " + ", ".join(map(_say, sorted(r - a)[:6]))
    if dates(rendition) - dates(answer):
        return "changed dates " + ", ".join(sorted(dates(rendition) - dates(answer))[:4])
    if dates(answer) - dates(shown):
        return "dropped dates " + ", ".join(sorted(dates(answer) - dates(shown))[:4])
    return None


def _marks_kept(answer: str, rendition: str, shown: str) -> str | None:
    """A figure keeps its unit, currency and direction. A layout may leave a unit or currency to its
    label, but never gives a figure one the answer never gives it, and never turns a fall into a rise."""
    said = _figs(answer)
    units: dict[str, set[str]] = {}
    curs: dict[str, set[str]] = {}
    for f in said:
        units.setdefault(f.value, set()).add(f.unit)
        curs.setdefault(f.value, set()).add(f.cur)
    ways = {(f.value, f.way) for f in said if f.way}
    for f in _figs(rendition):
        if f.unit and f.value in units and "" not in units[f.value] and f.unit not in units[f.value]:
            written = ", ".join(sorted(units[f.value]))
            return f"changed the unit of {_say(f.value)}: the answer writes {written}, the layout '{f.raw}'"
        if f.cur and f.value in curs and "" not in curs[f.value] and f.cur not in curs[f.value]:
            written = ", ".join(sorted(curs[f.value]))
            return f"changed the currency of {_say(f.value)}: the answer writes {written}, the layout '{f.raw}'"
        if f.way and (f.value, f.way) not in ways:
            return (f"changed the direction of {_say(f.value)}: the layout says it went {f.way} ('{f.raw}'), "
                    "the answer does not")
    kept = {(f.value, f.way) for f in _figs(shown) if f.way}
    for f in said:
        if f.way and (f.value, f.way) not in kept:
            return (f"lost the direction of {_say(f.value)}: the answer says it went {f.way}, "
                    "the layout never shows that")
    return None


def _rows_kept(answer: str, rendition: str, shown: str, root: Any) -> str | None:
    """Every row of the answer's tables is shown whole: one row, bar, tile or record of the layout
    holds every cell of it. A cell is never matched inside another word ("No" in "November")."""
    if root is None:
        seen_rows = [r for head, rows, _ in _tables(shown) for r in (head, *rows)]
        all_rows = [r for head, rows, _ in _tables(rendition) for r in (head, *rows)]
    else:
        seen_rows, all_rows = _records(root, []), _records(root, [], shown=False)
    kept = [Counter(k for k in map(_key, r) if k) for r in seen_rows]
    seen = {k for c in kept for k in c}
    anywhere = {k for r in all_rows for k in map(_key, r) if k}
    flat = _squash(shown)
    for head, rows, _above in _tables(answer):
        lost = [c for c in head if _key(c) and _squash(c) not in flat]
        if lost:
            return "dropped table headings " + ", ".join(lost[:4])
        for row in rows:
            need = Counter(k for k in map(_key, row) if k)
            if not need or any(all(have[k] >= n for k, n in need.items()) for have in kept):
                continue
            line = " | ".join(c for c in row if _key(c))
            gone = [c for c in row if _key(c) and _key(c) not in seen]
            if gone and all(_key(c) in anywhere for c in gone):
                return "table cells only behind a click (a closed section or a button) " + ", ".join(gone[:4])
            if gone:
                return "dropped table cells " + ", ".join(gone[:4]) + " (row: " + line + ")"
            return "moved table cells: the row " + line + " is not shown together"
    return None


def _pairs_bound(answer: str, root: Any) -> str | None:
    """Every bar, point and table row of the layout pairs a label with a figure the answer gives that
    label: in one of its table rows, in one clause, or in one sentence that names no other item."""
    sentences = _sentences(_prose(answer))

    def read_(part: str) -> tuple[str, set[str], set[str]]:
        return _squash(part), _words(part), figures(part)
    # Read once: a long table checks every one of its cells against every part.
    places = [read_(" | ".join(r)) for _h, rs, _a in _tables(answer) for r in rs]
    places += [read_(c) for s in sentences for c in _CLAUSE.split(s)]
    whole = [read_(s) for s in sentences]
    text = openui_view.cell_text

    def bound(key: str, value: str, group: list[str]) -> bool:
        figs, k, kw = figures(value), _squash(key), _words(key)
        if not figs or not k:
            return True

        def holds(part: tuple[str, set[str], set[str]]) -> bool:
            return (k in part[0] or bool(kw) and kw <= part[1]) and figs <= part[2]
        if any(holds(part) for part in places):
            return True
        rivals = [_squash(o) for o in group if _squash(o) and _squash(o) not in k]
        return any(holds(s) and not any(r in s[0] for r in rivals) for s in whole)

    for node in _walk(root):
        p = node.props
        pairs: list[tuple[str, str, list[str]]] = []
        if node.type == "BarChart":
            labels, values = [text(x) for x in p.get("labels") or []], p.get("values") or []
            pairs = [(lb, text(values[i]), labels) for i, lb in enumerate(labels) if i < len(values)]
        elif node.type == "LineChart":
            labels = [text(x) for x in p.get("labels") or []]
            for s in _kids(p.get("series")):
                vals = s.props.get("values") or []
                pairs += [(lb, text(vals[i]), labels) for i, lb in enumerate(labels) if i < len(vals)]
        elif node.type == "Table":
            table = [[text(c) for c in r] for r in p.get("rows") or [] if isinstance(r, list) and r]
            keys = [r[0] for r in table]
            pairs = [(r[0], c, keys) for r in table for c in r[1:]]
        for key, value, group in pairs:
            if not bound(key, value, group):
                what = "chart" if node.type.endswith("Chart") else "table"
                return f"{what} pairs {key} with {value}, which the answer does not"
    return None


def _labels_fit(answer: str, root: Any) -> str | None:
    """A tile or field's figure is the one the answer gives its label. Its label's words must be said
    with that figure; when they are not, but are all said with another figure, the figure moved."""
    contexts, vocab = _contexts(answer), _stems(answer)
    for node in _walk(root):
        if node.type not in ("Metric", "Field"):
            continue
        label = str(node.props.get("label") or "")
        want = _stems(label) & vocab
        if not want:
            continue
        # The value, and a change in the note ("-0.73 points versus August"): the note's other figures
        # are often of something else ("of the 14 work orders").
        note = [f for f in _figs(str(node.props.get("note") or "")) if f.way]
        figs = _figs(openui_view.cell_text(node.props.get("value")))[:1] + note
        for f in figs:
            if any(want <= c for c in contexts.get(f.value, [])):
                continue
            fits = sorted(v for v, cs in contexts.items() if v != f.value and any(want <= c for c in cs))
            if fits:
                return f"labels {f.raw} '{label}', which the answer says of {_say(fits[0])}"
    return None


# ── a finding's facts stay with its issue ──

# A record or asset code: letters and figures joined by hyphens, with a capital and a figure in it
# (FRA-B-301-2025, CHILLER-101, Asset-4471, SP203-1). A date has no capital, so it is never one.
_CODE = re.compile(r"(?<![\w-])(?=[\w-]*[A-Z])(?=[\w-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![\w-])")
# Where a new block starts inside a paragraph: a list item or a heading on its own line.
_ITEM_START = re.compile(r"\n(?=[ \t]*(?:[-*+]|\d+[.)]|#{1,6})[ \t])")
_BINDING_CLAUSE = re.compile(_CLAUSE.pattern + r"|\s+\|\s+", re.IGNORECASE)


def _flat(text: str) -> str:
    """Text as compared for a finding's title or code: emphasis and code marks gone, spacing single,
    a possessive read as its name ("Northgate Electrical's" is Northgate Electrical, as names() reads
    it), lower-cased; word boundaries kept, so "Lift 4" is not found in "Lift 41"."""
    s = re.sub(r"(?<=\w)'s\b", "", re.sub(r"\*\*|__|[*`]", "", text or "").replace("’", "'"))
    return re.sub(r"\s+", " ", s).strip().lower()


def _mentions(flat: str, anchor: str) -> bool:
    return bool(anchor) and re.search(r"(?<![a-z0-9])" + re.escape(anchor) + r"(?![a-z0-9])", flat) is not None


class _Facts(NamedTuple):
    flat: str
    codes: set[str]
    dates: set[str]
    figures: set[str]  # outside its codes and dates: "condition score 2" is not the 2 of 2026-12-02


def _facts(text: str) -> _Facts:
    codes = {c.upper() for c in _CODE.findall(text or "")}
    rest = _CODE.sub(" ", text or "")
    return _Facts(_flat(text), codes, dates(rest), figures(_DATE.sub(" ", rest)))


def _blocks(answer: str) -> list[list[str]]:
    """The answer's blocks, each as its sentences: a paragraph, an item of a list, a table row."""
    out: list[list[str]] = []
    for para in re.split(r"\n\s*\n", _prose(answer)):
        out.extend(_sentences(part) for part in _ITEM_START.split(para) if part.strip())
    out.extend([" | ".join(row)] for _head, rows, _above in _tables(answer) for row in rows)
    return [b for b in out if b]


def _places(blocks: list[list[str]], names_it: Callable[[str], bool],
            rival: Callable[[str], bool]) -> list[_Facts]:
    """Where the answer speaks of one issue: a block that names it and no other issue; inside a block
    that names others too, the run of sentences from one that names only it up to the next that names
    another; and a clause that names it."""
    out: list[str] = []
    for block in blocks:
        flats = [_flat(s) for s in block]
        if not any(map(names_it, flats)):
            continue
        if not any(map(rival, flats)):
            out.append(" ".join(block))
            continue
        run: list[str] = []
        for sentence, flat in zip(block, flats):
            here, other = names_it(flat), rival(flat)
            if other:
                if run:
                    out.append(" ".join(run))
                run = []
                if here:
                    out.extend(c for c in _BINDING_CLAUSE.split(sentence) if c and names_it(_flat(c)))
            elif here or run:
                run.append(sentence)
        if run:
            out.append(" ".join(run))
    return [_facts(p) for p in out]


def _findings_bound(answer: str, root: Any) -> str | None:
    """A Finding is one issue the answer lists. Its title and code are the answer's own, its code is
    the one the answer gives that issue, and every figure, date and code in its text is one the answer
    says in the same paragraph (or, where a paragraph speaks of several issues, the same sentences) as
    its title or code (28 Oct 2026: a date or a code moved to another asset's finding would pass the
    set checks, as a figure under another vendor's label once did)."""
    findings = [n for n in _walk(root) if n.type == "Finding"]
    if not findings:
        return None
    whole, blocks = _flat(answer), _blocks(answer)

    def anchors(node: openui_view.Node) -> list[str]:
        title = _flat(openui_view.cell_text(node.props.get("title"))).strip(" .,:;—–-")
        code = _flat(openui_view.cell_text(node.props.get("code")))
        return [a for a in (title, code) if a]

    for node in findings:
        raw_title = openui_view.cell_text(node.props.get("title")).strip()
        raw_code = openui_view.cell_text(node.props.get("code")).strip()
        title = _flat(raw_title).strip(" .,:;—–-")
        code = _flat(raw_code)
        if not title:
            return "a finding has no title"
        # The title may leave out the code the answer wrote inside it: "Fire Risk Assessment — Lapsed"
        # for "Fire Risk Assessment (FRA-B-301-2025) — Lapsed", with the code as the finding's code.
        uncoded = re.compile(r"\s*\(\s*" + re.escape(code) + r"\s*\)") if code else None

        def titled(flat: str, title: str = title, uncoded: re.Pattern[str] | None = uncoded) -> bool:
            return _mentions(flat, title) or (uncoded is not None and _mentions(uncoded.sub("", flat), title))
        if not titled(whole):
            return f"a finding's title is not in the answer: '{raw_title}'"
        if code and not _mentions(whole, code):
            return f"a finding's code is not in the answer: {raw_code}"
        mine = anchors(node)
        others = {a for other in findings if other is not node for a in anchors(other)
                  if not any(a in m or m in a for m in mine)}

        def rival(flat: str, others: set[str] = others) -> bool:
            return any(_mentions(flat, a) for a in others)
        if code and not any(_mentions(p.flat, code) for p in _places(blocks, titled, rival)):
            return f"finding '{raw_title}' has the code {raw_code}, which the answer does not give it"

        def named(flat: str, titled: Callable[[str], bool] = titled, code: str = code) -> bool:
            return titled(flat) or bool(code) and _mentions(flat, code)
        places = _places(blocks, named, rival)
        said = _facts(openui_view.cell_text(node.props.get("text")))
        for kind, items, held in (("code", said.codes, lambda p: p.codes), ("date", said.dates, lambda p: p.dates),
                                  ("figure", said.figures, lambda p: p.figures)):
            for item in sorted(items):
                if not any(item in held(p) for p in places):
                    what = _say(item) if kind == "figure" else item
                    return f"finding '{raw_title}' gives the {kind} {what}, which the answer does not say of it"
    return None


def _levels_kept(answer: str, root: Any) -> str | None:
    """A figure the answer gives only as a change is never a tile's bare value under a label that
    reads as the level itself (27 Oct 2026: "dropped 2.04 points" drawn as an overall score of 2.04)."""
    said: dict[str, list[_Fig]] = {}
    for f in _figs(answer):
        said.setdefault(f.value, []).append(f)
    for node in _walk(root):
        if node.type not in ("Metric", "Field"):
            continue
        label, value = str(node.props.get("label") or ""), openui_view.cell_text(node.props.get("value"))
        figs = _figs(value)
        if not figs or figs[0].way or _CHANGE.search(value) or _CHANGE.search(label):
            continue
        seen = said.get(figs[0].value, [])
        if seen and all(f.way for f in seen):
            return (f"drew the change {figs[0].raw} as a level ('{label}: {value}'): "
                    "the answer gives it only as a change")
    return None


def _counted(pattern: re.Pattern[str], what: str, body: str, shown: str) -> str | None:
    want = Counter(m.group(0).lower() for m in pattern.finditer(body))
    have = Counter(m.group(0).lower() for m in pattern.finditer(shown))
    if sum(have.values()) < sum(want.values()):
        lost = ", ".join(sorted((want - have).elements())[:4])
        return f"dropped a {what} ({lost}): the answer has {sum(want.values())}, the layout shows {sum(have.values())}"
    return None


# An answer's own title ("# Weekly Summary — Portfolio"): a label over the answer, not a fact in
# it. The model often lets the headline say it instead, so its words are not held to the layout;
# a figure in it still is.
_TITLE = re.compile(r"\A\s*#{1,2}[ \t]+[^\n]*\n")


def faithful(answer: str, rendition: str, shown: str | None = None, root: Any = None) -> str | None:
    """None when the layout says what the answer says; else the reason it does not.

    `rendition` is everything the layout draws (render_markdown), `shown` what the reader sees
    without a click (render_markdown(shown=True)) and `root` the layout's tree. The answer's facts
    are held to `shown`; nothing the layout adds, even in a chip, may be missing from the answer."""
    shown = rendition if shown is None else shown
    body = _TITLE.sub("", answer, count=1)
    checks = [
        lambda: _figures_kept(answer, rendition, shown),
        lambda: _marks_kept(answer, rendition, shown),
        lambda: _rows_kept(answer, rendition, shown, root),
        lambda: _pairs_bound(answer, root) if root is not None else None,
        lambda: _labels_fit(answer, root) if root is not None else None,
        lambda: _findings_bound(answer, root) if root is not None else None,
        lambda: _levels_kept(answer, root) if root is not None else None,
        lambda: _counted(_NEGATION, "negation", body, shown),
        lambda: _counted(_CAVEAT, "caveat", body, shown),
    ]
    for check in checks:
        why = check()
        if why:
            return why
    have = _words(shown)
    lost = names(body) - have
    if lost:
        return "dropped names " + ", ".join(sorted(lost)[:6])
    for para in re.split(r"\n\s*\n", _prose(body)):
        want = _plain(para, 4)
        if len(want) >= 3 and len(want & have) / len(want) < PASSAGE_COVERAGE:
            words = " ".join(_WORD.findall(para)[:10])
            return f"dropped a passage ({', '.join(sorted(want - have)[:6])} gone): {words}…"
    for clause in (c for sentence in _sentences(_prose(body)) for c in _CLAUSE.split(sentence)):
        want = _plain(clause, 5)
        if len(want) >= 3 and len(want & have) / len(want) < CLAUSE_COVERAGE:
            return f"dropped a clause ({', '.join(sorted(want - have)[:6])} gone): {clause.strip()[:80]}…"
    plain = _plain(body, 5)
    if plain and len(plain & have) / len(plain) < WORD_COVERAGE:
        return "dropped words " + ", ".join(sorted(plain - have)[:6])
    return None


# ── the call ──────────────────────────────────────────────────────────────────────────────────

def read(output: str, answer: str) -> tuple[dict[str, Any] | None, str]:
    """(the view's payload, or None; why not). The model's output is checked whole: it parses to an
    Answer, the chat drops none of it, and what the reader sees says what the answer says."""
    _lead, program, found = openui_view.split_program(output or "")
    if not found or not program:
        return None, "no program"
    if len(program) > openui_view.MAX_PROGRAM_CHARS:
        return None, "too long"
    # A `+0.45` the model wrote is sent as the text "+0.45": OpenUI would move every cell after it.
    program = openui_view.quote_signed_numbers(program)
    try:
        result = openui_view.parse_full(program)
        rendition = openui_view.render_markdown(result.root)
        shown = openui_view.render_markdown(result.root, shown=True)
    except Exception as exc:  # noqa: BLE001 - a layout must never cost the answer
        return None, "unreadable: " + str(exc)[:80]
    if result.root is None or not rendition.strip():
        return None, "draws nothing"
    if result.dropped or result.unresolved or result.orphans:
        lost = result.dropped + [f"unresolved {n}" for n in result.unresolved] + [f"unused {n}" for n in result.orphans]
        return None, "the chat would drop part of it: " + "; ".join(lost[:4])[:160]
    why = faithful(answer, rendition, shown, result.root)
    if why:
        return None, why
    return {"v": 1, "lang": program}, ""


async def present(question: str, answer: str, llm: Llm) -> dict[str, Any] | None:
    """The `openui_view` payload for this answer, or None. Never raises."""
    try:
        output = await asyncio.wait_for(
            llm(system_prompt(), user_prompt(question, answer), "layout"), timeout=TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - timeouts, provider errors: the Markdown stands
        log.info("answer_presenter.unavailable", error=str(exc)[:200] or type(exc).__name__)
        return None
    view, why = read(output if isinstance(output, str) else "", answer)
    if view is None:
        log.info("answer_presenter.rejected", reason=why)
    return view
