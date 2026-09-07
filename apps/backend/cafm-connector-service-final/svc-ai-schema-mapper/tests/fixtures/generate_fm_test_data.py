"""Generate two FM source files for a Layer-1 ingest test (upload BOTH as one batch).

Embeds, by design, every Layer-1 pre-processing + unique-table scenario:
  - NaN / empty values                                  -> pre-processing (NaN handling)
  - an exact duplicate row                              -> duplicate-row removal
  - a 100%-empty column ('notes_blank')                -> null-column drop
  - CHALLENGE (2): two columns, DIFFERENT names, SAME values
      Assets.ID == Assets.ASSETNUM ; WorkOrders.ID == WorkOrders.WONUM
      -> merge; name inferred from the cell values
  - CHALLENGE (3): same column NAME, DIFFERENT values
      Assets.ID (asset IDs 'A-xxx')  vs  WorkOrders.ID (work-order IDs 'W-xxx')
      -> the SAME header 'ID' means different things -> split + propose distinct names
         (asset id vs work order id) by reading the cell values
  - a second source table that is the SAME entity as 'Assets' under a different name
      ('work_tasks.csv')                                -> unique-table consolidation candidate
  - natural primary keys per table (unique + non-null) -> PK detection
  - FM-native column names (ASSETNUM, EQKTX, WONUM ...) -> FM ontology Step-1b resolution

Run:  python tests/fixtures/generate_fm_test_data.py
Outputs:  tests/fixtures/fm_source_primary.xlsx  +  tests/fixtures/work_tasks.csv
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
NAN = np.nan

# ── Assets — has a generic 'ID' column AND an FM-native 'ASSETNUM' with the SAME values ──
assets = pd.DataFrame(
    {
        "ID":           ["A-001", "A-002", "A-003", "A-004", "A-005", "A-006", "A-007", "A-008"],
        "ASSETNUM":     ["A-001", "A-002", "A-003", "A-004", "A-005", "A-006", "A-007", "A-008"],
        "EQKTX":        ["Air Handling Unit AHU-1", "Chiller CH-1", "Passenger Lift L-1",
                         "Fire Alarm Panel", "Boiler B-1", "Extract Fan EF-2",
                         "Distribution Board DB-3", "Cooling Tower CT-1"],
        "manufacturer": ["Siemens", "Trane", "Otis", "Siemens", "Hamworthy",
                         "Vent-Axia", "Schneider", "Baltimore"],
        "model":        ["AHU-9000", "RTAC-140", "Gen2", "FAP-200", "B-450",
                         "EF-22", "DB-3P", "CT-880"],
        "site_ref":     ["S-01", "S-01", "S-02", "S-02", "S-03", "S-01", "S-02", "S-03"],
        "status":       ["Active", "Active", "Inactive", "Active", NAN, "Active", "Active", "Inactive"],
        "notes_blank":  [NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN],  # 100% empty -> dropped
    }
)
# append an EXACT duplicate of the A-003 row
assets = pd.concat([assets, assets.iloc[[2]]], ignore_index=True)

sites = pd.DataFrame(
    {
        "site_ref":  ["S-01", "S-02", "S-03"],
        "site_name": ["Bishopsgate Tower", "Riverside Campus", "Dock Logistics Park"],
        "city":      ["London", "Manchester", "Liverpool"],
        "postcode":  ["EC2M 4NR", "M1 2WD", "L3 4BL"],
    }
)

# ── WorkOrders — ALSO has a column called 'ID', but the values are WORK-ORDER ids ──
# Same header as Assets.ID, totally different values -> challenge (3).
# 'ID' == 'WONUM' here (same values, different names) -> challenge (2) within this table.
workorders = pd.DataFrame(
    {
        "ID":                ["W-1001", "W-1002", "W-1003", "W-1004", "W-1005", "W-1006"],
        "WONUM":             ["W-1001", "W-1002", "W-1003", "W-1004", "W-1005", "W-1006"],
        "asset_no":          ["A-001", "A-003", "A-002", "A-005", "A-001", "A-004"],  # FK -> Assets
        "fault_description": ["Replaced pump on Level 5 AHU", "Lift entrapment, reset controller",
                              "Chiller low pressure alarm", "Boiler not firing - annual service",
                              "Recurring damp on north wall of Level 3", "Fire panel fault LED"],
        "wo_status":         ["Closed", "In Progress", "Open", "Completed", "Open", "Raised"],
        "priority":          ["P2", "P1", "Routine", "Planned", "P3", "Critical"],
        "date_raised":       ["2026-01-04", "2026-02-11", "2026-03-02", "2026-03-20",
                              "2026-04-01", "2026-04-18"],
    }
)

vendors = pd.DataFrame(
    {
        "vendor_id":   ["V-01", "V-02", "V-03"],
        "vendor_name": ["Apex Lifts", "ColdChain HVAC", "SafeFire Ltd"],
        "trade":       ["Lift", "Mechanical", "Fire"],
        "phone":       ["020 7000 1111", "0161 200 2222", "0151 300 3333"],
    }
)

resources = pd.DataFrame(
    {
        "engineer_id": ["E-01", "E-02", "E-03", "E-04"],
        "full_name":   ["Sam Patel", "Joanne Clarke", "Marcus Reid", "Priya Shah"],
        "trade":       ["Electrical", "Mechanical", "Lift", "Fire"],
        "site_ref":    ["S-01", "S-02", "S-01", "S-03"],
    }
)

primary_path = os.path.join(HERE, "fm_source_primary.xlsx")
with pd.ExcelWriter(primary_path, engine="openpyxl") as xl:
    assets.to_excel(xl, sheet_name="Assets", index=False)
    sites.to_excel(xl, sheet_name="Sites", index=False)
    workorders.to_excel(xl, sheet_name="WorkOrders", index=False)
    vendors.to_excel(xl, sheet_name="Vendors", index=False)
    resources.to_excel(xl, sheet_name="Resources", index=False)

# ── File 2: same entity as Assets, different table name -> consolidation candidate ──
# Mostly the SAME assets as the Assets sheet (so its shared columns overlap heavily and are
# NOT mistaken for a same-name/divergent split), plus one new asset A-009.
work_tasks = pd.DataFrame(
    {
        "ID":           ["A-001", "A-002", "A-003", "A-004", "A-009"],
        "ASSETNUM":     ["A-001", "A-002", "A-003", "A-004", "A-009"],
        "EQKTX":        ["Air Handling Unit AHU-1", "Chiller CH-1", "Passenger Lift L-1",
                         "Fire Alarm Panel", "VRF Outdoor Unit"],
        "manufacturer": ["Siemens", "Trane", "Otis", "Siemens", "Daikin"],
        "model":        ["AHU-9000", "RTAC-140", "Gen2", "FAP-200", "VRV-X"],
        "site_ref":     ["S-01", "S-01", "S-02", "S-02", "S-02"],
        "status":       ["Active", "Active", "Inactive", "Active", "Active"],
    }
)
csv_path = os.path.join(HERE, "work_tasks.csv")
work_tasks.to_csv(csv_path, index=False)

print("Wrote:")
print(" ", primary_path, "(sheets: Assets, Sites, WorkOrders, Vendors, Resources)")
print(" ", csv_path, "(work_tasks — same entity as Assets, different name)")
print()
print("Challenge (3): both Assets and WorkOrders have a column named 'ID' —")
print("  Assets.ID   = A-001..A-008  (asset ids)")
print("  WorkOrders.ID = W-1001..W-1006 (work-order ids)")
print("  same header, no value overlap -> split + name each from its values.")
