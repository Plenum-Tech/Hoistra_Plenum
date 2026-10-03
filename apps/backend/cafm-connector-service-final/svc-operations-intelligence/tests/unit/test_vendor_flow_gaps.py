"""The vendor flow answers "which supplier is this?" for one company at a time.

Four gaps found on 17 Sep 2026 by walking every path that resolves, creates or attaches a
vendor, and measuring each against the live register (2,073 rows):

1. NO LOOKUP WAS TENANT-SCOPED. All six ``FROM plenum_cafm.vendors`` statements in
   vendor_identity.py and contractors.py matched on name alone. resolve_or_create_vendor
   took an organization_id and used it ONLY for the INSERT, never for the match — so the
   register was shared. Seven compliance certificates are already attached to a vendor
   belonging to a different organization, and twelve monthly scorecards score one.

2. HALF THE REGISTER COULD NOT BE LINKED, SILENTLY. vendors.id is character varying and
   1,025 of 2,073 rows carry legacy ids ("V-01", "VEN-CLIMATE-001"), while every column
   that points at a vendor is uuid. resolve_or_create_vendor ended its match with
   ``UUID(str(matched))``, which throws on those ids straight into a bare ``except
   Exception`` — so a contract naming "Apex Lifts" FOUND Apex Lifts, threw the answer
   away, and saved with no vendor at all. The reply said "Ingestion complete".

3. A VENDOR WITH NO TENANT WENT TO WHOEVER SORTS FIRST. Both default-org helpers ran
   ``SELECT id FROM organizations ORDER BY id LIMIT 1``, so an ingest arriving without an
   organization registered its supplier under an unrelated company.

4. THE int() CAST SURVIVED IN TWO MORE PLACES. certificates.py squashed the company's
   uuid to an integer before binding it, on the strength of the same comment that caused
   the original bug — once when creating a vendor for a certificate, once when linking a
   certificate's document to its entities.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.engines.compliance import certificates as certs_mod
from src.engines.compliance import contractors
from src.engines.contract_performance import extract as extract_mod
from src.shared import vendor_identity

ORG = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG = UUID("00000000-0000-0000-0000-000000000005")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0]["id"] if self._rows else None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    """Records every statement and its bound parameters; answers from a scripted queue."""

    def __init__(self, replies: list[list[dict]] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._replies = list(replies or [])

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.calls.append((sql, params or {}))
        return _Result(self._replies.pop(0) if self._replies else [])

    @property
    def selects(self) -> list[tuple[str, dict]]:
        return [c for c in self.calls if c[0].upper().lstrip().startswith("SELECT")]

    @property
    def inserts(self) -> list[tuple[str, dict]]:
        return [c for c in self.calls if "INSERT INTO" in c[0].upper()]


# --------------------------------------------------------------------------- gap 1: tenancy


@pytest.mark.asyncio
async def test_find_vendor_id_scopes_the_exact_name_lookup_to_one_organization():
    s = FakeSession()
    await vendor_identity.find_vendor_id(s, "Apex Lifts", organization_id=ORG)
    sql, params = s.selects[0]
    assert "organization_id" in sql, (
        "a name lookup without an organization filter reads every tenant's register"
    )
    assert str(ORG) in {str(v) for v in params.values()}


@pytest.mark.asyncio
async def test_find_vendor_id_scopes_the_normalised_candidate_lookup_too():
    # Exact match misses, so the normalised pass runs — that query must be scoped as well.
    s = FakeSession(replies=[[], []])
    await vendor_identity.find_vendor_id(s, "Gough & Kelly Limited", organization_id=ORG)
    sql, params = s.selects[1]
    assert "organization_id" in sql
    assert str(ORG) in {str(v) for v in params.values()}


@pytest.mark.asyncio
async def test_find_vendor_id_without_an_organization_stays_unscoped():
    # The one caller that genuinely has no tenant (a certificate with no org) must still work.
    s = FakeSession()
    await vendor_identity.find_vendor_id(s, "Apex Lifts")
    assert "organization_id" not in s.selects[0][0]


@pytest.mark.asyncio
async def test_vendor_named_in_reads_only_the_callers_own_register():
    s = FakeSession()
    await vendor_identity.vendor_named_in(s, "invoice from Apex Lifts", organization_id=ORG)
    sql, params = s.selects[0]
    assert "organization_id" in sql, (
        "an invoice was matched against every tenant's vendor names"
    )
    assert str(ORG) in {str(v) for v in params.values()}


@pytest.mark.asyncio
async def test_resolve_or_create_vendor_matches_within_the_company_not_across_it():
    s = FakeSession()
    await contractors.resolve_or_create_vendor(
        s, company_name="Apex Lifts", organization_id=ORG, create_if_missing=False
    )
    assert s.selects, "no lookup ran at all"
    assert all("organization_id" in sql for sql, _ in s.selects), (
        "every vendor lookup on this path must carry the company filter; "
        f"unscoped: {[sql[:70] for sql, _ in s.selects if 'organization_id' not in sql]}"
    )


@pytest.mark.asyncio
async def test_the_substring_fallback_cannot_reach_another_companys_vendor():
    # Exact and normalised both miss; the ILIKE '%name%' last resort must still be scoped.
    s = FakeSession(replies=[[], [], []])
    await contractors.resolve_or_create_vendor(
        s, company_name="Apex", organization_id=ORG, create_if_missing=False
    )
    like = [c for c in s.selects if "ILIKE" in c[0].upper()]
    assert like, "the substring fallback did not run"
    assert "organization_id" in like[0][0]


# ------------------------------------------------------- gap 2: legacy ids, silently dropped


@pytest.mark.asyncio
async def test_a_vendor_with_a_legacy_id_is_reported_not_swallowed(capsys):
    """"Apex Lifts" is row V-01. It is found, and it cannot go in a uuid column.

    The old code let UUID("V-01") raise into a bare except, which logged
    `resolve_vendor_failed` — the same message a genuine database error produces. The two
    need different names because they need different fixes.
    """
    s = FakeSession(replies=[[{"id": "V-01"}]])
    got = await contractors.resolve_or_create_vendor(
        s, company_name="Apex Lifts", organization_id=ORG, create_if_missing=True
    )
    assert got is None, "a legacy id cannot be returned as a UUID"
    assert not s.inserts, "the vendor exists — creating a second row would split its history"
    logged = capsys.readouterr()
    emitted = logged.err + logged.out
    assert "vendor_legacy_id_unlinkable" in emitted, (
        "the failure must carry its own event name — 'resolve_vendor_failed' is what a real "
        f"database error logs, and the two need different fixes; got: {emitted[-300:]!r}"
    )
    assert "V-01" in emitted, "the log must name the id a DBA has to migrate"


@pytest.mark.asyncio
async def test_a_uuid_keyed_vendor_still_resolves_normally():
    vid = uuid4()
    s = FakeSession(replies=[[{"id": str(vid)}]])
    got = await contractors.resolve_or_create_vendor(
        s, company_name="Gough and Kelly Ltd", organization_id=ORG, create_if_missing=False
    )
    assert got == vid


@pytest.mark.asyncio
async def test_the_contract_path_names_the_legacy_case_so_the_reply_can_explain_it():
    s = FakeSession(replies=[[{"id": "V-01"}]])
    out = await extract_mod._resolve_contract_vendor(
        s, vendor_id=None, vendor_name="Apex Lifts", organization_id=ORG
    )
    assert out["status"] == "legacy_id_unlinkable", (
        "'could_not_create' reads as 'we tried and the database refused'; this vendor "
        "exists and is merely unreachable from a uuid column"
    )
    assert out["vendor_id"] is None
    assert out["legacy_vendor_id"] == "V-01"


# ----------------------------------------------- gap 3: a vendor must never land in a guess


@pytest.mark.asyncio
async def test_no_organization_means_no_vendor_rather_than_someone_elses():
    s = FakeSession(replies=[[{"id": str(OTHER_ORG)}]])
    got = await extract_mod._default_org_for_vendor_create(s)
    assert got is None, (
        "ORDER BY id LIMIT 1 registered the supplier under whichever company sorts first"
    )


@pytest.mark.asyncio
async def test_the_compliance_default_org_refuses_to_guess_as_well():
    s = FakeSession(replies=[[{"id": str(OTHER_ORG)}]])
    got = await certs_mod.default_org_native(s)
    assert got is None


# ------------------------------------------------------------- gap 4: the surviving int()s


@pytest.mark.asyncio
async def test_creating_a_vendor_for_a_certificate_binds_the_company_id_as_itself():
    """int(UUID(...)) throws, was caught, and bound NULL — an unscoped vendor row."""

    class _Cert:
        id = uuid4()
        organization_id = ORG
        org_id = None
        vendor_id = None
        raw_metadata: dict = {}
        document_id = None

    class _S(FakeSession):
        async def get(self, model, pk):
            return _Cert()

        async def flush(self):
            return None

        async def commit(self):
            return None

        async def refresh(self, obj):
            return None

    s = _S(replies=[[]])
    await certs_mod.create_vendor_for_certificate(
        s, certificate_id=uuid4(), vendor_name="Brand New Supplier Ltd"
    )
    ins = [c for c in s.inserts if "plenum_cafm.vendors" in c[0]]
    assert ins, "no vendor was inserted"
    assert str(ins[0][1].get("org")) == str(ORG), (
        f"company id was mangled before the INSERT: {ins[0][1].get('org')!r}"
    )


def test_no_int_cast_of_an_organization_id_survives_anywhere():
    """A grep-level guard: this cast is what broke vendor creation for three weeks.

    ``int(`` on its own also matches ``print(`` and ``sprint(``, which is how the first
    version of this guard reported a log line as a bug — so the call has to be anchored.
    """
    import pathlib
    import re as _re

    call = _re.compile(r"(?<![A-Za-z0-9_])int\s*\(")
    src_root = pathlib.Path(certs_mod.__file__).resolve().parents[3]
    offenders = []
    for path in src_root.rglob("*.py"):
        if "test" in path.parts or path.name.startswith("test_"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if call.search(stripped) and ("organization_id" in stripped or "org_id" in stripped):
                offenders.append(f"{path.relative_to(src_root)}:{n}: {stripped[:90]}")
    assert not offenders, "organization ids are uuids; int() on one always throws:\n" + "\n".join(
        offenders
    )
