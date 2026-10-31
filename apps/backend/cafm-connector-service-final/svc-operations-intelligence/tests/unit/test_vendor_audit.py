"""The vendor audit trail: which vendor changed, how, when, and who or what changed it.

Hussain, 29 Sep 2026: a Vendor audit trail beside the Ingestion audit trail — asset
reassignments plus compliance blocks and clears. Reassignments are already recorded in
audit_logs by the vendor drawer (engines/energy/asset_vendor.py). Blocks and clears were
recorded nowhere: the scan overwrote block_reason and block_date on every run and a clear
nulled them, so a block left no history. They are now appended to ops_audit_log (append-only
by trigger) — once per change of state, never once per scan — and a failed record never
stops the block itself.

No database: a fake session routes by SQL text.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from src.engines.auth import vendor_audit as va
from src.engines.compliance import scan

ORG = "11111111-1111-1111-1111-111111111111"
V1 = "aaaaaaaa-0000-0000-0000-000000000001"


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeDB:
    """Answers by SQL substring; records every statement and every ORM row added."""

    def __init__(self, routes=None, flush_error=None, fail_on=None):
        self.routes = routes or {}
        self.statements: list[tuple[str, dict]] = []
        self.added: list = []
        self.flush_error = flush_error
        self.fail_on = fail_on

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append((sql, dict(params or {})))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("relation does not exist")
        for key, rows in self.routes.items():
            if key in sql:
                return _Result(rows)
        return _Result([])

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        if self.flush_error:
            self.added.clear()
            raise self.flush_error


def _audits(db):
    return [r for r in db.added if type(r).__name__ == "OpsAuditLog"]


# ── blocks and clears are recorded once per change ─────────────────────────────────────


def _vendor_state(state):
    return {"FROM plenum_cafm.vendors": [{"block_state": state, "vendor_name": "Coolair",
                                          "organization_id": ORG}]}


def test_a_vendor_blocked_for_the_first_time_is_recorded():
    db = FakeDB(_vendor_state("Clear"))
    ok = asyncio.run(scan._set_vendor_block(db, V1, accreditation_type="Gas Safe",
                                            reason="Accreditation lapsed: Gas Safe",
                                            certificate_id="c-1"))
    assert ok is True
    assert any("SET block_state = 'Blocked'" in s for s, _ in db.statements)
    [row] = _audits(db)
    assert row.action_type == va.BLOCKED == "vendor.blocked"
    assert row.actor == "system:compliance_scan"
    assert row.organization_id == UUID(ORG)
    assert row.detail == {"vendor": {"id": V1, "name": "Coolair"},
                          "reason": "Accreditation lapsed: Gas Safe", "accreditation_type": "Gas Safe",
                          "source": "compliance_scan", "certificate_id": "c-1"}


def test_a_vendor_already_blocked_is_not_recorded_again_by_the_next_scan():
    db = FakeDB(_vendor_state("Blocked"))
    assert asyncio.run(scan._set_vendor_block(db, V1, accreditation_type="Gas Safe", reason="x")) is True
    assert _audits(db) == []


def test_a_block_whose_record_fails_still_blocks():
    db = FakeDB(_vendor_state("Clear"), flush_error=RuntimeError("ops_audit_log missing"))
    assert asyncio.run(scan._set_vendor_block(db, V1, accreditation_type="Gas Safe", reason="x")) is True
    assert any("SET block_state = 'Blocked'" in s for s, _ in db.statements)


def test_a_lifted_block_is_recorded_with_what_lifted_it():
    db = FakeDB({"SET block_state = 'Clear'": [{"vendor_name": "Coolair", "organization_id": ORG}]})
    lifted = asyncio.run(scan._clear_vendor_block(db, V1, accreditation_type="Gas Safe",
                                                  enqueue_confirm=False, source="certificate_superseded"))
    assert lifted is True
    [row] = _audits(db)
    assert row.action_type == va.CLEARED == "vendor.block_cleared"
    assert row.actor == "system:certificate_superseded"
    assert row.detail["vendor"] == {"id": V1, "name": "Coolair"}
    assert row.detail["source"] == "certificate_superseded"


def test_a_vendor_that_was_not_blocked_records_no_clear():
    db = FakeDB({})
    assert asyncio.run(scan._clear_vendor_block(db, V1, enqueue_confirm=False)) is False
    assert _audits(db) == []


def test_a_legacy_vendor_id_is_recorded_as_it_is():
    db = FakeDB(_vendor_state("Clear"))
    asyncio.run(scan._set_vendor_block(db, "V-01", accreditation_type="Gas Safe", reason="x"))
    assert _audits(db)[0].detail["vendor"]["id"] == "V-01"


# ── the trail reads both records ───────────────────────────────────────────────────────

AT_1 = datetime(2026, 9, 28, 14, 5, tzinfo=timezone.utc)
AT_2 = datetime(2026, 9, 29, 9, 30, tzinfo=timezone.utc)
AT_3 = datetime(2026, 9, 29, 11, 0, tzinfo=timezone.utc)

REASSIGN = {
    "id": "r1", "created_at": AT_1, "user_id": "u1",
    "actor_name": "Aasim Shaik", "actor_email": "aasim@plenum-tech.com", "actor_role": "admin",
    "building_id": "b1", "building_name": "Bishopsgate Tower",
    "metadata": json.dumps({
        "asset": {"id": "a1", "name": "Boiler 1", "code": "B-301-BOILER-01"},
        "from": {"id": "v0", "name": "Apex Mechanical"}, "to": {"id": V1, "name": "Coolair"},
        "note": "contract moved", "by_role": "admin"}),
}
BLOCK = {"id": "o1", "created_at": AT_2, "action_type": "vendor.blocked", "actor": "system:compliance_scan",
         "detail": {"vendor": {"id": V1, "name": "Coolair"}, "reason": "Accreditation lapsed: Gas Safe",
                    "accreditation_type": "Gas Safe", "source": "compliance_scan", "certificate_id": "c-1"}}
CLEAR = {"id": "o2", "created_at": AT_3, "action_type": "vendor.block_cleared",
         "actor": "system:certificate_superseded",
         "detail": json.dumps({"vendor": {"id": V1, "name": "Coolair"}, "accreditation_type": "Gas Safe",
                               "source": "certificate_superseded"})}


def _trail(routes=None, fail_on=None):
    db = FakeDB(routes if routes is not None else {"FROM plenum_cafm.audit_logs": [REASSIGN],
                                                   "FROM plenum_cafm.ops_audit_log": [BLOCK, CLEAR]},
                fail_on=fail_on)
    return asyncio.run(va.list_events(db, organization_id=UUID(ORG))), db


def test_the_trail_is_every_change_newest_first():
    out, db = _trail()
    assert out["ok"] is True and out["unreadable"] == []
    assert [e["kind"] for e in out["events"]] == ["cleared", "blocked", "reassigned"]
    assert all(p.get("org") == ORG for _s, p in db.statements)


def test_a_reassignment_says_what_moved_from_whom_to_whom_and_who_moved_it():
    out, _ = _trail()
    e = out["events"][-1]
    assert e["kind"] == "reassigned" and e["at"] == AT_1.isoformat()
    assert e["actor"] == {"id": "u1", "name": "Aasim Shaik", "email": "aasim@plenum-tech.com", "role": "admin"}
    assert e["source"] == "person"
    assert e["asset"] == {"id": "a1", "name": "Boiler 1", "code": "B-301-BOILER-01"}
    assert e["from_vendor"] == {"id": "v0", "name": "Apex Mechanical"}
    assert e["to_vendor"] == e["vendor"] == {"id": V1, "name": "Coolair"}
    assert e["building"] == {"id": "b1", "name": "Bishopsgate Tower"}
    assert e["note"] == "contract moved"


def test_a_block_and_a_clear_say_what_did_it_and_why():
    out, _ = _trail()
    clear, block = out["events"][0], out["events"][1]
    assert block["kind"] == "blocked" and block["actor"] is None and block["source"] == "compliance_scan"
    assert block["vendor"] == {"id": V1, "name": "Coolair"}
    assert block["reason"] == "Accreditation lapsed: Gas Safe" and block["accreditation"] == "Gas Safe"
    assert block["certificate_id"] == "c-1"
    assert clear["kind"] == "cleared" and clear["source"] == "certificate_superseded"


def test_a_record_that_cannot_be_read_is_named_and_the_other_still_shows():
    out, _ = _trail(fail_on="FROM plenum_cafm.ops_audit_log")
    assert out["ok"] is True
    # The same read carries deletes (12 Oct 2026), so the name says they are missing too.
    assert out["unreadable"] == ["blocks, clears, deletions and building assignments"]
    assert [e["kind"] for e in out["events"]] == ["reassigned"]


def test_a_note_that_is_not_json_does_not_hide_the_row():
    bad = dict(REASSIGN, metadata="not json")
    out, _ = _trail({"FROM plenum_cafm.audit_logs": [bad], "FROM plenum_cafm.ops_audit_log": []})
    [e] = out["events"]
    assert e["kind"] == "reassigned" and e["asset"] is None and e["actor"]["name"] == "Aasim Shaik"


@pytest.mark.parametrize("limit", [0, -5])
def test_a_limit_below_one_still_reads_one(limit):
    db = FakeDB({})
    asyncio.run(va.list_events(db, organization_id=UUID(ORG), limit=limit))
    assert all(p.get("lim") == 1 for _s, p in db.statements)


def test_the_building_is_joined_on_the_key_the_buildings_table_has():
    # 29 Sep 2026: the first read joined buildings on b.id, which plenum_cafm.buildings does
    # not have — every read failed with "column b.id does not exist" and Hussain's first
    # reassignment never showed. Its key is building_id, as every other query here joins it.
    sql = " ".join(va._REASSIGNMENTS.split())
    assert "b.id" not in sql.replace("b.building_id", "")
    assert "b.building_id::text = to_jsonb(a)->>'building_id'" in sql


# ── pre-push review, 29 Sep 2026 ────────────────────────────────────────────────────────


def test_a_time_stored_without_a_zone_is_sent_as_utc():
    # audit_logs.created_at is timestamp without time zone; sent bare, the browser read it as
    # local time and a reassignment showed an hour early in BST, out of order with the blocks.
    naive = dict(REASSIGN, created_at=datetime(2026, 9, 29, 12, 56, 22))
    out, _ = _trail({"FROM plenum_cafm.audit_logs": [naive], "FROM plenum_cafm.ops_audit_log": [BLOCK]})
    assert [e["at"] for e in out["events"]] == ["2026-09-29T12:56:22+00:00", "2026-09-29T09:30:00+00:00"]


def test_a_block_set_from_a_scan_is_not_recorded_by_the_call_itself():
    db = FakeDB(_vendor_state("Clear"))
    asyncio.run(scan._set_vendor_block(db, V1, accreditation_type="Gas Safe", reason="x", record=False))
    assert _audits(db) == []


def _states(*rows):
    return [{"id": V1, "block_state": st, "vendor_name": "Coolair", "organization_id": ORG,
             "block_reason": reason, "blocked_accreditation_type": acct} for st, reason, acct in rows]


def test_a_scan_records_each_vendor_once_by_where_it_ended():
    # A vendor with a lapsed and a valid certificate was blocked and cleared by every scan —
    # two entries a night. The scan now records the vendor's net change over the run.
    db = FakeDB()
    before = va.states_by_vendor(_states(("Clear", None, None)))
    after = va.states_by_vendor(_states(("Blocked", "Accreditation lapsed: Gas Safe", "Gas Safe")))
    assert asyncio.run(va.record_net_changes(db, before, after, source="compliance_scan")) == 1
    [row] = _audits(db)
    assert row.action_type == va.BLOCKED and row.actor == "system:compliance_scan"
    assert row.detail["reason"] == "Accreditation lapsed: Gas Safe"
    assert row.detail["accreditation_type"] == "Gas Safe"


def test_a_scan_that_ends_where_it_started_records_nothing():
    db = FakeDB()
    same = va.states_by_vendor(_states(("Blocked", "x", "Gas Safe")))
    assert asyncio.run(va.record_net_changes(db, same, same, source="compliance_scan")) == 0
    assert _audits(db) == []


def test_a_scan_that_lifted_a_block_says_which_accreditation_it_was():
    db = FakeDB()
    before = va.states_by_vendor(_states(("Blocked", "Accreditation lapsed: Gas Safe", "Gas Safe")))
    after = va.states_by_vendor(_states(("Clear", None, None)))
    asyncio.run(va.record_net_changes(db, before, after, source="compliance_scan"))
    [row] = _audits(db)
    assert row.action_type == va.CLEARED and row.detail["accreditation_type"] == "Gas Safe"


def test_the_vendor_cert_worker_reads_states_around_the_run_and_records_the_difference(monkeypatch):
    calls = []

    class Cert:
        def __init__(self, vid):
            self.vendor_id, self.id = vid, "c-" + vid[-1]
            self.country_code = self.certificate_type_code = self.cert_type = None
            self.expiry_date = None

    async def pack(*a, **k):
        return None

    async def ladder(session, cert, **kw):
        calls.append(kw.get("record"))
        return {"alerts_created": 0, "blocks_set": 0, "details": [], "adversaries": []}

    reads = iter([_states(("Clear", None, None)), _states(("Blocked", "Accreditation lapsed: Gas Safe", "Gas Safe"))])

    async def states(session, ids):
        assert ids == [V1]
        return va.states_by_vendor(next(reads))

    monkeypatch.setattr(scan, "get_pack_type", pack)
    monkeypatch.setattr(scan, "apply_vendor_cert_ladder", ladder)
    monkeypatch.setattr(va, "read_vendor_states", states)
    db = FakeDB()
    asyncio.run(scan._process_vendor_certs(db, [Cert(V1), Cert(V1)], organization_id=UUID(ORG)))
    assert calls == [False, False], "the per-certificate calls record nothing themselves"
    assert [r.action_type for r in _audits(db)] == [va.BLOCKED]


# ── deletes (Hussain, 12 Oct 2026: delete a vendor, recorded in the Vendor audit trail) ──
#
# A delete is one ops_audit_log entry, action vendor.deleted, written by the admin's delete
# (admin/vendor_delete.py) with a snapshot of who did it, or one per run by a data reset.

ADMIN = "cccccccc-0000-0000-0000-000000000001"
AT_4 = datetime(2026, 10, 12, 10, 15, tzinfo=timezone.utc)
EFFECTS = {"removed": {"vendor_contacts": 1}, "unassigned": {"assets.vendor_id": 1, "work_orders.vendor_id": 1},
           "closed": {"approvals_queue_items": 1}, "revoked": {}, "ended": {}, "archived": {},
           "kept": {"invoice_verifications": 4}}
DELETE = {"id": "o3", "created_at": AT_4, "action_type": "vendor.deleted", "actor": ADMIN,
          "actor_user_id": ADMIN, "actor_name": "H. Kalabhai (renamed since)", "actor_email": "old@plenum-tech.com",
          "actor_role": "superadmin",
          "detail": json.dumps({
              "vendor": {"id": V1, "name": "Coolair", "code": "COOL", "trade": "HVAC", "block_state": "Clear"},
              "mode": "retire", "reason": "Contract ended",
              "by": {"id": ADMIN, "name": "Hussain Kalabhai", "email": "hussain@plenum-tech.com", "role": "admin"},
              "effects": EFFECTS,
              "assets": [{"id": "a1", "name": "Boiler 1", "code": "B-301-BOILER-01"}],
              "work_orders": [{"id": "w1", "code": "WO-1001", "title": "Boiler service"}],
              "approvals": ["q1"], "source": "person"})}
RESET = {"id": "o4", "created_at": AT_4, "action_type": "vendor.deleted", "actor": ADMIN,
         "actor_name": None, "actor_email": None, "actor_role": None,
         "detail": {"mode": "reset", "count": 2, "source": "data_reset",
                    "vendors": [{"id": V1, "name": "Coolair", "code": "COOL"},
                                {"id": "V-01", "name": "Legacy Ltd", "code": None}],
                    "by": {"id": ADMIN, "name": "Hussain Kalabhai", "email": "hussain@plenum-tech.com",
                           "role": "admin"}}}


class _ActionFilteringDB(FakeDB):
    """Answers each read with only the rows its bound action(s) select, as the WHERE would — so
    an action the read does not ask for never reaches the trail."""

    async def execute(self, stmt, params=None):
        res = await super().execute(stmt, params)
        p = dict(params or {})
        if "actions" in p:
            res._rows = [r for r in res._rows if r.get("action_type") in p["actions"]]
        elif "action" in p:
            res._rows = [r for r in res._rows if r.get("action") == p["action"]]
        return res


def _filtered_trail(ops_rows, log_rows=()):
    db = _ActionFilteringDB({"FROM plenum_cafm.audit_logs": list(log_rows),
                             "FROM plenum_cafm.ops_audit_log": list(ops_rows)})
    return asyncio.run(va.list_events(db, organization_id=UUID(ORG))), db


def test_the_trail_reads_deletes_beside_blocks_and_clears():
    # Before 12 Oct 2026 the read asked for blocks and clears only, so a delete never showed.
    out, _ = _filtered_trail([BLOCK, CLEAR, DELETE])
    assert [e["kind"] for e in out["events"]] == ["deleted", "cleared", "blocked"]


def test_a_delete_says_who_deleted_which_vendor_how_and_what_it_changed():
    out, _ = _filtered_trail([DELETE])
    [e] = out["events"]
    assert e == {
        "id": "o:o3", "kind": "deleted", "at": "2026-10-12T10:15:00+00:00",
        # 12 Oct 2026 (review R2): the users row the actor names wins over the entry's own
        # snapshot - ops_audit_log takes inserts from generic writers, and a snapshot is free
        # JSON. The role at the time is the snapshot's, being the same person's.
        "actor": {"id": ADMIN, "name": "H. Kalabhai (renamed since)", "email": "old@plenum-tech.com",
                  "role": "admin"},
        "source": "person",
        "vendor": {"id": V1, "name": "Coolair", "code": "COOL"},
        "mode": "retire", "reason": "Contract ended", "effects": EFFECTS,
        "assets": [{"id": "a1", "name": "Boiler 1", "code": "B-301-BOILER-01"}],
        "work_orders": [{"id": "w1", "code": "WO-1001", "title": "Boiler service"}],
        "vendors": None, "count": None, "unavailable": [],
        "from_vendor": None, "to_vendor": None, "asset": None, "building": None, "note": None,
        "accreditation": None, "certificate_id": None,
    }


def test_a_delete_with_no_snapshot_names_its_admin_from_the_users_record():
    d = json.loads(DELETE["detail"])
    d.pop("by")
    d.pop("source")
    out, _ = _filtered_trail([dict(DELETE, detail=d)])
    [e] = out["events"]
    assert e["actor"] == {"id": ADMIN, "name": "H. Kalabhai (renamed since)", "email": "old@plenum-tech.com",
                          "role": "superadmin"}
    assert e["source"] == "person", "a delete with no source recorded is a person's"


def test_a_delete_by_a_user_no_longer_on_record_still_says_a_person_did_it():
    # No snapshot and no users row: the id the entry carries is all there is, and the page
    # says "A user no longer on record" rather than "Unknown person".
    d = {k: v for k, v in json.loads(DELETE["detail"]).items() if k != "by"}
    row = dict(DELETE, detail=d, actor_user_id=None, actor_name=None, actor_email=None,
               actor_role=None)
    [e] = _filtered_trail([row])[0]["events"]
    assert e["actor"] == {"id": ADMIN, "name": None, "email": None, "role": None}


def test_a_delete_with_no_one_on_record_has_no_actor():
    d = {k: v for k, v in json.loads(DELETE["detail"]).items() if k != "by"}
    row = dict(DELETE, actor="hoistra-ui", detail=d, actor_user_id=None, actor_name=None,
               actor_email=None, actor_role=None)
    [e] = _filtered_trail([row])[0]["events"]
    assert e["actor"] is None and e["source"] == "person"


JANE = "dddddddd-0000-0000-0000-000000000009"


def test_a_snapshot_naming_someone_else_cannot_put_their_name_on_a_delete():
    # 12 Oct 2026 (review R2): a row inserted through a generic writer could carry
    # by: {name: 'Jane Finance'} and the trail showed Jane deleting the vendor.
    d = dict(json.loads(DELETE["detail"]),
             by={"id": JANE, "name": "Jane Finance", "email": "jane@ex.com", "role": "admin"})
    [e] = _filtered_trail([dict(DELETE, detail=d)])[0]["events"]
    assert e["actor"] == {"id": ADMIN, "name": "H. Kalabhai (renamed since)",
                          "email": "old@plenum-tech.com", "role": "superadmin"}


def test_a_snapshot_is_used_only_for_the_actor_it_names_when_no_users_row_matches():
    forged = dict(DELETE, actor="x", actor_user_id=None, actor_name=None, actor_email=None,
                  actor_role=None)
    [e] = _filtered_trail([forged])[0]["events"]
    assert e["actor"] is None, "a snapshot for an id the entry does not carry names no one"
    gone = dict(DELETE, actor_user_id=None, actor_name=None, actor_email=None, actor_role=None)
    [e] = _filtered_trail([gone])[0]["events"]
    assert e["actor"] == {"id": ADMIN, "name": "Hussain Kalabhai",
                          "email": "hussain@plenum-tech.com", "role": "admin"}


def test_the_delete_read_says_whether_a_users_row_matched():
    assert "u.id::text AS actor_user_id" in " ".join(va._BLOCKS.split())


def test_a_data_reset_lists_the_vendors_it_deleted():
    [e] = _filtered_trail([RESET])[0]["events"]
    assert e["kind"] == "deleted" and e["source"] == "data_reset" and e["mode"] == "reset"
    assert e["vendor"] is None
    assert e["vendors"] == [{"id": V1, "name": "Coolair", "code": "COOL"},
                            {"id": "V-01", "name": "Legacy Ltd", "code": None}]
    assert e["count"] == 2
    assert e["actor"] == {"id": ADMIN, "name": "Hussain Kalabhai", "email": "hussain@plenum-tech.com",
                          "role": "admin"}
    assert (e["effects"], e["assets"], e["work_orders"]) == ({}, [], [])


def test_a_delete_entry_of_an_unexpected_shape_still_shows():
    d = {"vendor": {"id": V1, "name": "Coolair"}, "mode": "remove",
         "effects": ["not", "a", "dict"], "assets": "a1", "work_orders": [{"id": "w1"}, "junk"],
         "vendors": "Coolair", "count": True}
    [e] = _filtered_trail([dict(DELETE, detail=d)])[0]["events"]
    assert e["kind"] == "deleted" and e["vendor"] == {"id": V1, "name": "Coolair"}
    assert e["effects"] == {} and e["assets"] == [] and e["work_orders"] == [{"id": "w1"}]
    assert e["vendors"] is None and e["count"] is None


def test_an_action_the_trail_does_not_know_is_not_shown_as_a_lifted_block():
    # The mapper labelled every entry that was not a block "cleared": a new action added to the
    # read without its own mapping would have shown as "Block lifted — automatic".
    unknown = dict(CLEAR, id="o9", action_type="vendor.renamed")
    out, _ = _trail({"FROM plenum_cafm.audit_logs": [], "FROM plenum_cafm.ops_audit_log": [BLOCK, unknown]})
    assert [(e["id"], e["kind"]) for e in out["events"]] == [("o:o1", "blocked")]


def test_the_assets_a_delete_unassigned_are_not_listed_again_as_reassignments():
    # Each unassigned asset gets its own audit_logs row (its own history says so); the trail
    # shows the one delete event that lists them, not one row per asset beside it.
    unassigned = dict(REASSIGN, id="r2", action=va.UNASSIGNED)
    out, _ = _filtered_trail([DELETE], log_rows=[dict(REASSIGN, action=va.REASSIGNED), unassigned])
    assert [e["id"] for e in out["events"]] == ["o:o3", "r:r1"]


def test_the_delete_actor_is_joined_on_the_users_key_the_entry_carries():
    # ops_audit_log.actor is free text: str(user_id) for a person, "system:<source>" for a scan.
    sql = " ".join(va._BLOCKS.split())
    assert "LEFT JOIN plenum_cafm.users u ON u.id::text = o.actor" in sql
