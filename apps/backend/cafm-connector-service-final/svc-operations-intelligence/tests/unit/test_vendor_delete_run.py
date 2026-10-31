"""Running a vendor delete: the order it writes in, what it refuses, and what it records.

The delete is one transaction. A dry run counts and rolls back; a real one locks the vendor,
checks the admin typed its name and still sees what they saw, writes in a fixed order, and
records the trail event in the same transaction - or changes nothing at all (Hussain, 12 Oct
2026). These run the real engine, schema introspection and audit writer included, against a
session that answers from the shipped schema and records every statement. No database.
"""
from __future__ import annotations

import importlib.util
import unicodedata
from pathlib import Path
from uuid import UUID

import pytest

from src.engines.admin import vendor_delete as vd

# The planner tests' schema loaders, loaded by path: the tests directory is not a package.
_spec = importlib.util.spec_from_file_location(
    "_vendor_delete_plan_tests", Path(__file__).with_name("test_vendor_delete_plan.py"))
_plan_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_plan_tests)
shipped, production_like = _plan_tests.shipped, _plan_tests.production_like

ORG = "11111111-1111-5111-8111-111111111111"
OTHER = "22222222-2222-5222-8222-222222222222"
APEX = "bbbbbbbb-0000-4000-8000-000000000001"
USER = UUID("cccccccc-0000-4000-8000-000000000001")
WRITES = ("UPDATE", "DELETE", "INSERT")


def vendor(vid=APEX, name="Apex Mechanical", org=ORG, status="active", code="APEX"):
    return {"id": vid, "vendor_name": name, "vendor_code": code, "trade": "Mechanical",
            "block_state": "Clear", "status": status, "organization_id": org}


class LockTimeout(Exception):
    """What asyncpg raises when lock_timeout expires (SQLSTATE 55P03)."""
    sqlstate = "55P03"


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Result:
    def __init__(self, rows=(), scalar=None, rowcount=0):
        self._rows, self._scalar, self.rowcount = list(rows), scalar, rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class FakeSession:
    """Answers the schema reads from a test shape, the vendor read from ``vendor``, each count
    and item read from maps keyed by the plan's own SQL, and records every statement."""

    def __init__(self, schema=None, vendor=None, counts=None, rowcounts=None, items=None,
                 recount=None, user=None, fail_on=None, busy=False, trail_fails=False,
                 user_fails=False):
        self.schema = schema or shipped()
        self.vendor, self.user = vendor, user or {"name": "Hana Admin", "email": "hana@ex.com"}
        self.counts, self.rowcounts = counts or {}, rowcounts or {}
        self.items, self.recount = items or {}, recount
        self.fail_on, self.busy = fail_on, busy
        self.trail_fails, self.user_fails = trail_fails, user_fails
        self.sql: list[tuple[str, dict]] = []
        self.added: list = []
        self.commits = self.rollbacks = 0
        self.vendor_gone = False

    def begin_nested(self):
        return _Nested()

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.trail_fails:
            raise RuntimeError("permission denied for table ops_audit_log")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1
        self.vendor_gone = False

    async def execute(self, stmt, params=None):
        sql, p = str(stmt), dict(params or {})
        self.sql.append((sql, p))
        if self.fail_on and sql.startswith(self.fail_on):
            raise RuntimeError("violates foreign key constraint on table x")
        if "information_schema.columns" in sql:
            return _Result([(t, c, col.data_type, "YES" if col.nullable else "NO", col.max_len)
                            for t, cs in self.schema.columns.items() for c, col in cs.items()])
        if "pg_constraint" in sql:
            return _Result([tuple(f) for f in self.schema.fks])
        if "FROM plenum_cafm.vendors v WHERE" in sql:
            # The read carries the company: another company's row is never returned (or locked).
            mine = self.vendor and str(self.vendor.get("organization_id") or "").lower() == \
                str(p.get("org") or "").lower()
            if mine and self.busy and "FOR UPDATE" in sql:
                raise LockTimeout("canceling statement due to lock timeout")
            return _Result([self.vendor] if mine else [])
        if "FROM plenum_cafm.users" in sql:
            if self.user_fails:
                raise RuntimeError("relation users is locked")
            return _Result([self.user])
        if sql.startswith("SELECT count(*)"):
            table = self.recount if (self.vendor_gone and self.recount is not None) else self.counts
            return _Result(scalar=table.get(sql, 0))
        if sql in self.items:
            return _Result(self.items[sql])
        if sql.startswith(WRITES):
            if sql.startswith("DELETE FROM plenum_cafm.vendors"):
                self.vendor_gone = True
            default = 1 if sql.startswith(("DELETE FROM plenum_cafm.vendors ",
                                           "UPDATE plenum_cafm.vendors ")) else 0
            return _Result(rowcount=self.rowcounts.get(sql, default))
        return _Result()

    # ── what the tests read ──
    def writes(self):
        return [q for q, _ in self.sql if q.startswith(WRITES)]

    def index(self, prefix):
        return next(i for i, (q, _) in enumerate(self.sql) if q.startswith(prefix))


def plan_of(schema=None):
    return vd.plan_delete(schema or shipped())


def sql(kind, group, key, schema=None):
    """The plan's own SQL for one step: what the fake keys its answers by."""
    plan = plan_of(schema)
    s = next(s for s in plan.writes + plan.references if s.group == group and s.key == key)
    return getattr(s, kind)


def counting(*pairs, schema=None):
    return {sql("count_sql", g, k, schema): n for (g, k), n in pairs}


async def apply(s, **kw):
    args = dict(organization_id=ORG, vendor_id=APEX, apply=True, user_id=USER, role="admin",
                confirm="Apex Mechanical", expect="remove", reason=None)
    args.update(kw)
    return await vd.run(s, **args)


# ── a dry run ───────────────────────────────────────────────────────────────────────────

async def test_a_dry_run_writes_nothing_and_rolls_back():
    s = FakeSession(vendor=vendor(), counts=counting((("unassigned", "assets"), 4)))
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert s.writes() == []
    assert s.commits == 0 and s.rollbacks >= 1 and s.added == []
    assert out["ok"] is True and out["applied"] is False
    assert out["groups"]["unassigned"] == [{"key": "assets", "label": "Assets", "rows": 4}]
    # Bounded before anything else runs: a stuck lock refuses in seconds, not minutes.
    assert s.sql[0][0] == "SET LOCAL lock_timeout = '5s'"
    assert s.sql[1][0] == "SET LOCAL statement_timeout = '120s'"


async def test_the_vendors_row_is_locked_while_a_real_delete_is_counted():
    s = FakeSession(vendor=vendor())
    await apply(s)
    lock = next(i for i, (q, _) in enumerate(s.sql) if "FROM plenum_cafm.vendors v" in q)
    assert s.sql[lock][0].rstrip().endswith("FOR UPDATE") and s.sql[lock][1]["vid"] == APEX
    assert lock < s.index("SELECT count(*)")


async def test_a_dry_run_reads_the_vendor_without_locking_it():
    # 12 Oct 2026 (review R1/R5): opening the dialog only reads, yet it held FOR UPDATE through
    # seconds of counts, stalling scans and reassignments - and answered "busy" behind them.
    # The real delete re-counts under the lock, and ``expect`` catches a changed mode.
    s = FakeSession(vendor=vendor(), busy=True)
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert out["mode"] == "remove"
    read = next(q for q, _ in s.sql if "FROM plenum_cafm.vendors v" in q)
    assert "FOR UPDATE" not in read and "FOR SHARE" not in read


@pytest.mark.parametrize("dry", [True, False])
async def test_the_vendor_read_is_bound_to_the_company(dry):
    # 12 Oct 2026 (review R2): the company was checked after the lock, so a guessed id of
    # another company's vendor locked that row and, while it was busy, answered 409 not 404.
    s = FakeSession(vendor=vendor())
    if dry:
        await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    else:
        await apply(s)
    q, p = next((q, p) for q, p in s.sql if "FROM plenum_cafm.vendors v" in q)
    assert "lower(to_jsonb(v)->>'organization_id') = lower(:org)" in q and p["org"] == ORG


async def test_another_companys_busy_vendor_is_not_found_not_busy():
    s = FakeSession(vendor=vendor(org=OTHER), busy=True)
    with pytest.raises(vd.VendorNotFound):
        await apply(s)


async def test_a_vendor_with_no_history_is_removed():
    s = FakeSession(vendor=vendor())
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert out["mode"] == "remove"
    assert out["groups"]["kept"] == [] and out["groups"]["other"] == []


async def test_one_row_of_history_anywhere_retires_the_vendor():
    s = FakeSession(vendor=vendor(), counts=counting((("kept", "invoice_lines"), 2)))
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert out["mode"] == "retire"
    assert out["groups"]["kept"] == [{"key": "invoice_lines", "label": "Invoice lines", "rows": 2}]


async def test_another_companys_row_naming_the_vendor_retires_it():
    s = FakeSession(vendor=vendor(), counts=counting((("other", "assets.vendor_id"), 1)))
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert out["mode"] == "retire"
    assert out["groups"]["other"] == [{"key": "assets.vendor_id", "label": "Other records",
                                       "rows": 1}]


async def test_the_plan_says_what_this_database_cannot_check():
    s = FakeSession(vendor=vendor())
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    # The shipped schema's maintenance plans name no vendor.
    assert {"key": "maintenance_plans", "label": "Maintenance plans"} in out["unavailable"]


# ── refusals: nothing written ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("row", [
    None,                                  # no such vendor
    vendor(org=OTHER),                     # another company's - never disclosed as such
    vendor(org=None),                      # nobody's: cannot be shown to be this company's
])
async def test_a_vendor_that_is_not_this_companys_is_not_found(row):
    s = FakeSession(vendor=row)
    with pytest.raises(vd.VendorNotFound):
        await apply(s)
    assert s.writes() == [] and s.commits == 0 and s.rollbacks >= 1


@pytest.mark.parametrize("bad", ["", "   ", "V 01", "x" * 101, "V-01\n", "V\x00", None])
async def test_a_malformed_id_is_refused_before_the_database(bad):
    s = FakeSession(vendor=vendor())
    with pytest.raises(vd.VendorDeleteBadId):
        await apply(s, vendor_id=bad)
    assert s.sql == []


async def test_a_legacy_id_is_read_and_written_as_text():
    s = FakeSession(vendor=vendor(vid="V-01"))
    out = await apply(s, vendor_id="V-01")
    assert out["vendor"]["id"] == "V-01" and out["applied"] is True
    assert {p["vid"] for q, p in s.sql if q.startswith(WRITES) and "vid" in p} == {"V-01"}


async def test_an_already_deleted_vendor_answers_already_and_writes_nothing():
    s = FakeSession(vendor=vendor(name="Apex Mechanical (deleted)", status="deleted", code=None))
    out = await apply(s, confirm="anything at all")
    assert out["ok"] is True and out["already"] is True and out["mode"] == "retire"
    assert s.writes() == [] and s.commits == 0 and s.added == []


@pytest.mark.parametrize("typed", ["apex mechanical", "Apex", "Apex Mechanical Ltd", ""])
async def test_the_name_must_be_typed_exactly(typed):
    s = FakeSession(vendor=vendor())
    with pytest.raises(vd.VendorConfirmMismatch) as e:
        await apply(s, confirm=typed)
    assert e.value.confirm_with == "Apex Mechanical"
    assert s.writes() == [] and s.commits == 0


async def test_spacing_and_accent_encoding_do_not_matter_to_the_confirmation():
    name = unicodedata.normalize("NFC", "Café  Services ")
    s = FakeSession(vendor=vendor(name=name))
    out = await apply(s, confirm=unicodedata.normalize("NFD", " Café Services"))
    assert out["applied"] is True and out["confirm_with"] == "Café Services"


@pytest.mark.parametrize("row,expected", [
    (vendor(name="  ", code="APX-7"), "APX-7"),
    (vendor(name=None, code=None), APEX),
])
async def test_a_vendor_with_no_name_is_confirmed_by_its_code_then_its_id(row, expected):
    s = FakeSession(vendor=row)
    out = await vd.run(s, organization_id=ORG, vendor_id=APEX, apply=False)
    assert out["confirm_with"] == expected


async def test_what_the_admin_saw_must_still_hold():
    # They saw "remove"; an invoice arrived since. Deleting the row now would orphan it.
    s = FakeSession(vendor=vendor(), counts=counting((("kept", "invoice_lines"), 1)))
    with pytest.raises(vd.VendorPlanChanged) as e:
        await apply(s, expect="remove")
    assert e.value.plan["mode"] == "retire"
    assert e.value.plan["groups"]["kept"] == [
        {"key": "invoice_lines", "label": "Invoice lines", "rows": 1}]
    assert s.writes() == [] and s.commits == 0


async def test_a_vendor_someone_else_is_changing_is_busy():
    s = FakeSession(vendor=vendor(), busy=True)
    with pytest.raises(vd.VendorBusy):
        await apply(s)
    assert s.writes() == [] and s.commits == 0


# ── a refusal partway undoes everything ─────────────────────────────────────────────────

async def test_a_refused_statement_undoes_everything_and_names_the_step():
    s = FakeSession(vendor=vendor(), fail_on="UPDATE plenum_cafm.approvals_queue_items")
    with pytest.raises(vd.VendorDeleteFailed) as e:
        await apply(s)
    assert e.value.step == "approvals_queue_items" and e.value.label == "Pending approvals"
    assert s.commits == 0 and s.rollbacks >= 1 and s.added == []
    # It got as far as the assets, which the rollback undoes.
    assert any(q.startswith("UPDATE plenum_cafm.assets") for q in s.writes())


async def test_a_delete_that_cannot_be_recorded_is_undone():
    s = FakeSession(vendor=vendor(), trail_fails=True)
    with pytest.raises(vd.VendorNotRecorded):
        await apply(s)
    assert s.commits == 0 and s.rollbacks >= 1
    assert any(q.startswith("DELETE FROM plenum_cafm.vendors") for q in s.writes())


async def test_a_reference_that_appears_mid_delete_stops_a_remove():
    # Counted clean, but by the time the row is gone an invoice names it: rolled back, and the
    # admin is shown the plan as it now stands.
    after = counting((("kept", "invoice_lines"), 1))
    s = FakeSession(vendor=vendor(), recount=after)
    with pytest.raises(vd.VendorPlanChanged):
        await apply(s)
    assert s.commits == 0 and s.rollbacks >= 1 and s.added == []
    assert any(q.startswith("DELETE FROM plenum_cafm.vendors") for q in s.writes())


async def test_a_retire_without_a_status_column_is_refused_before_any_write():
    s = FakeSession(schema=_plan_tests.without(shipped(), "vendors", "status"), vendor=vendor(),
                    counts=counting((("kept", "invoice_lines"), 1)))
    with pytest.raises(vd.VendorDeleteFailed) as e:
        await apply(s, expect="retire")
    assert e.value.step == "vendor" and s.writes() == []


# ── the writes ──────────────────────────────────────────────────────────────────────────

async def test_each_asset_is_recorded_before_it_loses_the_vendor():
    s = FakeSession(vendor=vendor())
    await apply(s)
    assert s.index("INSERT INTO plenum_cafm.audit_logs") < s.index("UPDATE plenum_cafm.assets")
    assert (s.index("UPDATE plenum_cafm.assets")
            < s.index("UPDATE plenum_cafm.approvals_queue_items"))


async def test_every_write_is_bound_to_this_vendor_and_this_company():
    s = FakeSession(vendor=vendor(), counts=counting((("kept", "invoice_lines"), 1)))
    await apply(s, expect="retire")
    for q, p in s.sql:
        if q.startswith(WRITES):
            assert p["vid"] == APEX and p["org"] == ORG, q


async def test_a_vendor_without_history_loses_its_row_and_nothing_is_archived():
    s = FakeSession(vendor=vendor())
    out = await apply(s)
    assert out["mode"] == "remove" and out["applied"] is True
    assert any(q.startswith("DELETE FROM plenum_cafm.vendors") for q in s.writes())
    assert not any(q.startswith(("UPDATE plenum_cafm.vendors",
                                 "UPDATE plenum_cafm.compliance_certificates")) for q in s.writes())
    assert s.commits == 1


async def test_a_vendor_with_history_is_marked_after_its_accreditation_is_archived():
    s = FakeSession(vendor=vendor(), counts=counting((("kept", "compliance_certificates"), 3)))
    out = await apply(s, expect="retire")
    assert out["mode"] == "retire"
    assert (s.index("UPDATE plenum_cafm.compliance_certificates")
            < s.index("UPDATE plenum_cafm.vendors"))
    assert not any(q.startswith("DELETE FROM plenum_cafm.vendors") for q in s.writes())
    marked = s.sql[s.index("UPDATE plenum_cafm.vendors")][1]["marked"]
    assert marked == "Apex Mechanical (deleted)"


async def test_the_mark_fits_a_shorter_name_column():
    shape = production_like()            # vendor_name varchar(200)
    name = "N" * 198
    s = FakeSession(schema=shape, vendor=vendor(name=name),
                    counts=counting((("kept", "invoice_lines"), 1), schema=shape))
    await apply(s, confirm=name, expect="retire")
    marked = s.sql[s.index("UPDATE plenum_cafm.vendors")][1]["marked"]
    assert marked == "N" * 190 + " (deleted)"


async def test_the_counts_reported_are_the_rows_written():
    rows = [{"id": f"a{i}", "name": f"Boiler {i}", "code": f"B-{i}"} for i in range(3)]
    s = FakeSession(vendor=vendor(),
                    rowcounts={sql("apply_sql", "unassigned", "assets"): 3},
                    items={sql("items_sql", "unassigned", "assets"): rows})
    out = await apply(s)
    assert out["groups"]["unassigned"] == [
        {"key": "assets", "label": "Assets", "rows": 3, "items": rows}]


# ── the trail event ─────────────────────────────────────────────────────────────────────

def _event(s):
    assert len(s.added) == 1, "exactly one trail event per delete"
    return s.added[0]


async def test_the_trail_event_records_the_vendor_as_it_was():
    assets = [{"id": f"a{i:03}", "name": f"Boiler {i}", "code": f"B-{i}"} for i in range(150)]
    wos = [{"id": "w1", "code": "WO-1", "title": "Fix boiler"}]
    approvals = [{"id": f"q{i}"} for i in range(120)]
    s = FakeSession(
        vendor=vendor(), counts=counting((("kept", "vendor_monthly_scorecards"), 6)),
        rowcounts={sql("apply_sql", "unassigned", "assets"): 150,
                   sql("apply_sql", "unassigned", "work_orders"): 1,
                   sql("apply_sql", "closed", "approvals_queue_items"): 120},
        items={sql("items_sql", "unassigned", "assets"): assets,
               sql("items_sql", "unassigned", "work_orders"): wos,
               sql("items_sql", "closed", "approvals_queue_items"): approvals})
    out = await apply(s, expect="retire", reason="  Merged into Apex Group  ")
    ev = _event(s)
    assert ev.action_type == "vendor.deleted" and ev.source_feature == "P"
    assert ev.actor == str(USER) and ev.organization_id == UUID(ORG)
    d = ev.detail
    # The original name and code: the row now carries the marked name and no code.
    assert d["vendor"] == {"id": APEX, "name": "Apex Mechanical", "code": "APEX",
                           "trade": "Mechanical", "block_state": "Clear"}
    assert d["mode"] == "retire" and d["reason"] == "Merged into Apex Group"
    assert d["by"] == {"id": str(USER), "name": "Hana Admin", "email": "hana@ex.com",
                       "role": "admin"}
    assert d["effects"]["unassigned"] == {"assets": 150, "work_orders": 1}
    assert d["effects"]["closed"] == {"approvals_queue_items": 120}
    assert d["effects"]["kept"] == {"vendor_monthly_scorecards": 6}
    assert d["assets"] == assets[:100] and d["work_orders"] == wos
    assert d["approvals"] == [f"q{i}" for i in range(100)]
    assert d["source"] == "person"
    assert out["audit_id"] == str(ev.id)
    # The plan shows fewer than the trail keeps.
    shown = out["groups"]["unassigned"][0]
    assert shown["rows"] == 150 and shown["items"] == assets[:50]


async def test_the_reason_goes_into_the_notes_of_what_it_closes_and_archives():
    s = FakeSession(vendor=vendor(), counts=counting((("kept", "invoice_lines"), 1)))
    await apply(s, expect="retire", reason="Contract ended " + "x" * 600)
    closed = s.sql[s.index("UPDATE plenum_cafm.approvals_queue_items")][1]
    assert closed["pm_note"].startswith("Closed: vendor deleted — Contract ended x")
    assert len(_event(s).detail["reason"]) == 500


async def test_who_deleted_it_is_recorded_even_when_their_details_cannot_be_read():
    s = FakeSession(vendor=vendor(), user_fails=True)
    out = await apply(s, role="superadmin")
    assert out["applied"] is True
    assert _event(s).detail["by"] == {"id": str(USER), "name": None, "email": None,
                                      "role": "superadmin"}
