"""Changing the vendor an asset is assigned to, from the Assets page's vendor drawer.

The vendor on an asset decides who receives its work-order, inspection and records emails —
sent for real — and whose scorecard its work counts towards. So the change is an admin's, it
stays inside the company, it refuses a blocked vendor and a vendor whose legacy id cannot be
attached, and it is written together with its audit entry or not at all (Hussain, 28 Sep 2026:
direct, confirmed, admins only).
"""
from __future__ import annotations

import json
import re
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from src.engines.energy import asset_vendor as av

ORG = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"
ASSET = "aaaaaaaa-0000-0000-0000-000000000001"
APEX = "bbbbbbbb-0000-0000-0000-000000000001"
MITIE = "bbbbbbbb-0000-0000-0000-000000000002"
USER = UUID("cccccccc-0000-0000-0000-000000000001")
UNBOUND = re.compile(r"(?<![:\w]):[A-Za-z_]\w*")


def vendor(vid, name, org=ORG, block="Clear", reason=None, status="active", trade="HVAC"):
    return {"id": vid, "vendor_name": name, "vendor_code": name[:4].upper(), "trade": trade,
            "accreditation": "SafeContractor", "block_state": block, "block_reason": reason,
            "phone": None, "status": status, "organization_id": org}


class _Nested:
    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        self.s.savepoints += 1
        return self

    async def __aexit__(self, exc_type, *exc):
        if exc_type:
            self.s.rolled_back += 1
        return False


class _Result:
    def __init__(self, rows, rowcount=1):
        self._rows, self.rowcount = rows, rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        r = self.first()
        return next(iter(r.values())) if r else None


class FakeDB:
    def __init__(self, vendors=None, asset_vendor=APEX, asset_org=ORG, col_type="uuid",
                 fail_on=None):
        self.vendors = {v["id"]: v for v in (vendors or [vendor(APEX, "Apex Mechanical"),
                                                          vendor(MITIE, "Mitie")])}
        self.asset = {"id": ASSET, "asset_name": "Boiler 1", "asset_code": "B-301-BOILER-01",
                      "vendor_id": asset_vendor, "organization_id": asset_org}
        self.col_type, self.fail_on = col_type, fail_on
        self.sql, self.params, self.writes = [], [], []
        self.savepoints = self.rolled_back = self.commits = 0

    def begin_nested(self):
        return _Nested(self)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def execute(self, clause, params=None):
        sql = str(clause.compile(dialect=postgresql.dialect()))
        p = params or {}
        self.sql.append(sql)
        self.params.append(p)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("relation does not exist")
        if sql.lstrip().startswith("UPDATE plenum_cafm.assets"):
            self.writes.append(("update", p))
            return _Result([], rowcount=1)
        if "INSERT INTO plenum_cafm.audit_logs" in sql:
            self.writes.append(("audit", p))
            return _Result([{"id": "dddddddd-0000-0000-0000-000000000001"}])
        if "information_schema.columns" in sql:
            return _Result([{"data_type": self.col_type}])
        if "FROM plenum_cafm.assets" in sql:
            return _Result([self.asset])
        if "FROM plenum_cafm.vendors v WHERE" in sql and "id::text = " in sql:
            v = self.vendors.get(p.get("vid"))
            return _Result([v] if v else [])
        if "FROM plenum_cafm.vendors v" in sql:
            return _Result([v for v in self.vendors.values() if v["organization_id"] == p.get("org")])
        if "FROM plenum_cafm.compliance_certificates" in sql:
            return _Result(list(getattr(self, "lapsed", [])))
        if "vendor_monthly_scorecards" in sql:
            return _Result([{"score": "82.5", "month": "2026-09-01"}])
        if "audit_logs" in sql:
            return _Result([])
        if "vendor_contacts" in sql:
            return _Result([{"email": "ops@apex.co.uk", "primary_flag": None}])
        return _Result([])


@pytest.fixture(autouse=True)
def _no_primary_probe(monkeypatch):
    """The is_primary probe caches its answer for the process; each test here stays out of it."""
    async def no(_s):
        return False
    monkeypatch.setattr(av.ai, "_has_is_primary", no)
    # assets.vendor_id's type is cached per process too; each test names its own database.
    monkeypatch.setattr(av, "_COL_TYPE", None)


def _no_unbound(db):
    for q in db.sql:
        assert not UNBOUND.findall(q), q


class TestTheDrawer:
    async def test_it_names_the_vendor_its_contact_score_and_the_choices(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor(MITIE, "Mitie"),
                             vendor("cccc", "Blocked Co", block="Blocked", reason="insurance lapsed"),
                             vendor("V-01", "Legacy Ltd"), vendor("dddddddd-0000-0000-0000-00000000000f", "Elsewhere", org=OTHER)])
        out = await av.vendor_view(db, asset_id=ASSET)
        _no_unbound(db)
        assert out["vendor"]["name"] == "Apex Mechanical" and out["vendor"]["block_state"] == "Clear"
        assert out["contacts"] == {"email": "ops@apex.co.uk", "candidates": []}
        assert out["score"] == {"score": 82.5, "month": "2026-09-01"}
        choices = {c["name"]: c for c in out["choices"]}
        assert "Elsewhere" not in choices, "another company's vendor is never offered"
        assert choices["Mitie"]["assignable"] is True
        assert choices["Apex Mechanical"]["current"] is True
        assert choices["Blocked Co"]["assignable"] is False and "insurance lapsed" in choices["Blocked Co"]["why"]
        assert choices["Legacy Ltd"]["assignable"] is False and "legacy id" in choices["Legacy Ltd"]["why"]

    async def test_an_asset_with_no_vendor_says_so(self):
        out = await av.vendor_view(FakeDB(asset_vendor=None), asset_id=ASSET)
        assert out["vendor"] is None and out["choices"]

    async def test_a_part_that_cannot_be_read_is_named_not_hidden(self):
        out = await av.vendor_view(FakeDB(fail_on="vendor_monthly_scorecards"), asset_id=ASSET)
        assert out["score"] is None and any("scorecard" in n for n in out["unreadable"])


class TestTheChange:
    async def test_the_change_and_its_audit_entry_are_written_together(self):
        db = FakeDB()
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin",
                                     note="contract moved to Mitie")
        _no_unbound(db)
        assert out["ok"] is True and out["changed"] is True
        assert out["vendor"] == {"id": MITIE, "name": "Mitie"}
        assert out["previous"] == {"id": APEX, "name": "Apex Mechanical"}
        assert [w[0] for w in db.writes] == ["update", "audit"]
        assert db.commits == 1
        update_sql = next(q for q in db.sql if q.lstrip().startswith("UPDATE plenum_cafm.assets"))
        assert "CAST(" in update_sql and "AS uuid)" in update_sql
        audit = db.writes[1][1]
        meta = json.loads(audit["meta"])
        assert audit["action"] == "asset.vendor_changed" and audit["o"] == ORG and audit["u"] == str(USER)
        assert meta["from"]["name"] == "Apex Mechanical" and meta["to"]["name"] == "Mitie"
        assert meta["note"] == "contract moved to Mitie"

    async def test_if_the_audit_entry_cannot_be_written_nothing_changes(self):
        db = FakeDB(fail_on="INSERT INTO plenum_cafm.audit_logs")
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin")
        assert out == {"ok": False, "reason": "not_recorded",
                       "error": "Not changed — the change could not be recorded in the audit log, so it was rolled back."}
        assert db.rolled_back == 1 and db.commits == 0

    async def test_only_an_admin_changes_it(self):
        db = FakeDB()
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="user")
        assert out["reason"] == "not_admin" and not db.writes

    async def test_a_blocked_vendor_is_refused_with_its_reason(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor(MITIE, "Mitie", block="Blocked", reason="insurance lapsed")])
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin")
        assert out["reason"] == "blocked" and "insurance lapsed" in out["error"] and not db.writes

    async def test_another_companys_vendor_is_not_found(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor(MITIE, "Mitie", org=OTHER)])
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin")
        assert out["reason"] == "vendor_not_found" and not db.writes

    async def test_a_legacy_vendor_id_cannot_be_attached_to_a_uuid_column(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor("V-01", "Legacy Ltd")])
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id="V-01", user_id=USER, role="admin")
        assert out["reason"] == "legacy_id" and not db.writes

    async def test_a_text_column_takes_a_legacy_id(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor("V-01", "Legacy Ltd")], col_type="character varying")
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id="V-01", user_id=USER, role="admin")
        assert out["changed"] is True
        update_sql = next(q for q in db.sql if q.lstrip().startswith("UPDATE plenum_cafm.assets"))
        assert "AS uuid" not in update_sql

    async def test_the_same_vendor_again_changes_nothing(self):
        db = FakeDB()
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=APEX, user_id=USER, role="admin")
        assert out == {"ok": True, "changed": False, "vendor": {"id": APEX, "name": "Apex Mechanical"}}
        assert not db.writes

    async def test_an_inactive_vendor_is_refused(self):
        db = FakeDB(vendors=[vendor(APEX, "Apex Mechanical"), vendor(MITIE, "Mitie", status="inactive")])
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin")
        assert out["reason"] == "inactive" and not db.writes

    async def test_an_overlong_note_is_cut_not_refused(self):
        db = FakeDB()
        await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin", note="x" * 900)
        assert len(json.loads(db.writes[1][1]["meta"])["note"]) == 500



# ── Same trade only (Hussain, 29 Sep 2026: "I should only see the vendors with the same trade
# as the current one, not all") ───────────────────────────────────────────────────────────

def _uuid(n):
    return "eeeeeeee-0000-0000-0000-%012d" % n


TRADES = [vendor(APEX, "Apex Mechanical", trade="Mechanical"),
          vendor(_uuid(1), "Mitie", trade="Multi-trade"),
          vendor(_uuid(2), "Coolair", trade="Mechanical Services"),
          vendor(_uuid(3), "Sparks", trade="Electrical"),
          vendor(_uuid(4), "M&E Co", trade="Mechanical & Electrical"),
          vendor(_uuid(5), "Nameless", trade=None)]


class TestSameTradeOnly:
    async def test_the_picker_lists_the_current_vendors_trade_only(self):
        out = await av.vendor_view(FakeDB(vendors=TRADES), asset_id=ASSET)
        assert [c["name"] for c in out["choices"]] == ["Apex Mechanical", "Coolair", "M&E Co"]
        assert out["trade_filter"] == {"trade": "Mechanical", "applied": True, "hidden": 3}

    async def test_a_current_vendor_with_no_trade_lists_everyone_and_says_so(self):
        vs = [vendor(APEX, "Apex Mechanical", trade=None)] + TRADES[1:]
        out = await av.vendor_view(FakeDB(vendors=vs), asset_id=ASSET)
        assert len(out["choices"]) == 6
        assert out["trade_filter"] == {"trade": None, "applied": False, "hidden": 0}

    async def test_an_asset_with_no_vendor_lists_everyone(self):
        out = await av.vendor_view(FakeDB(vendors=TRADES, asset_vendor=None), asset_id=ASSET)
        assert len(out["choices"]) == 6 and out["trade_filter"]["applied"] is False

    async def test_a_vendor_of_another_trade_is_refused_on_the_write_too(self):
        db = FakeDB(vendors=TRADES)
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=_uuid(3), user_id=USER, role="admin")
        assert out["reason"] == "different_trade" and not db.writes
        assert out["error"] == ("Sparks's trade (Electrical) is not Apex Mechanical's (Mechanical). "
                                "Only a vendor of the same trade can take over this asset.")

    async def test_a_vendor_of_the_same_trade_is_accepted(self):
        db = FakeDB(vendors=TRADES)
        out = await av.change_vendor(db, asset_id=ASSET, vendor_id=_uuid(2), user_id=USER, role="admin")
        assert out["changed"] is True

    def test_trades_match_on_their_names_not_their_filler(self):
        assert av.same_trade("Mechanical", "Mechanical Services")
        assert av.same_trade("Mechanical & Electrical", "electrical")
        assert av.same_trade("HVAC", "hvac")
        assert not av.same_trade("Mechanical Services", "Electrical Services")
        assert not av.same_trade("Mechanical", None)



class TestTheReviewOf29Sep:
    """Pre-push review, 29 Sep 2026."""

    async def test_a_vendor_whose_certificate_lapsed_is_blocked_as_compliance_counts_it(self):
        # Compliance counts a vendor with any lapsed certificate as Blocked "so counts stay
        # accurate between scans"; block_state only moves when a scan or an upload runs. The
        # drawer offered such a vendor as assignable, and PATCH took it.
        db = FakeDB()
        db.lapsed = [{"vid": MITIE, "type": "Gas Safe"}]
        out = await av.vendor_view(db, asset_id=ASSET, organization_id=ORG)
        mitie = next(c for c in out["choices"] if c["id"] == MITIE)
        assert mitie["assignable"] is False
        assert mitie["why"] == "blocked — Gas Safe certificate lapsed"
        res = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin",
                                     organization_id=ORG)
        assert res["reason"] == "blocked"
        assert "Gas Safe certificate has lapsed" in res["error"]
        assert not db.writes

    async def test_a_change_waits_when_the_accreditations_cannot_be_checked(self):
        db = FakeDB(fail_on="FROM plenum_cafm.compliance_certificates")
        res = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin",
                                     organization_id=ORG)
        assert res["reason"] == "unchecked" and not db.writes
        out = await av.vendor_view(db, asset_id=ASSET, organization_id=ORG)
        assert "the vendors' accreditations" in out["unreadable"]

    async def test_an_asset_of_another_company_is_not_found_whatever_building_it_sits_in(self):
        db = FakeDB(asset_org=OTHER)
        assert (await av.vendor_view(db, asset_id=ASSET, organization_id=ORG))["reason"] == "not_found"
        res = await av.change_vendor(db, asset_id=ASSET, vendor_id=MITIE, user_id=USER, role="admin",
                                     organization_id=ORG)
        assert res["reason"] == "not_found" and not db.writes

    async def test_only_an_admin_is_given_the_list_to_choose_from(self):
        db = FakeDB()
        out = await av.vendor_view(db, asset_id=ASSET, organization_id=ORG, include_choices=False)
        assert out["choices"] == [] and out["vendor"]["name"] == "Apex Mechanical"
        assert not any("FROM plenum_cafm.vendors v\n     WHERE" in q or "LIMIT 500" in q for q in db.sql)

    async def test_the_last_change_time_says_it_is_utc(self):
        from datetime import datetime
        db = FakeDB()
        orig = db.execute

        async def execute(clause, params=None):
            sql = str(clause.compile(dialect=postgresql.dialect()))
            if "FROM plenum_cafm.audit_logs" in sql:
                return _Result([{"created_at": datetime(2026, 9, 29, 12, 56, 22), "email": "a@x.com",
                                 "metadata": json.dumps({"from": {"name": "Apex"}, "to": {"name": "Mitie"}})}])
            return await orig(clause, params)

        db.execute = execute
        out = await av.vendor_view(db, asset_id=ASSET, organization_id=ORG)
        assert out["last_change"]["at"] == "2026-09-29T12:56:22+00:00"


    async def test_the_lapsed_rule_is_the_compliance_pages_own(self):
        # Re-review, 29 Sep 2026: vendor accreditation is any scope but "building", compared
        # case-blind, and archived / test-fixture / superseded certificates never count.
        sql = " ".join(av._LAPSED.split())
        assert "lower(COALESCE(to_jsonb(c)->>'cert_scope', '')) <> 'building'" in sql
        for flag in ("superseded_duplicate", "archived", "a1_test_fixture"):
            assert f"to_jsonb(c)->'raw_metadata'->>'{flag}'" in sql, flag
