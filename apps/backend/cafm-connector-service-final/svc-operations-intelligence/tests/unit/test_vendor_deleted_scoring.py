"""A deleted vendor is never scored, carded or rated against again.

Deleting a vendor with history RETIRES it (Hussain, 12 Oct 2026): status 'deleted', row kept so
the closed work orders and invoices it appears on still resolve. Scoring re-derives a vendor's
month from those work orders, so without these guards the next "Rebuild scorecards" — or the
month-end job on the 1st — cut a fresh card for the vendor the admin had just deleted, and the
Vendors directory listed it again. History stays where it is; nothing new is made of it.

The SQL guards are pinned in the repo's structural style (the statement sent must carry the
shared predicate); the refusals run the real engine against a session that answers only the
vendor's status. No database.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from src.engines.contract_performance import scoring as sc
from src.shared.vendor_identity import deleted_vendor_ids_sql

ORG = UUID("11111111-1111-1111-1111-111111111111")
RETIRED = UUID("33333333-3333-3333-3333-333333333333")
LIVE = UUID("44444444-4444-4444-4444-444444444444")

#: The hoistra_test shape of work_orders (test_udr_wo_select_types.REAL_WO_COLUMNS), plus the
#: text column some tenants name their vendor in.
WO_COLUMNS = {
    "id": "uuid", "wo_code": "character varying", "status": "character varying",
    "priority": "character varying", "reported_at": "timestamp with time zone",
    "attended_at": "timestamp with time zone", "completed_at": "timestamp with time zone",
    "first_fix": "boolean", "recall": "boolean", "actual_cost": "numeric",
    "estimated_cost": "numeric", "asset_id": "uuid", "vendor_id": "uuid",
    "assigned_vendor": "character varying", "organization_id": "uuid",
}


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, scalar=None, rows=()):
        self._scalar, self._rows = scalar, list(rows)

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def first(self):
        return self._rows[0] if self._rows else None


class _Session:
    """Answers the shared vendor-status read from ``statuses`` and records every statement;
    every other read finds nothing. ``broken`` fails every read."""

    def __init__(self, statuses=None, broken=False):
        self.statuses, self.broken = statuses or {}, broken
        self.statements: list = []
        self.params: list[dict] = []
        self.added: list = []
        self.committed = False

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        self.params.append(dict(params or {}))
        if self.broken:
            raise RuntimeError("connection reset")
        if "AS organization_id" in str(stmt) and "to_jsonb(v)->>'status'" in str(stmt):
            # The shared link check reads the vendor's company and status; a vendor in
            # ``statuses`` is ORG's unless given as (status, company).
            got = self.statuses.get((params or {}).get("vid"))
            if got is None:
                return _Result()
            status, org = got if isinstance(got, tuple) else (got, str(ORG))
            return _Result(rows=[{"organization_id": org, "status": status}])
        if "to_jsonb(v)->>'status'" in str(stmt):
            got = self.statuses.get((params or {}).get("vid"))
            return _Result(scalar=got[0] if isinstance(got, tuple) else got)
        return _Result()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        pass

    async def commit(self):
        self.committed = True

    async def rollback(self):
        pass


def _sql(stmt) -> str:
    return " ".join(str(stmt.compile(dialect=postgresql.dialect())).split())


@pytest.fixture
def no_weights(monkeypatch):
    """Reading the weights creates and commits a row when there is none; a refusal must come
    before it."""
    async def must_not(*_a, **_k):
        raise AssertionError("got as far as the weights for a deleted vendor")

    monkeypatch.setattr(sc, "get_or_create_weights", must_not)


def _wo():
    return {"wo_code": "WO-1", "priority": "P1", "status": "Completed",
            "reported_at": datetime(2026, 9, 1, 9, 0), "attended_at": datetime(2026, 9, 1, 9, 30),
            "completed_at": datetime(2026, 9, 1, 11, 0), "first_fix": True, "recall": False}


# ── scoring a batch ─────────────────────────────────────────────────────────────────────


async def test_a_retired_vendor_is_not_scored(no_weights):
    s = _Session({str(RETIRED): "deleted"})
    out = await sc.score_completed_work_orders(s, [_wo()], vendor_id=RETIRED, organization_id=ORG,
                                               score_month=date(2026, 9, 1))
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so it is no longer scored.",
                   "vendor_id": str(RETIRED), "scored": 0}
    assert s.added == [] and not s.committed


async def test_a_live_vendor_goes_on_to_the_contract_check(monkeypatch):
    async def weights(*_a, **_k):
        return sc.weights_to_dict(None)

    monkeypatch.setattr(sc, "get_or_create_weights", weights)
    out = await sc.score_completed_work_orders(_Session({str(LIVE): "active"}), [_wo()], vendor_id=LIVE,
                                               score_month=date(2026, 9, 1))
    assert out["error"] == "contract_parameters_unconfirmed"


# ── the monthly card ────────────────────────────────────────────────────────────────────


async def test_no_card_is_cut_for_a_retired_vendor(no_weights):
    s = _Session({str(RETIRED): "deleted"})
    out = await sc.generate_monthly_scorecard(s, vendor_id=RETIRED, score_month=date(2026, 9, 1),
                                              organization_id=ORG)
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so no scorecard is cut for it.",
                   "vendor_id": str(RETIRED), "score_month": "2026-09-01"}
    assert s.added == [] and not s.committed
    # Refused before its scores were read: only the status was asked.
    assert s.statements and all("to_jsonb(v)->>'status'" in str(st) for st in s.statements)


async def test_the_month_end_job_never_picks_a_retired_vendor():
    s = _Session()
    await sc.generate_scorecards_for_month(s, organization_id=ORG, score_month=date(2026, 9, 1))
    sql = str(s.statements[0])
    assert "FROM plenum_cafm.vendor_wo_scores s" in sql
    assert f"s.vendor_id::text NOT IN ({deleted_vendor_ids_sql()})" in sql


async def test_the_directory_lists_no_retired_vendors_card():
    s = _Session()
    await sc.list_scorecards(s, organization_id=ORG)
    sql = _sql(s.statements[0])
    assert ("CAST(plenum_cafm.vendor_monthly_scorecards.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in sql


# ── the contract a score is measured against ────────────────────────────────────────────


@pytest.mark.parametrize("vendor_id", [RETIRED, None])
async def test_a_retired_vendors_contract_never_governs(vendor_id):
    # With no vendor named the loader takes any confirmed contract — never a deleted vendor's.
    s = _Session()
    out = await sc._load_confirmed_params(s, vendor_id)
    assert out["_contract_confirmed"] is False
    sql = _sql(s.statements[0])
    assert "FROM plenum_cafm.contract_sla_parameters" in sql
    assert ("CAST(plenum_cafm.contract_sla_parameters.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in sql


# ── re-deriving from migrated work orders ───────────────────────────────────────────────


async def test_the_month_buckets_leave_out_a_retired_vendors_work_orders(monkeypatch):
    async def cols(_s):
        return WO_COLUMNS

    monkeypatch.setattr(sc, "_work_order_columns", cols)
    s = _Session()
    await sc.list_udr_score_months(s, organization_id=ORG)
    sql = str(s.statements[0])
    assert f"wo.vendor_id::text NOT IN ({deleted_vendor_ids_sql()})" in sql


async def test_the_work_order_fetch_leaves_out_a_retired_vendors_work_orders(monkeypatch):
    async def cols(_s):
        return WO_COLUMNS

    monkeypatch.setattr(sc, "_work_order_columns", cols)
    sql, params = await sc._build_udr_wo_query(_Session(), vendor_id=None, organization_id=ORG,
                                               start=None, end=None, limit=500)
    # The vendor the scorer attributes a job to — the same COALESCE the SELECT reads.
    who = "COALESCE(wo.vendor_id::text, wo.assigned_vendor::text)"
    assert f"{who} AS vendor_id" in sql
    assert f"({who} IS NULL OR {who} NOT IN ({deleted_vendor_ids_sql()}))" in sql


# ── the FM report staleness flag ────────────────────────────────────────────────────────


async def test_no_staleness_is_recorded_or_raised_for_a_retired_vendor(monkeypatch):
    async def must_not(*_a, **_k):
        raise AssertionError("raised an approval about a deleted vendor")

    monkeypatch.setattr(sc, "enqueue_approval", must_not)
    s = _Session({str(RETIRED): "deleted"})
    out = await sc.upsert_fm_staleness(s, vendor_id=RETIRED, last_report_at=None,
                                       data_as_of=date(2026, 1, 1), organization_id=ORG)
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so no report date was recorded for it."}
    assert s.added == [] and not s.committed


async def test_staleness_is_not_recorded_against_a_vendor_whose_status_cannot_be_read():
    s = _Session(broken=True)
    out = await sc.upsert_fm_staleness(s, vendor_id=RETIRED, last_report_at=None,
                                       data_as_of=date(2026, 1, 1), organization_id=ORG)
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert s.added == []


async def test_no_staleness_is_recorded_for_a_vendor_not_on_record(monkeypatch):
    # Review, 12 Oct 2026: a hard-removed vendor's id is refused like a retired one.
    async def must_not(*_a, **_k):
        raise AssertionError("raised an approval about a vendor not on record")

    monkeypatch.setattr(sc, "enqueue_approval", must_not)
    s = _Session({str(LIVE): "active"})
    out = await sc.upsert_fm_staleness(s, vendor_id=RETIRED, last_report_at=None,
                                       data_as_of=date(2026, 1, 1), organization_id=ORG)
    assert out["ok"] is False and out["reason"] == "vendor_not_found"
    assert s.added == []


# ── a status that cannot be read ────────────────────────────────────────────────────────


async def test_scoring_fails_closed_when_the_vendors_status_cannot_be_read(no_weights):
    s = _Session(broken=True)
    out = await sc.score_completed_work_orders(s, [_wo()], vendor_id=RETIRED, organization_id=ORG,
                                               score_month=date(2026, 9, 1))
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert out["scored"] == 0 and s.added == []


async def test_no_card_is_cut_while_the_vendors_status_cannot_be_read(no_weights):
    s = _Session(broken=True)
    out = await sc.generate_monthly_scorecard(s, vendor_id=RETIRED, score_month=date(2026, 9, 1),
                                              organization_id=ORG)
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert out["score_month"] == "2026-09-01" and s.added == []
