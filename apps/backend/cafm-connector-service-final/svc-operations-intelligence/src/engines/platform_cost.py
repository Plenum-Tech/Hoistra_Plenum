"""What running the platform cost per company in a month, against what it billed - for the Super Admin.

Three recorded sources, one row per company:

    chat       plenum_cafm.agent_trace_turns       every chat / cron / report turn, its model $ (Hoist Traces)
    migration  plenum_cafm.migration_llm_usage     every migration model call, from 5 Oct 2026 (schema mapper)
    billed     plenum_cafm.platform_usage_events   credits (1 per query, 5 per ingest)

Infrastructure is not recorded per company: the monthly figure (PLATFORM_INFRA_MONTHLY_USD) is shared
equally by the companies active that month. Revenue is credits x PLATFORM_CREDIT_USD. Both are
settings because they are pricing decisions, not facts the platform can read. Reads only, each
source in its own savepoint; a source that cannot be read is named, never counted as zero.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from .value_ledger import _grab_rows

log = get_logger(__name__)

DEFAULT_INFRA_MONTHLY_USD = 590.0
DEFAULT_CREDIT_USD = 0.31


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def month_bounds(month: str | None, today: date | None = None) -> tuple[date, date, str]:
    """(start, end exclusive, 'YYYY-MM') for 'YYYY-MM', 'this', 'last' or None (this month)."""
    t = today or datetime.now(timezone.utc).date()
    m = (month or "this").strip().lower()
    if m in ("this", "this_month", ""):
        start = date(t.year, t.month, 1)
    elif m in ("last", "last_month"):
        first = date(t.year, t.month, 1)
        start = (first - timedelta(days=1)).replace(day=1)
    else:
        y, mo = m.split("-")
        start = date(int(y), int(mo), 1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, end, start.strftime("%Y-%m")


def assemble(companies: list[dict[str, Any]], chat: list[dict[str, Any]], migration: list[dict[str, Any]],
             billed: list[dict[str, Any]], *, infra_monthly: float, credit_usd: float) -> dict[str, Any]:
    """The per-company table and the platform totals, from the three reads. Pure."""
    def by_org(rows):
        return {str(r["organization_id"]): r for r in rows if r.get("organization_id")}

    c, m, b = by_org(chat), by_org(migration), by_org(billed)
    names = {str(x["organization_id"]): x.get("name") or "Unnamed company" for x in companies}
    ids = list(dict.fromkeys([*names, *c, *m, *b]))
    active = [i for i in ids if i in c or i in m or i in b]
    share = round(infra_monthly / len(active), 2) if active else 0.0
    rows = []
    for i in ids:
        ch, mg, bl = c.get(i, {}), m.get(i, {}), b.get(i, {})
        turns = int(ch.get("turns") or 0)
        chat_usd = float(ch.get("cost_usd") or 0)
        mig_usd = float(mg.get("cost_usd") or 0)
        credits = float(bl.get("credits") or 0)
        infra = share if i in active else 0.0
        cost = round(chat_usd + mig_usd + infra, 4)
        revenue = round(credits * credit_usd, 2)
        rows.append({
            "organization_id": i, "name": names.get(i, "Unknown company"), "active": i in active,
            "chat_turns": turns, "chat_cost_usd": round(chat_usd, 4),
            "cost_per_query_usd": round(chat_usd / turns, 4) if turns else None,
            "compliance_turns": int(ch.get("compliance_turns") or 0),
            "compliance_cost_usd": round(float(ch.get("compliance_cost_usd") or 0), 4),
            "migration_runs": int(mg.get("runs") or 0), "migration_cost_usd": round(mig_usd, 4),
            "cost_per_migration_usd": round(mig_usd / int(mg["runs"]), 4) if mg.get("runs") else None,
            "credits": credits, "queries_billed": int(bl.get("queries") or 0), "ingests_billed": int(bl.get("ingests") or 0),
            "infra_share_usd": infra, "total_cost_usd": cost, "revenue_usd": revenue,
            "profit_usd": round(revenue - cost, 2),
            "margin": round((revenue - cost) / revenue, 4) if revenue else None,
        })
    rows.sort(key=lambda r: (-r["total_cost_usd"], r["name"]))
    tot = {k: round(sum(r[k] for r in rows), 4) for k in ("chat_turns", "chat_cost_usd", "migration_runs", "migration_cost_usd",
                                                         "credits", "infra_share_usd", "total_cost_usd", "revenue_usd", "profit_usd")}
    tot["margin"] = round(tot["profit_usd"] / tot["revenue_usd"], 4) if tot["revenue_usd"] else None
    tot["cost_per_query_usd"] = round(tot["chat_cost_usd"] / tot["chat_turns"], 4) if tot["chat_turns"] else None
    tot["active_companies"] = len(active)
    return {"companies": rows, "totals": tot, "infra_share_each_usd": share}


async def read_platform_cost(session: AsyncSession, *, month: str | None = None) -> dict[str, Any]:
    start, end, label = month_bounds(month)
    p = {"a": start, "b": end}
    companies = await _grab_rows(session, "SELECT id::text AS organization_id, name FROM plenum_cafm.organizations ORDER BY name", {})
    chat = await _grab_rows(session, """
        SELECT organization_id::text AS organization_id, count(*) AS turns, coalesce(sum(cost_usd), 0) AS cost_usd,
               count(*) FILTER (WHERE route ILIKE '%compliance%') AS compliance_turns,
               coalesce(sum(cost_usd) FILTER (WHERE route ILIKE '%compliance%'), 0) AS compliance_cost_usd
          FROM plenum_cafm.agent_trace_turns
         WHERE started_at >= CAST(:a AS date) AND started_at < CAST(:b AS date) AND organization_id IS NOT NULL
         GROUP BY 1""", p)
    migration = await _grab_rows(session, """
        SELECT j.organization_id::text AS organization_id, count(DISTINCT u.migration_id) AS runs, coalesce(sum(u.cost_usd), 0) AS cost_usd
          FROM plenum_cafm.migration_llm_usage u
          JOIN plenum_cafm.migration_jobs j ON j.id = u.migration_id
         WHERE u.created_at >= CAST(:a AS date) AND u.created_at < CAST(:b AS date)
         GROUP BY 1""", p)
    billed = await _grab_rows(session, """
        SELECT organization_id::text AS organization_id, coalesce(sum(credits), 0) AS credits,
               count(*) FILTER (WHERE kind = 'query') AS queries, count(*) FILTER (WHERE kind = 'ingest') AS ingests
          FROM plenum_cafm.platform_usage_events
         WHERE occurred_at >= CAST(:a AS date) AND occurred_at < CAST(:b AS date)
         GROUP BY 1""", p)
    models = await _grab_rows(session, """
        SELECT coalesce(model, 'unknown') AS model, count(*) AS calls, coalesce(sum(cost_usd), 0) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens
          FROM plenum_cafm.agent_trace_spans
         WHERE kind = 'llm' AND started_at >= CAST(:a AS date) AND started_at < CAST(:b AS date)
         GROUP BY 1 ORDER BY 3 DESC""", p)
    unread = [name for name, rows in (("companies", companies), ("chat", chat), ("migration", migration), ("billed", billed), ("models", models))
              if isinstance(rows, dict)]
    ok = lambda rows: rows if isinstance(rows, list) else []  # noqa: E731
    out = assemble(ok(companies), ok(chat), ok(migration), ok(billed),
                   infra_monthly=_f("PLATFORM_INFRA_MONTHLY_USD", DEFAULT_INFRA_MONTHLY_USD),
                   credit_usd=_f("PLATFORM_CREDIT_USD", DEFAULT_CREDIT_USD))
    out.update({
        "ok": True, "month": label, "from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat(), "currency": "USD",
        "by_model": [{"model": r["model"], "calls": int(r["calls"]), "cost_usd": round(float(r["cost_usd"]), 4),
                      "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"])} for r in ok(models)],
        "assumptions": {"infra_monthly_usd": _f("PLATFORM_INFRA_MONTHLY_USD", DEFAULT_INFRA_MONTHLY_USD),
                        "credit_usd": _f("PLATFORM_CREDIT_USD", DEFAULT_CREDIT_USD),
                        "infra_split": "equally among companies active in the month"},
        "not_recorded": ["document ingest model cost (not traced yet)"],
        "unreadable": unread,
    })
    if "migration" in unread:
        out["not_recorded"].append("migration model cost (no run recorded yet - the ledger table is created on the first call)")
    return out


# ── one company, every split (the Super Admin's drill-down) ──────────────────────────────

def route_label(route: str | None) -> str:
    """A turn's route as the reader thinks of it."""
    r = str(route or "").strip()
    if not r:
        return "General question"
    kind, _, eng = r.partition(":")
    if kind == "multi":
        return "Planned multi-step"
    return {"compliance": "Compliance engine", "clarify": "Clarify (asks back)", "orchestrator loop": "General loop (many tools)",
            "wo_engine": "Work orders engine", "contract_performance": "Vendor engine", "energy_intelligence": "Energy engine"}.get(eng, eng or r)


def _n(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def query_type_rows(routes: list[dict[str, Any]], target: float) -> list[dict[str, Any]]:
    """Turns grouped by what the reader calls them, with the price at the target margin. Pure."""
    total = sum(_n(r.get("cost_usd")) for r in routes)
    by: dict[str, dict[str, Any]] = {}
    for r in routes:
        k = route_label(r.get("route"))
        o = by.setdefault(k, {"type": k, "turns": 0, "cost": 0.0, "p90": 0.0, "llm": 0.0, "tools": 0.0, "lat": 0.0, "failed": 0})
        t = int(r.get("turns") or 0)
        o["turns"] += t
        o["cost"] += _n(r.get("cost_usd"))
        o["p90"] = max(o["p90"], _n(r.get("p90_usd")))
        o["llm"] += _n(r.get("llm_calls")) * t
        o["tools"] += _n(r.get("tool_calls")) * t
        o["lat"] += _n(r.get("latency_ms")) * t
        o["failed"] += int(r.get("failed") or 0)
    out = []
    for o in sorted(by.values(), key=lambda x: -x["cost"]):
        t = o["turns"] or 1
        avg = o["cost"] / t
        out.append({"type": o["type"], "turns": o["turns"], "cost_usd": round(o["cost"], 4), "avg_usd": round(avg, 4),
                    "p90_usd": round(o["p90"], 4), "llm_calls": round(o["llm"] / t, 1), "tool_calls": round(o["tools"] / t, 1),
                    "latency_s": round(o["lat"] / t / 1000, 1), "failed": o["failed"],
                    "share": round(o["cost"] / total, 4) if total else None,
                    "price_at_target_usd": round(avg / (1 - target), 4) if target < 1 else None})
    return out


def unit_economics(row: dict[str, Any] | None, *, credit_usd: float, target: float) -> dict[str, Any] | None:
    """What a query costs this company and what it should be priced at. Pure."""
    if not row:
        return None
    cpq = row.get("cost_per_query_usd")
    turns = row.get("chat_turns") or 0
    infra = row.get("infra_share_usd") or 0.0
    return {
        "target_margin": target,
        "model_cost_per_query_usd": cpq,
        "infra_per_query_usd": round(infra / turns, 4) if turns else None,
        "all_in_cost_per_query_usd": round(cpq + infra / turns, 4) if cpq is not None and turns else None,
        "price_per_query_at_target_usd": round(cpq / (1 - target), 4) if cpq is not None and target < 1 else None,
        "credit_value_usd": credit_usd,
        "credits_billed": row.get("credits"),
        "credits_to_cover_cost": round(row["total_cost_usd"] / credit_usd, 1) if credit_usd else None,
        "breakeven_queries_per_month": (round(infra / (credit_usd - cpq)) if cpq is not None and credit_usd > cpq else None),
    }


async def read_company_cost(session: AsyncSession, organization_id: str, *, month: str | None = None) -> dict[str, Any]:
    """Every split of one company's month: query type, source, agent, model, tool, day; the costliest
    turns; each migration run and stage; billing by kind; unit economics at the target margin."""
    start, end, label = month_bounds(month)
    p = {"a": start, "b": end, "o": str(organization_id)}
    base = await read_platform_cost(session, month=month)
    row = next((r for r in base["companies"] if r["organization_id"] == str(organization_id)), None)
    win = "organization_id::text = :o AND started_at >= CAST(:a AS date) AND started_at < CAST(:b AS date)"
    routes = await _grab_rows(session, f"""
        SELECT route, count(*) AS turns, coalesce(sum(cost_usd), 0) AS cost_usd,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY cost_usd) AS p90_usd, avg(llm_calls) AS llm_calls,
               avg(tool_calls) AS tool_calls, avg(latency_ms) AS latency_ms, count(*) FILTER (WHERE NOT ok) AS failed
          FROM plenum_cafm.agent_trace_turns WHERE {win} GROUP BY route""", p)
    sources = await _grab_rows(session, f"""
        SELECT CASE WHEN session_id LIKE 'report-%' THEN 'Scheduled (Hoist Crons, report cards)'
                    WHEN session_id LIKE 'rerun%' THEN 'Re-runs from a correction' ELSE 'Chat' END AS source,
               count(*) AS turns, coalesce(sum(cost_usd), 0) AS cost_usd
          FROM plenum_cafm.agent_trace_turns WHERE {win} GROUP BY 1 ORDER BY 3 DESC""", p)
    agents = await _grab_rows(session, f"""
        SELECT coalesce(agent, 'unknown') AS agent, count(*) AS calls, coalesce(sum(cost_usd), 0) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens,
               avg(latency_ms) AS latency_ms
          FROM plenum_cafm.agent_trace_spans WHERE kind = 'llm' AND {win} GROUP BY 1 ORDER BY 3 DESC""", p)
    models = await _grab_rows(session, f"""
        SELECT coalesce(model, 'unknown') AS model, count(*) AS calls, coalesce(sum(cost_usd), 0) AS cost_usd,
               coalesce(sum(input_tokens), 0) AS input_tokens, coalesce(sum(output_tokens), 0) AS output_tokens,
               coalesce(sum(cache_read_tokens), 0) AS cache_read_tokens
          FROM plenum_cafm.agent_trace_spans WHERE kind = 'llm' AND {win} GROUP BY 1 ORDER BY 3 DESC""", p)
    tools = await _grab_rows(session, f"""
        SELECT name AS tool, count(*) AS calls, avg(latency_ms) AS latency_ms, count(*) FILTER (WHERE NOT ok) AS failed
          FROM plenum_cafm.agent_trace_spans WHERE kind = 'tool' AND {win} GROUP BY 1 ORDER BY 2 DESC LIMIT 25""", p)
    days = await _grab_rows(session, f"""
        SELECT started_at::date AS day, count(*) AS turns, coalesce(sum(cost_usd), 0) AS cost_usd
          FROM plenum_cafm.agent_trace_turns WHERE {win} GROUP BY 1 ORDER BY 1""", p)
    top = await _grab_rows(session, f"""
        SELECT turn_id, left(question, 140) AS question, route, cost_usd, llm_calls, tool_calls, latency_ms, started_at, email
          FROM plenum_cafm.agent_trace_turns WHERE {win} AND cost_usd IS NOT NULL ORDER BY cost_usd DESC LIMIT 10""", p)
    runs = await _grab_rows(session, """
        SELECT u.migration_id::text AS migration_id, j.status, min(u.created_at) AS first_call, count(*) AS calls,
               coalesce(sum(u.input_tokens), 0) AS input_tokens, coalesce(sum(u.output_tokens), 0) AS output_tokens,
               coalesce(sum(u.cost_usd), 0) AS cost_usd
          FROM plenum_cafm.migration_llm_usage u JOIN plenum_cafm.migration_jobs j ON j.id = u.migration_id
         WHERE j.organization_id::text = :o AND u.created_at >= CAST(:a AS date) AND u.created_at < CAST(:b AS date)
         GROUP BY 1, 2 ORDER BY 7 DESC""", p)
    stages = await _grab_rows(session, """
        SELECT u.stage, u.model, count(*) AS calls, coalesce(sum(u.cost_usd), 0) AS cost_usd
          FROM plenum_cafm.migration_llm_usage u JOIN plenum_cafm.migration_jobs j ON j.id = u.migration_id
         WHERE j.organization_id::text = :o AND u.created_at >= CAST(:a AS date) AND u.created_at < CAST(:b AS date)
         GROUP BY 1, 2 ORDER BY 4 DESC""", p)
    billing = await _grab_rows(session, """
        SELECT kind, count(*) AS events, coalesce(sum(credits), 0) AS credits
          FROM plenum_cafm.platform_usage_events
         WHERE organization_id::text = :o AND occurred_at >= CAST(:a AS date) AND occurred_at < CAST(:b AS date)
         GROUP BY 1 ORDER BY 3 DESC""", p)

    def ok(rows):
        return rows if isinstance(rows, list) else []

    def money(rows, keys=("cost_usd",)):
        return [{k: (round(_n(v), 4) if k in keys else v) for k, v in r.items()} for r in ok(rows)]

    credit_usd = base["assumptions"]["credit_usd"]
    target = _f("PLATFORM_TARGET_MARGIN", 0.70)
    chat_total = sum(_n(r.get("cost_usd")) for r in ok(routes))
    return {
        "ok": True, "month": label, "organization_id": str(organization_id), "company": row,
        "query_types": query_type_rows(ok(routes), target),
        "sources": money(sources),
        "agents": [dict(a, share=round(_n(a["cost_usd"]) / chat_total, 4) if chat_total else None) for a in money(agents, ("cost_usd", "latency_ms"))],
        "models": money(models),
        "tools": money(tools, ("latency_ms",)),
        "days": [{"day": str(d["day"]), "turns": int(d["turns"]), "cost_usd": round(_n(d["cost_usd"]), 4)} for d in ok(days)],
        "top_turns": [dict(t, cost_usd=round(_n(t["cost_usd"]), 4), type=route_label(t.get("route")),
                           started_at=t["started_at"].isoformat() if t.get("started_at") else None) for t in ok(top)],
        "migration_runs": [dict(r, cost_usd=round(_n(r["cost_usd"]), 4),
                                first_call=r["first_call"].isoformat() if r.get("first_call") else None) for r in ok(runs)],
        "migration_stages": money(stages),
        "billing": money(billing, ("credits",)),
        "unit_economics": unit_economics(row, credit_usd=credit_usd, target=target),
        "assumptions": dict(base["assumptions"], target_margin=target),
        "not_recorded": base["not_recorded"],
        "unreadable": [n for n, rows in (("query types", routes), ("agents", agents), ("models", models), ("tools", tools),
                                         ("migrations", runs), ("billing", billing)) if isinstance(rows, dict)],
    }
