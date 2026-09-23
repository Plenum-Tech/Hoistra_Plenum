"""A migrated meter finds its building, and a migrated reading finds its meter.

The writer learned on 21 Sep 2026 to resolve a site reference into a building, but only for
assets and work orders. Energy went through the same writer and came out unlinked, and that
failure is silent in a way the asset one was not: `energy_meters.building_id` has no
constraint behind it, so the meter inserts, the anomaly scan sweeps it, and every finding is
written with a null building. Proved on hoistra_test on 22 Sep 2026 with a meter carrying
1,680 readings and a deliberate weekend fault: the scan reported success and one finding, the
finding had no building, nothing was visible on a building-scoped page, and no EUI snapshot
was written at all.

`meter_readings` failed louder. `meter_id` is NOT NULL behind a real foreign key and nothing
filled it, so every reading row was skipped.

These tests hold the resolution to what a CMMS export actually looks like. The module under
test is pure; the database is a dict here. Loaded by path so no service import chain is needed.
"""
import ast
import asyncio
import importlib.util
import os

import pytest

_HERE = os.path.dirname(__file__)
_NODES = os.path.join(_HERE, "..", "src", "graph", "nodes")
_SPEC = importlib.util.spec_from_file_location(
    "meter_link", os.path.join(_NODES, "meter_link.py")
)
ml = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ml)

ORG = "00000000-0000-0000-0000-000000000001"
HARBOUR = "c343c566-8cc2-40d4-93d3-5cf1ec52d26f"
ELEC = "91a81472-b211-4af9-acc7-91131a41f2b4"
GAS = "cec9c390-3ea7-479b-baec-4887af05025e"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakeDb:
    """energy_meters as the resolver queries it; records every read and every create."""

    def __init__(self, meters):
        self.meters = list(meters)      # [(id, mpan, mprn)]
        self.reads = 0
        self.creates = []

    async def fetch(self, sql, params):
        self.reads += 1
        k = params["k"]
        return [(m[0],) for m in self.meters
                if k in {(m[1] or "").lower(), (m[2] or "").lower(), m[0]}]

    async def create(self, *, mpan, mprn, meter_type, building_id,
                     section_id=None, is_sub_meter=False):
        new_id = f"created-{len(self.creates)}"
        self.creates.append({"mpan": mpan, "mprn": mprn, "meter_type": meter_type,
                             "building_id": building_id, "section_id": section_id,
                             "is_sub_meter": is_sub_meter})
        self.meters.append((new_id, mpan, mprn))
        return new_id


def resolver(db, *, can_create=True):
    return ml.MeterResolver(db.fetch, ORG, "plenum_cafm",
                            create=db.create if can_create else None)


class TestWhatNamesAMeter:
    def test_an_mpan_names_a_meter(self):
        assert ml.meter_hint({"MPAN": "NB-B-101-E0", "kwh": 4.2}) == "NB-B-101-E0"

    def test_so_does_an_mprn_and_a_customer_reference(self):
        assert ml.meter_hint({"mprn": "NB-B-101-G1"}) == "NB-B-101-G1"
        assert ml.meter_hint({"Meter Reference": "MTR-0042"}) == "MTR-0042"
        assert ml.meter_hint({"meter_serial": "K12345"}) == "K12345"

    def test_a_row_that_already_carries_a_real_meter_id_needs_no_resolution(self):
        assert ml.meter_hint({"meter_id": ELEC, "mpan": "NB-B-101-E0"}) is None

    def test_a_customer_reference_under_our_own_header_is_still_a_hint(self):
        """`meter_id` holding "MTR-7" is the customer's id, not ours."""
        assert ml.meter_hint({"meter_id": "MTR-7"}) == "MTR-7"

    def test_a_row_naming_nothing_returns_nothing(self):
        assert ml.meter_hint({"reading_at": "2026-09-01", "kwh": 4.2}) is None
        assert ml.meter_hint(None) is None


class TestWhichSupplyNumber:
    def test_a_named_column_is_taken_at_its_word(self):
        assert ml.supply_numbers({"mpan": "1200023305687"}) == ("1200023305687", None)
        assert ml.supply_numbers({"MPRN": "8841125"}) == (None, "8841125")

    def test_an_ambiguous_column_is_decided_by_the_rows_own_fuel(self):
        assert ml.supply_numbers({"supply_number": "8841125", "fuel": "Gas"}) == (None, "8841125")
        assert ml.supply_numbers({"supply_number": "1200023", "fuel": "Electricity"}) == ("1200023", None)

    def test_an_ambiguous_column_with_no_fuel_falls_to_electricity(self):
        """A wrong guess here decides which column the reference is stored under. It does not
        decide which building the meter lands on, which is the part that matters."""
        assert ml.supply_numbers({"meter_ref": "MTR-9"}) == ("MTR-9", None)

    def test_a_uuid_is_not_a_supply_number(self):
        assert ml.supply_numbers({"meter_id": ELEC}) == (None, None)


class TestTheFuel:
    def test_the_sheet_is_believed_when_it_says(self):
        assert ml.meter_type_for({"mpan": "120002", "fuel_type": "Gas"}) == "gas"
        assert ml.meter_type_for({"mprn": "884112", "meter_type": "Electricity"}) == "electricity"

    def test_otherwise_an_mprn_alone_is_gas(self):
        assert ml.meter_type_for({"mprn": "8841125"}) == "gas"

    def test_and_an_mpan_alone_is_electricity(self):
        assert ml.meter_type_for({"mpan": "1200023305687"}) == "electricity"

    def test_a_row_that_says_nothing_is_electricity_rather_than_null(self):
        """meter_type is NOT NULL with no default. Before this every row of a meter sheet
        without a fuel column failed outright."""
        assert ml.meter_type_for({"reading_at": "2026-09-01"}) == "electricity"


class TestResolvingToAMeter:
    def test_a_reading_finds_the_meter_already_on_record(self):
        db = FakeDb([(ELEC, "NB-B-101-E0", None), (GAS, None, "NB-B-101-G1")])
        assert run(resolver(db).resolve("NB-B-101-E0")) == ELEC
        assert run(resolver(db).resolve("NB-B-101-G1")) == GAS

    def test_a_year_of_half_hours_costs_one_lookup(self):
        """17,520 rows per meter. Without the cache this is 17,520 round trips."""
        db = FakeDb([(ELEC, "NB-B-101-E0", None)])
        r = resolver(db)
        for _ in range(500):
            assert run(r.resolve("NB-B-101-E0")) == ELEC
        assert db.reads == 1

    def test_a_meter_not_on_record_is_created_against_the_building_the_row_names(self):
        db = FakeDb([])
        r = resolver(db)
        got = run(r.resolve("NB-B-102-G1", building_id=HARBOUR, meter_type="gas",
                            mprn="NB-B-102-G1"))
        assert got == "created-0"
        assert db.creates == [{"mpan": None, "mprn": "NB-B-102-G1", "meter_type": "gas",
                               "building_id": HARBOUR, "section_id": None,
                               "is_sub_meter": False}]
        assert r.created == 1

    def test_a_meter_is_never_created_without_a_building(self):
        """The rule the whole module exists for. A meter with no building satisfies the
        foreign key, so the readings land and the run reports success, and every figure they
        should have produced is attributed to nothing."""
        db = FakeDb([])
        r = resolver(db)
        assert run(r.resolve("NB-B-999-E0", building_id=None)) is None
        assert db.creates == []
        assert r.unlinked == ["NB-B-999-E0"]

    def test_that_refusal_is_reported_rather_than_swallowed(self):
        db = FakeDb([])
        r = resolver(db)
        run(r.resolve("MTR-1"))
        run(r.resolve("MTR-2"))
        assert r.report()["unlinked"] == ["MTR-1", "MTR-2"]
        assert r.report()["created"] == 0

    def test_a_reference_answering_to_two_meters_resolves_to_neither(self):
        """Guessing one puts a year of consumption on the wrong building."""
        db = FakeDb([(ELEC, "SHARED", None), (GAS, None, "SHARED")])
        r = resolver(db)
        assert run(r.resolve("SHARED", building_id=HARBOUR)) is None
        assert r.ambiguous == ["shared"]
        assert db.creates == [], "an ambiguous reference must not create a third meter"

    def test_an_existing_meter_is_reused_rather_than_duplicated(self):
        db = FakeDb([(ELEC, "NB-B-101-E0", None)])
        r = resolver(db)
        assert run(r.resolve("NB-B-101-E0", building_id=HARBOUR)) == ELEC
        assert db.creates == []

    def test_a_meter_created_once_serves_the_rest_of_the_run(self):
        db = FakeDb([])
        r = resolver(db)
        for _ in range(10):
            run(r.resolve("NEW-1", building_id=HARBOUR, mpan="NEW-1"))
        assert len(db.creates) == 1
        assert r.created == 1

    def test_nothing_at_all_resolves_to_nothing(self):
        db = FakeDb([(ELEC, "NB-B-101-E0", None)])
        r = resolver(db)
        assert run(r.resolve(None)) is None
        assert run(r.resolve("   ")) is None
        assert db.reads == 0


class TestTheWriterActuallyUsesIt:
    """The resolution above is worth nothing if the writer's guard still names two tables.

    write_node.py is read rather than imported: it pulls in the service's whole model and
    logging chain, and the sibling building-link test avoids that the same way.
    """

    @staticmethod
    def _constants():
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        out = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name.startswith("_BUILDING"):
                    try:
                        out[name] = ast.literal_eval(node.value)
                    except ValueError:
                        pass
        return out, src

    def test_a_meter_is_one_of_the_tables_whose_building_is_resolved(self):
        consts, _ = self._constants()
        assert "energy_meters" in consts["_BUILDING_LINKED_TABLES"]
        assert "assets" in consts["_BUILDING_LINKED_TABLES"]
        assert "work_orders" in consts["_BUILDING_LINKED_TABLES"]

    def test_a_reading_sheet_is_read_for_the_site_it_names(self):
        """meter_readings has no building column, but the sheet often names the site, and that
        is what decides whether a meter can be created for it."""
        consts, _ = self._constants()
        assert "meter_readings" in consts["_BUILDING_HINT_TABLES"]

    def test_a_meter_with_no_site_falls_back_to_the_asset_it_names(self):
        consts, _ = self._constants()
        assert "energy_meters" in consts["_BUILDING_VIA_ASSET_TABLES"]

    def test_the_old_two_table_guard_is_gone(self):
        _, src = self._constants()
        assert 'safe_table in ("assets", "work_orders")' not in src

    def test_the_writer_resolves_a_meter_for_reading_rows(self):
        _, src = self._constants()
        assert "_meters.resolve(" in src
        assert 'safe_table == "meter_readings"' in src


class TestTheSheetReachesTheRightTable:
    """Resolution is worth nothing if a meter sheet never routes to energy_meters.

    deterministic_mapper.py is read rather than imported, for the same reason write_node.py is.
    """

    @staticmethod
    def _constants():
        src = open(os.path.join(_NODES, "deterministic_mapper.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        out = {}
        wanted = ("_TABLE_NAME_PATTERNS", "_ENTITY_FIELD_SIGNALS", "_DIRECT_NAME_DENY")
        for node in ast.walk(tree):
            # _ENTITY_FIELD_SIGNALS carries a type annotation, which makes it an AnnAssign
            # rather than an Assign. Reading only one of the two silently finds nothing.
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name, value = node.targets[0].id, node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name, value = node.target.id, node.value
            else:
                continue
            if name in wanted and value is not None:
                try:
                    out[name] = ast.literal_eval(value)
                except ValueError:
                    pass
        return out

    def _route(self, sheet):
        """First matching pattern wins, which is what the writer does."""
        low = sheet.lower().strip()
        for pattern, entity in self._constants()["_TABLE_NAME_PATTERNS"]:
            if pattern in low:
                return entity
        return None

    def test_a_readings_sheet_routes_to_readings_not_to_meters(self):
        """"Meter Readings" contains "meter". Routed to energy_meters it would write a meter
        per half hour and no consumption at all."""
        for sheet in ("Meter Readings", "meter_readings", "Half Hourly Data",
                      "Consumption 2026", "Electricity Readings"):
            assert self._route(sheet) == "meter_readings", sheet

    def test_the_names_a_half_hourly_export_actually_carries(self):
        """The Northbridge files are named harbour_point_electricity_halfhourly.csv. "half
        hour" with a separator does not match "halfhourly", so the pattern that was supposed
        to catch them caught nothing."""
        for sheet in ("harbour_point_electricity_halfhourly",
                      "ashgrove_court_gas_halfhourly",
                      "site_kwh_export", "Half Hourly Data", "half_hourly_2026"):
            assert self._route(sheet) == "meter_readings", sheet

    def test_a_meter_sheet_routes_to_energy_meters(self):
        for sheet in ("Meters", "Energy Meters", "MPAN List", "MPRN Register"):
            assert self._route(sheet) == "energy_meters", sheet

    def test_the_vestigial_meters_table_cannot_win_a_direct_name_match(self):
        """plenum_cafm.meters is an eighteen-row register that nothing reads for consumption.
        A sheet called "Meters" matched it by name before energy_meters was ever considered."""
        assert "meters" in self._constants()["_DIRECT_NAME_DENY"]

    def test_the_energy_patterns_did_not_capture_anyone_elses_sheet(self):
        cases = {"Asset Register": "assets", "Equipment List": "assets",
                 "Work Orders": "work_orders", "Scheduled PM": "maintenance_plans",
                 "Parts": "spare_parts", "Sites": "locations",
                 "Inspections": "findings", "Technicians": "technicians"}
        for sheet, expected in cases.items():
            assert self._route(sheet) == expected, f"{sheet} -> {self._route(sheet)}"

    def test_a_sheet_is_also_recognised_by_the_columns_it_carries(self):
        signals = self._constants()["_ENTITY_FIELD_SIGNALS"]
        assert "mpan" in signals["energy_meters"]
        assert "mprn" in signals["energy_meters"]
        assert "consumption_kwh" in signals["meter_readings"]
        assert "reading_at" in signals["meter_readings"]


class TestTheWriteActuallyHappens:
    """The resolving write path must be chosen, not reached by accident.

    It used to live only inside the SQL artifact's exception handler. Emptying the artifact
    to force it therefore did the opposite of what it looked like: control fell past both
    writes to a POST to svc-ingestion, a service this deployment does not run, and a
    migration that had passed every gate died with "Cannot connect to host svc-ingestion:8001"
    having written nothing at all.
    """

    @staticmethod
    def _write_node():
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "write_node":
                return node
        raise AssertionError("write_node not found")

    def test_the_resolving_write_is_not_only_an_error_handler(self):
        fn = self._write_node()
        handled = {id(n) for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)
                   for n in ast.walk(h)}
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "_apply_records_with_schema_alignment"]
        assert calls, "the resolving write is not called at all"
        assert any(id(c) not in handled for c in calls), \
            "the resolving write is only reachable when something else has already failed"

    def test_the_three_write_paths_are_one_chain(self):
        """aligned, then the artifact, then the handoff. Not a fall-through."""
        fn = self._write_node()
        for node in ast.walk(fn):
            if isinstance(node, ast.If) and ast.unparse(node.test) == "_use_aligned":
                chain = ["_use_aligned"]
                cur = node
                while cur.orelse and len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If):
                    cur = cur.orelse[0]
                    chain.append(ast.unparse(cur.test))
                assert chain == ["_use_aligned", "sql_script"], chain
                assert cur.orelse, "there is still a handoff branch for everything else"
                return
        raise AssertionError("no _use_aligned branch")

    def test_energy_tables_never_reach_the_handoff(self):
        """svc-ingestion is not part of this deployment. A meter sheet must not depend on it."""
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        assert "_use_aligned = bool(_needs) and isinstance(state.get(\"cleaned_tables\"), dict)" in src
        assert "meter_readings" in src

    def test_a_run_with_nothing_to_align_says_so_rather_than_silently_writing_literals(self):
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        assert "no cleaned tables are on the state" in src


class TestAMeterPerFloor:
    """A tower with a meter on every floor.

    Those rows are identical but for where they say the meter sits. Created without that,
    each one becomes the building's incoming supply, and the building's consumption is counted
    once per floor — a number that is wrong by a factor of however many meters there are, and
    wrong in the direction that looks like a finding.
    """

    @pytest.mark.parametrize("row,expected", [
        ({"Floor": "Level 3"}, "Level 3"),
        ({"section": "Tenant floors"}, "Tenant floors"),
        ({"Zone": "Car park"}, "Car park"),
        ({"Level": "7"}, "7"),
        ({"Demise": "Unit 4B"}, "Unit 4B"),
    ])
    def test_a_row_says_where_its_meter_sits(self, row, expected):
        assert ml.section_hint(row) == expected

    def test_a_building_level_meter_names_nowhere(self):
        assert ml.section_hint({"mpan": "NB-B-101-E0", "kwh": 4.2}) is None

    def test_a_meter_that_sits_somewhere_is_a_sub_meter(self):
        assert ml.is_sub_meter_for({"mpan": "X", "floor": "Level 3"}) is True

    def test_a_meter_that_names_nowhere_is_the_incoming_supply(self):
        assert ml.is_sub_meter_for({"mpan": "NB-B-101-E0"}) is False

    @pytest.mark.parametrize("value,expected", [
        ("true", True), ("Yes", True), ("1", True), ("sub-meter", True), ("tenant", True),
        ("false", False), ("No", False), ("0", False), ("main", False), ("incoming", False),
    ])
    def test_a_sheet_that_says_outright_is_believed(self, value, expected):
        assert ml.is_sub_meter_for({"is_sub_meter": value, "floor": "Level 3"}) is expected, value

    def test_saying_main_beats_naming_a_floor(self):
        """An incoming supply sitting in the basement plant room is still the incoming supply."""
        assert ml.is_sub_meter_for({"meter_role": "main", "floor": "Basement"}) is False

    def test_creation_carries_both(self):
        assert "is_sub_meter" in ml.CREATE_METER_SQL
        assert "section_id" in ml.CREATE_METER_SQL

    def test_a_section_is_looked_up_within_its_own_building(self):
        """"Level 3" means this building's third floor, not another tower's."""
        sql = ml.SECTION_LOOKUP_SQL
        assert "s.building_id = CAST(:b AS uuid)" in sql
        assert "floors" in sql and "f.level" in sql

    def test_the_writer_places_a_meter_sheet_row(self):
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        assert 'if safe_table == "energy_meters":' in src
        assert "_section_for(" in src
        assert "is_sub_meter_for(row)" in src


class TestIngestingTheSameMeterTwice:
    """A meter sheet listing a meter already on record must update it, not add a second row.

    Proved on hoistra_test on 23 Sep 2026. A per-building workbook was ingested that carried an
    Energy_Meters sheet, and Ashgrove Court came out with four meters where it has two:
    NB-B-102-E0 and NB-B-102-G1 each twice, the second copy carrying no readings. Nothing in
    the schema stops that — energy_meters has no unique index on mpan or mprn, so two rows for
    one supply insert as cleanly as one.

    The damage is not cosmetic. The building rollup sums per meter, so a supply with two rows
    is counted twice the moment the duplicate carries readings, and the EUI it produces is the
    number every card on the Energy page is derived from: the benchmark comparison, the excess,
    and the cost above benchmark.

    The resolver already knew how to find a meter by its supply number — it did so for every
    reading row. It was simply never asked on a meter row.
    """

    def test_a_supply_number_already_on_record_finds_its_meter(self):
        db = FakeDb([(ELEC, "NB-B-102-E0", None)])
        found = run(resolver(db).find("NB-B-102-E0"))
        assert found == ELEC
        assert db.creates == [], "looking a meter up must never create one"

    def test_a_supply_number_not_on_record_finds_nothing_and_still_creates_nothing(self):
        db = FakeDb([(ELEC, "NB-B-102-E0", None)])
        assert run(resolver(db).find("NB-B-101-E0")) is None
        assert db.creates == [], "an unknown meter is created by the writer, with its building"

    def test_a_blank_reference_is_not_a_lookup(self):
        db = FakeDb([(ELEC, "NB-B-102-E0", None)])
        for blank in (None, "", "   "):
            assert run(resolver(db).find(blank)) is None
        assert db.reads == 0

    def test_asking_twice_asks_the_database_once(self):
        db = FakeDb([(GAS, None, "NB-B-102-G1")])
        r = resolver(db)
        assert run(r.find("NB-B-102-G1")) == GAS
        assert run(r.find("nb-b-102-g1")) == GAS
        assert db.reads == 1

    def test_the_writer_asks_before_inserting_a_meter_row(self):
        """The whole fix. Without this call the branch inserts blind and duplicates."""
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        branch = src.split('if safe_table == "energy_meters":', 1)[1].split("\n\n", 1)[0]
        assert "_meters.find(" in branch, "a meter row must be matched against the register"
        assert 'safe_row["id"] = ' in branch, "a match must be reused as the row's own id"


class TestLookingForAMeterDoesNotStopItBeingMade:
    """The meter sheet and the reading sheet share one resolver, and one cache.

    resolve() answers from that cache BEFORE it looks anything up or creates anything, so a null
    left in it is final for the rest of the run. find() used to write its misses there, which
    made this sequence fatal:

      1. the Energy_Meters sheet asks find('nb-b-101-e0') — not on record yet — caching None
      2. the writer inserts that meter
      3. the Meter_Readings rows ask resolve('nb-b-101-e0') — the cached None comes straight
         back, with no lookup and no create

    meter_id is NOT NULL, so all 35,040 readings were rejected against a meter that existed by
    then. The log said "lookup and create both returned nothing", and it was right: neither ran.

    A hit is safe to remember — a meter does not stop existing mid-run. A miss is true only at
    the instant it is asked, because the caller asks precisely because it is about to insert one.
    """

    def test_a_miss_does_not_stop_a_later_resolve_creating_the_meter(self):
        db = FakeDb([])                                  # nothing on record yet
        r = resolver(db)
        assert run(r.find("NB-B-101-E0")) is None        # the meter sheet asks first
        got = run(r.resolve("NB-B-101-E0", building_id=HARBOUR, meter_type="electricity",
                            mpan="NB-B-101-E0"))
        assert got is not None, "the cached miss must not survive into resolve()"
        assert db.creates, "resolve() must still be free to create the meter"

    def test_a_miss_then_an_insert_then_a_resolve_finds_the_inserted_meter(self):
        """The real order of a workbook: sheet asks, writer inserts, readings resolve."""
        db = FakeDb([])
        r = resolver(db)
        run(r.find("NB-B-101-E0"))
        db.meters.append((ELEC, "NB-B-101-E0", None))    # the Energy_Meters row lands
        assert run(r.resolve("NB-B-101-E0", building_id=HARBOUR)) == ELEC
        assert not db.creates, "it was already there; creating a second is the other bug"

    def test_a_hit_is_still_cached_so_a_year_of_readings_costs_one_read(self):
        db = FakeDb([(ELEC, "NB-B-101-E0", None)])
        r = resolver(db)
        assert run(r.find("NB-B-101-E0")) == ELEC
        for _ in range(50):
            assert run(r.resolve("NB-B-101-E0", building_id=HARBOUR)) == ELEC
        assert db.reads == 1, "the whole point of the cache"

    def test_find_still_never_creates(self):
        db = FakeDb([])
        assert run(resolver(db).find("NB-B-101-E0")) is None
        assert db.creates == []
