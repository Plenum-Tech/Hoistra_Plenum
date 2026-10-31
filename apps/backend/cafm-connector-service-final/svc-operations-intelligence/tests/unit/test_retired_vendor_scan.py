"""The compliance scan, the block ladder and re-verification leave a retired vendor alone.

Hussain, 12 Oct 2026: a deleted vendor with history is RETIRED — its row stays, status
'deleted', and its vendor-scope certificates are archived. Before this, nothing in the
vendor stream looked at either: the nightly scan ran the ladder over every vendor
certificate (archived ones included, though the building stream has always skipped them),
so a retired vendor would be emailed a renewal, re-blocked, cleared, and given a trail entry
for each — for ever, by a cron.

No database: the fake routes by SQL text and answers the way Postgres would — a retired
vendor drops out only when the statement carries the live-vendor predicate, and the
deleted-ids subquery returns exactly the retired ids.
"""
from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace
from uuid import UUID

from src.engines.auth import vendor_audit as va
from src.engines.compliance import ccc_verify
from src.engines.compliance import resource_skills
from src.engines.compliance import reverify
from src.engines.compliance import scan
from src.shared import vendor_identity as vi

ORG = UUID("00000000-0000-0000-0000-000000000001")
OLD = UUID("aaaaaaaa-0000-0000-0000-000000000001")   # retired
NEW = UUID("bbbbbbbb-0000-0000-0000-000000000002")   # live

_LIVE = re.compile(re.escape(vi.live_vendor_sql("qq_alias")).replace("qq_alias", r"\w+"))
_DELETED_IDS = vi.deleted_vendor_ids_sql()


class _Result:
    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def mappings(self):
        return self

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class ScanDB:
    def __init__(self, vendors=(), certs=(), skills=()):
        self.vendors = {str(v["id"]): v for v in vendors}
        self.certs = list(certs)
        self.skills = list(skills)
        self.statements: list[tuple[str, dict]] = []
        self.added: list = []

    @property
    def retired(self) -> set[str]:
        return {k for k, v in self.vendors.items() if v.get("status") == vi.DELETED_STATUS}

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.statements.append((sql, p))
        live_only = bool(_LIVE.search(sql)) or (_DELETED_IDS in sql and "NOT IN" in sql)
        if _DELETED_IDS in sql and "NOT IN" not in sql:                 # retired ids asked for
            ids = {str(i) for i in p.get("ids", [])}
            return _Result([{"id": i} for i in sorted(self.retired & ids)])
        if "FROM plenum_cafm.resource_skills" in sql:
            return _Result([k for k in self.skills
                            if not (live_only and str(k.vendor_id) in self.retired)])
        if "FROM plenum_cafm.compliance_certificates" in sql:
            return _Result([c for c in self.certs
                            if not (live_only and str(c.vendor_id) in self.retired
                                    and str(c.cert_scope).lower() != "building")])
        if "plenum_cafm.vendors" not in sql:
            return _Result([])
        if "to_jsonb(v)->>'status') FROM" in sql:                         # is_deleted_vendor
            return _Result(scalar=(self.vendors.get(str(p.get("vid"))) or {}).get("status"))
        rows = [v for k, v in self.vendors.items()
                if k == str(p.get("vid")) and not (live_only and k in self.retired)]
        if sql.upper().startswith("UPDATE"):
            if "RETURNING" not in sql.upper():
                return _Result([])
            rows = [v for v in rows if v.get("block_state") == "Blocked"]
        return _Result(rows)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    def ran(self, needle: str) -> bool:
        return any(needle in s for s, _ in self.statements)


def vendor(vid, name, status="active", block_state="Clear"):
    return {"id": str(vid), "vendor_name": name, "status": status, "block_state": block_state,
            "organization_id": str(ORG), "email": "ops@acme.example"}


def retired(block_state="Clear"):
    return vendor(OLD, "Acme Lifts (deleted)", status="deleted", block_state=block_state)


# ── the vendor stream ────────────────────────────────────────────────────────────────────


def _cert(n, vid, scope="Vendor", **meta):
    return SimpleNamespace(id=UUID(int=n), vendor_id=vid, cert_scope=scope, raw_metadata=meta,
                           country_code="UK", certificate_type_code="GAS_SAFE",
                           cert_type="GAS_SAFE", expiry_date=date(2026, 1, 1), status=None,
                           days_to_expiry=None, insurance_risk_flag=False, updated_at=None,
                           organization_id=ORG)


async def test_the_vendor_stream_skips_retired_vendors_and_certificates_set_aside(monkeypatch):
    laddered = []

    async def pack(*a, **k):
        return None

    async def ladder(session, cert, **kw):
        laddered.append(cert.id.int)
        return {"alerts_created": 0, "blocks_set": 0, "details": [], "adversaries": []}

    async def states(session, ids):
        return {}

    monkeypatch.setattr(scan, "get_pack_type", pack)
    monkeypatch.setattr(scan, "apply_vendor_cert_ladder", ladder)
    monkeypatch.setattr(va, "read_vendor_states", states)
    db = ScanDB([retired(), vendor(NEW, "Brook Gas")])
    certs = [
        _cert(1, NEW),                                   # scanned
        _cert(2, OLD),                                   # its vendor was deleted
        _cert(3, NEW, archived=True),                    # archived, as the building stream skips
        _cert(4, NEW, superseded_duplicate=True),        # a renewal replaced it
        _cert(5, NEW, a1_test_fixture=True),             # seeded fixture
    ]
    wr = await scan._process_vendor_certs(db, certs, organization_id=ORG)
    assert laddered == [1]
    assert wr.scanned == 5


async def test_the_ladder_does_nothing_for_a_retired_vendor(monkeypatch):
    queued = []

    async def enqueue(session, **kw):
        queued.append(kw)
        return SimpleNamespace(id=UUID(int=9))

    monkeypatch.setattr(scan, "enqueue_approval", enqueue)
    db = ScanDB([retired()])
    out = await scan.apply_vendor_cert_ladder(
        db, _cert(2, OLD), life=SimpleNamespace(days_to_expiry=-5), organization_id=ORG,
        type_name="Gas Safe", renewal_url=None, source="certificate",
    )
    assert out == {"alerts_created": 0, "blocks_set": 0, "details": [], "adversaries": []}
    assert queued == [] and not db.ran("UPDATE plenum_cafm.vendors") and db.added == []


async def test_a_retired_vendor_is_never_blocked_and_gets_no_trail_entry():
    db = ScanDB([retired(block_state="Clear")])
    ok = await scan._set_vendor_block(db, OLD, accreditation_type="Gas Safe", reason="lapsed")
    assert ok is False
    assert db.added == [], "no vendor.blocked entry for a vendor that was deleted"


async def test_a_live_vendor_is_still_blocked():
    db = ScanDB([vendor(NEW, "Brook Gas")])
    assert await scan._set_vendor_block(db, NEW, accreditation_type="Gas Safe", reason="x") is True
    assert [type(r).__name__ for r in db.added] == ["OpsAuditLog"]


async def test_a_retired_vendors_old_block_is_never_cleared(monkeypatch):
    queued = []

    async def enqueue(session, **kw):
        queued.append(kw)
        return SimpleNamespace(id=UUID(int=9))

    monkeypatch.setattr("src.shared.approvals.enqueue_approval", enqueue)
    db = ScanDB([retired(block_state="Blocked")])
    assert await scan._clear_vendor_block(db, OLD, accreditation_type="Gas Safe") is False
    assert db.added == [] and queued == []


async def test_a_retired_vendors_contact_is_not_read():
    db = ScanDB([retired()])
    assert await scan._fetch_vendor_contact(db, OLD) == ("Vendor", None)


def _skill(vid):
    return SimpleNamespace(id=UUID(int=11), vendor_id=vid, operative_name="J. Smith",
                           skill_type_code="ACS_CARD", certificate_number="ACS-1",
                           expiry_date=None, status="Lapsed", days_to_expiry=-3)


async def test_lapsed_operatives_of_a_retired_vendor_are_not_flagged():
    db = ScanDB([retired(), vendor(NEW, "Brook Gas")], skills=[_skill(OLD), _skill(NEW)])
    out = await resource_skills.lapsed_operative_flags_for_vendors(db, [OLD, NEW])
    assert list(out) == [str(NEW)]


# ── re-verification ──────────────────────────────────────────────────────────────────────


async def test_reverification_skips_retired_vendors_and_archived_certificates(monkeypatch):
    checked = []

    async def verify(session, cert_id):
        checked.append(cert_id.int)
        return {"ok": True, "status": "verified", "verified": True}

    monkeypatch.setattr(ccc_verify, "verify_stored_certificate", verify)
    db = ScanDB(
        [retired(), vendor(NEW, "Brook Gas")],
        certs=[
            _cert(1, OLD),                                 # the retired vendor's accreditation
            _cert(2, NEW, scope="Building", archived=True),
            _cert(3, OLD, scope="Building"),               # a building's own certificate stays
            _cert(4, NEW),
        ],
    )
    for c in db.certs:
        c.expiry_date, c.status = date(2027, 6, 30), "Current"
    out = await reverify.reverify_due_certificates(db, organization_id=ORG)
    assert checked == [3, 4]
    assert out["due_count"] == 2


async def test_the_dashboard_auto_verify_skips_them_too(monkeypatch):
    checked = []

    async def verify(session, cert_id):
        checked.append(cert_id.int)
        return {"ok": True, "status": "verified", "verified": True}

    monkeypatch.setattr(ccc_verify, "verify_stored_certificate", verify)
    db = ScanDB(
        [retired(), vendor(NEW, "Brook Gas")],
        certs=[_cert(1, OLD), _cert(2, NEW, archived=True), _cert(4, NEW)],
    )
    for c in db.certs:
        c.expiry_date, c.status = date(2027, 6, 30), "Current"
    out = await reverify.auto_verify_listed_certificates(db, organization_id=ORG)
    assert checked == [4] and out["processed"] == 1
