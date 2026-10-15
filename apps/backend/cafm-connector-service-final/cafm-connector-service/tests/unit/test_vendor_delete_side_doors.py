"""A vendor is deleted through one door, and this service is not it (Hussain, 12 Oct 2026).

Deleting a vendor is a checked, recorded act: a dry run, a typed confirmation, its current
assignments cleared, its history kept under "<name> (deleted)", and an entry in the vendor audit
trail. All of that lives in operations-intelligence. This service had its own doors onto the
same rows — a DELETE that removed the vendor with no check and no record, a rename that worked
on a retired vendor, contact, contract and work-order writes that attached new data to one, and
a Table Editor that could delete the row or flip its status back to active.

These pin each door shut, and pin the reads that must keep answering: a retired vendor is gone
from the live list but still found by id, because every closed work order and invoice that names
it still has to resolve. Nothing here reaches a database — the session is a stand-in that records
what it was asked.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SECRETS_AES_KEY", "00" * 32)

from cafm_connector.api import security  # noqa: E402
from cafm_connector.api.routes.plenum_cafm import plenum_router  # noqa: E402
from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db  # noqa: E402
from cafm_connector.api.routes.plenum_cafm.table_customizer import (  # noqa: E402
    table_editor_inner,
)
from cafm_connector.api.schemas.plenum_cafm import VendorResponse  # noqa: E402
from cafm_connector.models.plenum_cafm import (  # noqa: E402
    Vendor, VendorContact, VendorContract, WorkOrder,
)

ORG = UUID("11111111-1111-1111-1111-111111111111")
VID = UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER_VID = UUID("aaaaaaaa-0000-0000-0000-000000000002")
WHEN = datetime(2026, 10, 12, 9, 0, 0)

USE_VENDOR_DELETE = ("Vendors are deleted from the Vendors page, where the delete is checked "
                     "and recorded in the vendor audit trail.")


# ── a session that records instead of reaching a database ─────────────────────────────────

class _Result:
    def __init__(self, value=None, *, keys=(), rowcount=0):
        self._value = value
        self._keys = list(keys)
        self.rowcount = rowcount

    def __iter__(self):
        return iter(self._value or [])

    def scalar(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return list(self._value or [])

    def fetchone(self):
        rows = self._value or []
        return rows[0] if rows else None

    def keys(self):
        return self._keys


class _Session:
    """Answers each statement from a queue, in order, and records every statement it ran.

    ``objects`` stands in for ``session.get``; ``refresh`` fills the server defaults a real
    INSERT would, so a 201 can be serialised."""

    def __init__(self, *answers, objects=None):
        self._answers = list(answers)
        self.statements: list = []
        self.objects = dict(objects or {})
        self.added: list = []
        self.deleted: list = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        if not self._answers:
            raise AssertionError(f"unexpected statement: {stmt}")
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, _Result) else _Result(answer)

    async def get(self, model, key):
        return self.objects.get((model.__name__, str(key)))

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = WHEN


def _admin() -> security.Principal:
    return security.Principal(user_id=uuid4(), email="admin@example.com",
                              organization_id=ORG, role="admin", building_ids=None)


@pytest.fixture
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(plenum_router, prefix="/api/v1/plenum")
    a.mount("/table-editor", table_editor_inner)
    yield a
    a.dependency_overrides.clear()
    table_editor_inner.dependency_overrides.clear()


def _serve(app: FastAPI, session: _Session) -> TestClient:
    async def _me():
        return _admin()

    async def _db():
        yield session

    for target in (app, table_editor_inner):
        target.dependency_overrides[security.current_principal] = _me
        target.dependency_overrides[get_plenum_db] = _db
    return TestClient(app)


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return " ".join(str(stmt.compile(dialect=postgresql.dialect(),
                                     compile_kwargs={"literal_binds": True})).split())


def _vendor(status, *, vid=VID, name="Acme Lifts") -> Vendor:
    return Vendor(id=vid, organization_id=ORG, vendor_name=name, address=None,
                  status=status, created_at=WHEN)


# ── DELETE /vendors/{id}: not here ───────────────────────────────────────────────────────

def test_the_connector_no_longer_deletes_a_vendor_and_says_where_it_is_done(app):
    """It ran ``db.delete`` + commit for any signed-in user of the company: no admin check, no
    dependency check, no audit row, and on databases with the dump's foreign keys it cascaded
    the vendor's contacts and contracts away. Nothing calls it. It now refuses before any
    session exists — the stand-in below fails the test if one is even opened."""

    async def _me():
        return _admin()

    async def _never():
        raise AssertionError("the vendor delete opened a database session")
        yield  # pragma: no cover

    app.dependency_overrides[security.current_principal] = _me
    app.dependency_overrides[get_plenum_db] = _never

    resp = TestClient(app).delete(f"/api/v1/plenum/vendors/{VID}")

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["ok"] is False
    assert detail["reason"] == "use_vendor_delete"
    assert detail["error"] == USE_VENDOR_DELETE


def test_the_refused_delete_still_wants_a_signed_in_caller(app):
    # The refusal must not become the one vendor route that answers an anonymous caller.
    resp = TestClient(app).delete(f"/api/v1/plenum/vendors/{VID}")
    assert resp.status_code == 401
    assert resp.json()["detail"]["reason"] == "missing_token"


# ── GET /vendors: the live list ─────────────────────────────────────────────────────────

LIVE_PREDICATE = "coalesce(lower(plenum_cafm.vendors.status), 'active') != 'deleted'"


def test_the_vendor_list_leaves_out_a_deleted_vendor_in_its_rows_and_its_total(app):
    """The Vendors directory and the Hoist vendors list read this. A retired vendor listed
    there reads as a live supplier one can still pick. NULL status is a live vendor — the
    column defaults to 'active' but older rows were written without it — and the comparison
    ignores case, so 'Deleted' is as gone as 'deleted'. The total is narrowed too, or the
    pager reads "1 of 2"."""
    session = _Session(1, [_vendor("active")])
    resp = _serve(app, session).get("/api/v1/plenum/vendors", params={"organization_id": str(ORG)})

    assert resp.status_code == 200
    count_sql, rows_sql = (_sql(s) for s, _ in session.statements)
    assert LIVE_PREDICATE in count_sql, count_sql
    assert LIVE_PREDICATE in rows_sql, rows_sql


def test_a_deleted_vendor_is_still_found_by_id_and_says_it_is_deleted(app):
    """History lookups — a closed work order, an old invoice — resolve the vendor by id, and
    must keep doing so. The answer now carries the status so a caller can tell."""
    session = _Session(objects={("Vendor", str(VID)): _vendor("deleted", name="Acme Lifts (deleted)")})
    resp = _serve(app, session).get(f"/api/v1/plenum/vendors/{VID}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "deleted"
    assert body["vendor_name"] == "Acme Lifts (deleted)"


def test_status_on_a_vendor_response_is_optional():
    # Additive: a row read without the column (or an older client's fixture) still validates.
    r = VendorResponse.model_validate({"id": VID, "organization_id": ORG,
                                       "vendor_name": "Acme Lifts", "address": None,
                                       "created_at": WHEN})
    assert r.status is None


# ── PUT /vendors/{id}: a retired vendor is history ──────────────────────────────────────

@pytest.mark.parametrize("status", ["deleted", "Deleted", "DELETED"])
def test_a_deleted_vendor_cannot_be_renamed(app, status):
    """Renaming it would drop the " (deleted)" mark that every history screen shows, and the
    next exact-name match would land on the retired row again."""
    vendor = _vendor(status, name="Acme Lifts (deleted)")
    session = _Session(objects={("Vendor", str(VID)): vendor})
    resp = _serve(app, session).put(f"/api/v1/plenum/vendors/{VID}",
                                    json={"vendor_name": "Acme Lifts"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert vendor.vendor_name == "Acme Lifts (deleted)"
    assert session.commits == 0


def test_a_live_vendor_with_no_status_can_still_be_renamed(app):
    vendor = _vendor(None)
    session = _Session(objects={("Vendor", str(VID)): vendor})
    resp = _serve(app, session).put(f"/api/v1/plenum/vendors/{VID}",
                                    json={"vendor_name": "Acme Lifts Ltd"})

    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Acme Lifts Ltd"
    assert session.commits == 1


# ── vendor contacts: nothing new on a vendor that is gone ───────────────────────────────

def _contact_body(vid=VID) -> dict:
    return {"vendor_id": str(vid), "name": "Accounts", "email": "accounts@acme.example"}


def test_a_contact_cannot_be_added_to_a_deleted_vendor(app):
    """The delete removes a vendor's contacts, because asset intelligence hands out a contact's
    email for real sends without looking at the vendor. A contact added afterwards would be
    mailed for a supplier the company has let go."""
    session = _Session("deleted")
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=_contact_body())

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert session.added == [] and session.commits == 0


def test_a_contact_cannot_be_added_to_a_vendor_the_company_does_not_have(app):
    """Contacts carry no company of their own, so the vendor is the only boundary: a vendor
    the session cannot see (missing, or another company's) is not one to attach to."""
    session = _Session(None)
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=_contact_body())

    assert resp.status_code == 404
    assert resp.json()["detail"]["reason"] == "vendor_not_found"
    assert session.added == [] and session.commits == 0


def test_a_contact_is_still_added_to_a_live_vendor(app):
    # Hoist vendors creates a vendor and then its accounts contact; that must keep working.
    session = _Session("active")
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=_contact_body())

    assert resp.status_code == 201
    assert [type(o).__name__ for o in session.added] == ["VendorContact"]
    assert session.commits == 1


def test_the_vendor_check_reads_the_id_as_text_within_the_callers_company(app):
    """Production vendor ids are varchar with legacy values like 'V-01'; comparing the column
    to a uuid parameter is an operator error there, so the id is compared as text. The check
    is an ORM read, so the session guard narrows it to the caller's company — which is what
    makes another company's vendor read as not found."""
    session = _Session("active")
    _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=_contact_body())

    stmt, _ = session.statements[0]
    for option in security._options_for(_admin()):
        stmt = stmt.options(option)
    sql = _sql(stmt)
    assert f"lower(CAST(plenum_cafm.vendors.id AS VARCHAR)) = lower('{VID}')" in sql, sql
    assert f"plenum_cafm.vendors.organization_id = '{ORG}'" in sql, sql
    assert "coalesce(lower(plenum_cafm.vendors.status), 'active')" in sql, sql


def test_a_vendor_that_cannot_be_checked_is_not_written_to(app):
    """A write that links new data to a vendor refuses when it cannot tell whether the vendor
    is still current, and says so — guessing "live" is how a deleted vendor gets a contact."""
    session = _Session(OperationalError("SELECT", {}, Exception("connection reset")))
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=_contact_body())

    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "vendor_check_failed"
    assert session.added == [] and session.commits == 0


def test_a_contact_of_a_deleted_vendor_cannot_be_edited(app):
    contact = VendorContact(id=uuid4(), vendor_id=VID, name="Accounts", email=None,
                            phone=None, designation=None)
    session = _Session("deleted", objects={("VendorContact", str(contact.id)): contact})
    resp = _serve(app, session).put(f"/api/v1/plenum/vendor-contacts/{contact.id}",
                                    json={"email": "new@acme.example"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert contact.email is None and session.commits == 0


def test_a_contact_of_another_companys_vendor_cannot_be_edited(app):
    contact = VendorContact(id=uuid4(), vendor_id=OTHER_VID, name="Accounts", email=None,
                            phone=None, designation=None)
    session = _Session(None, objects={("VendorContact", str(contact.id)): contact})
    resp = _serve(app, session).put(f"/api/v1/plenum/vendor-contacts/{contact.id}",
                                    json={"email": "new@acme.example"})

    assert resp.status_code == 404
    assert resp.json()["detail"]["reason"] == "vendor_not_found"
    assert contact.email is None and session.commits == 0


# ── vendor contracts ─────────────────────────────────────────────────────────────────────

def _contract_body(vid=VID) -> dict:
    return {"organization_id": str(ORG), "vendor_id": str(vid), "contract_name": "Lift PPM 2027"}


def test_a_contract_cannot_be_created_for_a_deleted_vendor(app):
    """A new contract is a new current relationship — the scorecards and the contract-terms
    reads would start counting the retired vendor again."""
    session = _Session("deleted")
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contracts", json=_contract_body())

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert session.added == [] and session.commits == 0


def test_a_contract_is_still_created_for_a_live_vendor(app):
    session = _Session("active")
    resp = _serve(app, session).post("/api/v1/plenum/vendor-contracts", json=_contract_body())

    assert resp.status_code == 201
    assert [type(o).__name__ for o in session.added] == ["VendorContract"]


def test_a_deleted_vendors_contract_cannot_be_edited(app):
    contract = VendorContract(id=uuid4(), organization_id=ORG, vendor_id=VID,
                              contract_name="Lift PPM 2025", status="expired", created_at=WHEN)
    session = _Session("deleted", objects={("VendorContract", str(contract.id)): contract})
    resp = _serve(app, session).put(f"/api/v1/plenum/vendor-contracts/{contract.id}",
                                    json={"status": "active"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert contract.status == "expired" and session.commits == 0


# ── work orders ──────────────────────────────────────────────────────────────────────────

def _wo_body(**extra) -> dict:
    return {"organization_id": str(ORG), "title": "Lift 2 stuck at level 3", **extra}


def test_a_work_order_cannot_be_raised_against_a_deleted_vendor(app):
    session = _Session("deleted")
    resp = _serve(app, session).post("/api/v1/plenum/work-orders",
                                     json=_wo_body(assigned_vendor=str(VID)))

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert session.added == [] and session.commits == 0


def test_a_work_order_with_no_vendor_does_not_look_one_up(app):
    session = _Session()
    resp = _serve(app, session).post("/api/v1/plenum/work-orders", json=_wo_body())

    assert resp.status_code == 201
    assert session.statements == []


def _work_order(vendor) -> WorkOrder:
    return WorkOrder(id=uuid4(), organization_id=ORG, title="Lift 2 stuck at level 3",
                     priority="medium", status="open", assigned_vendor=vendor, created_at=WHEN)


def test_a_work_order_cannot_be_reassigned_to_a_deleted_vendor(app):
    wo = _work_order(OTHER_VID)
    session = _Session("deleted", objects={("WorkOrder", str(wo.id)): wo})
    resp = _serve(app, session).put(f"/api/v1/plenum/work-orders/{wo.id}",
                                    json={"assigned_vendor": str(VID)})

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert wo.assigned_vendor == OTHER_VID and session.commits == 0


def test_a_closed_work_order_keeps_its_deleted_vendor_when_it_is_saved_again(app):
    """Closed work keeps the vendor who did it (the delete only unassigns OPEN work). A client
    that sends the whole record back — vendor unchanged — is not linking anything new, so it
    is not asked about the vendor at all."""
    wo = _work_order(VID)
    session = _Session(objects={("WorkOrder", str(wo.id)): wo})
    resp = _serve(app, session).put(f"/api/v1/plenum/work-orders/{wo.id}",
                                    json={"assigned_vendor": str(VID), "status": "closed"})

    assert resp.status_code == 200
    assert wo.status == "closed" and session.commits == 1
    assert session.statements == []


# ── the Table Editor ─────────────────────────────────────────────────────────────────────

def test_the_table_editor_does_not_delete_a_vendor_row(app):
    """Raw SQL, any company's row, no audit — the widest of the side doors. Refused before any
    statement runs."""
    session = _Session()
    resp = _serve(app, session).delete(f"/table-editor/tables/vendors/rows/{VID}")

    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "use_vendor_delete"
    assert resp.json()["detail"]["error"] == USE_VENDOR_DELETE
    assert session.statements == [] and session.commits == 0


def test_the_table_editor_still_deletes_rows_of_other_tables(app):
    session = _Session([("vendor_contacts",)], _Result(rowcount=1))
    resp = _serve(app, session).delete(f"/table-editor/tables/vendor_contacts/rows/{uuid4()}")

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


def _patch_vendor(app, session, data):
    return _serve(app, session).patch(f"/table-editor/tables/vendors/rows/{VID}",
                                      json={"data": data})


VENDOR_COLUMNS = [("id",), ("vendor_name",), ("status",), ("organization_id",)]


def test_the_table_editor_does_not_edit_a_deleted_vendor(app):
    """Clearing the mark or the status here would revive a vendor the company deleted, with no
    trail entry saying who did it."""
    session = _Session([("vendors",)], VENDOR_COLUMNS, ["deleted"])
    resp = _patch_vendor(app, session, {"vendor_name": "Acme Lifts"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert len(session.statements) == 3 and session.commits == 0


@pytest.mark.parametrize("current, new", [
    ("active", "deleted"),      # a delete with no dry run, no confirmation and no trail entry
    (None, "Deleted"),
    ("inactive", " DELETED "),
])
def test_the_table_editor_does_not_change_a_vendors_status(app, current, new):
    session = _Session([("vendors",)], VENDOR_COLUMNS, [current])
    resp = _patch_vendor(app, session, {"status": new})

    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "use_vendor_delete"
    assert len(session.statements) == 3 and session.commits == 0


def test_the_vendor_check_locks_the_same_row_the_update_writes(app):
    session = _Session([("vendors",)], VENDOR_COLUMNS, ["deleted"])
    _patch_vendor(app, session, {"vendor_name": "x"})

    check, params = session.statements[2]
    sql = " ".join(str(check).split())
    assert "WHERE v.id = :_row_id" in sql and sql.endswith("FOR UPDATE"), sql
    assert params == {"_row_id": str(VID)}


@pytest.mark.parametrize("current, new", [("active", "Active"), (None, None), (None, "active")])
def test_the_table_editor_still_saves_a_vendor_when_the_status_reads_the_same(app, current, new):
    """The editor PATCHes the one cell that was clicked. Clicking the status cell and leaving
    it as it was must not read as a status change."""
    row = _Result([(str(VID), "Acme Lifts", new)], keys=["id", "vendor_name", "status"])
    session = _Session([("vendors",)], VENDOR_COLUMNS, [current], row)
    resp = _patch_vendor(app, session, {"status": new, "vendor_name": "Acme Lifts"})

    assert resp.status_code == 200
    assert session.commits == 1


def test_the_table_editor_still_edits_a_live_vendor(app):
    row = _Result([(str(VID), "Acme Lifts Ltd", "active")], keys=["id", "vendor_name", "status"])
    session = _Session([("vendors",)], VENDOR_COLUMNS, ["active"], row)
    resp = _patch_vendor(app, session, {"vendor_name": "Acme Lifts Ltd"})

    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Acme Lifts Ltd"
    update_sql = " ".join(str(session.statements[3][0]).split())
    assert update_sql.startswith('UPDATE "plenum_cafm"."vendors" SET'), update_sql


# ── review fixes, 12 Oct 2026 ────────────────────────────────────────────────────────────

def _serve_as(app: FastAPI, session: _Session, role: str) -> TestClient:
    async def _me():
        return security.Principal(user_id=uuid4(), email="x@example.com",
                                  organization_id=ORG, role=role, building_ids=None)

    async def _db():
        yield session

    for target in (app, table_editor_inner):
        target.dependency_overrides[security.current_principal] = _me
        target.dependency_overrides[get_plenum_db] = _db
    return TestClient(app)


@pytest.mark.parametrize("role", ["user", "admin"])
def test_an_organisation_is_not_deleted_by_anyone_but_a_superadmin(app, role):
    """Deleting an organisation cascades to its vendors (vendors_organization_id_fkey ON DELETE
    CASCADE) with no trail entry, and the Organization row has no company column for the session
    guard to narrow on — so any signed-in user could delete another company. Refused before the
    row is even read."""
    from cafm_connector.models.plenum_cafm import Organization

    other = uuid4()
    session = _Session(objects={("Organization", str(other)): Organization(id=other)})
    resp = _serve_as(app, session, role).delete(f"/api/v1/plenum/organizations/{other}")

    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "superadmin_required"
    assert session.deleted == [] and session.commits == 0


def test_a_superadmin_still_deletes_an_organisation(app):
    from cafm_connector.models.plenum_cafm import Organization

    other = uuid4()
    org = Organization(id=other)
    session = _Session(objects={("Organization", str(other)): org})
    resp = _serve_as(app, session, "superadmin").delete(f"/api/v1/plenum/organizations/{other}")

    assert resp.status_code == 200
    assert session.deleted == [org] and session.commits == 1


def test_the_vendor_check_ignores_the_case_of_the_id_and_waits_for_a_running_delete(app):
    """A differently spelled uuid (upper case) must find the same vendor, and the read is
    FOR SHARE, so a writer racing the vendor delete waits for it and then sees 'deleted'."""
    session = _Session("active")
    body = _contact_body()
    body["vendor_id"] = str(VID).upper()
    _serve(app, session).post("/api/v1/plenum/vendor-contacts", json=body)

    sql = _sql(session.statements[0][0])
    assert f"lower(CAST(plenum_cafm.vendors.id AS VARCHAR)) = lower('{VID}')" in sql, sql
    assert sql.endswith("FOR SHARE"), sql


@pytest.mark.parametrize("table", ["ops_audit_log", "audit_logs"])
def test_the_table_editor_does_not_write_the_audit_trail(app, table):
    """The vendor audit trail is read from ops_audit_log; an entry inserted here would be a
    'deleted' record nobody made. Refused before any statement; reading stays open."""
    session = _Session()
    client = _serve(app, session)
    rid = uuid4()
    for resp in (client.post(f"/table-editor/tables/{table}/rows", json={"data": {"action": "x"}}),
                 client.patch(f"/table-editor/tables/{table}/rows/{rid}", json={"data": {"action": "x"}}),
                 client.delete(f"/table-editor/tables/{table}/rows/{rid}")):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["reason"] == "audit_trail_read_only"
    assert session.statements == [] and session.commits == 0


def test_the_table_editor_still_reads_the_audit_trail(app):
    rows = _Result([(1, "vendor_deleted")], keys=["id", "action"])
    session = _Session([("ops_audit_log",)], 1, rows)
    resp = _serve(app, session).get("/table-editor/tables/ops_audit_log/rows")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_the_table_editor_does_not_add_or_drop_vendor_columns(app):
    """Dropping vendors.status reads every retired vendor as live again, in every company."""
    session = _Session()
    client = _serve(app, session)
    add = client.post("/table-editor/tables/vendors/columns", params={"confirm": "true"},
                      json={"column_name": "note", "data_type": "text"})
    drop = client.delete("/table-editor/tables/vendors/columns/status", params={"confirm": "true"})
    for resp in (add, drop):
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["reason"] == "use_vendor_delete"
    assert session.statements == [] and session.commits == 0


WO_COLUMNS = [("id",), ("title",), ("assigned_vendor",), ("vendor_id",), ("supplier_id",),
              ("contractor_id",)]


@pytest.mark.parametrize("column", ["vendor_id", "assigned_vendor", "supplier_id", "contractor_id"])
def test_the_table_editor_does_not_link_new_work_to_a_deleted_vendor(app, column):
    session = _Session([("work_orders",)], WO_COLUMNS, ["active", "Deleted"])
    resp = _serve(app, session).post("/table-editor/tables/work_orders/rows",
                                     json={"data": {"title": "x", column: str(VID).upper()}})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert len(session.statements) == 3 and session.commits == 0
    sql = " ".join(str(session.statements[2][0]).split())
    assert "lower(v.id::text) = ANY(:_vendor_ids)" in sql and sql.endswith("FOR SHARE"), sql
    assert session.statements[2][1] == {"_vendor_ids": [str(VID)]}


def test_the_table_editor_does_not_reassign_a_row_to_a_deleted_vendor(app):
    session = _Session([("work_orders",)], WO_COLUMNS, ["deleted"])
    resp = _serve(app, session).patch(f"/table-editor/tables/work_orders/rows/{uuid4()}",
                                      json={"data": {"assigned_vendor": str(VID)}})
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "vendor_deleted"
    assert session.commits == 0


def test_the_table_editor_refuses_a_vendor_link_it_cannot_check(app):
    session = _Session([("work_orders",)], WO_COLUMNS,
                       OperationalError("SELECT", {}, Exception("connection reset")))
    resp = _serve(app, session).post("/table-editor/tables/work_orders/rows",
                                     json={"data": {"vendor_id": str(VID)}})
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason"] == "vendor_check_failed"
    assert session.commits == 0


def test_the_table_editor_still_links_a_live_vendor_and_skips_the_check_with_none(app):
    row = _Result([("w-1", str(VID))], keys=["id", "vendor_id"])
    session = _Session([("work_orders",)], WO_COLUMNS, ["active"], row)
    resp = _serve(app, session).post("/table-editor/tables/work_orders/rows",
                                     json={"data": {"vendor_id": str(VID)}})
    assert resp.status_code == 201 and session.commits == 1

    row = _Result([("w-1", None)], keys=["id", "vendor_id"])
    session = _Session([("work_orders",)], WO_COLUMNS, row)
    resp = _serve(app, session).post("/table-editor/tables/work_orders/rows",
                                     json={"data": {"vendor_id": None}})
    assert resp.status_code == 201 and len(session.statements) == 3


@pytest.mark.parametrize("current, new", [("active", "inactive"), ("inactive", "active"),
                                          (None, "inactive")])
def test_the_table_editor_still_moves_a_live_vendor_between_live_statuses(app, current, new):
    """Only retiring and reviving belong to the Vendors page; active and inactive are ordinary
    edits, and nothing else sets them."""
    row = _Result([(str(VID), "Acme Lifts", new)], keys=["id", "vendor_name", "status"])
    session = _Session([("vendors",)], VENDOR_COLUMNS, [current], row)
    resp = _patch_vendor(app, session, {"status": new})
    assert resp.status_code == 200, resp.text
    assert session.commits == 1
