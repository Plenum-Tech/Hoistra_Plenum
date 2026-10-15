"""A migration never links new work to a deleted vendor, and never mistakes one for a twin.

Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row stays, so the work
orders and invoices it appears on still read "Acme Ltd (deleted)", and its status becomes
'deleted'. Nothing in the migration knew that. Re-uploading a workbook afterwards would:

  * link the new work orders, visits, contracts and certificates that name the vendor — by its
    code, its name or its id — to the retired row, so live work landed on a vendor that is gone
    from every list and can never be assigned, scored or emailed again;
  * write an id the file already carries (an export taken before the delete) straight through,
    without asking anything at all;
  * count a retired row as "already written", so a supplier taken on again was skipped as a
    twin of the vendor that was deleted.

Status is the authority, exactly as ops-intelligence's live_vendor_sql says. The name mark is
defence in depth only: an exported history sheet carries "Acme Ltd (deleted)" verbatim, and an
exact-name match would land on the retired row without the status check.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import types

import pytest

_HERE = os.path.dirname(__file__)
_SM = os.path.join(_HERE, "..")
_SPEC = importlib.util.spec_from_file_location(
    "reference_link_for_delete", os.path.join(_SM, "src", "graph", "nodes", "reference_link.py"))
rl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rl)

ORG = "00000000-0000-0000-0000-000000000001"
RETIRED = "aaaa1111-2222-4333-8444-555566667777"
LIVE = "bbbb1111-2222-4333-8444-555566667777"
UNKNOWN = "cccc1111-2222-4333-8444-555566667777"

# ops-intelligence's live_vendor_sql('t'), written out by hand: the predicate every service
# filters vendors on. A change to it there must be a change here.
LIVE_SQL = "COALESCE(lower(to_jsonb(t)->>'status'), 'active') <> 'deleted'"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class Register:
    """plenum_cafm.vendors, answering the two questions the resolver asks as Postgres would.

    A row is live unless its status reads 'deleted' in any case; a missing status is live.
    The status filter applies only when the SQL actually carries it — which is the point.
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.reads = 0

    @staticmethod
    def _live(r: dict) -> bool:
        return (r.get("status") or "active").lower() != "deleted"

    async def fetch(self, sql: str, params: dict) -> list:
        self.reads += 1
        k = params["k"]
        if "count(*)" in sql:
            hits = [r for r in self.rows if r["id"].lower() == k]
            if f"NOT ({LIVE_SQL})" in sql:
                hits = [r for r in hits if not self._live(r)]
            return [(len(hits),)]
        hits = [r for r in self.rows
                if k in {(r.get("code") or "").lower(), (r.get("name") or "").lower()}
                or k == r["id"]]
        if LIVE_SQL in sql:
            hits = [r for r in hits if self._live(r)]
        return [(r["id"],) for r in hits]


def resolver(db):
    return rl.ReferenceResolver(db.fetch, ORG, "plenum_cafm")


class TestANameOrCodeNeverFindsARetiredVendor:
    def test_the_marked_name_an_exported_history_sheet_carries_finds_nothing(self):
        """An export of closed work carries the retired vendor's name, mark and all."""
        db = Register([{"id": RETIRED, "name": "Apex Mechanical Ltd (deleted)", "status": "deleted"}])
        r = resolver(db)
        assert run(r.resolve("vendor_id", "Apex Mechanical Ltd (deleted)")) is None
        assert r.report()["unresolved"] == {"vendor_id": ["Apex Mechanical Ltd (deleted)"]}

    def test_a_code_still_on_a_retired_row_finds_nothing(self):
        """A retire clears the code, but the status decides even where one was left behind."""
        db = Register([{"id": RETIRED, "code": "APX-01", "name": "Apex (deleted)", "status": "deleted"}])
        assert run(resolver(db).resolve("vendor_id", "APX-01")) is None

    def test_a_legacy_id_naming_a_retired_row_finds_nothing(self):
        db = Register([{"id": "ven-0042", "name": "Apex (deleted)", "status": "deleted"}])
        assert run(resolver(db).resolve("vendor_id", "VEN-0042")) is None

    def test_the_status_is_read_whatever_its_case(self):
        db = Register([{"id": RETIRED, "code": "APX-01", "status": "Deleted"}])
        assert run(resolver(db).resolve("vendor_id", "APX-01")) is None

    def test_a_supplier_taken_on_again_is_found_rather_than_ambiguous(self):
        """Without the status filter the retired row and the new one both answer, and an
        ambiguous name resolves to neither — the new vendor's work would read Unassigned."""
        db = Register([
            {"id": RETIRED, "name": "Apex Mechanical Ltd", "status": "deleted"},
            {"id": LIVE, "name": "Apex Mechanical Ltd", "status": "active"},
        ])
        r = resolver(db)
        assert run(r.resolve("vendor_id", "Apex Mechanical Ltd")) == LIVE
        assert r.report()["ambiguous"] == []

    def test_a_row_with_no_status_at_all_is_live(self):
        db = Register([{"id": LIVE, "name": "Apex Mechanical Ltd"}])
        assert run(resolver(db).resolve("vendor_id", "Apex Mechanical Ltd")) == LIVE

    def test_only_the_vendor_lookup_carries_the_filter(self):
        """Assets, contracts and parts have no retired state; their lookups are unchanged."""
        seen: list[str] = []

        async def fetch(sql, params):
            seen.append(sql)
            return []

        r = rl.ReferenceResolver(fetch, ORG, "plenum_cafm")
        for col in ("asset_id", "contract_id", "part_id", "vendor_id"):
            run(r.resolve(col, "X-1"))
        assert [LIVE_SQL in s for s in seen] == [False, False, False, True]


class TestAnIdTheFileAlreadyCarries:
    def test_an_old_id_naming_a_retired_vendor_is_not_linked_and_is_reported(self):
        db = Register([{"id": RETIRED, "name": "Apex (deleted)", "status": "deleted"}])
        r = resolver(db)
        assert run(r.linkable("vendor_id", RETIRED)) is False
        assert r.report()["unresolved"] == {"vendor_id": [RETIRED]}

    def test_an_id_naming_a_live_vendor_is_kept(self):
        db = Register([{"id": LIVE, "name": "Apex", "status": "active"}])
        r = resolver(db)
        assert run(r.linkable("vendor_id", LIVE)) is True
        assert r.report()["unresolved"] == {}

    def test_an_id_naming_no_vendor_yet_is_kept(self):
        """It may be a vendor this same file writes; that is not this check's to refuse."""
        db = Register([{"id": LIVE, "name": "Apex", "status": "active"}])
        assert run(resolver(db).linkable("vendor_id", UNKNOWN)) is True

    def test_the_id_matches_whatever_its_case(self):
        db = Register([{"id": RETIRED, "status": "deleted"}])
        assert run(resolver(db).linkable("vendor_id", RETIRED.upper())) is False

    def test_a_check_that_could_not_be_read_refuses_the_link(self):
        """The writer's fetch answers [] when a lookup raises. A count always returns a row,
        so [] can only mean the check failed — and linking new work to a vendor nobody could
        confirm is live is the mistake this exists to prevent."""
        async def broken(sql, params):
            return []

        r = rl.ReferenceResolver(broken, ORG, "plenum_cafm")
        assert run(r.linkable("vendor_id", LIVE)) is False
        assert r.report()["unresolved"] == {"vendor_id": [LIVE]}

    def test_a_failed_check_is_asked_again_rather_than_remembered(self):
        calls = {"n": 0}

        async def flaky(sql, params):
            calls["n"] += 1
            return [] if calls["n"] == 1 else [(0,)]

        r = rl.ReferenceResolver(flaky, ORG, "plenum_cafm")
        assert run(r.linkable("vendor_id", LIVE)) is False
        assert run(r.linkable("vendor_id", LIVE)) is True

    def test_a_year_of_rows_carrying_one_id_costs_one_read(self):
        db = Register([{"id": RETIRED, "status": "deleted"}, {"id": LIVE, "status": "active"}])
        r = resolver(db)
        for _ in range(200):
            assert run(r.linkable("vendor_id", RETIRED)) is False
            assert run(r.linkable("vendor_id", LIVE)) is True
        assert db.reads == 2

    def test_a_column_with_no_retired_state_is_not_checked(self):
        db = Register([])
        r = resolver(db)
        assert run(r.linkable("asset_id", RETIRED)) is True
        assert run(r.linkable("contract_id", RETIRED)) is True
        assert db.reads == 0


# ── the writer ─────────────────────────────────────────────────────────────────────────────

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import write_node as wn  # noqa: E402

WN_SRC = open(wn.__file__, encoding="utf-8").read()


class TestARetiredVendorIsNotAlreadyWritten:
    def test_the_vendor_key_lookup_ignores_retired_rows(self):
        sql, params = wn._natural_key_sql(
            "plenum_cafm", "vendors", ("vendor_name",), ["Apex Mechanical Ltd"], True, ORG, {})
        assert sql == (
            "SELECT 1 FROM plenum_cafm.vendors t WHERE vendor_name = :v0 "
            "AND organization_id::text = :org "
            "AND COALESCE(lower(to_jsonb(t)->>'status'), 'active') <> 'deleted' LIMIT 1"
        )
        assert params == {"v0": "Apex Mechanical Ltd", "org": ORG}

    def test_another_tables_key_lookup_asks_what_it_always_asked(self):
        sql, params = wn._natural_key_sql(
            "plenum_cafm", "buildings", ("building_code",), ["B-101"], True, ORG, {})
        assert sql == ("SELECT 1 FROM plenum_cafm.buildings t WHERE building_code = :v0 "
                       "AND organization_id::text = :org LIMIT 1")
        assert params == {"v0": "B-101", "org": ORG}

    def test_an_empty_optional_column_and_a_rounded_numeric_still_match_as_before(self):
        sql, params = wn._natural_key_sql(
            "plenum_cafm", "asset_reading_bands", ("reading_type", "unit", "lo"),
            ["Supply temp", None, 7.700000000000001], False, ORG, {"lo": 4})
        assert sql == ("SELECT 1 FROM plenum_cafm.asset_reading_bands t WHERE reading_type = :v0 "
                       "AND unit IS NULL AND lo = round(CAST(:v2 AS numeric), 4) LIMIT 1")
        assert params == {"v0": "Supply temp", "v2": 7.700000000000001}

    def test_the_writer_asks_through_it(self):
        assert "_natural_key_sql(" in WN_SRC.split("async def _already_written", 1)[1][:1500]


class TestTheWriterChecksAnIdTheFileCarries:
    def test_a_carried_id_is_checked_and_dropped_when_it_names_a_retired_vendor(self):
        loop = WN_SRC.split("for _ref_col in REFERENCES:", 1)[1][:900]
        assert "if not await _refs.linkable(_ref_col, safe_row.get(_ref_col)):" in loop
        assert loop.index("_refs.linkable(") < loop.index("_rh = hint_for(")


class TestTheEngineAsksTheSameQuestion:
    """Go is the default engine and Python the kill switch: both must refuse the same rows."""

    @staticmethod
    def _go(*parts: str) -> str:
        return open(os.path.join(_SM, "engine", "internal", *parts), encoding="utf-8").read()

    def test_the_python_predicate_is_ops_intelligences(self):
        assert rl.LIVE_ROW == {"vendors": LIVE_SQL}

    def test_the_go_resolver_carries_the_same_predicate(self):
        assert f'"{LIVE_SQL}"' in self._go("resolve", "resolve.go")

    def test_the_go_key_lookups_use_it(self):
        src = self._go("write", "insert.go")
        assert src.count("resolve.LiveRow(p.dest)") == 2
