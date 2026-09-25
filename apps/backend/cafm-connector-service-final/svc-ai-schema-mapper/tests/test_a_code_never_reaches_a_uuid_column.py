"""A source code in a uuid column must not cost the whole row.

Every branch of _coerce_value_for_db_type validates its destination and returns
_COERCE_TYPE_MISMATCH when the value cannot fit — the caller then drops that one field,
keeps the row, and reports the column at the mapping gate. The uuid branch did not: it
returned any string untouched.

So a CMMS export whose Work_Orders sheet carries `asset_code` mapped onto `work_orders.
asset_id` sent 'B-101-AHU-01' to asyncpg, which raised DataError while BINDING the
parameter — before Postgres saw a statement, so the orphan-FK recovery below (which reads
the constraint name out of the error) could never match it. The row was logged as
"Skipping bad row" and dropped.

On 22 Sep 2026 that cost 160 of 195 rows in a migration the UI reported as complete:
work_orders 0/16, ppm_visits 0/132, inspections 0/8, resources 0/4.
"""
from __future__ import annotations

from src.graph.nodes.write_node import _COERCE_TYPE_MISMATCH, _coerce_value_for_db_type

REAL = "a6edb6cf-ba72-46ed-ba80-fc5e3bb731e6"


class TestARealUuidStillPasses:
    def test_a_uuid_string_is_kept(self):
        assert _coerce_value_for_db_type(REAL, "uuid") == REAL

    def test_upper_case_and_braces_are_still_a_uuid(self):
        assert _coerce_value_for_db_type("{" + REAL.upper() + "}", "uuid") is not _COERCE_TYPE_MISMATCH

    def test_none_stays_none(self):
        assert _coerce_value_for_db_type(None, "uuid") is None


class TestACodeIsReportedNotBound:
    def test_an_asset_code_is_a_type_mismatch(self):
        assert _coerce_value_for_db_type("B-101-AHU-01", "uuid") is _COERCE_TYPE_MISMATCH

    def test_an_inspection_reference_is_a_type_mismatch(self):
        assert _coerce_value_for_db_type("INS-B-101-2026-01", "uuid") is _COERCE_TYPE_MISMATCH

    def test_a_legacy_vendor_id_is_a_type_mismatch(self):
        assert _coerce_value_for_db_type("VEN-CLIMATE-001", "uuid") is _COERCE_TYPE_MISMATCH

    def test_an_empty_string_is_null_not_a_mismatch(self):
        assert _coerce_value_for_db_type("", "uuid") is None

    def test_a_number_is_not_a_uuid(self):
        assert _coerce_value_for_db_type(12345, "uuid") is _COERCE_TYPE_MISMATCH
