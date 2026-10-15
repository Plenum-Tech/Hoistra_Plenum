"""A retired vendor is never matched, re-used, offered or recommended again.

Hussain, 12 Oct 2026: deleting a vendor that has history RETIRES it — the row stays so its
past records still resolve, status becomes 'deleted' and the name carries " (deleted)". A
supplier named again on a later document is a NEW vendor (re-onboarding), never the
retired row brought back to life.

The name mark already stops an exact match ("Acme Lifts" is not "Acme Lifts (deleted)"),
but every fuzzy lookup — an ILIKE '%name%', a first-word candidate list, a register search
— still finds the marked row, and a row retired without its mark (the status is the
authority, not the name) would be found by all of them. Each test here therefore keeps the
retired row's plain name where that is the sharper case.

No database: the fake register answers the way Postgres would — it drops a retired row
only when the statement carries the live-vendor predicate from shared/vendor_identity.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.engines.compliance import certificates as certs_mod
from src.engines.compliance import contractors
from src.engines.compliance import coverage
from src.engines.compliance import verification
from src.shared import vendor_identity as vi

ORG = UUID("00000000-0000-0000-0000-000000000001")
OLD = "aaaaaaaa-0000-0000-0000-000000000001"   # the retired row
NEW = "bbbbbbbb-0000-0000-0000-000000000002"   # a live row

_LIVE = re.compile(re.escape(vi.live_vendor_sql("qq_alias")).replace("qq_alias", r"\w+"))


def filters_retired(sql: str) -> bool:
    """Whether this statement asks Postgres to leave retired vendors out."""
    return bool(_LIVE.search(sql)) or vi.deleted_vendor_ids_sql() in sql


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

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

    async def __aexit__(self, *a):
        return False


class Register:
    """The vendor register, answering name lookups by their bound parameters."""

    def __init__(self, vendors, *, fail_on: str | None = None):
        self.vendors = vendors
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        p = dict(params or {})
        self.calls.append((sql, p))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("column does not exist")
        if sql.upper().startswith(("INSERT", "UPDATE")) or "plenum_cafm.vendors" not in sql:
            return _Result([])
        rows = [v for v in self.vendors
                if not (filters_retired(sql) and v.get("status") == vi.DELETED_STATUS)]
        if "name" in p:                       # find_vendor_id exact / registration check
            rows = [v for v in rows if _norm(v["vendor_name"]) == _norm(p["name"])
                    or ("like" in p and _norm(p["like"]).strip("%") in _norm(v["vendor_name"]))]
        elif "first" in p and "q" not in p:   # find_vendor_id candidates
            rows = [v for v in rows if _norm(p["first"]).strip("%") in _norm(v["vendor_name"])]
        elif "like" in p:                     # resolve_or_create_vendor substring fallback
            rows = [v for v in rows if _norm(p["like"]).strip("%") in _norm(v["vendor_name"])]
        elif "q" in p:                        # link-candidate search
            rows = [v for v in rows if _norm(p["first"]).strip("%") in _norm(v["vendor_name"])]
            rows = [{"id": v["id"], "name": v["vendor_name"], "code": None} for v in rows]
        return _Result(rows)

    async def scalar(self, stmt, params=None):
        return None

    def inserts(self):
        return [p for s, p in self.calls if s.upper().startswith("INSERT INTO PLENUM_CAFM.VENDORS")]

    def updates(self):
        return [(s, p) for s, p in self.calls if s.upper().startswith("UPDATE PLENUM_CAFM.VENDORS")]


def retired(name, vid=OLD):
    return {"id": vid, "vendor_name": name, "status": "deleted", "organization_id": str(ORG)}


def live(name, vid=NEW):
    return {"id": vid, "vendor_name": name, "status": "active", "organization_id": str(ORG)}


# ── find_vendor_id / vendor_named_in ─────────────────────────────────────────────────────


async def test_the_exact_name_of_a_retired_vendor_resolves_to_nothing():
    reg = Register([retired("Acme Lifts")])
    assert await vi.find_vendor_id(reg, "Acme Lifts", organization_id=ORG) is None


async def test_the_normalised_name_of_a_retired_vendor_resolves_to_nothing():
    # Exact misses ("Gough & Kelly Limited" vs "Gough and Kelly Ltd"), so the first-word
    # candidate list is read — and it held the retired row.
    reg = Register([retired("Gough and Kelly Ltd")])
    assert await vi.find_vendor_id(reg, "Gough & Kelly Limited", organization_id=ORG) is None


async def test_a_live_vendor_of_the_same_name_wins_over_the_retired_one():
    # The retired row is older, so the earliest-first ordering used to pick it.
    reg = Register([retired("Acme Lifts"), live("Acme Lifts")])
    assert await vi.find_vendor_id(reg, "Acme Lifts", organization_id=ORG) == NEW


async def test_a_document_naming_a_retired_vendor_is_attributed_to_no_one():
    reg = Register([retired("Halden Building Services")])
    out = await vi.vendor_named_in(reg, "VAT INVOICE Halden Building Services Manchester",
                                   organization_id=ORG)
    assert out is None


# ── contractors ──────────────────────────────────────────────────────────────────────────


async def test_a_supplier_named_again_after_its_vendor_was_retired_is_registered_afresh():
    # The ILIKE fallback: "%Acme Lifts%" matches "Acme Lifts (deleted)" — the mark alone does
    # not stop it, so the new contract would have attached itself to the retired row.
    reg = Register([retired("Acme Lifts (deleted)")])
    out = await contractors.resolve_or_create_vendor(
        reg, company_name="Acme Lifts", organization_id=ORG, create_if_missing=True,
    )
    assert out is not None and str(out) != OLD
    [ins] = reg.inserts()
    assert ins["name"] == "Acme Lifts" and ins["org"] == str(ORG)


async def test_the_substring_fallback_does_not_reach_a_retired_vendor_even_when_not_creating():
    reg = Register([retired("Acme Lifts Holdings")])
    out = await contractors.resolve_or_create_vendor(
        reg, company_name="Acme Lifts", organization_id=ORG, create_if_missing=False,
    )
    assert out is None


def _contractor(vid, name, status):
    return {"id": vid, "vendor_name": name, "status": status, "vendor_code": None,
            "block_state": "Clear", "blocked_accreditation_type": None,
            "certificate_type_code": "GAS_SAFE", "expiry_date": None, "days_to_expiry": 200,
            "contact_email": f"{vid[:4]}@example.com"}


@pytest.mark.parametrize("fail_first", [False, True], ids=["with-contact", "fallback"])
async def test_a_retired_vendor_is_never_recommended(monkeypatch, fail_first):
    monkeypatch.setattr(contractors, "_IS_PRIMARY", False)
    reg = Register(
        [_contractor(OLD, "Acme Lifts (deleted)", "deleted"),
         _contractor(NEW, "Brook Gas", "active")],
        fail_on="vendor_contacts vc" if fail_first else None,
    )
    out = await contractors.recommend_contractors(
        reg, required_accreditation="GAS_SAFE", organization_id=ORG,
    )
    assert [r["vendor_id"] for r in out] == [NEW]


# ── certificates: the create-vendor form and the link candidates ─────────────────────────


class _CertSession(Register):
    def __init__(self, vendors, cert):
        super().__init__(vendors)
        self.cert = cert

    async def get(self, model, key):
        return self.cert

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def refresh(self, obj):
        return None


def _cert(**kw):
    base = dict(id=UUID(int=7), organization_id=ORG, org_id=ORG, raw_metadata={},
                document_id=None, vendor_id=None, cert_scope="Vendor",
                certificate_type_code="GAS_SAFE", cert_type="GAS_SAFE", building_name=None,
                site_id=None, site_ref=None, asset_id=None)
    base.update(kw)
    return SimpleNamespace(**base)


async def test_the_create_vendor_form_never_re_uses_a_retired_vendor():
    cert = _cert()
    s = _CertSession([retired("Acme Lifts")], cert)
    out = await certs_mod.create_vendor_for_certificate(s, cert.id, vendor_name="Acme Lifts")
    assert out["ok"] is True and out["created"] is True
    assert out["vendor_id"] != OLD and cert.vendor_id == out["vendor_id"]
    assert len(s.inserts()) == 1


async def test_the_create_vendor_form_cannot_retire_a_vendor_through_its_status_field():
    # The form fills empty profile columns on a matched vendor, status among them. A vendor
    # with no status, sent status "deleted", would be retired with no plan, no unassignment
    # and no trail entry — retiring goes through the delete flow only.
    cert = _cert()
    s = _CertSession([{**live("Acme Lifts"), "status": None}], cert)
    out = await certs_mod.create_vendor_for_certificate(
        s, cert.id, vendor_name="Acme Lifts", profile={"status": "deleted", "phone": "0161 000"},
    )
    assert out["ok"] is True and out["created"] is False
    set_cols = [re.search(r"SET (\w+) =", sql).group(1) for sql, _ in s.updates()]
    assert set_cols == ["phone"]


async def test_the_link_candidates_never_offer_a_retired_vendor():
    cert = _cert(raw_metadata={"vendor_name": "Acme Lifts"})
    s = _CertSession([retired("Acme Lifts (deleted)"), live("Acme Lifts Ltd")], cert)
    out = await certs_mod.resolve_link_candidates(s, cert)
    assert [c["id"] for c in out["vendor"]["candidates"]] == [NEW]


# ── the registration check and vendor coverage ───────────────────────────────────────────


async def test_a_retired_vendor_is_not_on_the_platform_for_the_registration_check(monkeypatch):
    async def pack(*a, **k):
        return SimpleNamespace(required_contractor_accreditation=None,
                               issuing_body="Gas Safe Register", verification_url=None)

    async def link(*a, **k):
        return {}

    monkeypatch.setattr(verification, "get_pack_type", pack)
    monkeypatch.setattr(verification, "build_verify_now_link", link)
    reg = Register([{**retired("Acme Lifts"), "vendor_code": None, "block_state": "Clear",
                     "blocked_accreditation_type": None}])
    out = await verification.check_vendor_registration_compliance(
        reg, vendor_name="Acme Lifts", certificate_type_code="GAS_SAFE", organization_id=ORG,
    )
    assert out["vendor_found"] is False and out["vendor_id"] is None
    assert out["platform_compliance"] == "unknown_vendor"


async def test_vendor_coverage_leaves_retired_vendors_out():
    reg = Register([retired("Acme Lifts (deleted)"), live("Brook Gas")])
    rows = await coverage._vendor_rows(reg, ORG)
    assert [r["id"] for r in rows] == [NEW]
