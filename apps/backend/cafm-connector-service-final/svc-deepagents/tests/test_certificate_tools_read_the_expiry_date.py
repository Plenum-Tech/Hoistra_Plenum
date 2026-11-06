"""The certificate tools read a certificate's expiry date, not only its stored status (3 Nov 2026).

"Which buildings have lapsed certificates and overdue work orders?" left out LOLER-B-301-4471
(expired 18 Oct 2026) and FGAS-B-301-CH101: both are stored as "valid" because the lifecycle scan
that moves a status on had not run. The compliance register read (orchestrator
_status_from_expiry, 2 Nov) already reads the date; list_building_certificates and
list_vendor_accreditations asked the service for status=Lapsed, which filters on the stored
column, so a stale "valid" never came back. Read-only: what the answer is given changes, the
register does not.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from src.agents import compliance_engine_agent as ce

PAST = (date.today() - timedelta(days=16)).isoformat()
FUTURE = (date.today() + timedelta(days=200)).isoformat()

ROWS = [
    {"certificate_number": "LOLER-B-301-4471", "status": "valid", "expiry_date": PAST, "building_name": "Bishopsgate Tower"},
    {"certificate_number": "FRA-B-301", "status": "Lapsed", "expiry_date": PAST, "building_name": "Bishopsgate Tower"},
    {"certificate_number": "EICR-B-301", "status": "Current", "expiry_date": FUTURE, "building_name": "Bishopsgate Tower"},
    {"certificate_number": "DEC-NO-DATE", "status": "valid", "expiry_date": None, "building_name": "Manchester Town Hall"},
]

# FGAS-B-301-CH101 (chiller CH101) and LOLER-B-301-4471 (Lift Asset-4471) are certificates on a
# building's assets: cert_scope "Asset". The tool asked the service for cert_scope=Building only,
# so neither ever reached the Compliance Engine's answer (3 Nov 2026).
ASSET_ROWS = [
    {"id": "a1", "certificate_number": "FGAS-B-301-CH101", "status": "valid", "expiry_date": PAST,
     "building_name": "Bishopsgate Tower", "cert_scope": "Asset"},
]


@pytest.fixture
def service(monkeypatch):
    sent = []

    class _Resp:
        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return {"ok": True, "count": len(self.rows), "certificates": [dict(r) for r in self.rows]}

    async def fake_request(method, base, path, **kw):
        params = dict(kw.get("params") or {})
        sent.append(params)
        scope = params.get("cert_scope")
        # The service filters cert_scope on equality: an Asset row comes back only when asked for.
        rows = [r for r in ROWS + ASSET_ROWS
                if r.get("cert_scope", "Building") == scope or (scope == "Vendor" and "cert_scope" not in r)]
        return _Resp(rows)

    monkeypatch.setattr(ce, "_request", fake_request)
    return sent


def _run(tool, **kw):
    return asyncio.run(getattr(tool, "coroutine", tool)(**kw))


@pytest.mark.parametrize("asked", ["Lapsed", "expired", "lapsed"])
def test_lapsed_includes_a_certificate_stored_valid_whose_date_has_passed(service, asked):
    out = _run(ce.list_building_certificates, status=asked)
    nums = [r["certificate_number"] for r in out["certificates"]]
    assert nums == ["LOLER-B-301-4471", "FRA-B-301", "FGAS-B-301-CH101"]
    loler = out["certificates"][0]
    assert loler["status"] == "Lapsed" and loler["status_recorded"] == "valid" and loler["days_to_expiry"] < 0
    assert out["count"] == 3
    assert all("status" not in p for p in service), "the stored-status filter would drop the stale row"


def test_current_leaves_out_a_certificate_whose_date_has_passed(service):
    out = _run(ce.list_building_certificates, status="Current")
    nums = [r["certificate_number"] for r in out["certificates"]]
    assert "LOLER-B-301-4471" not in nums and "EICR-B-301" in nums


def test_without_a_status_every_row_is_read_by_its_date(service):
    out = _run(ce.list_building_certificates)
    by = {r["certificate_number"]: r for r in out["certificates"]}
    assert by["LOLER-B-301-4471"]["status"] == "Lapsed"
    assert by["EICR-B-301"]["status"] == "Current" and by["EICR-B-301"]["days_to_expiry"] > 0
    assert by["DEC-NO-DATE"]["status"] == "valid", "no readable date: the stored status stands"
    assert out.get("status_rule"), "the result says statuses were read from the expiry date"


def test_vendor_accreditations_read_the_date_too(service):
    out = _run(ce.list_vendor_accreditations, status="lapsed")
    assert "LOLER-B-301-4471" in [r["certificate_number"] for r in out["certificates"]]
    assert "status" not in service[-1]


def test_the_limit_still_holds_after_the_status_is_read(service):
    out = _run(ce.list_building_certificates, status="Lapsed", limit=1)
    assert len(out["certificates"]) == 1 and out["count"] == 1


def test_the_orchestrator_and_the_tools_share_one_rule():
    from src.agents import cert_expiry
    import src.agents.orchestrator as O

    row = {"status": "valid", "expiry_date": PAST}
    assert O.DeepAgentOrchestrator._status_from_expiry(row) == cert_expiry.status_from_expiry(row)


def test_a_certificate_on_a_buildings_asset_is_one_of_its_certificates(service):
    out = _run(ce.list_building_certificates, building_name="Bishopsgate Tower")
    nums = [r["certificate_number"] for r in out["certificates"]]
    assert "FGAS-B-301-CH101" in nums
    assert sorted(p["cert_scope"] for p in service) == ["Asset", "Building"]
    fgas = next(r for r in out["certificates"] if r["certificate_number"] == "FGAS-B-301-CH101")
    assert fgas["status"] == "Lapsed"


def test_a_row_returned_under_both_scopes_is_listed_once(monkeypatch):
    class _Resp:
        def json(self):
            return {"ok": True, "certificates": [{"id": "c1", "certificate_number": "EICR-1", "status": "Current",
                                                  "expiry_date": FUTURE}]}

    async def fake_request(method, base, path, **kw):
        return _Resp()

    monkeypatch.setattr(ce, "_request", fake_request)
    out = _run(ce.list_building_certificates)
    assert [r["id"] for r in out["certificates"]] == ["c1"]


# ── an overdue certificate says when it was due and what is already raised (4 Nov 2026) ──────────
# Trace Lab R34-1 / R35-1, failure mode overdue_reported_as_none (S-1): "when is the next loler
# inspection due?" was answered "There is no next LOLER inspection due" for LOLER-B-301-4471 (due
# 18 Oct 2026, overdue), and missed WO-B-301-4533 (Draft), the examination already raised on the
# same lift.

def test_an_overdue_certificate_carries_its_due_date_and_the_work_order_raised(service, monkeypatch):
    from src.agents import wo_sla

    asked = []

    async def rows(sql, params):
        asked.append((sql, params))
        return [{"asset_id": "lift-4471", "wo_code": "WO-B-301-4533", "status": "Draft",
                 "title": "LOLER thorough examination for Lift Asset-4471 before expiry"}]

    monkeypatch.setattr(wo_sla, "_rows", rows)
    monkeypatch.setattr(wo_sla, "_scope", lambda: "ad88d1ed-4376-447c-8196-e4e5b81d5af6")
    ROWS[0]["asset_id"] = "lift-4471"
    try:
        out = _run(ce.list_building_certificates)
    finally:
        ROWS[0].pop("asset_id", None)
    loler = next(r for r in out["certificates"] if r["certificate_number"] == "LOLER-B-301-4471")
    assert loler["next_due"]["due_date"] == PAST and loler["next_due"]["days_overdue"] > 0
    assert loler["open_work_orders"][0]["wo_code"] == "WO-B-301-4533"
    sql, params = asked[0]
    assert "organization_id" in sql and params["org"] == "ad88d1ed-4376-447c-8196-e4e5b81d5af6"
    assert "never" in out["status_rule"].lower() and "work order" in out["status_rule"].lower()


def test_without_an_asset_or_a_company_nothing_is_read(service, monkeypatch):
    from src.agents import wo_sla

    async def boom(sql, params):
        raise AssertionError("no read")

    monkeypatch.setattr(wo_sla, "_rows", boom)
    monkeypatch.setattr(wo_sla, "_scope", lambda: None)
    out = _run(ce.list_building_certificates)
    assert all("open_work_orders" not in r for r in out["certificates"])
