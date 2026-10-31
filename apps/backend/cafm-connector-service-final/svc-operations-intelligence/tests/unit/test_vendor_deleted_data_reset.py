"""A data reset that deletes a company's vendors says so in the Vendor audit trail.

The reset (admin/org_data_reset.py) has always hard-deleted a company's vendors with the
Contracts page, and recorded only its own "organization.data_reset" row — so the Vendor audit
trail, where every other vendor deletion is recorded (Hussain, 12 Oct 2026), showed nothing: a
company's whole vendor register could go without one entry naming a vendor. The reset now
appends ONE vendor.deleted event per run, in its own transaction, naming the vendors it deleted
(read before they go, since afterwards there is nothing left to read). A dry run writes nothing.
No database: the session answers the schema reads and the vendor read, and records the rest.
"""
from __future__ import annotations

from uuid import UUID

import pytest

from src.engines.admin import org_data_reset as rs
from src.engines.auth import vendor_audit
from src.models import OpsAuditLog

ORG = "11111111-1111-5111-8111-111111111111"
ADMIN = "22222222-2222-4222-8222-222222222222"
BUILDING = "c343c566-0000-4000-8000-000000000001"


def _shape() -> dict[str, set[str]]:
    """The Contracts and Assets tables, each with its company column, as plan_reset reads them."""
    shape = {t: {"id", "organization_id"}
             for t in rs.AREAS["contracts"].tables + rs.AREAS["assets"].tables}
    shape["buildings"] = {"building_id", "organization_id", "name"}
    shape["vendors"] |= {"vendor_name", "vendor_code", "status"}
    shape["approvals_queue_items"] = {"id", "organization_id", "item_type"}
    return shape


class _Rows:
    def __init__(self, rows=(), scalar=0, rowcount=0):
        self._rows, self._scalar, self.rowcount = list(rows), scalar, rowcount

    def all(self):
        return self._rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Answers the schema and building reads; the vendor read returns ``going``; the vendors
    DELETE removes ``deleted`` rows. Records every statement and every row added."""

    def __init__(self, going=(), deleted=None, audit_fails=False, vendors_fail=False,
                 user_fails=False):
        self.going = list(going)
        self.vendors_fail, self.user_fails = vendors_fail, user_fails
        self.deleted = len(self.going) if deleted is None else deleted
        self.audit_fails = audit_fails
        self.sql: list[tuple[str, dict]] = []
        self.added: list = []
        self.committed = self.rolled_back = False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append((sql, dict(params or {})))
        if "information_schema.columns" in sql:
            return _Rows([(t, c, "YES") for t, cs in _shape().items() for c in cs])
        if "pg_constraint" in sql:
            return _Rows([])
        if "org_buildings" in sql:
            return _Rows([(BUILDING,)])
        if "to_jsonb(vendors)" in sql:
            if self.vendors_fail:
                raise RuntimeError('(asyncpg.UndefinedColumnError) column "vendor_name" of '
                                   'relation "vendors" does not exist')
            return _Rows(self.going)
        if "FROM plenum_cafm.users" in sql:
            if self.user_fails:
                raise RuntimeError("relation users is locked")
            return _Rows([{"name": "Ada Admin", "email": "ada@ex.com", "role": "admin"}])
        if sql.startswith("DELETE FROM plenum_cafm.vendors "):
            return _Rows(rowcount=self.deleted)
        return _Rows()

    def begin_nested(self):
        return _Nested()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.audit_fails:
            raise RuntimeError("permission denied for table ops_audit_log")

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


GOING = [("v-1", "Acme Lifts Ltd", "ACME"), ("V-02", "Halden Building Services", None)]


def _events(s: _Session) -> list[OpsAuditLog]:
    return [r for r in s.added if isinstance(r, OpsAuditLog) and r.action_type == vendor_audit.DELETED]


async def test_a_reset_that_deletes_vendors_records_them_in_the_vendor_trail():
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True, actor=ADMIN)
    [event] = _events(s)
    assert event.actor == ADMIN
    assert event.source_feature == "P"
    assert event.organization_id == UUID(ORG)
    assert event.detail == {
        "mode": "reset",
        "vendors": [{"id": "v-1", "name": "Acme Lifts Ltd", "code": "ACME"},
                    {"id": "V-02", "name": "Halden Building Services", "code": None}],
        "count": 2,
        "source": "data_reset",
        "building_id": None,
        # 12 Oct 2026 (review R1/V2): who ran it, as they were - so the entry still names them
        # after their user record is scrubbed, and shows their company role, not 'user'.
        "by": {"id": ADMIN, "name": "Ada Admin", "email": "ada@ex.com", "role": "admin"},
    }
    assert s.committed


async def test_the_vendors_are_read_before_anything_is_deleted():
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True)
    statements = [q for q, _ in s.sql]
    read = next(i for i, q in enumerate(statements) if "to_jsonb(vendors)" in q)
    first_delete = next(i for i, q in enumerate(statements) if q.startswith("DELETE"))
    assert read < first_delete
    sql, params = s.sql[read]
    assert "FROM plenum_cafm.vendors WHERE organization_id::text = :org" in sql
    assert "LIMIT 200" in sql
    assert params["org"] == ORG


async def test_a_reset_with_no_person_named_is_recorded_as_the_reset():
    # The admin route should pass the admin; until it does, the entry still says what did it.
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True)
    assert _events(s)[0].actor == "system:data_reset"


async def test_the_count_is_what_was_deleted_even_beyond_the_names_listed():
    # Two hundred names are listed; the count is every vendor the delete removed.
    names = [(f"v-{i}", f"Vendor {i}", None) for i in range(200)]
    s = _Session(going=names, deleted=250)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True)
    [event] = _events(s)
    assert event.detail["count"] == 250 and len(event.detail["vendors"]) == 200


async def test_one_event_per_run_however_many_vendors():
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["contracts", "assets"], apply=True)
    assert len(_events(s)) == 1


async def test_a_dry_run_writes_nothing_and_reads_no_names():
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=False)
    assert s.added == [] and not s.committed
    assert not any("to_jsonb(vendors)" in q for q, _ in s.sql)


async def test_a_reset_that_keeps_the_vendors_records_no_vendor_event():
    s = _Session(going=GOING)
    await rs.run_reset(s, ORG, areas=["assets"], apply=True)
    assert _events(s) == []
    assert not any("to_jsonb(vendors)" in q for q, _ in s.sql)


async def test_a_reset_that_found_no_vendors_to_delete_records_nothing():
    s = _Session(going=[], deleted=0)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True)
    assert _events(s) == []


async def test_a_trail_entry_that_cannot_be_written_undoes_the_reset():
    # A deletion nobody can trace must not happen: the event is in the reset's transaction.
    s = _Session(going=GOING, audit_fails=True)
    with pytest.raises(rs.ResetFailed) as e:
        await rs.run_reset(s, ORG, areas=["contracts"], apply=True)
    assert e.value.table == "ops_audit_log"
    assert not s.committed and s.rolled_back


async def test_who_ran_the_reset_is_recorded_even_when_their_details_cannot_be_read():
    s = _Session(going=GOING, user_fails=True)
    await rs.run_reset(s, ORG, areas=["contracts"], apply=True, actor=ADMIN)
    [event] = _events(s)
    assert event.detail["by"] == {"id": ADMIN, "name": None, "email": None, "role": None}
    assert s.committed


async def test_a_vendor_read_that_fails_says_so_in_plain_words():
    # 12 Oct 2026 (review R2): the database's own text reached the reset dock, naming tables
    # and driver classes on screen. It is logged; the admin reads a sentence.
    s = _Session(going=GOING, vendors_fail=True)
    with pytest.raises(rs.ResetFailed) as e:
        await rs.run_reset(s, ORG, areas=["contracts"], apply=True, actor=ADMIN)
    assert e.value.error == ("The vendors to delete could not be read, so nothing was deleted.")
    assert "asyncpg" not in str(e.value) and "relation" not in str(e.value)
    assert not s.committed and s.rolled_back
