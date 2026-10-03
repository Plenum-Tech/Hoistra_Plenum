"""The Home page's money cards read the store, and claim nothing the store cannot back.

The Platform value card states its own rule: a line exists only where a cost was
detected, an action was approved, and the cost afterwards is measured or contractually
fixed. These pin that rule in the shaping — a module whose engines record no priced
value says so instead of showing a figure, "saved" never includes money still being
argued about, and the P&L's budget column stays null for as long as no budget ledger
exists to read.
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.engines.value_ledger import shape_value_summary


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def shape(raw, **kw):
    kw.setdefault("year", 2026)
    kw.setdefault("now", NOW)
    kw.setdefault("restricted", False)
    return shape_value_summary(raw, **kw)


def module(out, key):
    return next(m for m in out["ledger"]["modules"] if m["key"] == key)


def head(out, key):
    return next(h for h in out["pnl"]["heads"] if h["key"] == key)


# ── nothing readable ─────────────────────────────────────────────────────


def test_unreadable_sources_degrade_to_not_counted_not_a_failed_read():
    raw = {k: {"error": "relation does not exist"} for k in (
        "anomalies", "anomaly_items", "invoice_lines", "invoice_items", "variance",
        "recommendations", "rec_items", "approvals", "certificates", "work_orders",
        "pnl_maintenance", "pnl_energy", "pnl_unplanned",
    )}
    out = shape(raw)
    assert out["ok"] is True
    assert out["year"] == 2026
    for m in out["ledger"]["modules"]:
        assert m["counted"] is False
        assert m["detected"] is None and m["saved"] is None
        assert m["note"]  # says why, never blank
    assert out["ledger"]["total_detected"] is None
    assert out["ledger"]["total_saved"] is None
    for h in out["pnl"]["heads"]:
        assert h["budget"] is None
        assert h["actual"] is None


def test_all_five_modules_and_four_heads_always_present_in_order():
    out = shape({})
    assert [m["key"] for m in out["ledger"]["modules"]] == [
        "energy", "vendors", "maintenance", "compliance", "assets"]
    assert [h["key"] for h in out["pnl"]["heads"]] == [
        "maintenance", "energy", "compliance", "unplanned"]


# ── energy ───────────────────────────────────────────────────────────────


def test_energy_detected_and_saved_come_from_the_anomaly_store():
    raw = {
        "anomalies": {"firings": 14, "unpriced": 3, "detected": 41250.0,
                      "saved": 29000.0, "actioned": 6, "meters": 9},
        "anomaly_items": [
            {"what": "Out of hours spike", "building": "Kingsway House",
             "amount": 31000.0, "status": "resolved", "at": "2026-03-02T03:00:00Z"},
        ],
    }
    out = shape(raw)
    m = module(out, "energy")
    assert m["counted"] is True
    assert m["detected"] == 41250.0
    assert m["saved"] == 29000.0
    assert "3" in m["note"]  # the unpriced firings are disclosed, not dropped
    assert "9 meters" in m["note"]
    # The overlap rule travels with the figure: detected is per meter, and the note
    # says so rather than letting the number read as a sum of firings.
    assert "largest priced finding" in m["note"]
    assert m["items"][0]["what"].startswith("Out of hours spike")
    assert m["items"][0]["saved"] == 31000.0  # a resolved item's price was saved


def test_energy_open_item_is_detected_only():
    raw = {
        "anomalies": {"firings": 1, "unpriced": 0, "detected": 500.0,
                      "saved": 0.0, "actioned": 0},
        "anomaly_items": [{"what": "Baseline drift", "building": None,
                           "amount": 500.0, "status": "open", "at": None}],
    }
    m = module(shape(raw), "energy")
    assert m["items"][0]["detected"] == 500.0
    assert m["items"][0]["saved"] is None


def test_energy_with_no_firings_is_an_honest_zero_not_a_gap():
    raw = {"anomalies": {"firings": 0, "unpriced": 0, "detected": 0.0,
                         "saved": 0.0, "actioned": 0}, "anomaly_items": []}
    m = module(shape(raw), "energy")
    assert m["counted"] is True
    assert m["detected"] == 0.0 and m["saved"] == 0.0


# ── vendors ──────────────────────────────────────────────────────────────


def test_vendor_saved_counts_only_rejected_lines_never_challenges_in_flight():
    raw = {
        "invoice_lines": {"flagged": 9, "detected": 5400.0, "rejected": 2,
                          "saved_rejected": 1450.0, "challenged": 3,
                          "challenged_delta": 2100.0},
        "variance": {"alerts": 2, "detected": 8300.0},
    }
    m = module(shape(raw), "vendors")
    assert m["counted"] is True
    assert m["detected"] == 5400.0 + 8300.0
    assert m["saved"] == 1450.0          # challenge money is still an argument
    assert "challenge" in m["note"].lower()


def test_vendor_variance_alerts_alone_still_count_as_detected():
    raw = {"invoice_lines": {"error": "boom"},
           "variance": {"alerts": 1, "detected": 900.0}}
    m = module(shape(raw), "vendors")
    assert m["counted"] is True
    assert m["detected"] == 900.0
    assert m["saved"] == 0.0
    assert "invoice lines" in m["note"].lower()  # the unreadable half is named


# ── maintenance and compliance say so instead of inventing figures ───────


def test_maintenance_claims_nothing_because_the_store_prices_nothing():
    raw = {"work_orders": {"closed": 41}}
    m = module(shape(raw), "maintenance")
    assert m["counted"] is False
    assert m["detected"] is None and m["saved"] is None
    assert "41" in m["note"]  # activity is reported, value is not claimed


def test_compliance_reports_activity_but_prices_no_exposure():
    raw = {"approvals": {"decided": 12, "approved": 9},
           "certificates": {"issued": 17}}
    m = module(shape(raw), "compliance")
    assert m["counted"] is False
    assert m["detected"] is None and m["saved"] is None
    assert "9" in m["note"] and "17" in m["note"]


# ── assets ───────────────────────────────────────────────────────────────


def test_assets_preserved_is_replace_minus_repair_on_actioned_rows_only():
    raw = {"recommendations": {"recs": 4, "at_risk": 118000.0,
                               "actioned": 3, "preserved": 80000.0}}
    m = module(shape(raw), "assets")
    assert m["counted"] is True
    assert m["detected"] == 118000.0
    assert m["saved"] == 80000.0
    assert "estimated" in m["note"].lower()  # a model, and it says so


# ── totals ───────────────────────────────────────────────────────────────


def test_totals_sum_only_the_counted_modules():
    raw = {
        "anomalies": {"firings": 2, "unpriced": 0, "detected": 100.0,
                      "saved": 40.0, "actioned": 1},
        "recommendations": {"recs": 1, "at_risk": 50.0, "actioned": 1,
                            "preserved": 30.0},
    }
    out = shape(raw)
    assert out["ledger"]["total_detected"] == 150.0
    assert out["ledger"]["total_saved"] == 70.0
    # vendors' sources were not readable in this fixture, so it is not counted
    assert out["ledger"]["counted_modules"] == 2

def test_vendors_all_sources_down_is_not_counted():
    raw = {"invoice_lines": {"error": "x"}, "variance": {"error": "y"}}
    m = module(shape(raw), "vendors")
    assert m["counted"] is False


# ── the P&L ──────────────────────────────────────────────────────────────


def test_pnl_budget_is_null_on_every_head_until_a_ledger_exists():
    raw = {
        "pnl_maintenance": {"actual": 3860000.0, "lines": 812},
        "pnl_energy": {"actual": 3050000.0, "meters": 24},
        "pnl_unplanned": {"actual": 190000.0, "lines": 60, "type_column": "wo_type"},
    }
    out = shape(raw)
    assert out["pnl"]["budget_connected"] is False
    assert all(h["budget"] is None for h in out["pnl"]["heads"])
    assert head(out, "maintenance")["actual"] == 3860000.0
    assert head(out, "energy")["actual"] == 3050000.0
    assert head(out, "unplanned")["actual"] == 190000.0
    assert head(out, "compliance")["actual"] is None
    assert out["pnl"]["saved"] is None  # underspend needs a budget to exist


def test_pnl_unplanned_without_a_type_column_says_why_it_cannot_split():
    raw = {"pnl_unplanned": {"actual": None, "lines": 0, "type_column": None}}
    h = head(shape(raw), "unplanned")
    assert h["actual"] is None
    assert "type" in h["basis"].lower()


# ── scope ────────────────────────────────────────────────────────────────


def test_a_restricted_read_says_it_is_narrowed():
    out = shape({}, restricted=True)
    assert out["scoped_to_buildings"] is True


# ── the route boundary ───────────────────────────────────────────────────

from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.api.routes import auth as auth_routes  # noqa: E402
from src.app import app  # noqa: E402
from src.db import get_session  # noqa: E402
from src.engines.auth.tokens import Principal  # noqa: E402

ORG = UUID("11111111-1111-5111-8111-111111111111")


def _principal():
    return Principal(user_id=uuid4(), email="x@example.com", organization_id=ORG,
                     session_id=None, issued_at=NOW, password_changed_at=0,
                     role="user", can_ingest=False, building_ids=None)


class Exploding:
    async def execute(self, *a, **k):
        raise AssertionError("route reached the database before its access check")

    async def commit(self):
        pass


async def _exploding():
    yield Exploding()


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _exploding
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _as(p):
    async def _dep():
        return p
    app.dependency_overrides[auth_routes.current_principal] = _dep


def test_value_summary_requires_a_signed_in_caller(client):
    assert client.get("/api/value/summary").status_code == 401


def test_value_summary_refuses_another_company(client):
    _as(_principal())
    r = client.get(f"/api/value/summary?organization_id={uuid4()}")
    assert r.status_code == 403


def test_an_unreadable_database_degrades_the_read_not_the_route(client):
    # Every source read is inside its own guard, so a database that answers nothing
    # still yields the two cards' shape — with every line saying why it shows no figure.
    _as(_principal())
    r = client.get("/api/value/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert all(m["counted"] is False for m in body["ledger"]["modules"])
    assert all(h["actual"] is None and h["budget"] is None for h in body["pnl"]["heads"])


def test_energy_says_each_supply_counts_once_when_sub_meters_split_it():
    # Harbour Point, 25 Sep 2026: 2 supply meters and 34 sub-meters derived from them. The
    # figure is the SQL's per-supply maximum; the note must say why it is not the meter sum.
    raw = {"anomalies": {"firings": 164, "unpriced": 0, "detected": 131000.0, "saved": 0.0,
                         "actioned": 0, "meters": 36, "sub_meters": 34, "meter_sum": 375070.0}}
    m = module(shape(raw), "energy")
    assert m["detected"] == 131000.0
    assert "each supply once" in m["note"]
    assert "34 sub-meters" in m["note"]


def test_energy_note_is_unchanged_where_no_sub_meter_exists():
    raw = {"anomalies": {"firings": 4, "unpriced": 0, "detected": 900.0, "saved": 0.0,
                         "actioned": 0, "meters": 2, "sub_meters": 0}}
    assert "each supply once" not in module(shape(raw), "energy")["note"]


def test_the_pnl_energy_head_says_it_bills_supply_meters_only():
    raw = {"pnl_energy": {"actual": 329401.0, "meters": 2}}
    head = next(h for h in shape(raw)["pnl"]["heads"] if h["key"] == "energy")
    assert head["actual"] == 329401.0
    assert "supply meters" in head["basis"]


def test_an_overrun_on_both_an_invoice_and_a_variance_alert_is_counted_once():
    raw = {"invoice_lines": {"flagged": 1, "detected": 2000.0, "rejected": 0, "saved_rejected": 0.0,
                             "challenged": 0, "challenged_delta": 0.0},
           "variance": {"alerts": 1, "detected": 2500.0},
           "vendor_overlap": {"overlap": 2000.0}}
    m = module(shape(raw), "vendors")
    assert m["detected"] == 2500.0            # 2000 + 2500 − the 2000 they share
    assert "counted once" in m["note"]


def test_the_overlap_is_not_taken_off_when_the_invoice_half_could_not_be_read():
    raw = {"invoice_lines": {"error": "boom"},
           "variance": {"alerts": 1, "detected": 900.0},
           "vendor_overlap": {"overlap": 400.0}}
    assert module(shape(raw), "vendors")["detected"] == 900.0
