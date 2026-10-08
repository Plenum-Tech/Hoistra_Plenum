"""The cost-saving read for the chat (svc-operations-intelligence GET /api/value/savings).

One tool, read-only, held by the work-order engine because a saving is captured by work: an
open job that removes priced energy waste, a repeat-failure asset worth replacing, an invoice
line to challenge. The skill reference skills/wo-engine/cost-savings.md says how to answer.
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_SERVICE = "operations_intelligence"
_TIMEOUT = 90.0
_PERIODS = ("last_month", "this_month", "this_year", "last_90_days")


def _period(p: str | None) -> str:
    v = (p or "last_month").strip().lower().replace(" ", "_").replace("-", "_")
    if v in ("ytd", "year_to_date", "year", "this_year"):
        return "this_year"
    if v in ("quarter", "last_quarter", "90_days", "last_3_months"):
        return "last_90_days"
    return v if v in _PERIODS else "last_month"


@tool
async def get_cost_savings(building_name: str | None = None, building_id: str | None = None,
                           period: str | None = None) -> dict:
    """Where money can be saved and the work that captures it - for one building or the whole company.

    USE THIS for any cost-saving, money, waste or spend question: "where can we save money",
    "which work orders save cost", "reduce maintenance spend", "are we overpaying vendors",
    "what is reactive work costing us", "cost-saving corrective action plan".

    Returns, for the period (last_month by default; also this_month, this_year, last_90_days):
    `ledger` (detected vs saved per module), `open_jobs` (open work orders with the cost to act and
    the priced energy waste on their asset), `repeat_failures` (assets with 3+ reactive jobs, their
    reactive cost against replacement value, open work orders on them), `overcharges` (invoice
    lines over contract rates, by vendor), `spend` (reactive share), and `warnings` - figures to
    check before quoting. Pass the building the user named as `building_name`.
    """
    from .energy_intelligence_agent import _resolve_building
    from .thread_scope import scope_building, scope_period_key

    # A follow-up ("out of those, which save cost?") with no building named: the thread's working set.
    if not (building_name or building_id):
        building_name, building_id = scope_building()
    if not period:
        period = scope_period_key()
    bid = None
    if building_name or building_id:
        bid, problem = await _resolve_building(building_name, building_id)
        if problem:
            return problem
    params = {"period": _period(period), **({"building_id": bid} if bid else {})}
    try:
        resp = await _request("GET", settings.operations_intelligence_base_url.rstrip("/"), "/api/value/savings",
                              service=_SERVICE, timeout=_TIMEOUT, params=params)
        body = resp.json()
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise into the agent loop
        log.error("cost_savings.failed", error=str(exc)[:300])
        return {"ok": False, "error": "The cost-saving read failed: " + str(exc).splitlines()[0][:300]}
    if isinstance(body, dict):
        body["building"] = building_name or building_id or "all buildings"
        body["answer_rules"] = ANSWER_RULES
    return body


ANSWER_RULES = """Answer a cost-saving question as an FM cost manager, from this read only:
- Lead with the money in one line: what can be saved, what is already recovered (say "nothing yet" when
  `saved` is 0), and the period and building.
- "Work orders that save money" = `open_jobs`: a table | WO number | What | Vendor | Cost to act |
  Money at stake |. Money at stake is `energy_waste_gbp_per_year` as "up to £N/yr energy" when priced; for
  an unpriced energy finding or a predictive job say what it prevents (failure of a £replacement_value
  asset, waste the job names). Energy figures are each asset's largest finding - never add them up.
- `repeat_failures`: assets where reactive cost in the period is near or above `replacement_value`
  (`cost_to_replacement_ratio` >= 0.5) are replace-or-fix-the-cause cases - name the asset, jobs, cost,
  ratio, vendors and its open work order.
- `overcharges`: total, by vendor, the top lines with WO numbers - money to recover before the next payment.
- `spend`: the reactive share; above ~70 % say planned/predictive work is the lever.
- Then a "Corrective action plan" table, biggest saving per £ first:
  | # | Action | WO / vendor | Cost | Saving | When |. Every row names a WO number or an asset and a vendor.
- Put every `warnings` entry under "Check before quoting" - detected is not saved, an outlier finding,
  an implausible call-out rate. Never present a detected figure as saved, and never invent a figure.
"""
