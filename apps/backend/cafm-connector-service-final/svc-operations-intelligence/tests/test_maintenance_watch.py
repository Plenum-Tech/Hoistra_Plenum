"""Maintenance watch: findings raise once, clear when gone, and a building run touches only its own.

The five checks were run read-only against hoistra_test on 1 Oct 2026 (Plenum Technologies:
9 work orders past SLA, 41 plans due in 14 days, 3 visits to rebook, 2 parts to reorder,
September 83.6 % in SLA); these hold the rules that do not need the database.
"""
from __future__ import annotations

import re

import uuid

import pytest

from src.engines.crons import jobs as cron
from src.engines.maintenance import watch


def test_the_five_maintenance_jobs_are_in_the_catalogue():
    by = {j["key"]: j for j in cron.catalogue()}
    keys = ["maintenance_sla_watch", "maintenance_ppm_due", "maintenance_ppm_missed",
            "maintenance_parts_reorder", "maintenance_monthly_summary"]
    for k in keys:
        assert by[k]["module"] == "Maintenance"
    assert not by["maintenance_parts_reorder"]["per_building"], "stock is the company's"
    assert all(by[k]["per_building"] for k in keys if k != "maintenance_parts_reorder")
    assert by["maintenance_monthly_summary"]["suggested_refresh"] == {"monthly_day": 1, "time": "06:00"}


def test_a_building_narrows_by_whatever_link_the_table_has():
    assert watch._scope_sql(None, building_col="w.building_id") == ("", {})
    assert watch._scope_sql((), building_col="w.building_id")[0] == " AND FALSE"
    b = uuid.uuid4()
    sql, params = watch._scope_sql((b,), asset_col="p.asset_id", code_col="p.building_code")
    assert "p.asset_id::text IN" in sql and "p.building_code IN" in sql and " OR " in sql
    assert params == {"mw_bids": [str(b)]}


def test_the_report_lines_are_the_report_not_a_figure():
    s = cron.summarise({"ok": True, "past_sla": 9, "report_lines": ["a", "b"]})
    assert s == {"ok": True, "past_sla": 9}


class _Res:
    rowcount = 0


class _Session:
    def __init__(self):
        self.updates, self.commits = [], 0

    async def execute(self, stmt, params=None):
        self.updates.append(params)
        r = _Res()
        r.rowcount = len((params or {}).get("ids") or [])
        return r

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_a_finding_is_raised_once_and_cleared_when_gone_only_in_its_own_building(monkeypatch):
    from src.shared import approvals

    raised = []

    async def fake_enqueue(session, **kw):
        raised.append(kw)
    b1, b2 = str(uuid.uuid4()), str(uuid.uuid4())
    pending = {"wo_sla:A": {"id": "q-a", "building_id": b1},      # still found -> left alone
               "wo_sla:GONE1": {"id": "q-g1", "building_id": b1},  # gone, this building -> cleared
               "wo_sla:GONE2": {"id": "q-g2", "building_id": b2}}  # gone, other building -> kept

    async def fake_pending(session, org, item_type):
        return pending
    monkeypatch.setattr(approvals, "enqueue_approval", fake_enqueue)
    monkeypatch.setattr(watch, "_pending_keys", fake_pending)
    s = _Session()
    findings = [{"key": "wo_sla:A", "summary": "x", "building_id": b1},
                {"key": "wo_sla:NEW", "summary": "Work order WO-9 past its SLA", "severity": "high", "building_id": b1}]
    out = await watch.raise_and_clear(s, org=uuid.uuid4(), item_type="wo_sla_breach", findings=findings,
                                      building_ids=(uuid.UUID(b1),), job_label="Work-order SLA watch")
    assert [r["payload"]["dedupe_key"] for r in raised] == ["wo_sla:NEW"]
    assert raised[0]["source_feature"] == "M" and raised[0]["payload"]["raised_by"] == watch.RAISED_BY
    assert s.updates[-1]["ids"] == ["q-g1"] and "no longer finds it" in s.updates[-1]["n"]
    assert out == {"approvals_raised": 1, "approvals_cleared": 1}
    # Company-wide, both gone items clear.
    raised.clear()
    out = await watch.raise_and_clear(_Session(), org=uuid.uuid4(), item_type="wo_sla_breach", findings=findings,
                                      building_ids=None, job_label="Work-order SLA watch")
    assert out["approvals_cleared"] == 2


# ── a deleted vendor (review, 12 Oct 2026) ─────────────────────────────────────────────
#
# A retired vendor's missed visits stay history, but the watch must not send the PM to it:
# 'Rebook the visit with the vendor' and 'Review with the vendor' named a vendor that is gone.

from datetime import date  # noqa: E402

from src.shared.vendor_identity import live_vendor_sql  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ReadSession:
    """Hands ``replies`` to the reads in order; records the SQL."""

    def __init__(self, *replies):
        self.replies, self.sql = list(replies), []

    async def execute(self, stmt, params=None):
        self.sql.append(" ".join(str(stmt).split()))
        return _Rows(self.replies.pop(0) if self.replies else [])


def _visit(vid, vendor, retired):
    return {"id": vid, "ppm_ref": "P-1", "task": "Lift service", "asset_code": "L1", "status": "Missed",
            "scheduled_date": date(2026, 9, 1), "deferred_to": None, "deferral_reason": None,
            "vendor_name": vendor, "building_code": "B1", "building_id": None, "vendor_retired": retired}


@pytest.mark.asyncio
async def test_a_retired_vendors_missed_visit_is_rebooked_with_another_vendor():
    s = _ReadSession([_visit("v1", "Acme Lifts Ltd", True), _visit("v2", "Gough & Kelly", False)])
    out = await watch.ppm_missed(s, organization_id=uuid.uuid4(), raise_approvals=False)
    assert "LEFT JOIN plenum_cafm.vendors" in s.sql[0] and live_vendor_sql("vd") in s.sql[0]
    assert "vd.id::text = v.vendor_id::text" in s.sql[0]
    assert out["visits_to_rebook"] == 2
    assert not any("Acme" in line for line in out["report_lines"])


@pytest.mark.asyncio
async def test_a_retired_vendors_missed_visit_finding_names_no_vendor(monkeypatch):
    raised = {}

    async def capture(session, *, findings, **_k):
        raised["findings"] = findings
        return {"approvals_raised": len(findings), "approvals_cleared": 0}

    monkeypatch.setattr(watch, "raise_and_clear", capture)
    s = _ReadSession([_visit("v1", "Acme Lifts Ltd", True), _visit("v2", "Gough & Kelly", False)])
    await watch.ppm_missed(s, organization_id=uuid.uuid4())
    gone, live = raised["findings"]
    assert "Acme" not in gone["summary"] and gone["payload"]["vendor"] is None
    assert gone["payload"]["action"] == "Rebook the visit with another vendor — its vendor was deleted."
    assert gone["payload"]["vendor_deleted"] is True
    assert live["summary"].endswith("· Gough & Kelly")
    assert live["payload"]["action"] == "Rebook the visit with the vendor; approve once it has a date."


def _wo_group(vendor, retired, *, closed=5, with_sla=5, in_sla=1):
    return {"vendor": vendor, "building": "B1", "vendor_retired": retired, "raised": closed,
            "closed": closed, "with_sla": with_sla, "in_sla": in_sla}


def _ppm_group(vendor, retired):
    return {"vendor": vendor, "building": "B1", "vendor_retired": retired, "scheduled": 5,
            "completed": 1, "missed": 4, "on_time": 1}


@pytest.mark.asyncio
async def test_the_monthly_summary_raises_no_review_of_a_retired_vendor():
    s = _ReadSession([_wo_group("Acme Lifts Ltd", True), _wo_group("Gough & Kelly", False)],
                     [_ppm_group("Acme Lifts Ltd", True), _ppm_group("Gough & Kelly", False)])
    out = await watch.monthly_summary(s, organization_id=uuid.uuid4(), month=date(2026, 9, 1),
                                      raise_approvals=False)
    # Matched by id where the row has one, else by the retired vendor's unmarked name.
    for sql in s.sql[:2]:
        assert "AS vendor_retired" in sql and live_vendor_sql("dv") in sql
        # Uncorrelated: the company's vendors read once per statement, not once per row.
        assert "vendor_id::text IN (" in sql and "dv.organization_id::text = CAST(CAST(:o AS uuid) AS text)" in sql
        assert not re.search(r"(?<![a-z])[wv]\.organization_id::text", sql), "no per-row correlation"
    assert out["vendors_below_target"] == 2, "only Gough's SLA and PPM findings"
    by_vendor = out["report_lines"][out["report_lines"].index("By vendor"):]
    assert not any("Acme" in line for line in by_vendor)
    # The company's month still counts the retired vendor's work: it happened.
    assert out["work_orders_closed"] == 10 and out["ppm_scheduled"] == 10
