"""The UDR agent's own lookups find a record however its name is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" — the building is named "JASMEET" (B-090765). Each lookup in
agents/udr_agent.py compared the typed text its own way: find_location searched sites and locations
(never buildings) with ILIKE '%JASMEET BUILDING%'; find_asset and get_asset_documents did the same
over assets, and get_asset_documents then took the first of five rows; query_table compared a name
filter with `=`; udr_search_records sent the phrase once to a substring search. So a kind word the
user added, a code typed without its hyphen, another word order or one wrong letter made a record
that is on the register unfindable, and "chiller 1" read "Chiller 10". find_location, find_asset and
get_asset_documents also read plenum_cafm with no company at all: "Bishopsgate Tower" answered from
another company's site (14 Oct 2026).

Each now runs the resolution ladder (agents/name_match.py and ontology_qa's SQL rungs): exact,
compact, phrase, every word in any order, close spelling — stopping at the first rung that finds a
record, returning every record at that rung, and saying when the match is a close spelling or more
matched than are shown. A phrase of kind words alone ("the building") finds nothing. The reads go
through plenum_scoped with the caller's company set; a site is a company's only through a company
building standing on it.

The database is SQLite standing in for Postgres: the SQL the tools write runs as written, with
Postgres's regex word boundaries (\\m, \\M), casts and settings translated. The rungs against a real
Postgres: tests/test_the_resolution_ladder_on_postgres.py.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid

import pytest

from src.agents import ontology_qa as oq
from src.agents import udr_agent
from src.http_client import caller_organization_id
from src.services import principal as P

ORG_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _u(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


JAS, JAH, HUS, RT5, B7, B70 = _u(1), _u(2), _u(3), _u(4), _u(5), _u(6)
B_JAS, B_BIS = _u(91), _u(92)
RIVERSIDE = [_u(100 + i) for i in range(27)]
A_BUILDINGS = [JAS, JAH, HUS, RT5, B7, B70, *RIVERSIDE]

BUILDING_COLS = ["building_id", "organization_id", "name", "building_code", "site_id", "floors"]
BUILDINGS = [
    (JAS, ORG_A, "JASMEET", "B-090765", "S-090", 89),
    (JAH, ORG_A, "Jasmeet Heights", "B-090766", None, 12),
    (HUS, ORG_A, "hussain tower", "B-019087", None, 30),
    (RT5, ORG_A, "RT5, Crossrail", "B-302", None, 4),
    (B7, ORG_A, "Building 7", "B-07", None, 7),
    (B70, ORG_A, "Building 70", "B-070", None, 70),
    *[(b, ORG_A, f"Riverside Block {i + 1}", f"R-{i + 1:03d}", None, 3) for i, b in enumerate(RIVERSIDE)],
    # Company B: a namesake of JASMEET, and the Bishopsgate Tower the 14 Oct question found.
    (B_JAS, ORG_B, "JASMEET", "B-555001", "S-B01", 3),
    (B_BIS, ORG_B, "Bishopsgate Tower", "B-0001", "S-B02", 34),
]
# sites.organization_id is an INTEGER and NULL on every row: a site is a company's only through a
# company building standing on it.
SITE_COLS = ["site_id", "id", "organization_id", "site_name", "site_code", "building_name", "city"]
SITES = [
    ("S-090", _u(500), None, "Jasmeet Campus", "JC-1", None, "London"),
    ("S-B01", _u(501), None, "Jasmeet Park", "JP-1", None, "Leeds"),
    ("S-001", _u(502), None, "Bishopsgate Tower", "BT-1", "Bishopsgate Tower", "London"),  # demo seed
    ("S-B02", _u(503), None, "Bishopsgate", "BG-1", None, "London"),
]
# locations carry no company column: a building's, else a site's.
LOCATION_COLS = ["id", "name", "address", "city", "building_id", "site_id"]
LOCATIONS = [
    ("L-1", "Plant Room", "1 Jasmeet Road", "London", JAS, None),
    ("L-2", "Plant Room", "2 Tower Street", "London", HUS, None),
    ("L-3", "Plant Room", "9 Other Way", "Leeds", B_JAS, None),
    ("L-4", "Roof", "1 Jasmeet Road", "London", None, "S-090"),
    ("L-5", "Roof Annex", "9 Other Way", "Leeds", None, "S-B01"),
]
ASSET_COLS = ["id", "organization_id", "building_id", "asset_code", "asset_name", "barcode",
              "serial_number", "external_asset_id", "qr_code", "document_ids"]
ASSETS = [
    ("A-10", ORG_A, JAS, "PT-B-301-CH-10", "Chiller 10", None, "SN-10", None, None, "[]"),
    ("A-1", ORG_A, JAS, "PT-B-301-CH-01", "Chiller 1", None, "SN-01", None, None, '["D-1"]'),
    ("A-B1", ORG_B, B_JAS, "PT-B-901-CH-01", "Chiller 1", None, "SN-B1", None, None, "[]"),
    ("A-AHU1", ORG_A, HUS, "AHU-1", "AHU", None, None, None, None, "[]"),
    ("A-AHU2", ORG_A, HUS, "AHU-2", "AHU", None, None, None, None, "[]"),
    ("A-L", ORG_A, JAS, "B-301-LIFT-4471", "Lift Asset-4471", None, None, None, None, "[]"),
]
VENDOR_COLS = ["id", "organization_id", "vendor_name", "vendor_code", "status"]
VENDORS = [
    ("V-1", ORG_A, "Acme Facilities Ltd", "ACME01", "active"),
    ("V-2", ORG_A, "Acme Lifts (deleted)", "ACME02", "deleted"),
    ("V-3", ORG_A, "Brook Lifts", "BRK01", "active"),
]
DOC_COLS = ["id", "original_filename", "blob_url", "document_type", "status", "page_count"]
DOCS = [("D-1", "chiller-1-service.pdf", None, "service_report", "done", 2)]
CHUNK_COLS = ["ingestion_id", "chunk_index", "page_start", "heading", "chunk_text"]
CHUNKS = [("D-1", 0, 1, "Service", "Chiller 1 service report for PT-B-301-CH-01: all checks passed.")]

TABLES = {"buildings": (BUILDING_COLS, BUILDINGS), "sites": (SITE_COLS, SITES),
          "locations": (LOCATION_COLS, LOCATIONS), "assets": (ASSET_COLS, ASSETS),
          "vendors": (VENDOR_COLS, VENDORS), "ingestion_documents": (DOC_COLS, DOCS),
          "document_chunks": (CHUNK_COLS, CHUNKS)}


# ── Postgres, as far as these reads need it, in SQLite ───────────────────────────────────────

def _py_regex(pattern: str) -> str:
    """A Postgres ARE as these tools write it, in Python's dialect."""
    p = pattern.replace("[:alnum:]", "0-9A-Za-z")
    return p.replace("\\m", "\\b(?=\\w)").replace("\\M", "\\b(?<=\\w)")


def _to_sqlite(sql: str) -> str:
    s = re.sub(r"::text\b", "", sql)
    s = s.replace(" ~* ", " REGEXP ")
    s = re.sub(r"to_jsonb\((\w+)\)->>'(\w+)'", r'"\2"', s)
    s = re.sub(r"= ANY\(CAST\((:\w+) AS uuid\[\]\)\)", r"IN (SELECT value FROM json_each(\1))", s)
    s = re.sub(r"= ANY\((:\w+)\)", r"IN (SELECT value FROM json_each(\1))", s)
    return re.sub(r"\bILIKE\b", "LIKE", s)


class _Result:
    def __init__(self, names, rows):
        self.names, self.rows = names, rows

    def mappings(self):
        return self

    def all(self):
        return [dict(zip(self.names, r)) for r in self.rows]

    def __iter__(self):
        return iter(self.rows)


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Db:
    """plenum_cafm, and plenum_scoped's self-filtering views as udr_scoped_views.sql builds them."""

    def __init__(self):
        self.settings: dict[str, str] = {}
        self.seen: list[str] = []
        db = sqlite3.connect(":memory:")
        db.execute("PRAGMA trusted_schema = ON")
        db.create_function("lower", 1, lambda v: None if v is None else str(v).lower())
        db.create_function("regexp", 2, lambda pat, v: None if v is None else
                           bool(re.search(_py_regex(pat), str(v), re.I)))
        db.create_function("regexp_replace", 4, lambda v, pat, rep, flags: None if v is None else
                           re.sub(_py_regex(pat), rep, str(v)))
        db.create_function("current_setting", 2, lambda name, missing_ok: self.settings.get(name))
        db.create_function("set_config", 3, self._set)
        db.execute("ATTACH DATABASE ':memory:' AS plenum_cafm")
        db.execute("ATTACH DATABASE ':memory:' AS plenum_scoped")
        for t, (cols, rows) in TABLES.items():
            col_sql = ", ".join(f'"{c}"' for c in cols)
            marks = ", ".join("?" for _ in cols)
            for target in (f'plenum_cafm."{t}"', f'plenum_scoped."_base_{t}"'):
                db.execute(f"CREATE TABLE {target} ({col_sql})")
                db.executemany(f"INSERT INTO {target} VALUES ({marks})", rows)
            conds = []
            if "organization_id" in cols:
                conds.append("t.organization_id = current_setting('app.udr_org', 1)")
            if "building_id" in cols:
                conds.append("(coalesce(current_setting('app.udr_buildings', 1), '') = '' OR "
                             "instr(',' || current_setting('app.udr_buildings', 1) || ',', "
                             "',' || t.building_id || ',') > 0)")
            body = f'SELECT t.* FROM "_base_{t}" t'
            if conds:
                body += (" WHERE current_setting('app.udr_unrestricted', 1) = '1' OR ("
                         + " AND ".join(conds) + ")")
            db.execute(f'CREATE VIEW plenum_scoped."{t}" AS {body}')
        self.db = db

    def _set(self, name, value, local):
        self.settings[name] = value
        return value

    def run(self, sql: str, params: dict) -> _Result:
        self.seen.append(sql)
        s = sql.strip()
        if re.match(r"(?i)^SET\b", s):
            return _Result([], [])
        if "information_schema.columns" in s:
            cols = TABLES.get(params.get("t"), ([], []))[0]
            return _Result(["column_name"], [(c,) for c in cols])
        bound = {k: (json.dumps(v) if isinstance(v, (list, tuple)) else v) for k, v in params.items()}
        cur = self.db.execute(_to_sqlite(s), bound)
        return _Result([d[0] for d in cur.description or []], cur.fetchall())

    def selects(self) -> list[str]:
        return [s for s in self.seen if s.lstrip().upper().startswith("SELECT")
                and "set_config" not in s and "information_schema" not in s]


class _Session:
    def __init__(self, db: _Db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def begin(self):
        return None

    def begin_nested(self):
        return _Nested()

    async def rollback(self):
        self.db.settings.clear()            # set_config(..., true) lasts for the transaction

    async def execute(self, stmt, params=None):
        return self.db.run(str(stmt), dict(params or {}))


def _admin(org=ORG_A, role="admin", buildings=None):
    return P.Principal(user_id=uuid.uuid4(), email="someone@example.test",
                       organization_id=uuid.UUID(org) if org else None, role=role, can_ingest=False,
                       building_ids=None if buildings is None else tuple(uuid.UUID(b) for b in buildings))


@pytest.fixture
def db(monkeypatch):
    d = _Db()
    monkeypatch.setattr(oq, "_session_factory", lambda: (lambda: _Session(d)))
    monkeypatch.setattr(udr_agent.database, "AsyncSessionLocal", lambda: _Session(d))
    monkeypatch.setattr(P, "_columns_cache", {})
    token = P.caller_principal.set(_admin())        # an administrator of company A
    yield d
    P.caller_principal.reset(token)


def _as(principal):
    return P.caller_principal.set(principal)


def _run(tool, **args):
    return asyncio.run(tool.ainvoke(args))


def _names(rows, col="name"):
    return [r.get(col) for r in rows]


# ── find_location ────────────────────────────────────────────────────────────────────────────

class TestFindLocationFindsTheBuilding:
    @pytest.mark.parametrize("typed", ["JASMEET BUILDING", "jasmeet", "JASMEET", "the Jasmeet building",
                                       "Jasmeet's", "building JASMEET", "B090765", "b-090765", "B 090765"])
    def test_the_building_is_found_however_it_is_typed(self, db, typed):
        out = _run(udr_agent.find_location, identifier=typed)
        assert _names(out["buildings"]) == ["JASMEET"], out
        assert out["buildings"][0]["building_id"] == JAS          # company A's, not B's namesake
        assert out["buildings"][0]["floors"] == 89

    def test_an_exact_name_beats_a_longer_one_that_starts_the_same(self, db):
        """"JASMEET" is exactly the name of one building; "Jasmeet Heights" only starts with it."""
        out = _run(udr_agent.find_location, identifier="JASMEET BUILDING")
        assert "Jasmeet Heights" not in _names(out["buildings"])

    def test_the_words_in_another_order(self, db):
        out = _run(udr_agent.find_location, identifier="tower hussain")
        assert _names(out["buildings"]) == ["hussain tower"]

    def test_a_stray_comma(self, db):
        out = _run(udr_agent.find_location, identifier="RT5 Crossrail")
        assert _names(out["buildings"]) == ["RT5, Crossrail"]

    def test_building_7_is_not_building_70(self, db):
        out = _run(udr_agent.find_location, identifier="Building 7")
        assert _names(out["buildings"]) == ["Building 7"]

    def test_one_letter_wrong_is_the_closest_name_and_says_so(self, db):
        out = _run(udr_agent.find_location, identifier="Jasmet")
        assert _names(out["buildings"]) == ["JASMEET"]
        assert out["matched_by"] == "fuzzy"
        assert "not on the register as written" in out["note"] and "JASMEET" in out["note"]

    @pytest.mark.parametrize("typed", ["the building", "building", "the site", "this property"])
    def test_a_phrase_of_kind_words_alone_names_nothing_and_reads_nothing(self, db, typed):
        out = _run(udr_agent.find_location, identifier=typed)
        assert out["count"] == 0 and out["buildings"] == out["sites"] == out["locations"] == []
        assert db.selects() == []


class TestFindLocationReadsOnlyTheCallersCompany:
    def test_another_companys_building_and_a_demo_site_are_not_found(self, db):
        """The 14 Oct question: acting for one company, "Bishopsgate Tower" answered 34 floors from
        a demo site no company owns, while the real one belonged to another company."""
        out = _run(udr_agent.find_location, identifier="Bishopsgate Tower")
        assert out["count"] == 0, out
        assert out["buildings"] == out["sites"] == out["locations"] == []

    def test_a_superadmin_acting_for_a_company_reads_that_company(self, db):
        token = _as(_admin(org=None, role="superadmin"))
        acting = caller_organization_id.set(ORG_B)
        try:
            out = _run(udr_agent.find_location, identifier="Bishopsgate Tower")
        finally:
            caller_organization_id.reset(acting)
            P.caller_principal.reset(token)
        assert [b["building_id"] for b in out["buildings"]] == [B_BIS]
        assert out["sites"] == []          # the demo site stands under no building of company B

    def test_a_site_is_found_through_the_companys_building_on_it(self, db):
        out = _run(udr_agent.find_location, identifier="Jasmeet Campus")
        assert _names(out["sites"], "site_name") == ["Jasmeet Campus"]
        assert _run(udr_agent.find_location, identifier="Jasmeet Park")["count"] == 0   # company B's

    def test_a_user_allocated_to_one_building_sees_only_it(self, db):
        token = _as(_admin(role="user", buildings=[JAH]))
        try:
            out = _run(udr_agent.find_location, identifier="JASMEET BUILDING")
        finally:
            P.caller_principal.reset(token)
        assert _names(out["buildings"]) == ["Jasmeet Heights"]
        assert out["sites"] == []

    def test_a_location_belongs_to_its_building_else_its_site(self, db):
        assert [x["id"] for x in _run(udr_agent.find_location, identifier="Roof")["locations"]] == ["L-4"]
        assert _run(udr_agent.find_location, identifier="Roof Annex")["count"] == 0     # company B's site

    def test_with_no_caller_nothing_is_read(self, db):
        token = _as(None)
        try:
            out = _run(udr_agent.find_location, identifier="JASMEET")
        finally:
            P.caller_principal.reset(token)
        assert out["count"] == 0 and "error" in out
        assert db.selects() == []


class TestFindLocationReturnsEveryMatch:
    def test_a_shared_name_returns_every_record_and_says_so(self, db):
        out = _run(udr_agent.find_location, identifier="Plant Room")
        assert sorted(x["id"] for x in out["locations"]) == ["L-1", "L-2"]     # not company B's L-3
        assert "2" in out["note"] and "never pick one" in out["note"]

    def test_more_matches_than_are_shown_is_said(self, db):
        out = _run(udr_agent.find_location, identifier="riverside")
        assert len(out["buildings"]) == udr_agent._MATCH_CAP
        assert f"More than {udr_agent._MATCH_CAP}" in out["note"]


# ── find_asset and get_asset_documents ───────────────────────────────────────────────────────

class TestFindAsset:
    def test_chiller_1_is_not_chiller_10_nor_another_companys_chiller_1(self, db):
        out = _run(udr_agent.find_asset, identifier="chiller 1")
        assert [a["id"] for a in out["matches"]] == ["A-1"], out

    def test_a_code_typed_without_its_hyphens(self, db):
        assert [a["id"] for a in _run(udr_agent.find_asset, identifier="PTB301CH01")["matches"]] == ["A-1"]

    def test_one_letter_wrong(self, db):
        out = _run(udr_agent.find_asset, identifier="Chiler 1")
        assert [a["id"] for a in out["matches"]] == ["A-1"]
        assert "not on the register as written" in out["note"]

    def test_a_shared_name_returns_both(self, db):
        out = _run(udr_agent.find_asset, identifier="AHU")
        assert sorted(a["id"] for a in out["matches"]) == ["A-AHU1", "A-AHU2"]
        assert "never pick one" in out["note"]

    def test_another_companys_asset_is_not_found(self, db):
        assert _run(udr_agent.find_asset, identifier="PT-B-901-CH-01")["matches"] == []

    def test_the_asset_names_nothing(self, db):
        assert _run(udr_agent.find_asset, identifier="the asset")["matches"] == []
        assert db.selects() == []


class TestGetAssetDocuments:
    def test_it_reads_the_asset_that_was_named_not_the_first_partial_match(self, db):
        out = _run(udr_agent.get_asset_documents, identifier="chiller 1")
        assert out["asset"]["id"] == "A-1", out
        assert [d["id"] for d in out["documents"]] == ["D-1"]
        assert out["chunks"] and "PT-B-301-CH-01" in out["chunks"][0]["text"]

    def test_a_shared_name_asks_which_one_instead_of_picking(self, db):
        out = _run(udr_agent.get_asset_documents, identifier="AHU")
        assert out["asset"] is None and out["chunks"] == []
        assert sorted(c["id"] for c in out["candidates"]) == ["A-AHU1", "A-AHU2"]
        assert "never pick one" in out["note"]

    def test_another_companys_asset_is_not_read(self, db):
        out = _run(udr_agent.get_asset_documents, identifier="PT-B-901-CH-01")
        assert out["asset"] is None


# ── query_table ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def company_a_user(db):
    """A user allocated to every company-A building: query_table's own boundary (building ids)."""
    token = _as(_admin(role="user", buildings=A_BUILDINGS))
    yield db
    P.caller_principal.reset(token)


def _table(table, filters):
    return asyncio.run(udr_agent.query_table.ainvoke({"table_name": table, "filters": filters}))


class TestQueryTableNameFilters:
    @pytest.mark.parametrize("filters", [{"name": "JASMEET BUILDING"}, {"name": "jasmeet"},
                                         {"building_code": "B090765"}, {"building_code": "b-090765"}])
    def test_a_name_or_code_filter_finds_the_building(self, company_a_user, filters):
        rows = _table("buildings", filters)
        assert [r["building_id"] for r in rows] == [JAS], rows

    def test_the_words_in_another_order(self, company_a_user):
        assert _names(_table("buildings", {"name": "tower hussain"})) == ["hussain tower"]

    def test_building_7_is_not_building_70(self, company_a_user):
        assert _names(_table("buildings", {"name": "building 7"})) == ["Building 7"]

    def test_one_letter_wrong_is_marked_as_the_closest_spelling(self, company_a_user):
        rows = _table("buildings", {"name": "Jasmet"})
        assert _names(rows) == ["JASMEET"]
        assert "closest spelling" in rows[0]["_name_match"]

    def test_the_building_boundary_holds_on_every_rung(self, db):
        token = _as(_admin(role="user", buildings=[JAH]))
        try:
            rows = _table("buildings", {"name": "JASMEET BUILDING"})
        finally:
            P.caller_principal.reset(token)
        assert _names(rows) == ["Jasmeet Heights"]
        assert db.selects() and all("scope_buildings" in s for s in db.selects())

    def test_a_filter_of_kind_words_alone_reads_nothing(self, company_a_user):
        assert _table("buildings", {"name": "the building"}) == []
        assert company_a_user.selects() == []

    def test_an_exact_name_is_read_with_the_one_equality_it_always_was(self, company_a_user):
        assert _names(_table("buildings", {"name": "JASMEET"})) == ["JASMEET"]
        assert company_a_user.selects() == [
            "SELECT * FROM plenum_cafm.buildings WHERE name = :p_name AND building_id = "
            "ANY(CAST(:scope_buildings AS uuid[])) LIMIT 100"]

    def test_a_vendor_found_by_the_start_of_its_name_never_a_deleted_one(self, db):
        token = _as(None)                     # an admin read: no building clause
        try:
            rows = _table("vendors", {"vendor_name": "Acme"})
        finally:
            P.caller_principal.reset(token)
        assert [r["id"] for r in rows] == ["V-1"]
        assert all(udr_agent._LIVE_VENDORS in s for s in db.selects())

    @pytest.mark.parametrize("typed", ["Acme Lifts (deleted)", "acme lifts", "ACME LIFTS (DELETED)"])
    def test_a_deleted_vendor_stays_out_on_every_rung(self, db, typed):
        """tests/test_a_deleted_vendor_is_left_out.py's rule, now that a name filter that found
        nothing climbs the ladder: no rung brings back a deleted vendor."""
        token = _as(None)
        try:
            assert _table("vendors", {"vendor_name": typed}) == []
        finally:
            P.caller_principal.reset(token)
        assert len(db.selects()) > 1 and all(udr_agent._LIVE_VENDORS in s for s in db.selects())

    def test_a_vendor_code_in_another_case_but_never_a_part_of_one(self, db):
        token = _as(None)
        try:
            assert [r["id"] for r in _table("vendors", {"vendor_code": "acme01"})] == ["V-1"]
            assert _table("vendors", {"vendor_code": "ACME"}) == []
        finally:
            P.caller_principal.reset(token)



# ── udr_search_records ───────────────────────────────────────────────────────────────────────

SEARCHED = [{"building_id": b[0], "name": b[2], "building_code": b[3]} for b in BUILDINGS
            if b[1] == ORG_A and not b[2].startswith("Riverside")]


class _Udr:
    """svc-udr's POST /records/search: ILIKE '%term%' over the columns, already in the company."""

    def __init__(self):
        self.terms: list[str] = []

    async def __call__(self, method, base, path, **kw):
        body = kw["json"]
        term = body["search_term"].lower()
        self.terms.append(body["search_term"])
        rows = [r for r in SEARCHED
                if any(term in str(r.get(c) or "").lower() for c in body["search_columns"])]
        rows = rows[body["offset"]: body["offset"] + body["limit"]]
        payload = {"table": "buildings", "search_term": body["search_term"], "rows": rows,
                   "count": len(rows), "limit": body["limit"], "offset": body["offset"]}

        class _R:
            def json(self):
                return payload
        return _R()


@pytest.fixture
def udr(db, monkeypatch):
    fake = _Udr()
    monkeypatch.setattr(udr_agent, "_request", fake)
    token = _as(None)          # svc-udr scopes the search itself; _scoped passes an admin's rows
    yield fake
    P.caller_principal.reset(token)


def _search(term, cols=("name", "building_code")):
    return asyncio.run(udr_agent.udr_search_records.ainvoke(
        {"table": "buildings", "search_term": term, "search_columns": list(cols)}))


class TestUdrSearchRecords:
    def test_a_kind_word_beside_the_name(self, udr):
        out = _search("JASMEET BUILDING")
        assert _names(out["rows"])[0] == "JASMEET", out
        assert "jasmeet" in out["match_note"]

    @pytest.mark.parametrize("typed,name", [("B090765", "JASMEET"), ("tower hussain", "hussain tower"),
                                            ("RT5 Crossrail", "RT5, Crossrail")])
    def test_a_code_without_its_hyphen_another_order_a_stray_comma(self, udr, typed, name):
        assert _names(_search(typed)["rows"]) == [name]

    def test_one_letter_wrong(self, udr):
        out = _search("Jasmet")
        assert _names(out["rows"]) == ["JASMEET"]
        assert "not on the register as written" in out["match_note"]

    def test_the_building_sends_no_search(self, udr):
        out = _search("the building")
        assert out["rows"] == [] and udr.terms == []

    def test_a_term_found_as_typed_is_answered_as_svc_udr_answered_it(self, udr):
        out = _search("Jasmeet")
        assert _names(out["rows"]) == ["JASMEET", "Jasmeet Heights"]
        assert udr.terms == ["Jasmeet"] and "match_note" not in out
