"""Golden sets: a company's list of questions with known right answers (Trace Lab M3).

A company admin uploads an .xlsx or .csv. parse_upload reads it without writing anything to disk and returns
every case, error and warning with the sheet's own row numbers. .xlsx is read with zipfile and
xml.etree.ElementTree (no spreadsheet library): every part is size-capped, a DOCTYPE or ENTITY refuses the
file, formulas are read from their saved values only, and date serials become ISO dates. save_version is the
only write: each upload is a new version of the company's set, and the previous one is superseded.

Spec: docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md, "Golden set, safe replay, golden runs and
grading", section 1.
"""
from __future__ import annotations

# The imports below are all standard library. Later sections of this module (reading .xlsx/.csv, parse_upload,
# case identity, the question templates) are appended to the end of this file and use the ones this section
# does not.
import csv
import functools
import hashlib
import io
import json
import logging
import math
import os
import posixpath
import re
import unicodedata
import uuid
import zipfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_BYTES = int(os.getenv("TRACE_LAB_GOLDEN_MAX_BYTES") or 5_000_000)
MAX_ROWS = int(os.getenv("TRACE_LAB_GOLDEN_MAX_ROWS") or 2000)
MAX_ZIP_ENTRIES = 500
MAX_DECLARED_TOTAL = 50 * 1024 * 1024
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_RATIO = 200
MAX_COLUMNS = 40
MAX_FOLLOWUPS = 5
MAX_VALUES = 40
MAX_REQUIREMENTS = 20
HEADER_SCAN_ROWS = 10

#: canonical column -> normalised header aliases (lower case, letters and digits only; "#" kept alone).
COLUMNS: dict[str, frozenset[str]] = {
    "id": frozenset({"id", "caseid", "case", "ref", "#"}),
    "question": frozenset({"question", "ask", "prompt", "query", "q"}),
    "followups": frozenset({"followups", "followup", "followupquestions"}),
    "expected_answer": frozenset({"expectedanswer", "expected", "answer", "rightanswer", "referenceanswer",
                                  "correctanswer", "criterion", "expectedoutcome"}),
    "evaluation": frozenset({"evaluation", "eval", "evaluationtype", "checktype", "type"}),
    "expected_values": frozenset({"expectedvalues", "values", "keyvalues", "keyfacts", "mustcontain"}),
    "source": frozenset({"evidence"}),               # plus any header that starts with "source"
    "verified_on": frozenset({"verifiedon", "verified", "asof", "checkedon", "verifieddate"}),
    "page_context": frozenset({"pagecontext", "page", "context"}),
    "ask_as": frozenset({"askas", "scope", "askedas"}),
    "requirement_ids": frozenset({"requirementids", "requirements", "reqids"}),
    "notes": frozenset({"notes", "note", "comments", "comment"}),
}
CANON_LABEL: dict[str, str] = {
    "id": "ID", "question": "Question", "followups": "Follow-ups", "expected_answer": "Expected answer",
    "evaluation": "Evaluation", "expected_values": "Expected values", "source": "Source", "verified_on": "Verified on",
    "page_context": "Page/context", "ask_as": "Ask as", "requirement_ids": "Requirement IDs", "notes": "Notes",
}
CURRENCY_SYMBOL: dict[str, str] = {"GBP": "£", "USD": "$", "EUR": "€", "AED": "AED "}


class UploadRefused(ValueError):
    """The whole file is refused before any row is read. reason: bad_file | file_too_large | too_many_rows."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class CaseRow:
    row_no: int
    case_key: str
    key_source: str
    case_version: str
    question: str
    followups: tuple[str, ...]
    expected_answer: str
    evaluation: str
    expected_values: tuple[dict, ...]
    source: str | None
    verified_on: date | None
    page_context: str | None
    ask_as: str
    requirement_ids: tuple[str, ...]
    notes: str | None
    flags: tuple[str, ...]


@dataclass
class ParseResult:
    cases: list[CaseRow]
    errors: list[dict]
    warnings: list[dict]
    sheet_name: str | None
    header_row: int
    source_kind: str


# ── dates ─────────────────────────────────────────────────────────────────────────────

_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
           "september": 9, "october": 10, "november": 11, "december": 12, "jan": 1, "feb": 2, "mar": 3,
           "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
_MONTH_RE = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + ")"
_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r"\.?,?\s+(\d{4})(?!\d)", re.I),
    re.compile(r"\b" + _MONTH_RE + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?,\s*(\d{4})(?!\d)", re.I),
    re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)"),
)


def _mk_date(y: Any, m: Any, d: Any) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def dates_in(text_: str) -> list[tuple[date, int, int]]:
    """Every date written in the text as ISO, d Month yyyy, Month d, yyyy or dd/mm/yyyy (day first)."""
    out: list[tuple[date, int, int]] = []
    for i, rx in enumerate(_DATE_PATTERNS):
        for m in rx.finditer(text_ or ""):
            g = m.groups()
            if i == 0:
                d = _mk_date(g[0], g[1], g[2])
            elif i == 1:
                d = _mk_date(g[2], _MONTHS[g[1].lower()], g[0])
            elif i == 2:
                d = _mk_date(g[2], _MONTHS[g[0].lower()], g[1])
            else:
                d = _mk_date(g[2], g[1], g[0])
            if d is not None:
                out.append((d, m.start(), m.end()))
    return out


def parse_date_text(s: str) -> date | None:
    """A date when the whole text is one date (a trailing ISO time is dropped); None otherwise."""
    t = (s or "").strip()
    iso_time = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?", t)
    if iso_time:
        t = iso_time.group(1)
    for d, start, end in dates_in(t):
        if start == 0 and end == len(t):
            return d
    return None


# ── Expected values (spec §1.3) ───────────────────────────────────────────────────────

_FORCED_KINDS = ("number", "date", "name", "code")
_CURRENCY = {"£": "GBP", "$": "USD", "€": "EUR", "AED": "AED"}
_LABEL = re.compile(r"^(?P<label>[^:!\"]{1,40}?):\s+(?P<rest>.+)$", re.S)
_FORCED = re.compile(r"^(?P<kind>number|date|name|code):\s*(?P<rest>.+)$", re.I | re.S)
_TOL = re.compile(r"\s*(?:±|\+/-)\s*(\d+(?:\.\d+)?)\s*(%)?\s*$")
_VALUE_NUM = re.compile(
    r"^(?P<cur>£|\$|€|AED\s?)?\s*(?P<sign>-)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"\s*(?P<mult>[kKmM](?![A-Za-z]))?\s*(?P<pct>%|per\s?cent|percent)?\s*(?P<unit>[^\d\s].{0,39})?$")


def _jnum(d: Any) -> int | float | None:
    """A Decimal as JSON: an int when whole, else a float."""
    if d is None:
        return None
    d = Decimal(d)
    return int(d) if d == d.to_integral_value() else float(d)


def split_items(cell: str) -> list[str]:
    """Items separated by semicolons or new lines; a separator inside double quotes is text."""
    items, buf, quoted = [], [], False
    for ch in cell or "":
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif not quoted and ch in ";\r\n":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [i.strip() for i in items if i.strip()]


def _parse_number_value(v: str) -> dict | None:
    m = _VALUE_NUM.match(v.strip())
    if not m:
        return None
    cur = (m.group("cur") or "").strip()
    unit = (m.group("unit") or "").strip() or None
    mult = m.group("mult")
    if mult and not cur:          # k and m multiply only after a currency; otherwise they start a unit (m²)
        unit = (mult + (unit or "")).strip()
        mult = None
    dec = m.group("dec") or ""
    value = Decimal(m.group("int").replace(",", "") + dec)
    if m.group("sign"):
        value = -value
    places = len(dec) - 1 if dec else 0
    factor = Decimal(1000) if mult and mult.lower() == "k" else Decimal(1_000_000) if mult else Decimal(1)
    value *= factor
    kind = "percent" if m.group("pct") else ("money" if cur else "number")
    tol_abs = Decimal(5) * (Decimal(10) ** -(places + 1)) * factor if places else Decimal(0)
    return {"kind": kind, "value": _jnum(value), "unit": unit if kind == "number" else None,
            "currency": _CURRENCY.get(cur) if cur else None, "tol_abs": _jnum(tol_abs)}


def _parse_item(raw: str) -> dict:
    rest, label = raw.strip(), None
    m = _LABEL.match(rest)
    if m and re.search(r"[A-Za-z]", m.group("label")) and m.group("label").strip().lower() not in _FORCED_KINDS:
        label, rest = m.group("label").strip(), m.group("rest").strip()
    negate = rest.startswith("!")
    if negate:
        rest = rest[1:].strip()
    forced = None
    fm = _FORCED.match(rest)
    if fm:
        forced, rest = fm.group("kind").lower(), fm.group("rest").strip()
    tol_abs = tol_pct = None
    tm = _TOL.search(rest)
    if tm:
        if tm.group(2):
            tol_pct = Decimal(tm.group(1))
        else:
            tol_abs = Decimal(tm.group(1))
        rest = rest[:tm.start()].strip()
    approx = rest[:1] in ("~", "≈")
    if approx:
        rest = rest[1:].strip()
    if not rest:
        raise ValueError(raw)
    item: dict[str, Any] = {"label": label, "kind": None, "value": None, "unit": None, "currency": None,
                            "tol_abs": 0, "tol_pct": None, "negate": negate, "raw": raw}
    quoted = len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"'
    if forced in ("name", "code") or (forced is None and quoted):
        txt = rest[1:-1].strip() if quoted else rest
        if not txt:
            raise ValueError(raw)
        item.update(kind=forced or "name", value=txt)
    elif forced is None and rest.lower() in ("yes", "no"):
        item.update(kind="yesno", value=rest.lower())
    else:
        d = parse_date_text(rest) if forced in (None, "date") else None
        if d is not None:
            item.update(kind="date", value=d.isoformat())
        elif forced == "date":
            raise ValueError(raw)
        else:
            num = _parse_number_value(rest)
            if num is not None:
                item.update(kind=num["kind"], value=num["value"], unit=num["unit"], currency=num["currency"],
                            tol_abs=num["tol_abs"])
                if approx:
                    item.update(tol_abs=0, tol_pct=2)
            elif forced == "number":
                raise ValueError(raw)
            else:
                item.update(kind="name", value=rest)
    numeric = item["kind"] in ("number", "percent", "money")
    if (tol_abs is not None or tol_pct is not None or approx) and not numeric:
        raise ValueError(raw)
    if tol_abs is not None or tol_pct is not None:
        item["tol_abs"] = _jnum(tol_abs) if tol_abs is not None else 0
        item["tol_pct"] = _jnum(tol_pct)
    return item


def parse_expected_values(cell: str) -> list[dict]:
    """The Expected values cell as stored items; ValueError naming the first item that cannot be read."""
    items = split_items(cell or "")
    if len(items) > MAX_VALUES:
        raise ValueError(f"more than {MAX_VALUES} items")
    return [_parse_item(raw) for raw in items]


# ── reading the file (spec §1.4, §1.5) ────────────────────────────────────────────────

_OLE2 = b"\xd0\xcf\x11\xe0"
_TYPE_MESSAGE = "Upload the list as .xlsx or .csv. An older .xls can be saved as .xlsx from Excel."
_PASSWORD_MESSAGE = "This file is password-protected or an old .xls: save it as .xlsx without a password."
_BOMB_MESSAGE = "This workbook looks like a zip bomb (one part unpacks to over 200 times its size)."
_DATE_FORMATS = frozenset({14, 15, 16, 17, 22})
_PERCENT_FORMATS = frozenset({9, 10})
_REF = re.compile(r"^([A-Z]{1,3})(\d+)$")
_MAX_NUMBER_CHARS = 64
_MAX_EXPONENT = 310
_EXCEL_DIGITS = 15                          # significant digits Excel shows; a double is stored with 17
_UNREADABLE_MESSAGE = "This workbook has content that is not valid. Open it in Excel and save it again."


class _NoSavedValue:
    """A formula cell whose cached value is missing: the writing tool never calculated it."""

    def __repr__(self) -> str:
        return "<no saved value>"


NO_SAVED_VALUE = _NoSavedValue()


def col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def _bad(message: str) -> UploadRefused:
    return UploadRefused("bad_file", message)


def refuse_dtd(buf: bytes) -> None:
    """A DOCTYPE or ENTITY declaration anywhere in a part refuses the file, checked before parsing."""
    if buf[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            low = buf.decode("utf-16").lower()
        except UnicodeDecodeError as exc:
            raise _bad("This workbook has a part that cannot be read.") from exc
    else:
        low = buf.decode("utf-8", "replace")
    # The XML parser also reads UTF-16 without a byte order mark; its NUL padding hides the ASCII letters of a
    # DOCTYPE from a UTF-8 search, so the NULs are dropped before looking.
    low = low.replace("\x00", "").lower()
    if "<!doctype" in low or "<!entity" in low:
        raise _bad("This workbook contains XML declarations that are not allowed (DOCTYPE or ENTITY).")


class _Package:
    """The zip container, opened with every cap checked before anything is parsed."""

    def __init__(self, data: bytes) -> None:
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, NotImplementedError, RuntimeError) as exc:
            # NotImplementedError: a central directory entry asks for a zip version above 6.3 (one damaged byte does it).
            raise _bad("This file is not a readable .xlsx workbook. Open it in Excel and save it as .xlsx again.") from exc
        if len(infos) > MAX_ZIP_ENTRIES:
            raise _bad("This workbook has too many parts to read safely.")
        if sum(i.file_size for i in infos) > MAX_DECLARED_TOTAL:
            raise _bad("This workbook is too large once unpacked.")
        self.infos = {i.filename: i for i in infos}

    def has(self, name: str) -> bool:
        return name in self.infos

    def read(self, name: str) -> bytes:
        info = self.infos[name]
        if info.flag_bits & 0x1:
            raise _bad(_PASSWORD_MESSAGE)
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise _bad(_BOMB_MESSAGE)
        try:
            with self.zip.open(info) as f:
                buf = f.read(MAX_ENTRY_BYTES + 1)
        except (zipfile.BadZipFile, EOFError, zlib.error, OSError, NotImplementedError, RuntimeError) as exc:
            raise _bad("This workbook is damaged. Open it in Excel and save it again.") from exc
        if len(buf) > MAX_ENTRY_BYTES:
            raise _bad("One part of this workbook is too large to read safely.")
        if info.compress_size and len(buf) / info.compress_size > MAX_RATIO:
            raise _bad(_BOMB_MESSAGE)
        refuse_dtd(buf)
        return buf

    def xml(self, name: str) -> ET.Element:
        buf = self.read(name)
        try:
            return ET.fromstring(buf)
        except (ET.ParseError, LookupError) as exc:     # LookupError: the part declares an encoding Python has no codec for
            raise _bad("This workbook has a part that is not valid XML. Open it in Excel and save it again.") from exc


@dataclass(frozen=True)
class _SheetRef:
    name: str
    hidden: bool
    part: str


def _workbook(pkg: _Package) -> tuple[list[_SheetRef], bool]:
    if not pkg.has("[Content_Types].xml") or not pkg.has("xl/workbook.xml"):
        raise _bad("This file is not an Excel workbook. " + _TYPE_MESSAGE)
    if b"macroenabled" in pkg.read("[Content_Types].xml").lower():
        raise _bad(_TYPE_MESSAGE)
    parts: dict[str, str] = {}
    if pkg.has("xl/_rels/workbook.xml.rels"):
        for rel in pkg.xml("xl/_rels/workbook.xml.rels").iter():
            if _local(rel.tag) == "Relationship":
                target = (rel.get("Target") or "").replace("\\", "/")
                target = target.lstrip("/") if target.startswith("/") else "xl/" + target
                parts[rel.get("Id") or ""] = posixpath.normpath(target)
    date1904, sheets = False, []
    for el in pkg.xml("xl/workbook.xml").iter():
        tag = _local(el.tag)
        if tag == "workbookPr":
            date1904 = (el.get("date1904") or "").strip().lower() in ("1", "true")
        elif tag == "sheet":
            sheets.append(_SheetRef(name=el.get("name") or "", hidden=(el.get("state") or "visible") != "visible",
                                    part=parts.get(_attr(el, "id") or "", "")))
    return sheets, date1904


def _text_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    tag = _local(el.tag)
    if tag == "rPh":
        return ""
    if tag == "t":
        return el.text or ""
    return "".join(_text_of(c) for c in el)


def _shared_strings(pkg: _Package) -> list[str]:
    if not pkg.has("xl/sharedStrings.xml"):
        return []
    return [_text_of(si) for si in pkg.xml("xl/sharedStrings.xml") if _local(si.tag) == "si"]


def _fmt_kind(fid: int, code: str | None) -> str:
    if fid in _DATE_FORMATS:
        return "date"
    if fid in _PERCENT_FORMATS:
        return "percent"
    if code:
        bare = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)
        if "%" in bare:
            return "percent"
        if re.search(r"[dmy]", bare, re.I):
            return "date"
    return ""


def _styles(pkg: _Package) -> list[str]:
    """Per cell-style index: 'date', 'percent' or ''."""
    if not pkg.has("xl/styles.xml"):
        return []
    root = pkg.xml("xl/styles.xml")
    custom = {int(el.get("numFmtId") or -1): el.get("formatCode") or ""
              for el in root.iter() if _local(el.tag) == "numFmt"}
    for el in root.iter():
        if _local(el.tag) == "cellXfs":
            return [_fmt_kind(int(xf.get("numFmtId") or 0), custom.get(int(xf.get("numFmtId") or 0)))
                    for xf in el if _local(xf.tag) == "xf"]
    return []


def _plain(d: Decimal) -> str:
    return str(int(d)) if d == d.to_integral_value() else format(d.normalize(), "f")


def _as_excel_shows(d: Decimal) -> Decimal | None:
    """d rounded to the 15 significant digits Excel displays, through the double the file really stores.
    None when no double holds the value (it overflows to infinity or underflows to zero)."""
    f = float(d)
    if math.isinf(f) or (f == 0 and d != 0):
        return None
    return Decimal(format(f, f".{_EXCEL_DIGITS}g"))


def _number_text(v: str, kind: str, date1904: bool) -> str:
    text = v.strip()
    if len(text) > _MAX_NUMBER_CHARS:       # no spreadsheet writes a number this long; keep it as text
        return text
    try:
        d = Decimal(text)
    except InvalidOperation:
        return text
    # NaN, Infinity and exponents past a double's range are kept as text: expanding 1E+999999999 to digits
    # would hang the reader on a file of a few hundred bytes.
    if not d.is_finite() or (d and abs(d.adjusted()) > _MAX_EXPONENT):
        return text
    # A writer may store a typed 2.3 as 2.2999999999999998, and a cached =0.1+0.2 is 0.30000000000000004; Excel
    # shows both with 15 significant digits ("2.3", "0.3") and so must we, or a value check built from the
    # decimal places written would fail the right answer. Up to 15 digits is returned exactly as stored.
    if len(d.as_tuple().digits) > _EXCEL_DIGITS:
        d = _as_excel_shows(d)
        if d is None:
            return text
    if kind == "date":
        base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
        try:
            return (base + timedelta(days=int(d.to_integral_value(rounding=ROUND_FLOOR)))).isoformat()
        except OverflowError:               # a serial outside the years 1 to 9999 is not a date
            return text
    if kind == "percent":
        return _plain(d * 100) + "%"
    return _plain(d)


def _cell_value(c: ET.Element, strings: list[str], styles: list[str], date1904: bool) -> tuple[Any, str | None]:
    t = c.get("t") or "n"
    s_idx = int(c.get("s") or 0)
    kind = styles[s_idx] if 0 <= s_idx < len(styles) else ""
    v_el = f_el = is_el = None
    for ch in c:
        tag = _local(ch.tag)
        if tag == "v":
            v_el = ch
        elif tag == "f":
            f_el = ch
        elif tag == "is":
            is_el = ch
    if t == "inlineStr":
        return _text_of(is_el), None
    v = v_el.text if v_el is not None and v_el.text is not None else None
    if v is None:
        return (NO_SAVED_VALUE, None) if f_el is not None else ("", None)
    if t == "s":
        try:
            i = int(v)
        except ValueError:
            return "", None
        return (strings[i] if 0 <= i < len(strings) else ""), None
    if t == "b":
        return ("TRUE" if v.strip() == "1" else "FALSE"), None
    if t == "e":
        return "", v.strip()
    if t in ("str", "d"):
        return v, None
    return _number_text(v, kind, date1904), None


def _sheet_rows(buf: bytes, strings: list[str], styles: list[str],
                date1904: bool) -> tuple[list[tuple[int, dict[int, Any]]], list[dict]]:
    try:
        root = ET.fromstring(buf)
    except (ET.ParseError, LookupError) as exc:         # LookupError: an encoding Python has no codec for
        raise _bad("This workbook has a sheet that is not valid XML. Open it in Excel and save it again.") from exc
    rows: list[tuple[int, dict[int, Any]]] = []
    warnings: list[dict] = []
    wide = False
    for row_el in root.iter():
        if _local(row_el.tag) != "row":
            continue
        r = int(row_el.get("r") or (rows[-1][0] + 1 if rows else 1))
        cells: dict[int, Any] = {}
        next_col = 0
        for c in row_el:
            if _local(c.tag) != "c":
                continue
            m = _REF.match(c.get("r") or "")
            col = _col_index(m.group(1)) if m else next_col
            next_col = col + 1
            if col >= MAX_COLUMNS:
                wide = True
                continue
            value, err_code = _cell_value(c, strings, styles, date1904)
            if err_code:
                warnings.append({"row": r, "column": col_letter(col),
                                 "message": f"the cell holds an error ({err_code}) and is read as empty"})
            cells[col] = value
        rows.append((r, cells))
    if wide:
        warnings.append({"row": None, "column": None, "message": "Only the first 40 columns are read; the rest are ignored."})
    return rows, warnings


def _choose_sheets(sheets: list[_SheetRef], sheet: str | None) -> list[_SheetRef]:
    if sheet:
        want = sheet.strip().casefold()
        for s in sheets:
            if s.name.strip().casefold() == want:
                return [s]
        raise _bad(f'There is no sheet named "{sheet}" in this workbook.')
    visible = [s for s in sheets if not s.hidden]
    named = [s for s in visible if s.name.strip().casefold() == "golden set"]
    return named + [s for s in visible if s not in named]


def _csv_rows(data: bytes) -> tuple[list[tuple[int, dict[int, Any]]], list[dict]]:
    if data.startswith(b"\xef\xbb\xbf"):
        body = data[3:].decode("utf-8", "replace")
    else:
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            body = data.decode("cp1252", "replace")
    try:
        delim = csv.Sniffer().sniff(body[:8192], delimiters=",;\t").delimiter
    except csv.Error:
        delim = ","
    csv.field_size_limit(100_000)
    rows: list[tuple[int, dict[int, Any]]] = []
    warnings: list[dict] = []
    wide = False
    try:
        for i, rec in enumerate(csv.reader(io.StringIO(body, newline=""), delimiter=delim), start=1):
            if len(rec) > MAX_COLUMNS:
                wide = True
            rows.append((i, {j: cell for j, cell in enumerate(rec[:MAX_COLUMNS])}))
    except csv.Error as exc:
        raise _bad(f"The CSV could not be read: {exc}") from exc
    if wide:
        warnings.append({"row": None, "column": None, "message": "Only the first 40 columns are read; the rest are ignored."})
    return rows, warnings


def source_kind_for(filename: str, data: bytes) -> str:
    """'xlsx' or 'csv' from the file name, after the size and OLE2 checks; UploadRefused otherwise."""
    if len(data) > MAX_BYTES:
        raise UploadRefused("file_too_large", f"The file is larger than {MAX_BYTES:,} bytes.")
    ext = Path(filename or "").suffix.lower()
    if ext not in (".xlsx", ".csv"):
        raise _bad(_TYPE_MESSAGE)
    if data[:4] == _OLE2:
        raise _bad(_PASSWORD_MESSAGE)
    return ext[1:]


def read_tables(filename: str, data: bytes, *,
                sheet: str | None = None) -> tuple[str, list[tuple[str | None, list[tuple[int, dict[int, Any]]], list[dict]]]]:
    """The file's candidate tables in preference order: (source kind, [(sheet name, rows, read warnings)])."""
    kind = source_kind_for(filename, data)
    if kind == "csv":
        rows, warnings = _csv_rows(data)
        return kind, [(None, rows, warnings)]
    try:
        return kind, _xlsx_tables(data, sheet)
    except UploadRefused:
        raise
    except (ValueError, ArithmeticError, RecursionError) as exc:
        # A crafted attribute, encoding or nesting depth must surface as a refusal, never as a bare exception.
        raise _bad(_UNREADABLE_MESSAGE) from exc


def _xlsx_tables(data: bytes, sheet: str | None) -> list[tuple[str | None, list[tuple[int, dict[int, Any]]], list[dict]]]:
    pkg = _Package(data)
    sheets, date1904 = _workbook(pkg)
    strings, styles = _shared_strings(pkg), _styles(pkg)
    out = []
    for ref in _choose_sheets(sheets, sheet):
        if not ref.part or not pkg.has(ref.part):
            raise _bad(f'The sheet "{ref.name}" is missing from this workbook.')
        rows, warnings = _sheet_rows(pkg.read(ref.part), strings, styles, date1904)
        out.append((ref.name, rows, warnings))
    return out


# ── header, rows and identity (spec §1.1, §1.2, §1.6, §1.7) ───────────────────────────

_MAX_REQ_ID = 40           # characters in one requirement ID
_MAX_TEXT = 2000           # characters in the Question and in one follow-up
_MAX_DATE_TEXT = 100       # characters in Verified on: a date is 10 to 30 of them, a time after it a few more
_SEPARATOR_ALLOWANCE = 8   # the separator and the padding a cell may have beside each of its items
_NUMBERED_FOLLOWUP = re.compile(r"^followup([1-5])$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REQ_RE = re.compile(rf"^[A-Za-z0-9._-]{{1,{_MAX_REQ_ID}}}$")
_OBJECTIVE = frozenset({"objective", "exact", "fact"})
_JUDGMENT = frozenset({"judgment", "judgement", "human"})
_NO_VALUE_MESSAGE = "the formula has no saved value. Open the file in Excel and save it again."
_MAX_VALUE_ITEM = 200      # characters in one Expected values item (a name, a number with its unit and tolerance)
_MAX_SHOWN = 40            # characters of an admin's own cell text that a message may quote back
# The longest column header that is read. The longest alias is 20 characters and a "Source ..." header may carry a
# note; no column is called anything near this. The header search normalises every cell of up to 10 rows x 40
# columns, and one shared string can fill all 400 of them (4 minutes for the 19 MB string the reader accepts).
_MAX_HEADER = 200
# The most a list cell can hold: its full items and the room each leaves for a separator and padding. A cell past
# this is refused on its length alone, before anything cuts it up. An .xlsx shared string can be the cell of every
# row (up to MAX_ROWS), so work that grows with the cell is paid once per row: a 4 MB string cost 0.3 s a row.
_MAX_VALUES_CELL = MAX_VALUES * (_MAX_VALUE_ITEM + _SEPARATOR_ALLOWANCE)
_MAX_REQUIREMENTS_CELL = MAX_REQUIREMENTS * (_MAX_REQ_ID + _SEPARATOR_ALLOWANCE)
_MAX_FOLLOWUPS_CELL = MAX_FOLLOWUPS * (_MAX_TEXT + _SEPARATOR_ALLOWANCE)


def _shown(text: str) -> str:
    """A cell's text for a message: a 20 MB cell is never echoed whole into an error response."""
    return text if len(text) <= _MAX_SHOWN else text[:_MAX_SHOWN] + "…"


def _fold_if_short(text: str, longest: int) -> str | None:
    """text.casefold() when text has at most `longest` characters, else None.

    For a column that takes one of a few short words. Full case folding maps every code point to one, two or three
    code points and never to none, so a text longer than the longest word cannot fold to a word: it is turned away on
    its length, and the cell is never copied. str.casefold costs time in proportion to the cell (about 17 times more
    per character once it holds one non-ASCII character), and one shared string can be the cell of every row."""
    return text.casefold() if len(text) <= longest else None


_LONGEST_EVALUATION = max(map(len, _OBJECTIVE | _JUDGMENT))     # "objective" and "judgement": 9
_LONGEST_ASK_AS = len("company")
_NO_CONTEXT_WORD = "none"


def _hnorm(h: Any) -> str:
    s = unicodedata.normalize("NFKC", str(h or "")).strip().casefold()
    return "#" if s == "#" else re.sub(r"[^0-9a-z]", "", s)


def _canonical(header: Any) -> tuple[str | None, int | None]:
    h = _hnorm(header)
    if not h:
        return None, None
    m = _NUMBERED_FOLLOWUP.match(h)
    if m:
        return "followups_n", int(m.group(1))
    for canon, aliases in COLUMNS.items():
        if h in aliases:
            return canon, None
    if h.startswith("source"):
        return "source", None
    return None, None


def _filled(v: Any) -> bool:
    return v is NO_SAVED_VALUE or bool(str(v if v is not None else "").strip())


def _map_header(cells: dict[int, Any]) -> tuple[dict[int, tuple[str, int | None]], list[str]]:
    mapping: dict[int, tuple[str, int | None]] = {}
    unknown: list[str] = []
    seen: set[tuple[str, int | None]] = set()
    for col in sorted(cells):
        raw = cells[col]
        if raw is NO_SAVED_VALUE:
            continue
        text = str(raw)
        if len(text) > _MAX_HEADER:         # no column is called this: not normalised, not copied, only named briefly
            unknown.append(text[:_MAX_SHOWN + 1].strip() or "(a very long header)")
            continue
        text = text.strip()
        if not text:
            continue
        canon, n = _canonical(text)
        if canon is None:
            unknown.append(text)
        elif (canon, n) in seen:
            unknown.append(text + " (repeated; the first one is used)")
        else:
            seen.add((canon, n))
            mapping[col] = (canon, n)
    return mapping, unknown


def _find_header(rows: list[tuple[int, dict[int, Any]]]):
    for r, cells in rows:
        if r > HEADER_SCAN_ROWS:
            break
        mapping, unknown = _map_header(cells)
        canons = {c for c, _ in mapping.values()}
        if "question" in canons and "expected_answer" in canons:
            return r, mapping, unknown
    return None


def norm_text(s: str | None) -> str:
    """NFKC, casefold, collapse whitespace, strip trailing ?.! and spaces."""
    if not s:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(s)).casefold().split()).rstrip("?.! ")


def canonical_json(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _ctx_token(page_context: str | None) -> str:
    if page_context is None:
        return ""
    return "none" if page_context == "" else norm_text(page_context)


def case_key_for(question: str, page_context: str | None, *, ask_as: str = "company",
                 id_cell: str | None = None) -> tuple[str, str]:
    """(case_key, key_source): the ID column, else sha1 of the normalised question and context, 12 hex."""
    if id_cell:
        return id_cell, "id_column"
    base = norm_text(question) + "|" + _ctx_token(page_context)
    if ask_as != "company":
        base += "|" + ask_as
    return hashlib.sha1(base.encode()).hexdigest()[:12], "hash"


def _version(question: str, followups: tuple[str, ...], expected_answer: str, expected_values: tuple[dict, ...],
             evaluation: str, page_context: str | None, ask_as: str) -> str:
    parts = [norm_text(question), *[norm_text(f) for f in followups], "\x1e", " ".join(expected_answer.split()),
             canonical_json(list(expected_values)), evaluation, _ctx_token(page_context), ask_as]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]


def case_version_for(row: CaseRow) -> str:
    return _version(row.question, row.followups, row.expected_answer, row.expected_values, row.evaluation,
                    row.page_context, row.ask_as)


def _txt(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _parse_row(row_no: int, vals: dict[str, Any], *, today: date) -> tuple[CaseRow | None, list[dict], list[dict]]:
    errs: list[dict] = []
    warns: list[dict] = []
    flags: list[str] = []

    def err(col: str, msg: str) -> None:
        errs.append({"row": row_no, "column": col, "message": msg})

    def warn(col: str | None, msg: str) -> None:
        warns.append({"row": row_no, "column": col, "message": msg})

    numbered: dict[int, Any] = vals.get("followups_n") or {}
    for key, v in vals.items():
        if key != "followups_n" and v is NO_SAVED_VALUE:
            err(CANON_LABEL[key], _NO_VALUE_MESSAGE)
    for n, v in numbered.items():
        if v is NO_SAVED_VALUE:
            err(f"Follow-up {n}", _NO_VALUE_MESSAGE)
    if errs:
        return None, errs, warns
    s = {k: _txt(v) for k, v in vals.items() if k != "followups_n"}
    nums = {n: _txt(v) for n, v in numbered.items()}
    if not any(s.values()) and not any(nums.values()):
        return None, [], []
    if not any(v for k, v in s.items() if k != "notes") and not any(nums.values()):
        warn("Notes", "Only Notes are filled in, so the row was skipped.")
        return None, [], warns

    question, expected = s.get("question", ""), s.get("expected_answer", "")
    if not question:
        err("Question", "Question is missing.")
    elif len(question) > _MAX_TEXT:
        err("Question", "Question is longer than 2,000 characters.")
    if not expected:
        err("Expected answer", "Expected answer is missing.")
    elif len(expected) > 6000:
        err("Expected answer", "Expected answer is longer than 6,000 characters.")

    ev_raw = s.get("evaluation", "")
    ev = _fold_if_short(ev_raw, _LONGEST_EVALUATION)       # None for a cell too long to be a word: never folded
    evaluation = "objective"
    if not ev_raw:
        flags.append("evaluation_defaulted")
        warn("Evaluation", "Evaluation is blank, so the case is checked as objective.")
    elif ev in _JUDGMENT:
        evaluation = "judgment"
    elif ev not in _OBJECTIVE:
        err("Evaluation", "Evaluation must be objective or judgment.")

    followups: list[str] = []
    if s.get("followups"):
        if len(s["followups"]) > _MAX_FOLLOWUPS_CELL:      # refused on its length: the cell is never cut into lines
            err("Follow-ups", f"Follow-ups are too long. Use at most {MAX_FOLLOWUPS} follow-ups of 2,000 "
                              "characters each, one per line.")
        else:
            followups = [line.strip() for line in re.split(r"\r\n|\r|\n", s["followups"]) if line.strip()]
    else:
        followups = [nums[n] for n in sorted(nums) if nums[n]]
    if len(followups) > MAX_FOLLOWUPS:
        err("Follow-ups", "At most 5 follow-ups.")
    if any(len(f) > _MAX_TEXT for f in followups):
        err("Follow-ups", "A follow-up is longer than 2,000 characters.")

    for col, texts in (("Question", [question]), ("Follow-ups", followups)):
        left = left_placeholders(*(t for t in texts if len(t) <= _MAX_TEXT))   # past the limit the row already fails
        if left:
            shown = ", ".join(left[:5]) + (f" and {len(left) - 5} more" if len(left) > 5 else "")
            err(col, f"{col} still has the placeholder {shown}. Replace it with a real name from your own data.")

    values: list[dict] = []
    if s.get("expected_values"):
        if len(s["expected_values"]) > _MAX_VALUES_CELL:   # refused on its length, judgment or not: never cut up
            err("Expected values", f"Expected values are too long. Use at most {MAX_VALUES} values of "
                                   f"{_MAX_VALUE_ITEM} characters each.")
        elif evaluation == "judgment":
            warn("Expected values", "Expected values are ignored on judgment cases.")
        else:
            items = split_items(s["expected_values"])
            if len(items) > MAX_VALUES:
                err("Expected values", "At most 40 expected values.")
            elif any(len(i) > _MAX_VALUE_ITEM for i in items):
                # Bounds the work and the size of what is stored: a 4,300-digit number cannot be written as JSON.
                err("Expected values", f"An expected value is longer than {_MAX_VALUE_ITEM} characters.")
            else:
                # One space per run of whitespace before the value grammar (Task 3) reads it: its number pattern
                # takes cubic time on a number, a long run of spaces, then a digit. Items are re-joined by the
                # same separator split_items cut them on, so they split back into the same items.
                cell = "\n".join(" ".join(i.split()) for i in items)
                try:
                    values = parse_expected_values(cell)
                except ValueError as exc:
                    err("Expected values", f'cannot read "{exc}". See the column rules in the template.')
                except KeyError:
                    # dates_in matches month names case-insensitively, so a long s or a dotless i matches "sep"
                    # or "april" and then has no entry in its month table.
                    err("Expected values", "cannot read a date in this cell. Write the month in plain letters, "
                                           "for example 13 October 2026.")

    source = s.get("source") or None
    if source and len(source) > 500:
        err("Source", "Source is longer than 500 characters.")

    verified: date | None = None
    if s.get("verified_on"):
        try:
            # The date patterns read the whole text, so a long one is not read: it is no date.
            verified = parse_date_text(s["verified_on"]) if len(s["verified_on"]) <= _MAX_DATE_TEXT else None
        except KeyError:            # a look-alike month letter (see Expected values above): not a date
            verified = None
        if verified is None:
            err("Verified on", "Verified on is not a date (use 2026-10-13, 13 October 2026 or 13/10/2026).")
        elif verified > today:
            err("Verified on", "Verified on cannot be in the future.")
    else:
        flags.append("unverified")

    ctx_raw = s.get("page_context", "")
    page_context: str | None = None
    if ctx_raw:
        if _fold_if_short(ctx_raw, len(_NO_CONTEXT_WORD)) == _NO_CONTEXT_WORD:
            page_context = ""
        elif len(ctx_raw) > 4000:
            err("Page/context", "Page/context is longer than 4,000 characters.")
        else:
            page_context = ctx_raw

    if _fold_if_short(s.get("ask_as", ""), _LONGEST_ASK_AS) not in ("", "company"):
        err("Ask as", "v1 runs company-wide questions only. Name the building in the question instead.")
    ask_as = "company"

    reqs: list[str] = []
    if len(s.get("requirement_ids", "")) > _MAX_REQUIREMENTS_CELL:     # refused on its length: never tokenised
        err("Requirement IDs", f"At most {MAX_REQUIREMENTS} requirement IDs of {_MAX_REQ_ID} characters each.")
    else:
        reqs = [t.strip() for t in re.split(r"[,;]", s.get("requirement_ids", "")) if t.strip()]
        if len(reqs) > MAX_REQUIREMENTS:
            err("Requirement IDs", "At most 20 requirement IDs.")
        for t in reqs[:MAX_REQUIREMENTS]:   # past the cap the row already fails; one error per token would be unbounded
            if not _REQ_RE.match(t):
                err("Requirement IDs",
                    f'"{_shown(t)}" is not a requirement ID (letters, digits, dots, dashes, underscores).')

    notes = s.get("notes") or None
    if notes and len(notes) > 2000:
        err("Notes", "Notes are longer than 2,000 characters.")

    id_cell = s.get("id") or None
    if id_cell and not _ID_RE.match(id_cell):
        err("ID", "ID must be 1-64 letters, digits, dots, dashes or underscores, starting with a letter or digit.")

    # Reused, never edited: the app's own write-intent check, imported here (not at the top of the module) so
    # that importing trace_lab loads no application module (tests/test_trace_lab_golden_rule.py pins that).
    from src.agents.skill_lab import WRITE_INTENT
    # Only text inside its limit is scanned: past the limit the row already fails, and the scan reads every character.
    if question and any(WRITE_INTENT.search(t) for t in (question, *followups) if len(t) <= _MAX_TEXT):
        flags.append("write_intent")
        warn("Question", "This reads like a request to change something. A test run refuses any change, "
                         "so the answer may come back degraded.")
    if errs:
        return None, errs, warns
    key, key_source = case_key_for(question, page_context, ask_as=ask_as, id_cell=id_cell)
    fu, ev_items = tuple(followups), tuple(values)
    return CaseRow(row_no=row_no, case_key=key, key_source=key_source,
                   case_version=_version(question, fu, expected, ev_items, evaluation, page_context, ask_as),
                   question=question, followups=fu, expected_answer=expected, evaluation=evaluation,
                   expected_values=ev_items, source=source, verified_on=verified, page_context=page_context,
                   ask_as=ask_as, requirement_ids=tuple(reqs), notes=notes, flags=tuple(flags)), [], warns


def _row_values(cells: dict[int, Any], mapping: dict[int, tuple[str, int | None]]) -> dict[str, Any]:
    out: dict[str, Any] = {"followups_n": {}}
    for col, (canon, n) in mapping.items():
        v = cells.get(col, "")
        if canon == "followups_n":
            out["followups_n"][n] = v
        else:
            out[canon] = v
    return out


def parse_upload(filename: str, data: bytes, *, sheet: str | None = None, today: date | None = None) -> ParseResult:
    """Read the whole file: cases, errors (row, column, message) and warnings. Nothing is saved here."""
    kind, tables = read_tables(filename, data, sheet=sheet)
    today = today or datetime.now(timezone.utc).date()
    for name, rows, read_warnings in tables:
        found = _find_header(rows)
        if found is None:
            continue
        header_row, mapping, unknown = found
        errors: list[dict] = []
        warnings = list(read_warnings)
        for u in unknown:
            warnings.append({"row": header_row, "column": _shown(u),
                             "message": f'The column "{_shown(u)}" is not used and was ignored.'})
        canons = {c for c, _ in mapping.values()}
        if "followups" in canons and "followups_n" in canons:
            errors.append({"row": header_row, "column": "Follow-ups",
                           "message": "Use one Follow-ups column (one per line) or Follow-up 1 … Follow-up 5, not both."})
        data_rows = [(r, cells) for r, cells in rows if r > header_row]
        filled = [1 for _, cells in data_rows if any(_filled(v) for col, v in cells.items() if col in mapping)]
        if len(filled) > MAX_ROWS:
            raise UploadRefused("too_many_rows", f"This file has {len(filled):,} cases; a set holds at most "
                                                 f"{MAX_ROWS:,}. Split it into smaller sets.")
        cases: list[CaseRow] = []
        seen: dict[str, int] = {}
        for r, cells in data_rows:
            case, errs, warns = _parse_row(r, _row_values(cells, mapping), today=today)
            errors.extend(errs)
            warnings.extend(warns)
            if case is None:
                continue
            if case.case_key in seen:
                first = seen[case.case_key]
                if case.key_source == "id_column":
                    errors.append({"row": r, "column": "ID", "message": f"The ID {case.case_key} is already used on row {first}."})
                else:
                    errors.append({"row": r, "column": "Question",
                                   "message": f"This question and page context repeat row {first}."})
                continue
            seen[case.case_key] = r
            cases.append(case)
        if not cases and not errors:
            errors.append({"row": None, "column": None, "message": "The sheet has a header row but no cases under it."})
        return ParseResult(cases=cases, errors=errors, warnings=warnings, sheet_name=name, header_row=header_row,
                           source_kind=kind)
    return ParseResult(cases=[], errors=[{"row": None, "column": None,
                                          "message": "No header row: one of the first 10 rows needs a Question "
                                                     "column and an Expected answer column."}],
                       warnings=[], sheet_name=tables[0][0] if tables else None, header_row=0, source_kind=kind)


# ── question templates, stale rule, set hash and diff (spec §1.2, §1.7, §1.8, §1.10) ──────────────────────

TEMPLATE_HEADER: tuple[str, ...] = ("ID", "Question", "Follow-ups", "Expected answer", "Evaluation",
                                    "Expected values", "Source", "Verified on", "Page/context", "Ask as",
                                    "Requirement IDs", "Notes")
#: Per-row facts about the compliance question bank, frozen from it (tests/compliance_bank_generator.py makes the
#: file; a drift test reads the bank and fails when it changes). The export never reads the bank itself.
TEMPLATES_PATH = Path(__file__).resolve().parent / "data" / "compliance_bank_templates.json"
#: A placeholder left in a question: the admin has not yet put in a real name.
PLACEHOLDER = re.compile(r"\{[a-z_0-9]+\}")
TEMPLATE_CONTEXT = "The user is on the Hoistra compliance console. No scope filter is applied."
_TIER_LABEL = {1: "Pack question", 2: "Register question", 3: "Entity question"}
#: core.md section 5's answer form for each shape, in plain words (no document or tool names).
_SHAPE_FORM = {
    "one fact": "one or two sentences, one section", "which one": "name it first, name any ties",
    "particular": "groups for those owners and the rows", "particular rows": "groups for those owners and the rows",
    "law": "two parts, Building then Vendor, with regulation and interval, no held or missing wording",
    "lifecycle": "one section per scope asked, count in the heading, rows sorted by expiry",
    "authenticity": "forged and suspect under separate headings, unassessed counted apart",
    "blocked": "a reason per vendor from its own row", "renewals": "instructions only, no work orders",
    "coverage": "owners plus every missing type", "gaps": "owners plus every missing type",
    "risk": "ranked by consequence, naming the order used", "cross-domain": "your half from the register, join keys out",
    "portfolio": "full treatment: figures, groups, actions", "count": "vendors with their counts",
    "trade": "rows of that trade in each scope, with counts", "evidence": "the pack or passport and what it shows",
    "pending": "items waiting on a decision, as decisions", "two parts": "two sections, never merged",
}


def left_placeholders(*texts: str) -> list[str]:
    """The distinct {placeholders} in these texts, in order of first appearance."""
    seen: dict[str, None] = {}
    for t in texts:
        for m in PLACEHOLDER.findall(t or ""):
            seen.setdefault(m)
    return list(seen)


def csv_safe(cell: str) -> str:
    """A cell Hoist writes never starts like a formula Excel would run."""
    s = "" if cell is None else str(cell)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _csv_text(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows:
        w.writerow([csv_safe(c) for c in row])
    return buf.getvalue()


def template_csv() -> str:
    """The upload template: the header row only (house rule: no sample data)."""
    return _csv_text([list(TEMPLATE_HEADER)])


@functools.lru_cache(maxsize=1)
def _bank_templates() -> tuple[dict, ...]:
    return tuple(json.loads(TEMPLATES_PATH.read_text(encoding="utf-8")))


def _shape_forms(shape: str) -> str:
    toks = [t.strip() for t in re.split(r"[+,]", re.sub(r"\(.*?\)", "", shape).replace("×3", "")) if t.strip()]
    forms = [_SHAPE_FORM[t] for t in toks if t in _SHAPE_FORM and t != "two parts" or t == "two parts" and len(toks) == 1]
    return "; ".join(dict.fromkeys(forms)) or "as the question asks"


def _fill_text(row: dict) -> str:
    if row["tier"] == 1:
        text_ = ("Same answer for every company on the UK pack; change UK if your buildings use another pack. "
                 "Write the expected answer.")
    elif row["tier"] == 3:
        names = row["placeholders"]
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        what = "a real name" if len(names) == 1 else "real names"
        text_ = f"Replace {listed} with {what} from your own data, then write the expected answer."
    else:
        text_ = "Write the expected answer from your own register."
    if row.get("fill_hint"):
        text_ += " " + row["fill_hint"]
    if row["time_window"]:
        text_ += " Time window: the answer changes with the date, so fill Verified on close to the run."
    return text_


def _notes(row: dict) -> str:
    parts = [_TIER_LABEL[row["tier"]], f"Shape: {row['shape']}, {_shape_forms(row['shape'])}", _fill_text(row)]
    if row["trap"]:
        parts.append(f"Trap: {row['trap_note']}")
    return " · ".join(parts)


def compliance_bank_template_csv() -> str:
    """The compliance question bank as question templates in the golden format: 161 rows, questions only.
    Expected answer, values, Source and Verified on are blank (each company's own data supplies them)."""
    rows: list[list[str]] = [list(TEMPLATE_HEADER)]
    for t in _bank_templates():
        rows.append([f"CQB-{t['id']}", t["question"], "", "", t["evaluation"], "", "", "", TEMPLATE_CONTEXT, "",
                     ", ".join(t["requirement_ids"]), _notes(t)])
    return _csv_text(rows)


def stale_days() -> int:
    return int(os.getenv("TRACE_LAB_GOLDEN_STALE_DAYS") or 30)


def is_stale(verified_on: date | None, *, on: date | None = None, days: int | None = None) -> bool:
    """Verified on is set and older than the stale window at `on` (default today, UTC)."""
    if verified_on is None:
        return False
    on = on or datetime.now(timezone.utc).date()
    return verified_on < on - timedelta(days=stale_days() if days is None else days)


def set_hash(keys_and_versions: list[tuple[str, str]]) -> str:
    return hashlib.sha256("\n".join(sorted(f"{k}:{v}" for k, v in keys_and_versions)).encode()).hexdigest()[:12]


def version_diff(previous: list[tuple[str, str, str]], cases: list[CaseRow], *, previous_version: int | None) -> dict:
    """previous: (case_key, case_version, key_source) of the active set being replaced."""
    prev = {k: v for k, v, _ in previous}
    new = {c.case_key: c.case_version for c in cases}
    prev_ids = any(s == "id_column" for _, _, s in previous)
    new_ids = any(c.key_source == "id_column" for c in cases)
    return {"previous_version": previous_version,
            "added": sorted(k for k in new if k not in prev),
            "removed": sorted(k for k in prev if k not in new),
            "changed": sorted(k for k in new if k in prev and prev[k] != new[k]),
            "unchanged": sum(1 for k in new if prev.get(k) == new[k]),
            "keys_restarted": bool(previous) and prev_ids != new_ids}
