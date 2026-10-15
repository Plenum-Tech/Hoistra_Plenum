"""A deleted vendor takes no new contract, invoice or link, and leaves the Vendors page's lists.

Deleting a vendor with history RETIRES it (Hussain, 12 Oct 2026): status 'deleted', row kept so
the invoices and closed work it appears on still resolve. The contract and invoice engines are
where new data attaches itself to a vendor — a contract upload, an invoice upload, a workbook's
Contract_Terms and Invoice_Lines, the post-migration re-link by name — and where the Vendors
page reads its contracts, evidence and insights. Each is pinned here: writes refuse a retired
vendor (and one whose status cannot be read), reads leave it out. No database: the vendor's
status is answered by a fake session the way the shared check asks it, and the SQL guards are
pinned in the repo's structural style.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from src.engines.contract_performance import evidence as ev
from src.engines.contract_performance import extract as ex
from src.engines.contract_performance import insights as ins
from src.engines.contract_performance import invoice as inv
from src.engines.contract_performance import parameters as pm
from src.engines.contract_performance import post_migration as post
from src.engines.contract_performance import workbook_extras as wx
from src.shared.vendor_identity import deleted_vendor_ids_sql, live_vendor_sql

ORG = UUID("11111111-1111-1111-1111-111111111111")
RETIRED = UUID("33333333-3333-3333-3333-333333333333")
LIVE = UUID("44444444-4444-4444-4444-444444444444")

#: One term the document stated, so the no-terms guard (which runs first) lets the ingest on.
TERMS = {"sla_response_p1_hours": 4, "vendor_name": "Acme Lifts Ltd"}


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, scalar=None, rows=(), rowcount=0):
        self._scalar, self._rows, self.rowcount = scalar, list(rows), rowcount

    def scalar(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self


class _Session:
    """Answers the shared vendor-status read from ``statuses``; hands ``replies`` to the other
    statements in order (an empty result once they run out). Records every statement."""

    def __init__(self, statuses=None, replies=(), broken=False):
        self.statuses, self.replies, self.broken = statuses or {}, list(replies), broken
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
        return self.replies.pop(0) if self.replies else _Result()

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


# ── a contract ──────────────────────────────────────────────────────────────────────────


async def test_a_contract_is_not_recorded_against_a_retired_vendor():
    s = _Session({str(RETIRED): "deleted"})
    out = await pm.ingest_contract_parameters(s, extracted=dict(TERMS), organization_id=ORG,
                                              vendor_id=RETIRED)
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so no contract was recorded against it."}
    assert s.added == [] and not s.committed


async def test_a_contract_is_not_recorded_while_the_vendors_status_cannot_be_read():
    s = _Session(broken=True)
    out = await pm.ingest_contract_parameters(s, extracted=dict(TERMS), organization_id=ORG,
                                              vendor_id=RETIRED)
    assert out["ok"] is False and out["reason"] == "vendor_unreadable"
    assert s.added == []


async def test_a_name_that_resolves_to_a_retired_vendor_records_nothing_against_it(monkeypatch):
    # The name matchers are being taught to skip retired rows; until every one is, the write
    # itself checks whichever id it ended up with.
    async def resolver(*_a, **_k):
        return RETIRED

    monkeypatch.setattr(pm, "resolve_or_create_vendor", resolver)
    s = _Session({str(RETIRED): "deleted"})
    out = await pm.ingest_contract_parameters(s, extracted=dict(TERMS), organization_id=ORG)
    assert out["reason"] == "vendor_deleted" and s.added == []


async def test_the_contracts_list_leaves_out_a_retired_vendors_contracts():
    s = _Session()
    await pm.list_contract_parameters(s, organization_id=ORG)
    assert ("CAST(plenum_cafm.contract_sla_parameters.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in _sql(s.statements[0])


async def test_a_contract_upload_naming_a_retired_vendor_says_so():
    s = _Session({str(RETIRED): "deleted"})
    out = await ex._resolve_contract_vendor(s, vendor_id=RETIRED, vendor_name="Acme Lifts Ltd",
                                            organization_id=ORG)
    assert out == {"vendor_id": None, "vendor_name": "Acme Lifts Ltd", "status": "vendor_deleted"}


async def test_a_contract_upload_for_a_live_vendor_is_still_supplied():
    s = _Session({str(LIVE): "active"})
    out = await ex._resolve_contract_vendor(s, vendor_id=LIVE, vendor_name="Acme", organization_id=ORG)
    assert out["status"] == "supplied" and out["vendor_id"] == LIVE


async def test_a_contract_upload_for_a_retired_vendor_records_nothing_and_asks_no_one_to_confirm():
    s = _Session({str(RETIRED): "deleted"})
    out = await ex.extract_contract_parameters(s, extracted_fields=dict(TERMS), organization_id=ORG,
                                               vendor_id=RETIRED)
    assert out["vendor"]["status"] == "vendor_deleted"
    assert out["ingest"]["ok"] is False and out["ingest"]["reason"] == "vendor_deleted"
    assert out["requires_pm_confirmation"] is False, "nothing was recorded, so nothing awaits a PM"
    assert s.added == []


# ── an invoice ──────────────────────────────────────────────────────────────────────────


async def test_an_invoice_is_not_verified_against_a_retired_vendor(monkeypatch):
    async def must_not(*_a, **_k):
        raise AssertionError("read the weights for a deleted vendor's invoice")

    monkeypatch.setattr(inv, "get_or_create_weights", must_not)
    s = _Session({str(RETIRED): "deleted"})
    out = await inv.verify_invoice(s, invoice_ref="INV-1", lines=[{"wo_code": "WO-1", "amount": 10}],
                                   work_orders=[], vendor_id=RETIRED, organization_id=ORG)
    assert out == {"ok": False, "reason": "vendor_deleted",
                   "error": "This vendor has been deleted, so the invoice was not verified against it."}
    assert s.added == [] and not s.committed


async def test_an_invoice_upload_for_a_retired_vendor_answers_with_the_refusal():
    # The upload path wrapped every verification in ok: true; a refusal inside it read as an
    # invoice that had been checked.
    wos = [{"id": "1", "wo_code": "WO-1", "status": "Completed", "completed_at": None,
            "cost_actual": 10.0, "cost_estimated": 10.0, "labour_hours": None, "parts_cost": None,
            "asset_id": None}]
    s = _Session({str(RETIRED): "deleted"})
    out = await ex.extract_and_verify_invoice(
        s, source_text="Invoice No: INV-1\nwo_code,amount\nWO-1,10",
        lines=[{"line_id": "1", "wo_code": "WO-1", "amount": 10}], work_orders=wos,
        invoice_ref="INV-1", vendor_id=RETIRED, organization_id=ORG,
        labour_day_rate=350.0, labour_hour_rate=45.0, parts_pricing_json={"markup_cap_pct": 10})
    assert out["ok"] is False and out["reason"] == "vendor_deleted"
    assert out["error"] == "This vendor has been deleted, so the invoice was not verified against it."


# ── a vendor that is not on record ──────────────────────────────────────────────────────
#
# A write that links new data refuses a vendor with no row in the company too, not only a
# retired one (review, 12 Oct 2026): a tab opened before the vendor was removed still holds
# its id, and a contract or invoice filed against it points at nothing.

GONE = UUID("55555555-5555-5555-5555-555555555555")


async def test_a_contract_is_not_recorded_against_a_vendor_not_on_record():
    s = _Session({str(LIVE): "active"})
    out = await pm.ingest_contract_parameters(s, extracted=dict(TERMS), organization_id=ORG,
                                              vendor_id=GONE)
    assert out["ok"] is False and out["reason"] == "vendor_not_found"
    assert s.added == [] and not s.committed


async def test_an_invoice_is_not_verified_against_a_vendor_not_on_record(monkeypatch):
    async def must_not(*_a, **_k):
        raise AssertionError("read the weights for a vendor not on record")

    monkeypatch.setattr(inv, "get_or_create_weights", must_not)
    s = _Session({str(LIVE): "active"})
    out = await inv.verify_invoice(s, invoice_ref="INV-1", lines=[{"wo_code": "WO-1", "amount": 10}],
                                   work_orders=[], vendor_id=GONE, organization_id=ORG)
    assert out["ok"] is False and out["reason"] == "vendor_not_found"
    assert s.added == []


async def test_another_companys_vendor_takes_no_contract():
    s = _Session({str(GONE): ("active", "99999999-9999-9999-9999-999999999999")})
    out = await pm.ingest_contract_parameters(s, extracted=dict(TERMS), organization_id=ORG,
                                              vendor_id=GONE)
    assert out["ok"] is False and out["reason"] == "vendor_not_found"
    assert s.added == []




# ── the Vendors page's evidence and insights ────────────────────────────────────────────


async def test_the_evidence_tab_leaves_out_a_retired_vendors_scored_work():
    s = _Session()
    await ev.list_wo_evidence(s, organization_id=ORG)
    assert ("CAST(plenum_cafm.vendor_wo_scores.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in _sql(s.statements[0])


async def test_insights_leave_out_a_retired_vendors_scores_and_invoices():
    score = SimpleNamespace(cost_actual=None, cost_estimated=None, wo_code="WO-1", component_scores={})
    s = _Session(replies=[_Result(rows=[score]), _Result(rows=[])])
    await ins.compute_insights(s, vendor_id=None, organization_id=ORG, from_date=None, to_date=None)
    scores_sql, invoices_sql = _sql(s.statements[0]), _sql(s.statements[1])
    assert ("CAST(plenum_cafm.vendor_wo_scores.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in scores_sql
    assert ("CAST(plenum_cafm.invoice_verifications.vendor_id AS VARCHAR) NOT IN ("
            + deleted_vendor_ids_sql() + ")") in invoices_sql


# ── a workbook's contract terms and invoice lines ───────────────────────────────────────


async def test_a_workbook_row_never_attaches_to_a_retired_vendor():
    s = _Session()
    got = await wx._vendor(s, ORG, "ACME", "Acme Lifts Ltd")
    assert got is None
    sql = str(s.statements[0])
    assert "FROM plenum_cafm.vendors v" in sql and live_vendor_sql("v") in sql
    assert s.params[0] == {"o": ORG, "c": "ACME", "n": "Acme Lifts Ltd"}


# ── the post-migration re-link by name ──────────────────────────────────────────────────


#: The columns each table has on the hoistra_test / production shapes: ppm_visits keeps the
#: vendor's name but has no ppm_code column.
_COLUMNS = {
    "work_orders": ["id", "wo_code", "vendor_id", "organization_id"],
    "ppm_visits": ["id", "vendor_id", "vendor_name", "organization_id"],
}


class _ReconcileSession(_Session):
    """Answers the column introspection from ``columns``; one code per table to reconcile.
    ``fail_on`` raises on a statement containing that text. Counts savepoints and rollbacks."""

    def __init__(self, columns=None, fail_on=None):
        super().__init__()
        self.columns = columns if columns is not None else {
            "work_orders": ["id", "wo_code", "vendor_id", "vendor_name", "organization_id"],
            "ppm_visits": ["id", "ppm_code", "vendor_id", "vendor_name", "organization_id"],
        }
        self.fail_on, self.savepoints, self.rollbacks = fail_on, 0, 0

    def begin_nested(self):
        self.savepoints += 1
        return _Nested()

    async def rollback(self):
        self.rollbacks += 1

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(stmt)
        self.params.append(dict(params or {}))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("statement failed")
        if "information_schema.columns" in sql:
            return _Result(rows=[(c,) for c in self.columns.get((params or {}).get("t"), [])])
        if sql.lstrip().startswith("SELECT DISTINCT"):
            return _Result(rows=[("WO-1",)])
        return _Result(scalar=0)


def _relinks(s: _ReconcileSession) -> list[tuple[str, dict]]:
    return [(str(st), p) for st, p in zip(s.statements, s.params)
            if "SET vendor_id = CAST(v.id AS uuid)" in str(st)]


def _row_statements(s: _ReconcileSession) -> list[tuple[str, dict]]:
    """Every statement that reads or writes the reconciled tables' rows."""
    return [(" ".join(str(st).split()), p) for st, p in zip(s.statements, s.params)
            if "information_schema" not in str(st)]


async def test_a_reconcile_never_relinks_work_to_a_retired_vendor():
    s = _ReconcileSession()
    await post.reconcile_feature_b_migration(
        s, vendor_links={"work_orders": {"WO-1": "Acme Lifts Ltd"}}, organization_id=ORG)
    relinks = _relinks(s)
    # By the name on the row, and by the caller's map — for each table.
    assert len(relinks) == 3
    assert all(live_vendor_sql("v") in sql for sql, _ in relinks)


async def test_a_reconcile_matches_names_only_in_the_callers_company():
    # A vendor of the same name in another company is not this company's vendor.
    s = _ReconcileSession()
    await post.reconcile_feature_b_migration(
        s, vendor_links={"work_orders": {"WO-1": "Acme Lifts Ltd"}}, organization_id=ORG)
    for sql, params in _relinks(s):
        assert "v.organization_id::text = :org" in sql
        assert params["org"] == str(ORG)


# Review, 12 Oct 2026 (critical): the reconcile picked codes, de-duplicated and linked across
# every tenant — company B's WO-0001 was deleted when company A also had one with a larger id.


async def test_a_reconcile_with_no_company_named_refuses_and_touches_nothing():
    s = _ReconcileSession()
    out = await post.reconcile_feature_b_migration(s, vendor_links=None)
    assert out["ok"] is False and out["reason"] == "no_company"
    assert s.statements == [] and not s.committed


async def test_every_row_a_reconcile_reads_or_writes_is_the_callers_companys():
    s = _ReconcileSession()
    await post.reconcile_feature_b_migration(s, vendor_links=None, organization_id=ORG)
    stmts = _row_statements(s)
    assert stmts
    for sql, params in stmts:
        assert params.get("org") == str(ORG), sql
        if sql.startswith("DELETE"):
            # Both sides: a row is only ever a duplicate of its own company's row.
            assert "a.organization_id::text = :org" in sql and "b.organization_id::text = :org" in sql
        else:
            assert "t.organization_id::text = :org" in sql, sql


async def test_a_reconcile_reads_vendor_ids_as_text():
    # vendors.id is uuid on hoistra_test: `uuid ~ text` failed every relink there.
    s = _ReconcileSession()
    await post.reconcile_feature_b_migration(s, vendor_links=None, organization_id=ORG)
    for sql, _ in _relinks(s):
        assert "v.id::text ~ " in sql and "AND v.id ~" not in sql


async def test_a_table_without_its_code_column_is_skipped():
    s = _ReconcileSession(columns=_COLUMNS)
    out = await post.reconcile_feature_b_migration(s, vendor_links=None, organization_id=ORG)
    assert out["ok"] is True
    assert out["tables"]["ppm_visits"].get("skipped")
    assert not any("plenum_cafm.ppm_visits" in sql for sql, _ in _row_statements(s))


async def test_one_table_failing_keeps_the_others_links():
    # Each table in its own savepoint; a session.rollback() undid work_orders' links when
    # ppm_visits failed after it.
    s = _ReconcileSession(fail_on="UPDATE plenum_cafm.ppm_visits")
    out = await post.reconcile_feature_b_migration(s, vendor_links=None, organization_id=ORG)
    assert out["ok"] is False and "error" in out["tables"]["ppm_visits"]
    assert "error" not in out["tables"]["work_orders"]
    assert s.rollbacks == 0 and s.savepoints >= 2 and s.committed


@pytest.fixture
def client():
    from src.api.routes import auth as auth_routes
    from src.app import app
    from src.db import get_session
    from src.engines.auth.tokens import Principal

    async def _session():
        yield _Session()

    async def _principal():
        return Principal(user_id=UUID(int=2), email="pm@example.com", organization_id=ORG,
                         session_id=None, issued_at=datetime.now(timezone.utc),
                         password_changed_at=0, role="admin", can_ingest=True, building_ids=None)

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[auth_routes.current_principal] = _principal
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_the_reconcile_route_passes_the_callers_company(client, monkeypatch):
    seen: dict = {}

    async def fake(session, **kw):
        seen.update(kw)
        return {"ok": True, "tables": {}}

    monkeypatch.setattr(post, "reconcile_feature_b_migration", fake)
    r = client.post("/api/contract-performance/migration/reconcile",
                    json={"vendor_links": {"work_orders": {"WO-1": "Acme"}}})
    assert r.status_code == 200
    assert seen == {"vendor_links": {"work_orders": {"WO-1": "Acme"}}, "organization_id": ORG}


def test_the_reconcile_route_answers_400_when_no_company_is_named(client, monkeypatch):
    # A platform caller viewing no company reaches the engine with none; it refuses, and the
    # route says so rather than answering 200 (review, 12 Oct 2026).
    async def refuse(session, **kw):
        return {"ok": False, "reason": "no_company", "error": "No company was named."}

    monkeypatch.setattr(post, "reconcile_feature_b_migration", refuse)
    r = client.post("/api/contract-performance/migration/reconcile", json={})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "no_company"


def test_approving_a_deleted_vendors_item_here_is_a_conflict_too(client, monkeypatch):
    # The Vendors page decides its items through this router, not /api/approvals.
    from src.api.routes import contract_performance as cp_routes

    refusal = {"ok": False, "reason": "vendor_deleted",
               "error": "This vendor has been deleted, so this can no longer be approved. "
                        "Dismiss it instead."}

    async def refuse(*_a, **_k):
        return dict(refusal)

    monkeypatch.setattr(cp_routes.approvals_svc, "decide_queue_item", refuse)
    r = client.post(f"/api/contract-performance/approvals/{UUID(int=9)}/decide",
                    json={"decision": "approve"})
    assert r.status_code == 409
    assert r.json()["detail"] == refusal
