"""Maintenance watch: findings raise once, clear when gone, and a building run touches only its own.

The five checks were run read-only against hoistra_test on 1 Oct 2026 (Plenum Technologies:
9 work orders past SLA, 41 plans due in 14 days, 3 visits to rebook, 2 parts to reorder,
September 83.6 % in SLA); these hold the rules that do not need the database.
"""
from __future__ import annotations

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
