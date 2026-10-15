"""The Super Admin's per-company platform cost: model spend, infrastructure share, billed, margin."""
from __future__ import annotations

from datetime import date

from src.engines import platform_cost as pc


def test_months_resolve_to_their_bounds():
    t = date(2026, 10, 5)
    assert pc.month_bounds(None, t) == (date(2026, 10, 1), date(2026, 11, 1), "2026-10")
    assert pc.month_bounds("last", t) == (date(2026, 9, 1), date(2026, 10, 1), "2026-09")
    assert pc.month_bounds("2026-12", t) == (date(2026, 12, 1), date(2027, 1, 1), "2026-12")


def test_each_company_carries_its_model_cost_infra_share_revenue_and_margin():
    companies = [{"organization_id": "p", "name": "Plenum Technologies"}, {"organization_id": "n", "name": "Northbridge"},
                 {"organization_id": "z", "name": "Dormant Ltd"}]
    chat = [{"organization_id": "p", "turns": 50, "cost_usd": 4.6, "compliance_turns": 8, "compliance_cost_usd": 1.4}]
    mig = [{"organization_id": "n", "runs": 2, "cost_usd": 1.0}]
    billed = [{"organization_id": "p", "credits": 1500, "queries": 1000, "ingests": 100}, {"organization_id": "n", "credits": 100, "queries": 100, "ingests": 0}]
    out = pc.assemble(companies, chat, mig, billed, infra_monthly=600, credit_usd=0.31)
    rows = {r["organization_id"]: r for r in out["companies"]}
    p, n, z = rows["p"], rows["n"], rows["z"]
    assert out["infra_share_each_usd"] == 300.0 and out["totals"]["active_companies"] == 2
    assert (p["chat_turns"], p["cost_per_query_usd"], p["infra_share_usd"], p["total_cost_usd"]) == (50, 0.092, 300.0, 304.6)
    assert (p["revenue_usd"], p["profit_usd"], p["margin"]) == (465.0, 160.4, 0.3449)
    assert (n["migration_runs"], n["cost_per_migration_usd"], n["revenue_usd"], n["margin"]) == (2, 0.5, 31.0, -8.7097)
    assert (z["active"], z["total_cost_usd"], z["margin"]) == (False, 0.0, None)        # no activity, no infra charged
    assert out["totals"]["total_cost_usd"] == 605.6 and out["totals"]["revenue_usd"] == 496.0
    assert [r["organization_id"] for r in out["companies"]] == ["p", "n", "z"]          # costliest first


def test_the_route_is_superadmin_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.superadmin import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/superadmin/platform-cost").status_code in (401, 403)



def test_routes_read_as_query_types_with_the_price_at_the_target_margin():
    assert pc.route_label(None) == "General question" and pc.route_label("single:compliance") == "Compliance engine"
    assert pc.route_label("multi:wo_engine,wo_engine") == "Planned multi-step" and pc.route_label("single:clarify") == "Clarify (asks back)"
    rows = pc.query_type_rows([
        {"route": "single:compliance", "turns": 4, "cost_usd": 0.8, "p90_usd": 0.38, "llm_calls": 6, "tool_calls": 2, "latency_ms": 58000, "failed": 0},
        {"route": "multi:a", "turns": 1, "cost_usd": 0.06, "p90_usd": 0.06, "llm_calls": 4, "tool_calls": 0, "latency_ms": 25000, "failed": 0},
        {"route": "multi:b,c", "turns": 1, "cost_usd": 0.08, "p90_usd": 0.08, "llm_calls": 5, "tool_calls": 0, "latency_ms": 20000, "failed": 1}], 0.7)
    assert [r["type"] for r in rows] == ["Compliance engine", "Planned multi-step"]
    comp, plan = rows
    assert (comp["avg_usd"], comp["price_at_target_usd"], comp["latency_s"], comp["share"]) == (0.2, 0.6667, 58.0, 0.8511)
    assert (plan["turns"], plan["avg_usd"], plan["p90_usd"], plan["llm_calls"], plan["failed"]) == (2, 0.07, 0.08, 4.5, 1)


def test_unit_economics_say_what_a_query_costs_and_what_breaks_even():
    row = {"cost_per_query_usd": 0.0878, "chat_turns": 56, "infra_share_usd": 295.0, "credits": 42.0, "total_cost_usd": 299.91}
    u = pc.unit_economics(row, credit_usd=0.31, target=0.7)
    assert u["price_per_query_at_target_usd"] == 0.2927 and u["infra_per_query_usd"] == 5.2679
    assert u["all_in_cost_per_query_usd"] == 5.3557 and u["credits_to_cover_cost"] == 967.5
    assert u["breakeven_queries_per_month"] == 1328
    assert pc.unit_economics(None, credit_usd=0.31, target=0.7) is None
