"""A spreadsheet's columns, mapped to the tables they belong in.

The migration flow's canonical registry covered assets, work orders, parts, scheduled PM and
users. Energy was not in it at all, so a half-hourly export or a supplier bill had no target
and became rows nobody could query. This maps headers against the SAME catalogue the document
extractor uses, because a field's destination has to be declared once: two registries
disagreeing about where serial_number lands is how a column silently stops arriving while the
import still reports success.
"""
from src.engines.ingestion import tabular_mapping as tm

ASSET_REGISTER = ["Asset Tag", "Equipment Name", "Make", "Model No", "Serial No",
                  "Date Installed", "Warranty End", "Asset Criticality", "Replacement Cost",
                  "Useful Life", "Location Code", "Parent Asset"]
ENERGY_EXPORT = ["MPAN", "Meter Serial", "Fuel", "UOM", "Read From", "Read To",
                 "Consumption kWh", "Rate", "Standing Charge", "Supplier"]
PPM_SCHEDULE = ["PPM Reference", "Service Frequency", "Due Date", "Completion Date",
                "Job Number", "Work Description", "WO Type", "Engineer", "Site"]


def lands_of(result, header):
    return next((m["lands"] for m in result["mapped"] if m["header"] == header), None)


class TestTheThreeDomainsThatHadNoMapping:

    def test_an_energy_export_reaches_the_meter_tables(self):
        """MPAN had no canonical target at all, so the file imported into nothing.

        energy_meters, not `meters`: meter_readings.meter_id carries a foreign key to
        energy_meters.id. `meters` has the plausible column names and 18 rows nothing reads.
        """
        r = tm.map_headers(ENERGY_EXPORT)
        assert lands_of(r, "MPAN") == "energy_meters.mpan"
        assert lands_of(r, "Fuel") == "energy_meters.meter_type"
        assert lands_of(r, "Consumption kWh") == "meter_readings.consumption_kwh"

    def test_an_asset_register_reaches_the_asset_table(self):
        r = tm.map_headers(ASSET_REGISTER)
        assert lands_of(r, "Asset Tag") == "assets.asset_code"
        assert lands_of(r, "Serial No") == "assets.serial_number"
        assert lands_of(r, "Date Installed") == "assets.installation_date"
        assert r["unmapped"] == []

    def test_a_ppm_schedule_splits_across_the_tables_it_actually_touches(self):
        """One sheet is not one table. A PPM export carries visit, work order and inspection
        fields, and writing it into one table would lose two thirds of it."""
        r = tm.map_headers(PPM_SCHEDULE)
        tables = {t["table"] for t in r["tables"]}
        assert {"ppm_visits", "work_orders"} <= tables
        assert lands_of(r, "PPM Reference") == "ppm_visits.ppm_ref"
        assert lands_of(r, "Job Number") == "work_orders.wo_code"


class TestAHeaderIsMatchedOrItIsUnmapped:

    def test_an_unknown_column_is_named_not_guessed(self):
        r = tm.map_headers(["Asset Tag", "Zorblatt Index"])
        assert r["unmapped"] == ["Zorblatt Index"]
        assert all(m["header"] != "Zorblatt Index" for m in r["mapped"])

    def test_matching_survives_the_punctuation_real_exports_carry(self):
        for header in ("Asset_Code", "ASSET CODE", "asset-code", "Asset Code #"):
            r = tm.map_headers([header])
            assert lands_of(r, header) == "assets.asset_code", header

    def test_every_match_says_how_it_was_made(self):
        """A mapping nobody can question is a mapping nobody can correct."""
        r = tm.map_headers(["serial_number", "Make"])
        hows = {m["header"]: m["how"] for m in r["mapped"]}
        assert hows["serial_number"] == "field name"
        assert hows["Make"] == "alias"

    def test_an_empty_sheet_is_not_an_error(self):
        r = tm.map_headers([])
        assert r["mapped"] == [] and r["unmapped"] == [] and r["coverage_pct"] == 0.0


class TestAmbiguityIsRefusedOrResolvedOnEvidence:

    def test_an_exact_field_name_beats_an_alias_without_needing_the_sheet(self):
        """"Asset Code" IS the name of assets.asset_code and merely an alias of
        maintenance.asset_code_ref. The literal wins; there is nothing to break."""
        r = tm.map_headers(["Asset Code"])
        assert lands_of(r, "Asset Code") == "assets.asset_code"
        assert r["ambiguous"] == []

    def test_the_sheet_decides_when_a_header_is_an_alias_of_both(self):
        """'Asset Tag' is an alias under assets AND maintenance at equal strength, so the
        rest of the sheet is the only evidence for which one is meant."""
        assert lands_of(tm.map_headers(ASSET_REGISTER), "Asset Tag") == "assets.asset_code"
        ppm = tm.map_headers(["Asset Tag", "PPM Reference", "Service Frequency",
                              "Due Date", "Completion Date"])
        assert lands_of(ppm, "Asset Tag") == "ppm_visits.asset_code"

    def test_a_header_with_nothing_else_to_judge_it_by_stays_unresolved(self):
        """A draw stays a draw. Picking the first in dictionary order is a coin toss wearing
        a confidence score."""
        r = tm.map_headers(["Asset Tag"])
        assert r["mapped"] == []
        assert r["ambiguous"] and r["ambiguous"][0]["header"] == "Asset Tag"
        assert len(r["ambiguous"][0]["candidates"]) > 1

    def test_naming_the_domain_settles_it_outright(self):
        r = tm.map_headers(["Asset Code"], domains=["assets"])
        assert lands_of(r, "Asset Code") == "assets.asset_code"
        assert r["ambiguous"] == []


class TestTwoColumnsCannotShareOneTarget:

    def test_a_collision_is_held_back_rather_than_written(self):
        """The second write lands on top of the first and the import reports success. That is
        data loss that looks like a clean run."""
        r = tm.map_headers(["Make", "Manufacturer"])
        assert r["collisions"], "both target assets.manufacturer"
        assert r["collisions"][0]["lands"] == "assets.manufacturer"
        assert set(r["collisions"][0]["headers"]) == {"Make", "Manufacturer"}
        assert all(m["lands"] != "assets.manufacturer" for m in r["mapped"])

    def test_the_collision_reaches_the_import_plan_as_blocked(self):
        p = tm.import_plan(["Make", "Manufacturer"], row_count=10)
        assert any("overwrite" in b["why"] for b in p["blocked"])

    def test_a_serial_and_a_supply_number_no_longer_share_one_column(self):
        """Both pointed at mpan_mprn, so a sheet carrying each would have written the serial
        over the supply number. On energy_meters they are separate columns and both land."""
        r = tm.map_headers(ENERGY_EXPORT)
        assert lands_of(r, "MPAN") == "energy_meters.mpan"
        assert lands_of(r, "Meter Serial") == "energy_meters.dcc_device_id"
        assert r["collisions"] == []

    def test_a_field_with_no_column_on_this_schema_is_named_not_written(self):
        """A unit and a standing charge have nowhere to live on this schema. Both are read and
        shown; neither is written into a column that would ignore it."""
        r = tm.map_headers(ENERGY_EXPORT)
        no_target = {n["header"] for n in r["no_target"]}
        assert {"UOM", "Standing Charge"} <= no_target


class TestTheImportPlanIsHonestBeforeItWrites:

    def test_it_says_which_tables_and_how_many_rows(self):
        p = tm.import_plan(ASSET_REGISTER, row_count=1200)
        t = next(x for x in p["would_write"] if x["table"] == "assets")
        assert t["rows"] == 1200
        assert "asset_code" in t["columns"]

    def test_coverage_is_reported_so_a_thin_mapping_is_visible(self):
        p = tm.import_plan(["Serial No", "Nonsense One", "Nonsense Two", "Nonsense Three"])
        assert p["coverage_pct"] == 25.0
        assert len(p["unmapped"]) == 3
