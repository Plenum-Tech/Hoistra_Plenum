"""Grading golden answers (Trace Lab M3): the free deterministic value check, the LLM grader, the outcome rules
and grader validation. The value check and the grader never see each other's result, so they stay independent;
the grader never sees Notes, reviewer notes, human labels or earlier verdicts.
Spec: docs/superpowers/specs/2026-10-15-hoist-trace-lab-design.md, Golden set section §4.
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any

from . import golden

OUTCOMES = ("pass", "fail", "unsure", "needs_person", "degraded", "stale_reference", "infra_failed",
            "grading_pending", "context_unavailable")

# ── the deterministic value check (spec §4.2) ─────────────────────────────────────────

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_TENS_RE = re.compile(r"\b(" + "|".join(_TENS) + r")(?:[-\s](one|two|three|four|five|six|seven|eight|nine))?\b", re.I)
_UNITS_RE = re.compile(r"\b(" + "|".join(sorted(_UNITS, key=len, reverse=True)) + r")\b", re.I)
_SKIP_KEYS = frozenset({"id", "icon", "action", "url", "href"})
_TOKEN = re.compile(r"(?<![\w.])(?P<sign>-)?(?P<int>\d{1,3}(?:[,   ]\d{3})+|\d+)(?P<dec>\.\d+)?"
                    r"(?:\s?(?P<k>[kK])\b)?(?P<pct>\s?%|\s?per\s?cent\b|\s?percent\b)?")
_CODE_SPLIT = re.compile(r"[\s,;:()\[\]{}\"']+")


def words_to_digits(text_: str) -> str:
    """Number words zero to ninety-nine become digits ("twenty-two" -> "22")."""
    out = _TENS_RE.sub(lambda m: str(_TENS[m.group(1).lower()] + (_UNITS[m.group(2).lower()] if m.group(2) else 0)),
                       text_ or "")
    return _UNITS_RE.sub(lambda m: str(_UNITS[m.group(1).lower()]), out)


def flatten_cards(cards: list[dict]) -> str:
    """Every string and number leaf of the card payloads, skipping ids, icons, actions and links."""
    out: list[str] = []

    def walk(v: Any, key: str | None) -> None:
        if key is not None and (key in _SKIP_KEYS or key.endswith("_id")):
            return
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, None)
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            out.append(str(v))
        elif isinstance(v, str) and v.strip():
            out.append(v)

    for c in cards or []:
        walk(c, None)
    return "\n".join(out)


def _numbers(hay: str) -> list[tuple[Decimal, bool, str, int, int]]:
    out = []
    for m in _TOKEN.finditer(hay):
        digits = re.sub(r"[,   ]", "", m.group("int")) + (m.group("dec") or "")
        try:
            v = Decimal(digits)
        except InvalidOperation:
            continue
        if m.group("sign"):
            v = -v
        if m.group("k"):
            v *= 1000
        out.append((v, bool(m.group("pct")), m.group(0).strip(), m.start(), m.end()))
    return out


def _norm_words(s: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", unicodedata.normalize("NFKC", s or "").casefold()).split())


def _code_norm(s: str) -> str:
    return re.sub(r"[-_/.]", "", s).casefold()


def _snippet(hay: str, start: int, end: int) -> str:
    return "…" + " ".join(hay[max(0, start - 40):end + 40].split()) + "…"


def _first_sentence_says(answer: str) -> str | None:
    first = re.split(r"(?<=[.!?])\s+|\n", (answer or "").strip(), maxsplit=1)[0][:200]
    yes = re.search(r"\byes\b", first, re.I)
    no = re.search(r"\b(no|not)\b", first, re.I)
    if yes and not no:
        return "yes"
    if no and not yes:
        return "no"
    return None


def check_values(items: list[dict], answer: str, cards: list[dict]) -> dict:
    """The det block: each expected value looked for in the final answer and the flattened cards."""
    if not items:
        return {"status": "none", "items": []}
    hay = words_to_digits((answer or "") + "\n" + flatten_cards(cards or []))
    numbers = _numbers(hay)
    dates = golden.dates_in(hay)
    words_hay = " " + _norm_words(hay) + " "
    codes = {_code_norm(t) for t in _CODE_SPLIT.split(hay) if t}
    out: list[dict] = []
    for it in items:
        kind, negate = it.get("kind"), bool(it.get("negate"))
        entry: dict[str, Any] = {"raw": it.get("raw"), "kind": kind, "ok": None}
        found, evidence, nearest = False, None, None
        if kind in ("number", "money", "percent"):
            target = Decimal(str(it.get("value")))
            tol = max(Decimal(str(it.get("tol_abs") or 0)), abs(target) * Decimal(str(it.get("tol_pct") or 0)) / 100)
            cands = [n for n in numbers if n[1]] if kind == "percent" else numbers
            hit = next((n for n in cands if abs(n[0] - target) <= tol), None)
            found = hit is not None
            if hit:
                evidence = _snippet(hay, hit[3], hit[4])
            elif cands:
                nearest = min(cands, key=lambda n: abs(n[0] - target))[2]
        elif kind == "date":
            hit = next((d for d in dates if d[0].isoformat() == it.get("value")), None)
            found = hit is not None
            evidence = _snippet(hay, hit[1], hit[2]) if hit else None
        elif kind == "name":
            # the expected value goes through the same number-word conversion as the haystack
            value = words_to_digits(str(it.get("value") or ""))
            needle = _norm_words(value)
            found = bool(needle) and (" " + needle + " ") in words_hay
            i = hay.lower().find(value.lower())
            evidence = _snippet(hay, i, i + len(value)) if found and i >= 0 else None
        elif kind == "code":
            found = _code_norm(words_to_digits(str(it.get("value") or ""))) in codes
        elif kind == "yesno":
            said = _first_sentence_says(answer)
            if said is None:
                out.append(entry)
                continue
            found = said == it.get("value")
        entry["ok"] = (not found) if negate else found
        if evidence and found:
            entry["evidence"] = evidence
        if nearest and not found:
            entry["nearest"] = nearest
        out.append(entry)
    if any(e["ok"] is False for e in out):
        status = "mismatch"
    elif any(e["ok"] is None for e in out):
        status = "unclear"
    else:
        status = "all_found"
    return {"status": status, "items": out}


# ── combining into an outcome (spec §4.5-§4.7, R28) ───────────────────────────────────

_OBJECTIVE_TABLE = {
    ("mismatch", "fail"): "fail", ("mismatch", "unsure"): "fail", ("mismatch", "pass"): "unsure",
    ("all_found", "pass"): "pass", ("all_found", "fail"): "fail", ("all_found", "unsure"): "unsure",
    ("unclear", "unsure"): "unsure", ("none", "unsure"): "unsure",
    ("unclear", "pass"): "pass", ("unclear", "fail"): "fail", ("none", "pass"): "pass", ("none", "fail"): "fail",
}


def graded_verdict(evaluation: str, det: dict | None, grade: dict | None) -> str:
    verdict = (grade or {}).get("verdict")
    if evaluation == "judgment":
        return verdict
    return _OBJECTIVE_TABLE[((det or {}).get("status") or "none", verdict)]


def combine(*, evaluation: str, det: dict | None, grade: dict | None, refused: bool, gate: bool, chat_error: bool,
            stale: bool, judgment_validated: bool, objective_demoted: bool = False, context_lost: bool = False) -> str:
    if context_lost:
        return "context_unavailable"              # R28: never pass or fail
    if chat_error:
        return "fail"
    if refused or gate:
        return "degraded"
    if not grade or grade.get("verdict") not in ("pass", "fail", "unsure"):
        return "grading_pending"
    verdict = graded_verdict(evaluation, det, grade)
    if verdict == "unsure":
        return "unsure"
    if evaluation == "judgment" and not judgment_validated:
        return "needs_person"
    if evaluation == "objective" and objective_demoted:
        return "needs_person"
    if stale and verdict == "fail":
        return "stale_reference"
    return verdict


def spot_rate() -> float:
    return float(os.getenv("TRACE_LAB_GOLDEN_SPOTCHECK") or 0.10)


def is_spot_check(run_id: str, case_key: str, rep: int, rate: float | None = None) -> bool:
    r = spot_rate() if rate is None else rate
    return int(hashlib.sha256(f"spot:{run_id}:{case_key}:{rep}".encode()).hexdigest()[:8], 16) / 2 ** 32 < r
