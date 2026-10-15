"""The summary that ends every Investigate: three datasets in, one checked paragraph out.

The Assets page's Investigate walks six sources and shows one line per source. Three of them —
bms_trend, utility_bill, weather — have a dataset behind the line (ops-intelligence's
bms-trend, utility-bill and degree-days endpoints). The frontend fetches those and sends them
here, and one model pass says what they add up to.

It may only say what the datasets say. Each line's figures are grounded against them with the
helpers contract_answer already uses (rounding tolerated, and a sum or difference of two
supplied figures): a line that cites a figure the data does not hold is dropped and named, and
the overall sentence is reported rather than rewritten — silently editing prose hides the
mistake. Nothing is read from a database and nothing is written.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import structlog

from . import contract_answer as ca

log = structlog.get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"
SOURCES = ("bms_trend", "utility_bill", "weather")
#: Characters of data sent to the model. Eight weeks of daily buckets fits well inside this;
#: past it the bulky arrays go first and the model is told the data was cut.
BUDGET_CHARS = 24_000

SYSTEM = """You summarise three datasets about one building asset for a facilities manager.

Return ONLY a JSON object, no prose around it:
{"lines": [{"source": "bms_trend", "text": "..."},
           {"source": "utility_bill", "text": "..."},
           {"source": "weather", "text": "..."}],
 "overall": "..."}

Rules — every one of them is checked:
- Use only figures that appear in the data, as they appear. Do not compute new figures — no
  differences, sums, averages or estimates — and never bring in a figure from outside the data.
- A dataset with status "not_found" has nothing on record: say it is not on record and give
  its detail in plain words. One with status "unreadable" could not be read: say it could not
  be read. Never describe an empty or unreadable source as normal, fine or flat.
- bms_trend: name each reading outside its band with its value, unit and band, and how many
  days it was out of band. If none are, say all graded readings are within their bands.
- utility_bill is METERED consumption standing in for the bill: say "metered", never "billed".
  If total.comparable is false, say there is no comparison with last year and why (days on
  record, when metered history starts) — and do not describe a rise or fall in consumption
  anywhere in the summary, the weather line and the overall sentence included. Last year's
  figure is withheld in that case because it is not a comparison.
- weather: for heating plant (boilers, heat pumps, calorifiers) lead with heating degree days;
  for cooling plant (chillers, cooling towers) with cooling degree days. Say whether the
  weather could explain a change in consumption. Name the location it was read for.
- One or two sentences per line, one sentence for "overall", about 120 words in all.
- Say what the data shows. Do not recommend actions; the investigation proposes those.
"""


def _withhold(sources: dict[str, Any]) -> dict[str, Any]:
    """Last year's figures removed wherever the dataset says they are not a comparison.

    Found on the first live run, 28 Sep 2026: with Bishopsgate's bill marked not comparable
    (one day of last year's window on record), the model still wrote "metered consumption has
    risen enormously" from that one day's 8,383 kWh. A rule in the prompt asked it not to; the
    figure being absent means it cannot, and grounding against the withheld data drops any line
    that quotes it anyway.
    """
    out = json.loads(json.dumps(sources or {}, default=str))
    bill = out.get("utility_bill")
    if isinstance(bill, dict):
        def strip(block: dict[str, Any], why: str) -> None:
            if isinstance(block, dict) and block.get("comparable") is False:
                block.pop("last_year_kwh", None)
                block.pop("change_pct", None)
                for w in block.get("weeks") or []:
                    if isinstance(w, dict):
                        w.pop("last_year_kwh", None)
                if isinstance(block.get("cost_gbp"), dict):
                    block["cost_gbp"].pop("last_year", None)
                block["last_year_withheld"] = why
        t = bill.get("total")
        if isinstance(t, dict):
            strip(t, f"not a comparison — {t.get('last_year_days')} of {t.get('now_days')} days of "
                     "last year's window are on record")
        for fuel in (bill.get("fuels") or {}).values():
            if isinstance(fuel, dict):
                strip(fuel, f"not a comparison — {fuel.get('last_year_days')} of "
                            f"{fuel.get('now_days')} days on record")
    wx = out.get("weather")
    if isinstance(wx, dict) and isinstance(wx.get("total"), dict) and wx["total"].get("comparable") is False:
        for k in ("last_year_hdd", "last_year_cdd", "hdd_change_pct", "cdd_change_pct"):
            wx["total"].pop(k, None)
        # The months carry last year too, and a month-by-month comparison is the same
        # comparison the total refused.
        for m in wx.get("months") or []:
            if isinstance(m, dict):
                m.pop("last_year_hdd", None)
                m.pop("last_year_cdd", None)
        wx["total"]["last_year_withheld"] = "not a comparison — last year's dates are not all on record"
    return out


def _compact(sources: dict[str, Any]) -> dict[str, Any]:
    """The same datasets with their per-day and per-week arrays removed — totals, bands,
    latest readings and out-of-band lists stay, which is what a summary is written from."""
    out: dict[str, Any] = {}
    for k, v in (sources or {}).items():
        if not isinstance(v, dict):
            out[k] = v
            continue
        v = dict(v)
        if isinstance(v.get("types"), list):
            v["types"] = [{kk: vv for kk, vv in t.items() if kk != "days"}
                          for t in v["types"] if isinstance(t, dict)]
        if isinstance(v.get("fuels"), dict):
            v["fuels"] = {f: {kk: vv for kk, vv in d.items() if kk != "weeks"}
                          for f, d in v["fuels"].items() if isinstance(d, dict)}
        v.pop("series", None)
        v.pop("months", None)
        out[k] = v
    return out


def _ground(by_source: dict[str, str], overall: str, digest: str,
            pcts: set[float]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    have = ca._row_numbers([digest])
    lines, dropped = [], []
    for src in SOURCES:
        text = by_source.get(src)
        if text is None:
            continue
        bad = _figures(text, have, pcts)
        if bad:
            dropped.append({"source": src, "figures": bad, "kept": False})
            continue
        lines.append({"source": src, "text": text})
    if overall:
        bad = _figures(overall, have, pcts)
        if bad:
            dropped.append({"source": "overall", "figures": bad, "kept": True})
    return lines, dropped


def _digest(asset: dict[str, Any], sources: dict[str, Any], budget: int) -> tuple[str, bool]:
    full = json.dumps({"asset": asset, "sources": sources}, default=str)
    if len(full) <= budget:
        return full, False
    small = json.dumps({"asset": asset, "sources": _compact(sources)}, default=str)
    return small[:budget], True


#: A figure in a line, and whether it is written as a percentage.
_FIGURE = re.compile(r"-?\d[\d,]*(?:\.\d+)?(\s*%)?")


def _pct_values(obj: Any) -> set[float]:
    """Every change the datasets state, as magnitudes: the values of their *_pct fields."""
    out: set[float] = set()

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif key.endswith("_pct") and isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(abs(float(v)))

    walk(obj)
    return out


def _pct_grounded(n: float, written: str, pcts: set[float]) -> bool:
    """A percentage stands only if the data states that change — to the precision written.

    contract_answer's shortcuts (any integer up to 12, any sum or difference of two figures)
    let "up 8% on last year" through for a bill with no comparison at all. A change is not a
    count, and it is not arithmetic on two unrelated figures: it is one of the data's own.
    """
    want = abs(n)
    places = len(written.split(".")[1]) if "." in written else 0
    return any(abs(p - want) <= 0.05 or round(p, places) == round(want, places) for p in pcts)


def _value_grounded(n: float, written: str, have: set[float]) -> bool:
    """A figure stands only if the data holds it, to the precision it is written.

    No arithmetic. contract_answer accepts any sum or difference of two figures, which on
    realistic data grounds most invented numbers — a withheld 97.6 passed as 69.43 + 28, the
    28 being a day of the month. A summary is not an analysis; it quotes. Whole counts up to
    12 stand ("2 readings", "8 weeks"): they are how a sentence is built, not claims.
    """
    want = abs(n)
    places = len(written.split(".")[1]) if "." in written else 0
    if places == 0 and want.is_integer() and want <= 12:
        return True
    return any(round(abs(h), places) == round(want, places) or abs(abs(h) - want) <= want * 0.005
               for h in have)


def _figures(text: str, have: set[float], pcts: set[float]) -> list[str]:
    bad = []
    for m in _FIGURE.finditer(text or ""):
        raw = m.group(0).rstrip("% ").strip()
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if m.group(1):
            if not _pct_grounded(n, raw.replace(",", ""), pcts):
                bad.append(f"{n:g}%")
        elif not _value_grounded(n, raw.replace(",", ""), have):
            bad.append(f"{n:g}")
    return bad


async def summarise(
    *,
    asset: dict[str, Any],
    sources: dict[str, Any],
    api_key: str,
    model: str = DEFAULT_MODEL,
    client: Any = None,
    budget_chars: int = BUDGET_CHARS,
) -> dict[str, Any]:
    """One grounded summary of an investigation's three datasets. ``client`` is the test seam.

    Returns ``{ok, lines, overall, dropped, meta}``; ``ok`` is false with a ``reason`` when
    there is no key, the model failed, the reply held no JSON, or every line was dropped.
    """
    meta: dict[str, Any] = {"model": model}
    if not (api_key or "").strip() and client is None:
        return {"ok": False, "reason": "no anthropic key", "meta": meta}
    sources = _withhold(sources)
    digest, truncated = _digest(asset or {}, sources, budget_chars)
    meta["truncated"] = truncated
    human = (
        ("NOTE: the data below was TRUNCATED to fit — per-day and per-week detail was removed. "
         "Summarise the totals; never report something absent because you cannot see it.\n\n"
         if truncated else "")
        + f"DATA:\n{digest}"
    )
    t0 = time.perf_counter()
    try:
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=model, max_tokens=1200, system=SYSTEM,
            messages=[{"role": "user", "content": human}],
        )
        blocks = getattr(resp, "content", None) or []
        text = "".join(getattr(b, "text", "") if not isinstance(b, dict) else b.get("text", "")
                       for b in blocks).strip()
    except Exception as exc:  # noqa: BLE001 — a failed summary leaves the walk standing
        log.warning("investigation_summary.failed", error=str(exc)[:200])
        return {"ok": False, "reason": f"the model call failed: {str(exc)[:160]}", "meta": meta}
    meta["ms"] = round((time.perf_counter() - t0) * 1000)
    payload = ca._first_json_object(text)
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "the model returned no JSON summary", "meta": meta}

    # Grounded against what the model was SENT: after _withhold, and after any cut. A figure
    # that exists only in the part that was cut was not evidence the model had.
    by_source: dict[str, str] = {}
    for line in payload.get("lines") or []:
        if isinstance(line, dict) and line.get("source") in SOURCES and str(line.get("text") or "").strip():
            by_source.setdefault(line["source"], str(line["text"]).strip())
    overall = str(payload.get("overall") or "").strip()
    # Off the event loop: the pairwise check is quadratic in the figures it holds, and this
    # service answers chat and WebSocket traffic on the same loop.
    lines, dropped = await asyncio.to_thread(_ground, by_source, overall, digest, _pct_values(sources))
    if dropped:
        log.warning("investigation_summary.ungrounded", dropped=dropped)
    if not lines:
        return {"ok": False, "reason": "every line cited a figure the data does not hold",
                "dropped": dropped, "meta": meta}
    return {"ok": True, "lines": lines, "overall": overall, "dropped": dropped, "meta": meta}
