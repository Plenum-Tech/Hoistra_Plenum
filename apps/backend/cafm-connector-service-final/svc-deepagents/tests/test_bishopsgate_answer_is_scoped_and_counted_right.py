"""2 Nov 2026: three answers to "What certificates does Bishop Gate Tower have?" were wrong.

- Every spelling ("Bishop Gate Tower", "Bishop's Gate Tower", even "bishopsgate tower.") matched no
  name: the building filter read only the certificate's own c.building_name, while the register
  names a building by its site (COALESCE(s.site_name, c.building_name)). With no match the read
  fell back to the whole register, and the answers gave all 224 of the company's certificates
  ("72 lapsed") as the building's.
- Tiles read "Building certificates on file 0" and "Total certificates 0": a count the analyst
  cited no certificate for was "corrected" to the number it cited - none.
- A building's Fire Risk Assessment was drawn as "Pennard Fire Services · Fire Risk Assessment":
  the owner was the analyst's guess, not the row's building.
"""
from __future__ import annotations

from src.agents.orchestrator import DeepAgentOrchestrator as O


# ── a building named on its site is found by name ───────────────────────────────────────────────

def _where(op: str, named=("Bishop Gate Tower",), names=("Bishopsgate Tower",)):
    f = {"field": "building_name", "op": op, "value": list(named) if op in ("in", "not_in") else named[0],
         "names": list(names), "named": list(named)}
    return O.__new__(O)._spec_where_clause([f], "and")


def test_a_building_name_matches_the_certificate_or_its_site():
    sql, params = _where("eq")
    # the certificate's own name, its site's, or the buildings register's for its id or code
    # (checked read-only on 2 Nov 2026: B-301's 63 certificates carry only building_code)
    assert sql == ("((lower(c.building_name::text) = ANY(CAST(:p0 AS text[])) "
                   "OR lower(s.site_name::text) = ANY(CAST(:p0 AS text[])) "
                   "OR EXISTS (SELECT 1 FROM plenum_cafm.buildings bd WHERE (bd.building_id::text = c.building_id::text "
                   "OR (bd.building_code::text = c.building_code::text AND bd.organization_id::text = c.organization_id::text)) "
                   "AND lower(bd.name::text) = ANY(CAST(:p0 AS text[])))))")
    assert params["p0"] == ["bishop gate tower", "bishopsgate tower"]
    sql, _ = _where("in")
    assert "OR lower(s.site_name::text) = ANY(CAST(:p0 AS text[]))" in sql
    sql, _ = _where("contains")
    assert "lower(s.site_name::text) = ANY(CAST(:p0 AS text[]))" in sql and "c.building_name::text ILIKE :p0l" in sql


def test_excluding_a_building_is_unchanged():
    sql, _ = _where("neq")
    assert sql == "(lower(c.building_name::text) <> ALL(CAST(:p0 AS text[])))"


def test_the_site_name_field_is_unchanged():
    f = {"field": "site_name", "op": "eq", "value": "X", "names": ["X"], "named": ["X"]}
    sql, _ = O.__new__(O)._spec_where_clause([f], "and")
    assert sql == "(lower(COALESCE(s.site_name, c.building_name)::text) = ANY(CAST(:p0 AS text[])))"


def test_the_name_lookup_reads_the_certificates_and_their_sites_names():
    import asyncio

    from src import database

    seen = []

    class _R:
        def mappings(self):
            return self

        def all(self):
            return [{"n": "Bishopsgate Tower"}]

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            seen.append(str(sql))
            return _R()

    old = database.AsyncSessionLocal
    database.AsyncSessionLocal = lambda: _S()
    try:
        names = asyncio.run(O.__new__(O)._stored_names(
            "building_name", ["Bishop Gate Tower"], O._BUILDING_KIND_WORDS, "TRUE", {}))
    finally:
        database.AsyncSessionLocal = old
    assert names == ["Bishopsgate Tower"]
    sql = seen[0]
    assert "LEFT JOIN plenum_cafm.sites s ON" in sql
    assert "CROSS JOIN LATERAL (VALUES (c.building_name::text), (s.site_name::text)) nm(n)" in sql
    assert "nm.n IS NOT NULL AND btrim(nm.n) <> ''" in sql
    assert "UNION SELECT DISTINCT bd.name::text AS n" in sql and "JOIN plenum_cafm.buildings bd ON" in sql


def test_each_row_carries_its_buildings_register_name_and_owns_by_it():
    import inspect

    src = inspect.getsource(O._fetch_compliance_table_direct)
    assert "AS building_register_name" in src
    out, _ = O._validate_compliance_response({"narrative": "x", "certificates": [
        {"id": "r1", "name": "Fire Risk Assessment", "scope": "Building", "company": "Pennard Fire Services"}]},
        [{"id": "r1", "cert_scope": "Building", "building_name": None, "site_name": None,
          "building_register_name": "Bishopsgate Tower", "vendor_name": "Pennard Fire Services"}])
    assert out["certificates"][0]["company"] == "Bishopsgate Tower"


# ── counts and owners are the rows' ─────────────────────────────────────────────────────────────

ROWS = [
    {"id": "b1", "cert_scope": "building", "building_name": None, "site_name": "Bishopsgate Tower",
     "vendor_name": "Pennard Fire Services", "status": "Lapsed"},
    {"id": "b2", "cert_scope": "building", "building_name": "Bishopsgate Tower", "site_name": "Bishopsgate Tower",
     "vendor_name": None, "status": "Current"},
    {"id": "v1", "cert_scope": "vendor", "building_name": None, "site_name": None,
     "vendor_name": "ProudCastle Fire", "status": "Lapsed"},
]


def _validated(analysis):
    return O._validate_compliance_response(analysis, ROWS)


def test_a_count_that_cites_no_certificate_is_left_out_not_shown_as_zero():
    out, issues = _validated({"narrative": "x", "kpis": [
        {"label": "Building certificates on file", "count": 10, "unit": "certificates", "cert_ids": []},
        {"label": "Lapsed", "count": 2, "unit": "certificates", "cert_ids": ["b1", "v1"]},
        {"label": "Blocked", "count": 0, "unit": "certificates", "cert_ids": []},
        {"label": "Made up", "count": 3, "unit": "certificates", "cert_ids": ["zz1", "zz2", "zz3"]},
    ]})
    labels = [(k["label"], k["count"]) for k in out["kpis"]]
    assert labels == [("Lapsed", 2), ("Blocked", 0)]
    assert any("left out" in i and "Building certificates on file" in i for i in issues)


def test_a_building_certificate_belongs_to_its_building_and_a_vendor_one_to_its_vendor():
    out, issues = _validated({"narrative": "x", "certificates": [
        {"id": "b1", "name": "Fire Risk Assessment", "scope": "Building", "company": "Pennard Fire Services", "status": "Lapsed"},
        {"id": "b2", "name": "EICR", "scope": "Building", "company": "Bishopsgate Tower", "status": "Current"},
        {"id": "v1", "name": "BAFE SP203-1", "scope": "Vendor", "company": "Bishopsgate Tower", "status": "Lapsed"},
    ]})
    owners = {c["id"]: (c["scope"], c["company"]) for c in out["certificates"]}
    assert owners == {"b1": ("Building", "Bishopsgate Tower"), "b2": ("Building", "Bishopsgate Tower"),
                      "v1": ("Vendor", "ProudCastle Fire")}
    assert any("owner corrected" in i and "Pennard Fire Services" in i for i in issues)


# ── a status the register never updated is read from the expiry date ───────────────────────────
# LOLER-B-301-4471 (expired 2026-10-18) and FGAS-B-301-CH101 (expired 2026-10-29) were stored as
# "valid" on 2 Nov 2026 - the lifecycle scan that moves them on had not run - and the answer called
# them current. The chat reads the date, as the overdue bars already did; the database is untouched.

def test_an_expired_certificate_stored_as_valid_reads_as_lapsed():
    from datetime import date

    today = date(2026, 11, 2)
    got = O._status_from_expiry({"status": "valid", "expiry_date": "2026-10-18", "days_to_expiry": 3}, today)
    assert got == {"status": "Lapsed", "status_recorded": "valid", "days_to_expiry": -15}
    row = {"status": "valid", "expiry_date": date(2026, 10, 29), "cert_scope": "Asset"}
    row.update(O._status_from_expiry(row, today))
    row.update(O._derive_row_flags(row))
    assert row["status"] == "Lapsed" and row["is_lapsed"] and not row["is_compliant"]


def test_a_status_that_agrees_with_its_date_is_left_as_stored():
    from datetime import date, datetime

    today = date(2026, 11, 2)
    assert O._status_from_expiry({"status": "Current", "expiry_date": "2027-02-16"}, today) == {"days_to_expiry": 106}
    assert O._status_from_expiry({"status": "Lapsed", "expiry_date": "2026-06-29"}, today) == {"days_to_expiry": -126}
    assert O._status_from_expiry({"status": "Draft awaiting review", "expiry_date": "2026-01-01"}, today) == {
        "days_to_expiry": -305}
    assert O._status_from_expiry({"status": "valid", "expiry_date": None, "days_to_expiry": 5}, today) == {}
    assert O._status_from_expiry({"status": "valid", "expiry_date": datetime(2026, 11, 2, 9, 0)}, today) == {
        "days_to_expiry": 0}


def test_every_register_row_reads_its_status_from_its_date():
    import inspect

    src = inspect.getsource(O._fetch_compliance_table_direct)
    assert src.index("_status_from_expiry(row") < src.index("_derive_row_flags(row)")


# ── a building's certificates include its assets' ──────────────────────────────────────────────
# The Compliance page's building vault holds 10 for Bishopsgate Tower: 8 building-scope and the 2
# asset-scope ones on its lift (LOLER) and chiller (F-Gas), both lapsed. A building-scope question
# read only the 8 and said "2 lapsed" (2 Nov 2026). Vendor accreditations stay out of it.

def test_a_building_scope_question_reads_every_certificate_that_is_not_a_vendors():
    # As the Compliance page reads scope: "Vendor" is the vendor's, everything else the
    # building's - asset certificates, no scope, and the free text some imports left in the column.
    import inspect

    src = inspect.getsource(O._fetch_compliance_table_direct)
    assert '{"field": "cert_scope", "op": "neq", "value": "vendor"}' in src
    assert '{"field": "cert_scope", "op": "eq", "value": "vendor"}' in src
    sql, params = O.__new__(O)._spec_where_clause(
        [{"field": "cert_scope", "op": "neq", "value": "vendor"}], "and")
    assert sql == "(COALESCE(lower(c.cert_scope::text), '') <> lower(:p0))"
    assert params == {"p0": "vendor"}


def test_an_asset_certificate_belongs_to_its_building():
    out, _ = O._validate_compliance_response({"narrative": "x", "certificates": [
        {"id": "a1", "name": "LOLER Thorough Examination", "scope": "Vendor", "company": "Apex Lifts"}]},
        [{"id": "a1", "cert_scope": "Asset", "building_name": None, "site_name": None,
          "building_register_name": "Bishopsgate Tower", "vendor_name": "Apex Lifts", "status": "Lapsed"}])
    assert (out["certificates"][0]["scope"], out["certificates"][0]["company"]) == ("Building", "Bishopsgate Tower")



# ── any building or vendor, not only Bishopsgate ────────────────────────────────────────────────
# 2 Nov 2026, read-only sweep of Plenum Technologies: 13 buildings, 4 with certificates. A question
# about one of the other 9 matched nothing and widened to the company's whole register.

def test_relaxing_never_drops_the_building_or_vendor_the_question_names():
    import asyncio

    asked = []

    async def fetch(self, limit=1000, spec=None):
        asked.append([f["field"] for f in spec["filters"]])
        return [{"id": "r1"}] if all(f["field"] != "building_name" for f in spec["filters"]) else []

    o = O.__new__(O)
    o._fetch_compliance_table_direct = fetch.__get__(o)
    rows, note = asyncio.run(o._relax_empty_spec({"filters": [
        {"field": "building_name", "op": "eq", "value": "sams tower"},
        {"field": "status", "op": "eq", "value": "Expired"}], "logic": "and"}))
    assert rows == [] and note == {}
    assert asked == [["building_name"]]  # only the status was ever dropped


def test_a_named_building_on_the_register_is_known_however_it_is_typed(monkeypatch):
    import asyncio

    from src import database

    seen = []

    class _R:
        def __init__(self, names):
            self.names = names

        def mappings(self):
            return self

        def all(self):
            return [{"n": n} for n in self.names]

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, sql, params=None):
            seen.append((str(sql), dict(params or {})))
            if "plenum_cafm.vendors" in str(sql):
                return _R(["Acme Facilities Ltd"])
            return _R(["sams tower", "Bishopsgate Tower", "Vasiliki House"])

    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _S())
    o = O.__new__(O)
    monkeypatch.setattr(o, "_caller_company", lambda: "org-1")
    spec = {"filters": [{"field": "building_name", "op": "eq", "value": "Sams Tower"}]}
    assert asyncio.run(o._known_names(spec)) == ["sams tower"]
    assert "organization_id::text = :org" in seen[0][0] and seen[0][1]["org"] == "org-1"
    assert asyncio.run(o._known_names({"filters": [{"field": "vendor_name", "op": "eq", "value": "acme"}]})) == [
        "Acme Facilities Ltd"]
    assert asyncio.run(o._known_names({"filters": [{"field": "building_name", "op": "eq", "value": "Kensington"}]})) == []
    assert asyncio.run(o._known_names({"filters": [{"field": "status", "op": "eq", "value": "Expired"}]})) == []


def test_nothing_on_file_for_a_known_building_is_said_not_widened():
    import inspect

    src = inspect.getsource(O)
    assert "await self._known_names(unique[i][1])" in src
    assert '"none_for": known' in src
    assert "NOTHING ON FILE:" in src and "do not answer from any other" in src


# ── the filters read the date, not the stored day count or status ───────────────────────────────
# "Which buildings have overdue certificates?" planned `days_to_expiry lt 0` and found 3; there are
# 5 - LOLER-B-301-4471 and FGAS-B-301-CH101 store no day count and the status "valid" (2 Nov 2026,
# read-only check). The rows were corrected after the read (_status_from_expiry), the filter was not.

def test_a_day_count_filter_is_worked_out_from_the_expiry_date():
    sql, params = O.__new__(O)._spec_where_clause([{"field": "days_to_expiry", "op": "lt", "value": 0}], "and")
    assert "(c.expiry_date::date - current_date)" in sql
    assert "COALESCE((c.expiry_date::date - current_date), NULLIF(c.days_to_expiry::text, '')::numeric)" in sql
    assert params == {"p0": 0}


def test_a_status_filter_reads_an_expired_in_date_status_as_lapsed():
    sql, _ = O.__new__(O)._spec_where_clause([{"field": "status", "op": "in", "value": ["Lapsed", "Expired"]}], "and")
    assert "CASE WHEN c.expiry_date::date < current_date AND lower(c.status::text) IN (" in sql
    assert "THEN 'Lapsed' ELSE c.status END" in sql
    # the statuses it treats as in date are the ones the rows are corrected by
    import re
    listed = set(re.findall(r"'([a-z ]+)'", sql.split(" IN (", 1)[1].split(")", 1)[0]))
    assert listed == set(O._IN_DATE_STATUSES)
