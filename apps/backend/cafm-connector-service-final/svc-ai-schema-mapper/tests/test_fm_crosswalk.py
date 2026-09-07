"""Tests for the complete FM crosswalk (matchers/fm_crosswalk.py): every per-platform
docx column alias resolves to a REAL plenum_cafm column on the destination table. Pure.
Run: python tests/test_fm_crosswalk.py"""
import importlib
import os
import re
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

X = importlib.import_module("matchers.fm_crosswalk")
S = importlib.import_module("matchers.plenum_cafm_schema")
F = importlib.import_module("matchers.fm_ontology")
XW = X.FM_COLUMN_ALIASES_BY_TABLE


def _n(s):
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── integrity: every alias targets a REAL column, none shadows a real column ─────
check("crosswalk populated", sum(len(m) for m in XW.values()) >= 200)
check("all crosswalk aliases resolve to real columns",
      all(_n(v) in {_n(c) for c in S.TABLES[t]} for t, m in XW.items() for v in m.values()))
check("no crosswalk alias shadows a real column name",
      all(a not in {_n(c) for c in S.TABLES[t]} for t, m in XW.items() for a in m))
check("destination tables are real", all(t in S.TABLES for t in XW))

# ── per-platform column aliases -> real plenum_cafm columns ──────────────────────
_CASES = {
    "assets": {
        "herst": "manufacturer", "eqktx": "asset_name", "serialnum": "serial_number",
        "modelnum": "model_number", "installdate": "installation_date",
        "warrantyexpdate": "warranty_expiry", "asset_tag": "barcode",
        "space_id": "location_id", "remarks": "notes", "assetnum": "asset_code",
    },
    "work_orders": {
        "wonum": "work_order_id", "aufnr": "work_order_id", "priok": "priority",
        "targcompdate": "scheduled_date", "actfinish": "completed_at",
        "lifnr": "assigned_vendor", "pernr": "assigned_technician",
        "fault_description": "description", "ktext": "title",
    },
    "sites": {"ort01": "city", "land1": "country", "grossarea": "gfa_sqm",
              "sitecode": "site_code", "propertytype": "site_type"},
    "vendors": {"stras": "address", "ort01": "city", "pstlz": "postal_code",
                "name1": "vendor_name", "lifnr": "vendor_code"},
    "vendor_contracts": {"start_date": "contract_start", "end_date": "contract_end",
                         "annual_value": "contract_value"},
    # NB: real column names (expiry_date, certificate_type, frequency_type) are matched by
    # exact-match, NOT the crosswalk — so we assert the PLATFORM aliases instead.
    "certificates": {"expirationdate": "expiry_date", "date_expires": "expiry_date",
                     "inspectiontype": "certificate_type"},
    "maintenance_plans": {"frequencyvalue": "frequency_value", "nextduedate": "next_due_date",
                          "triggertype": "frequency_type"},
    "files": {"file_type": "mime_type", "objecttype": "entity_type", "url": "blob_url"},
}
for _t, _m in _CASES.items():
    for _alias, _col in _m.items():
        check(f"{_t}.{_alias} -> {_col}", XW.get(_t, {}).get(_alias) == _col)

# ── source TABLE name -> real plenum_cafm table (master alias quick-reference) ───
check("FM_ENTITY_TO_TABLE maps to real tables",
      all(t in S.TABLES for t in X.FM_ENTITY_TO_TABLE.values()))


def route(name):
    ent = F.fm_table_lookup(name)[0]
    return X.FM_ENTITY_TO_TABLE.get(ent) if ent else None


# short platform codes that live only inside parentheses in the doc (now curated)
for _code, _ent in [("trigeography", "site"), ("bl", "building"), ("fl", "space"),
                    ("rm", "space"), ("eq", "asset"), ("wr", "work_order"),
                    ("ac", "work_order"), ("triworktask", "work_order"), ("em", "resource"),
                    ("tripeople", "resource"), ("vn", "vendor"), ("ct", "contract"),
                    ("mpnum", "ppm_schedule"), ("dir", "document"), ("dr", "document"),
                    ("fi", "document")]:
    check(f"table code {_code} -> {_ent}", F.fm_table_lookup(_code)[0] == _ent)

# SAP / Maximo / Archibus source TABLE names route to the real plenum_cafm table
for _src, _tbl in [("EQUI", "assets"), ("EQUL", "assets"), ("EQKT", "assets"),
                   ("AUFK", "work_orders"), ("AFKO", "work_orders"), ("WORKORDER", "work_orders"),
                   ("LFA1", "vendors"), ("LFB1", "vendors"), ("COMPANIES", "vendors"),
                   ("T001W", "sites"), ("IFLOT", "sites"), ("PERNR", "technicians"),
                   ("EKKO", "vendor_contracts"), ("EKPO", "vendor_contracts"),
                   ("DOCLINKS", "files")]:
    check(f"route {_src} -> {_tbl}", route(_src) == _tbl)

# end-to-end: a SAP EQUI sheet's HERST column resolves to assets.manufacturer
check("EQUI sheet routes to assets AND HERST->manufacturer",
      route("EQUI") == "assets" and XW["assets"]["herst"] == "manufacturer")

print("\ntotal:", sum(len(m) for m in XW.values()), "crosswalk aliases across", len(XW), "tables")
print("ALL TESTS PASSED")
