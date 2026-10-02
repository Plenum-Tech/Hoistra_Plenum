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

log = structlog.get_logger(__name__)

active_scope: ContextVar[dict[str, Any] | None] = ContextVar("thread_active_scope", default=None)

_FOLLOWUP = re.compile(r"\b(those|these|them|out of (?:those|these|them)|of (?:those|these|them)|the same ones?|"
                       r"that list|the above|from (?:that|the) list|the ones?)\b", re.I)
_NEW_SCOPE = re.compile(r"\b(all buildings|whole (?:estate|portfolio)|across the (?:estate|portfolio|company)|every building)\b", re.I)
_WO = re.compile(r"\bWO-[A-Z0-9][A-Z0-9-]*\d\b")
_BCODE = re.compile(r"\bB-\d{3}\b")
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
MAX_WO = 60


def is_followup(question: str) -> bool:
    q = question or ""
    return bool(_FOLLOWUP.search(q)) and not _NEW_SCOPE.search(q)


def parse_period(text: str, today: date | None = None) -> dict[str, Any] | None:
    """'last month', 'this month', 'September 2026', 'last 90 days' -> {from, to (exclusive), label, key}."""
    t = today or date.today()
    s = (text or "").lower()
    m0 = date(t.year, t.month, 1)
    if re.search(r"\blast month\b|\bprevious month\b", s):
        start = (m0 - timedelta(days=1)).replace(day=1)
        return {"from": start.isoformat(), "to": m0.isoformat(), "label": start.strftime("%B %Y"), "key": "last_month"}
    if re.search(r"\bthis month\b|\bcurrent month\b|\bmonth to date\b", s):
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
    followup = is_followup(question) and bool(previous)
    inputs = _tool_inputs(tools)
    building_name = next((str(i[k]) for i in inputs for k in ("building_name", "building") if i.get(k)), None)
    building_id = next((str(i["building_id"]) for i in inputs if i.get("building_id")), None)
    code = next(iter(_BCODE.findall((question or "") + " " + (answer or ""))), None)
    period = parse_period(question, today) or next((parse_period(str(i["period"]), today) for i in inputs if i.get("period")), None)
    named = list(dict.fromkeys(_WO.findall(answer or "")))[:MAX_WO]
    if followup:
        ws = dict(previous or {})
        ws["last_mentioned"] = named
        ws["turns"] = int(ws.get("turns") or 1) + 1
        return ws
    if not (building_name or building_id or code or period or named):
        return previous if previous and _NEW_SCOPE.search(question or "") is None else None
    return {"building_name": building_name, "building_id": building_id, "building_code": code,
            "period": period, "work_orders": named, "last_mentioned": named, "anchor_question": (question or "")[:300],
            "turns": 1}


def describe(ws: dict[str, Any] | None) -> str:
    if not ws:
        return ""
    parts = []
    b = ws.get("building_name") or ws.get("building_code")
    if b:
        parts.append("building " + b + (f" ({ws['building_code']})" if ws.get("building_code") and ws.get("building_name") else ""))
    if ws.get("period"):
        parts.append("period " + ws["period"]["label"] + f" ({ws['period']['from']} to {ws['period']['to']}, end exclusive)")
    if ws.get("work_orders"):
        wos = ws["work_orders"]
        parts.append(f"work orders named so far: {', '.join(wos[:20])}" + (f" and {len(wos) - 20} more" if len(wos) > 20 else ""))
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
