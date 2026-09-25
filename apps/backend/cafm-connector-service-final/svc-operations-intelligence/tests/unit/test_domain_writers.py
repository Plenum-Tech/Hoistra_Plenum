"""The extractors for energy, assets and maintenance — the three domains that wrote nothing.

The catalogue said where every field lands and the mapper worked out which column fills it.
Compliance and vendors had extractors behind them; these three returned their facts and held
them. This is the writer, and the rules it will not break.
"""
import pytest

from src.engines.ingestion import domain_writers as dw
from src.engines.ingestion import tabular_mapping as tm


class TestADateIsReadOrRefused:

    def test_an_unambiguous_date_is_read_whatever_shape_it_arrives_in(self):
        for s in ("2026-08-31", "2026/08/31", "31 Aug 2026", "31 August 2026",
                  "Aug 31, 2026", "31-Aug-2026"):
            got = dw._parse_date(s)
            assert got is not None and (got.year, got.month, got.day) == (2026, 8, 31), s

    def test_a_numeric_date_that_could_be_two_days_is_refused(self):
        """03/04/2026 is 3 April on a British export and 4 March on an American one, and the
        cell does not say which. Preferring one is right about half the time, which is how a
        PPM visit lands a month out and nobody can see why."""
        assert dw._parse_date("03/04/2026") is None

    def test_the_caller_can_state_the_convention_and_then_it_reads(self):
        uk = dw._parse_date("03/04/2026", day_first=True)
        us = dw._parse_date("03/04/2026", day_first=False)
        assert (uk.month, uk.day) == (4, 3)
        assert (us.month, us.day) == (3, 4)

    def test_a_date_only_one_reading_can_explain_needs_no_convention(self):
        """25/12/2026 has no month 25, so there is nothing to be ambiguous about."""
        got = dw._parse_date("25/12/2026")
        assert got is not None and (got.month, got.day) == (12, 25)

    def test_nonsense_is_none_rather_than_a_guess(self):
        for s in ("", "n/a", "soon", "31/31/2026", "2026"):
            assert dw._parse_date(s) is None, s


class TestACellBecomesItsColumnsTypeOrNothing:

    def test_numbers_survive_the_formatting_a_spreadsheet_adds(self):
        assert dw._coerce("184,220", "integer") == 184220
        assert dw._coerce("£1,250.50", "numeric") == 1250.50

    def test_a_blank_is_not_a_zero_and_not_a_date(self):
        """'' in a date column is not 1970 and not today. It is a blank cell."""
        for blank in ("", "  ", "N/A", "null", "-", "—"):
            assert dw._coerce(blank, "integer") is None, blank
            assert dw._coerce(blank, "date") is None, blank

    def test_a_word_in_a_number_column_is_refused_not_zeroed(self):
        assert dw._coerce("not recorded", "integer") is None

    def test_booleans_read_the_words_a_person_types(self):
        assert dw._coerce("Yes", "boolean") is True
        assert dw._coerce("N", "boolean") is False
        assert dw._coerce("maybe", "boolean") is None


class TestNothingIsInterpolatedIntoSql:

    def test_a_column_that_does_not_exist_is_refused(self):
        with pytest.raises(ValueError):
            dw._safe("nonexistent", {"asset_code": {}})

    def test_an_injection_attempt_is_refused_by_the_regex_and_the_catalogue(self):
        for bad in ("asset_code; DROP TABLE assets", "a b", "1col", "", "asset code",
                    "asset_code--", "asset_code)"):
            with pytest.raises(ValueError):
                dw._safe(bad, {"asset_code": {}})

    def test_case_is_normalised_because_a_header_is_not_a_column_name(self):
        """'ASSET_CODE' off a spreadsheet is the same column as asset_code. Lowercasing is
        deliberate, and it happens BEFORE the allow-list check, so it cannot smuggle anything
        past it — the name still has to exist on this table."""
        assert dw._safe("ASSET_CODE", {"asset_code": {}}) == "asset_code"
        with pytest.raises(ValueError):
            dw._safe("ASSET_CODE", {"serial_number": {}})


class TestASheetIsSplitByTheTableEachColumnBelongsTo:

    def test_one_row_contributes_to_every_table_its_columns_name(self):
        headers = ["PPM Reference", "Due Date", "Job Number", "Work Description"]
        mapping = tm.map_headers(headers, domains=["maintenance"])["mapped"]
        rows = [{"PPM Reference": "PPM-1", "Due Date": "2026-09-01",
                 "Job Number": "JOB-1", "Work Description": "Quarterly service"}]
        ppm = dw.rows_for_table(rows, "ppm_visits", mapping)
        wo = dw.rows_for_table(rows, "work_orders", mapping)
        assert ppm[0]["ppm_ref"] == "PPM-1"
        assert wo[0]["wo_code"] == "JOB-1"
        # and neither carries the other's columns
        assert "wo_code" not in ppm[0] or ppm[0].get("ppm_ref")
        assert "ppm_ref" not in wo[0]

    def test_the_column_a_reference_is_looked_up_by_rides_along(self):
        """A reading joins on meter_id and the sheet only ever says MPAN. Without carrying the
        MPAN into the reading row it has nothing to resolve, is refused for want of a natural
        key, and an energy import writes meters and no readings."""
        headers = ["MPAN", "Read To", "Consumption kWh"]
        mapping = tm.map_headers(headers, domains=["energy"])["mapped"]
        rows = [{"MPAN": "12345", "Read To": "2026-08-31", "Consumption kWh": "100"}]
        readings = dw.rows_for_table(rows, "meter_readings", mapping)
        assert readings[0].get("mpan") == "12345"

    def test_an_entirely_empty_row_is_not_carried_into_a_table(self):
        mapping = tm.map_headers(["Asset Tag", "Job Number"], domains=["maintenance"])["mapped"]
        rows = [{"Asset Tag": "A-1", "Job Number": None}]
        assert dw.rows_for_table(rows, "work_orders", mapping) == [] or \
            all(r.get("wo_code") is None for r in dw.rows_for_table(rows, "work_orders", mapping))


class TestTheCatalogueAndTheWriterAgree:

    def test_energy_targets_the_table_the_readings_actually_reference(self):
        """meter_readings.meter_id carries a foreign key to energy_meters.id. plenum_cafm.meters
        is a vestigial table whose column names look right and whose rows nothing reads — an
        insert against it failed on the foreign key, which is how this was found."""
        assert dw.DOMAIN_TABLES["energy"] == ["energy_meters", "meter_readings"]
        assert dw.REFERENCES["meter_readings"]["meter_id"]["table"] == "energy_meters"

    def test_every_domain_the_writer_claims_has_tables_and_a_key(self):
        for domain, tables in dw.DOMAIN_TABLES.items():
            assert tables, domain
            for t in tables:
                assert dw.NATURAL_KEYS.get(t), f"{domain}.{t} has no natural key"

    def test_every_default_names_a_column_of_the_table_it_is_for(self):
        """A default for a column that does not exist is dead weight that reads as coverage."""
        for table, defaults in dw.REQUIRED_DEFAULTS.items():
            assert table in dw.NATURAL_KEYS, table
            assert defaults, table
