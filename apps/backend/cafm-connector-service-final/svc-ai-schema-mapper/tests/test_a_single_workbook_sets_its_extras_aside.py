"""A single end-to-end workbook's contract terms and invoices are not migrated as tables.

They are read after the write by the contract ingest and the invoice matcher; written as
rows they would land with no vendor and no matching. Loaded by path - the predicate is pure.
"""
import importlib.util, os, re, types

_P = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes", "ingest_node.py")
_SRC = open(_P, encoding="utf-8").read()
_START = _SRC.index("POST_WRITE_SHEETS = ")
_END = _SRC.index("def _sanitize_column_names")
ns: dict = {}
exec(_SRC[_START:_END], ns)
is_post = ns["_is_post_write_sheet"]


def test_the_three_sheets_and_a_readme_are_set_aside_in_any_spelling():
    for name in ("Contract_Terms", "contract terms", "Invoices", "Invoice_Lines", "INVOICE-LINES", "README",
                 "Chiller_Design_Specs", "Chiller_Readings", "Weather_Degree_Days", "BMS_Trends", "bms trends"):
        assert is_post(name), name


def test_the_migrated_sheets_are_not():
    for name in ("Vendors", "Vendor_Contracts", "Work_Orders", "Compliance_Certificates", "Meter_Readings", "Invoice_Summary",
                 "Asset_Readings", "Energy_Meters"):
        assert not is_post(name), name
