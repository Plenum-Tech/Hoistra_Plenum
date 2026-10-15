"""The generic table CRUD does not delete, revive or rewrite a vendor (Hussain, 12 Oct 2026).

Deleting a vendor is a checked, recorded act that lives in operations-intelligence: a dry run,
a typed confirmation, the vendor's current assignments cleared, its history kept under
"<name> (deleted)", and an entry in the vendor audit trail. This service can write any table an
administrator names — from the REST routes, from its own agent's create/update/delete tools,
and from the chat agent's proxies to both — so "delete vendor X" or "make vendor X active
again" did all of it with none of that.

Every one of those paths goes through DatabaseService, so that is where the vendors table is
guarded, once. These pin the guard there, its answer on the wire, and the agent tool's view of
it. Nothing reaches a database: the session is a stand-in that records what it was asked.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/d")
logging.disable(logging.CRITICAL)

from src.agent.tools.db_tools import DBTools  # noqa: E402
from src.core.exceptions import UDRError  # noqa: E402
from src.services import principal as P  # noqa: E402
from src.services import scope  # noqa: E402
from src.services.database_service import (  # noqa: E402
    DatabaseService, VendorWriteRefused, WriteRefused,
)

ORG = UUID("11111111-1111-1111-1111-111111111111")

USE_VENDOR_DELETE = ("Vendors are deleted from the Vendors page, where the delete is checked "
                     "and recorded in the vendor audit trail.")


def _who(role: str = "admin") -> P.Principal:
    return P.Principal(user_id=uuid4(), email="admin@example.com", organization_id=ORG,
                       role=role, building_ids=None)


class _Row:
    def __init__(self, mapping: dict):
        self._mapping = mapping


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def scalar(self):
        return self._rows[0][0] if self._rows else None

    def all(self):
        return list(self._rows)


class _Session:
    """Answers by what the statement is, and records every statement it ran.

    ``statuses`` is what the vendors table holds for the targeted row(s) — the raw column
    values, NULL included."""

    def __init__(self, *, statuses=(), scope_columns=("organization_id",), ref_statuses=()):
        self.statuses = list(statuses)
        self.ref_statuses = ref_statuses
        self.scope_columns = list(scope_columns)
        self.statements: list[tuple[str, dict]] = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.statements.append((sql, dict(params or {})))
        if "information_schema.tables" in sql:
            return _Result([(1,)])
        if "information_schema.columns" in sql:
            return _Result([(c,) for c in self.scope_columns])
        if sql.endswith("FOR UPDATE"):
            return _Result(self.statuses)
        if sql.endswith("FOR SHARE"):
            if isinstance(self.ref_statuses, Exception):
                raise self.ref_statuses
            return _Result(self.ref_statuses)
        if sql.startswith("INSERT "):
            return _Result([_Row({"id": "new-1"})])
        if sql.startswith("SELECT COUNT(*)"):
            return _Result([(0,)])
        if sql.startswith("SELECT "):
            return _Result([])
        if sql.startswith("UPDATE "):
            return _Result([_Row({"id": params["_rid"], "vendor_name": "Acme Lifts Ltd"})])
        if sql.startswith("DELETE "):
            return _Result([_Row({"id": params["rid"]})])
        raise AssertionError(f"unexpected statement: {sql}")

    async def commit(self):
        self.commits += 1

    def ran(self, verb: str) -> bool:
        return any(sql.startswith(verb) for sql, _ in self.statements)


@pytest.fixture(autouse=True)
def _fresh_scope_cache():
    scope.reset_cache()
    yield
    scope.reset_cache()


# ── delete ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("who", [_who("admin"), _who("superadmin"), None],
                         ids=["admin", "superadmin", "internal"])
async def test_a_vendor_is_not_deleted_through_the_generic_crud(who):
    """No caller is exempt — a superadmin's delete needs the trail entry as much as anyone's,
    and an internal caller has no business removing a vendor around the delete either.
    Refused before any statement runs."""
    session = _Session()
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, who).delete_record("vendors", "V-01")

    assert exc.value.status_code == 403
    assert exc.value.reason == "use_vendor_delete"
    assert str(exc.value) == USE_VENDOR_DELETE
    assert session.statements == [] and session.commits == 0


async def test_naming_another_key_column_does_not_get_round_it():
    session = _Session()
    with pytest.raises(VendorWriteRefused):
        await DatabaseService(session, _who()).delete_record("vendors", "ACME", "vendor_code")
    assert session.statements == []


async def test_other_tables_are_still_deleted_from():
    session = _Session()
    deleted = await DatabaseService(session, _who()).delete_record("vendor_contacts", "c-1")
    assert deleted is True
    assert session.ran("DELETE FROM plenum_cafm.\"vendor_contacts\"")


# ── update ────────────────────────────────────────────────────────────────────────────────

async def test_a_deleted_vendor_is_not_edited():
    """A retired vendor's row is history: the work orders and invoices that name it resolve
    through it. Taking " (deleted)" off its name, or its status, revives it with no record."""
    session = _Session(statuses=["deleted"])
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who()).update_record(
            "vendors", "V-01", {"vendor_name": "Acme Lifts"})

    assert exc.value.status_code == 409
    assert exc.value.reason == "vendor_deleted"
    assert not session.ran("UPDATE") and session.commits == 0


async def test_a_deleted_vendor_is_not_edited_whatever_the_case_of_its_status():
    session = _Session(statuses=["Deleted"])
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who("superadmin")).update_record(
            "vendors", "V-01", {"notes": "x"})
    assert exc.value.reason == "vendor_deleted"
    assert not session.ran("UPDATE")


@pytest.mark.parametrize("current, new", [
    ("active", "deleted"),      # a delete with no dry run, no confirmation and no trail entry
    (None, "Deleted"),
    ("inactive", " DELETED "),
])
async def test_a_vendors_status_is_not_changed_here(current, new):
    session = _Session(statuses=[current])
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who()).update_record("vendors", "V-01", {"status": new})

    assert exc.value.status_code == 403
    assert exc.value.reason == "use_vendor_delete"
    assert not session.ran("UPDATE") and session.commits == 0


@pytest.mark.parametrize("current, new", [("active", "Active"), (None, None), (None, "active")])
async def test_a_vendor_is_still_saved_when_its_status_reads_the_same(current, new):
    session = _Session(statuses=[current])
    record = await DatabaseService(session, _who()).update_record(
        "vendors", "V-01", {"status": new, "vendor_name": "Acme Lifts Ltd"})

    assert record["vendor_name"] == "Acme Lifts Ltd"
    assert session.ran("UPDATE") and session.commits == 1


async def test_a_live_vendor_is_still_edited():
    session = _Session(statuses=["active"])
    record = await DatabaseService(session, _who()).update_record(
        "vendors", "V-01", {"vendor_name": "Acme Lifts Ltd"})

    assert record["vendor_name"] == "Acme Lifts Ltd"
    assert session.ran("UPDATE") and session.commits == 1


async def test_the_check_locks_the_rows_the_update_would_write():
    """Same WHERE as the UPDATE — key column and company scope — so it reads exactly the rows
    the UPDATE would touch, and locked, so the vendor delete cannot retire one in between."""
    session = _Session(statuses=["active"])
    await DatabaseService(session, _who()).update_record(
        "vendors", "ACME", {"vendor_name": "x"}, "vendor_code")

    check = next((sql, p) for sql, p in session.statements if sql.endswith("FOR UPDATE"))
    sql, params = check
    assert 'FROM plenum_cafm."vendors" t' in sql, sql
    assert 'WHERE "vendor_code" = :_rid AND "organization_id"::text = :scope_org' in sql, sql
    assert params == {"_rid": "ACME", "scope_org": str(ORG)}
    update = next(s for s, _ in session.statements if s.startswith("UPDATE "))
    assert 'WHERE "vendor_code" = :_rid AND "organization_id"::text = :scope_org' in update


async def test_other_tables_are_not_asked_about_vendor_status():
    session = _Session()
    await DatabaseService(session, _who()).update_record("assets", "a-1", {"status": "retired"})
    assert not any(sql.endswith("FOR UPDATE") for sql, _ in session.statements)
    assert session.ran("UPDATE")


# ── what the caller sees ─────────────────────────────────────────────────────────────────

def test_the_refusal_is_a_udr_error_so_the_agent_reads_it_rather_than_crashing():
    assert issubclass(VendorWriteRefused, UDRError)


async def test_the_agent_tool_is_told_where_vendors_are_deleted():
    """The chat agent relays this to the person who asked, so it says what happened and where
    to go — no table, route or service names."""
    out = await DBTools(DatabaseService(_Session(), _who())).delete_record("vendors", "V-01")
    assert out == {"success": False, "error": USE_VENDOR_DELETE}


@pytest.fixture
def client():
    from src.app import app
    from src.db import get_session

    holder: dict = {}

    async def _me():
        return _who()

    async def _session():
        yield holder["session"]

    app.dependency_overrides[P.current_principal] = _me
    app.dependency_overrides[get_session] = _session

    def _with(session):
        holder["session"] = session
        return TestClient(app)

    yield _with
    app.dependency_overrides.clear()


def _error(resp) -> dict:
    return (resp.json().get("errors") or [{}])[0]


def test_the_delete_route_answers_403_with_where_to_go(client):
    resp = client(_Session()).delete("/api/tables/vendors/records/V-01")

    assert resp.status_code == 403
    assert _error(resp)["code"] == "use_vendor_delete"
    assert _error(resp)["message"] == USE_VENDOR_DELETE


def test_the_update_route_answers_409_for_a_deleted_vendor(client):
    resp = client(_Session(statuses=["deleted"])).patch(
        "/api/tables/vendors/records/V-01", json={"data": {"vendor_name": "Acme Lifts"}})

    assert resp.status_code == 409
    assert _error(resp)["code"] == "vendor_deleted"


# ── review fixes, 12 Oct 2026 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("current, new", [("active", "inactive"), ("inactive", "active"),
                                          (None, "inactive")])
async def test_a_live_vendor_still_moves_between_live_statuses(current, new):
    """Only retiring and reviving belong to the Vendors page. active <-> inactive is an ordinary
    edit, and this service and the table editor are the only places it can be made."""
    session = _Session(statuses=[current])
    await DatabaseService(session, _who()).update_record("vendors", "V-01", {"status": new})
    assert session.ran("UPDATE") and session.commits == 1


@pytest.mark.parametrize("table", ["ops_audit_log", "audit_logs"])
async def test_the_audit_trail_is_not_written_through_the_generic_crud(table):
    """The vendor audit trail is read from ops_audit_log and trusts what a row says; a row
    inserted here would be a 'deleted' entry nobody made. Refused before any statement."""
    session = _Session()
    svc = DatabaseService(session, _who("superadmin"))
    for write in (svc.create_record(table, {"action": "vendor_deleted"}),
                  svc.update_record(table, "1", {"action": "x"}),
                  svc.delete_record(table, "1")):
        with pytest.raises(WriteRefused) as exc:
            await write
        assert exc.value.status_code == 403
        assert exc.value.reason == "audit_trail_read_only"
    assert session.statements == [] and session.commits == 0


async def test_the_audit_trail_is_still_read():
    session = _Session()
    out = await DatabaseService(session, _who()).read_records("ops_audit_log")
    assert out["rows"] == []


@pytest.mark.parametrize("column", ["vendor_id", "assigned_vendor", "supplier_id", "contractor_id"])
async def test_new_work_is_not_linked_to_a_deleted_vendor(column):
    session = _Session(ref_statuses=["active", "Deleted"])
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who()).create_record(
            "work_orders", {"title": "x", column: "AAAA-01"})
    assert exc.value.status_code == 409 and exc.value.reason == "vendor_deleted"
    assert not session.ran("INSERT") and session.commits == 0
    sql, params = next((q, p) for q, p in session.statements if q.endswith("FOR SHARE"))
    assert 'FROM plenum_cafm."vendors" v WHERE lower(v.id::text) = ANY(:_vendor_ids)' in sql, sql
    assert params == {"_vendor_ids": ["aaaa-01"]}


async def test_a_row_is_not_reassigned_to_a_deleted_vendor():
    session = _Session(ref_statuses=["deleted"])
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who()).update_record(
            "work_orders", "w-1", {"assigned_vendor": "V-01"})
    assert exc.value.reason == "vendor_deleted"
    assert not session.ran("UPDATE")


async def test_a_vendor_link_that_cannot_be_checked_is_refused():
    from sqlalchemy.exc import OperationalError

    session = _Session(ref_statuses=OperationalError("SELECT", {}, Exception("reset")))
    with pytest.raises(VendorWriteRefused) as exc:
        await DatabaseService(session, _who()).create_record("vendor_contacts", {"vendor_id": "V-01"})
    assert exc.value.status_code == 503 and exc.value.reason == "vendor_check_failed"
    assert not session.ran("INSERT")


async def test_a_live_vendor_is_still_linked_and_a_null_link_is_not_checked():
    session = _Session(ref_statuses=["active"])
    await DatabaseService(session, _who()).create_record("vendor_contacts", {"vendor_id": "V-01"})
    assert session.ran("INSERT") and session.commits == 1

    session = _Session()
    await DatabaseService(session, _who()).update_record("work_orders", "w-1", {"vendor_id": None})
    assert not any(q.endswith("FOR SHARE") for q, _ in session.statements)
    assert session.ran("UPDATE")


LIVE = "COALESCE(lower(to_jsonb(\"vendors\")->>'status'), 'active') <> 'deleted'"


async def test_vendor_list_reads_leave_out_deleted_vendors_in_rows_and_total():
    session = _Session()
    svc = DatabaseService(session, _who())
    await svc.read_records("vendors", {"vendor_name": "Acme"})
    await svc.search_records("vendors", "Acme", ["vendor_name"])
    reads = [q for q, _ in session.statements if q.startswith("SELECT * FROM")
             or q.startswith("SELECT COUNT(*)")]
    assert len(reads) == 3
    for q in reads:
        assert LIVE in q, q


@pytest.mark.parametrize("filters", [{"status": "deleted"}, {"status": "active"},
                                     {"id": "V-01"}, {"vendor_code": "ACME"}])
async def test_a_status_filter_or_an_exact_lookup_reads_deleted_vendors_too(filters):
    """History: 'which vendors did we delete', and resolving the vendor a closed work order
    names. An exact lookup cannot know in advance that the vendor is retired."""
    session = _Session()
    await DatabaseService(session, _who()).read_records("vendors", filters)
    assert not any(LIVE in q for q, _ in session.statements)


async def test_a_vendor_read_by_id_still_finds_a_deleted_vendor():
    session = _Session()
    with pytest.raises(UDRError):  # the stand-in has no row; the point is the SQL
        await DatabaseService(session, _who()).get_record("vendors", "V-01")
    assert not any(LIVE in q for q, _ in session.statements)


async def test_other_tables_reads_are_not_narrowed():
    session = _Session()
    await DatabaseService(session, _who()).read_records("work_orders")
    assert not any("to_jsonb" in q for q, _ in session.statements)


def test_the_agent_prompt_says_deleted_vendors_are_history():
    from src.agent.prompts import build_system_prompt

    p = build_system_prompt()
    assert "(deleted)" in p and "never assign" in p.lower()


def test_the_routes_answer_the_audit_and_retired_vendor_refusals_with_their_own_status(client):
    resp = client(_Session()).delete("/api/tables/ops_audit_log/records/1")
    assert resp.status_code == 403
    assert _error(resp)["code"] == "audit_trail_read_only"

    resp = client(_Session(ref_statuses=["deleted"])).patch(
        "/api/tables/work_orders/records/w-1", json={"data": {"assigned_vendor": "V-01"}})
    assert resp.status_code == 409
    assert _error(resp)["code"] == "vendor_deleted"
