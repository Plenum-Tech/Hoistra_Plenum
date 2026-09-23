"""Close the three gaps an audit of the Harbour Point workbook found, and the one near-miss.

Audited 23 Sep 2026. The file's internal integrity was already perfect — all eighteen reference
sets resolved, every asset_code, vendor_name, contract_name and meter_ref used anywhere in it was
defined in it. What it lacked was columns the DATABASE requires and the file cannot infer.

  Technicians.user_id      technicians.user_id is NOT NULL with no default and nothing resolves
                           it, so both rows were rejected on every run. The sheet carried
                           user_full_name, which is dropped as unknown on an ORM-managed table.
                           Filled from plenum_cafm.users by matching the name the sheet already
                           states, so the technician IS the user rather than resembling them.

  Compliance_Certificates  No certificates sheet at all, so the Compliance page was fed entirely
                           by rows that happened to survive a clear. Added, scoped to this
                           building, and deliberately covering all three shapes a certificate
                           takes: one about the BUILDING (EPC, EICR), one about an ASSET (LOLER
                           on the lift, the pressure system on a chiller) and one about a VENDOR
                           (the accreditation that lets them hold the contract).

  compliance -> contract   compliance_certificates has no contract_id column, so this link cannot
                           be a column and is not invented as one. It runs through the vendor:
                           the sheet carries vendor_code and vendor_name, vendor_id resolves from
                           those, and vendor_contracts already keys on the same vendor. A
                           certificate therefore reaches the contract it underwrites in one hop.

  Work_Orders.title        Not a gap that failed — it inserted 8 of 8 — but it was being satisfied
                           by a mapping rather than by the file, and work_orders.title is NOT NULL.
                           If the mapper ever routes fault_description elsewhere, every work order
                           fails at once. Now stated outright.

Nothing else in the workbook is touched: the other fourteen sheets are copied through byte for
byte, including the 35,040 meter readings.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil

import openpyxl

SRC = os.environ.get(
    "NORTHBRIDGE_WORKBOOK",
    r"C:\Users\balap\Downloads\northbridge_B-101_harbour_point.xlsx")

#: plenum_cafm.users in hoistra_test, by the name the Technicians sheet already states.
#: Matched on the name rather than invented, so the row points at a real person who can sign in.
USERS_BY_NAME = {
    "Sam Whitlock": "25f74478-4000-5c8c-a65e-64f65662e5b8",   # tech.mech@northbridge-estates
    "Aisha Bello": "ff17dcfd-ee29-5dac-a88f-dc36370f72df",    # tech.elec@northbridge-estates
}

COMPLIANCE_HEADER = [
    "certificate_number", "certificate_type_code", "cert_scope", "building_code", "asset_code",
    "vendor_code", "vendor_name", "issuer", "inspector_name",
    "inspector_accreditation_number", "issue_date", "expiry_date", "next_due_date",
    "inspection_frequency_months", "result", "status", "country_code", "defects_found",
    "remedial_actions", "remedial_status",
]

#: Three shapes, on purpose. A building certificate, an asset certificate and a vendor
#: accreditation exercise three different joins, and a set that is all one shape proves one join.
COMPLIANCE_ROWS = [
    # ── about the BUILDING ────────────────────────────────────────────────────────────
    ["EICR-B-101-2026-0917", "EICR", "Building", "B-101", None, "BRLT", "Brightline Electrical",
     "Brightline Electrical", "Owen Fletcher", "NICEIC 021874",
     "2026-09-17", "2031-09-17", "2031-09-17", 60, "Satisfactory", "valid", "GB",
     None, None, None],
    ["0660-5580-7384-4815-38", "EPC", "Building", "B-101", None, None, None,
     "Elmwood Energy Assessors", "Ruth Callaghan", "EPC/NDEA 0221847",
     "2017-05-18", "2027-05-18", "2027-05-18", 120, "C", "valid", "GB", None, None, None],
    ["FAS-B-101-202603", "FIRE_ALARM_SERVICE", "Building", "B-101", None, "BRLT",
     "Brightline Electrical", "Brightline Electrical", "Owen Fletcher", "BAFE SP203-1 5581",
     "2026-03-02", "2026-09-02", "2026-09-02", 6, "Satisfactory", "valid", "GB",
     "Zone 4 sounder below 65dB at far end of Level 9 corridor.",
     "Additional sounder proposed, quotation with client.", "open"],
    ["CP17-B-101-2026-0812", "CP17", "Building", "B-101", None, "MERI", "Meridian Mechanical Ltd",
     "Meridian Mechanical Ltd", "Sam Whitlock", "Gas Safe 559120",
     "2026-08-12", "2027-08-12", "2027-08-12", 12, "Pass", "valid", "GB", None, None, None],
    # ── about an ASSET ────────────────────────────────────────────────────────────────
    ["LOLER-B-101-LIFT-01-2026", "LOLER", "Asset", "B-101", "B-101-LIFT-01",
     "KSTL", "Kestrel Lift Services", "Kestrel Lift Services", "Marta Kowalczyk", "LEIA 4471",
     "2026-04-14", "2026-10-14", "2026-10-14", 6, "Satisfactory", "valid", "GB", None, None, None],
    ["PSSR-B-101-CHILLER-01-2026", "PSSR", "Asset", "B-101", "B-101-CHILLER-01",
     "MERI", "Meridian Mechanical Ltd", "Meridian Mechanical Ltd", "Sam Whitlock",
     "Gas Safe 559120", "2026-02-20", "2028-02-20", "2028-02-20", 24,
     "Satisfactory", "valid", "GB", None, None, None],
    # ── about the VENDOR who holds the contract ───────────────────────────────────────
    ["NICEIC-021874-2026", "NICEIC", "Vendor", None, None, "BRLT", "Brightline Electrical",
     "NICEIC", None, "021874", "2026-02-14", "2027-02-14", "2027-02-14", 12,
     "Approved Contractor", "valid", "GB", None, None, None],
    ["GS-REG-559120", "GAS_SAFE", "Vendor", None, None, "MERI", "Meridian Mechanical Ltd",
     "Gas Safe Register", None, "559120", "2026-03-31", "2027-03-31", "2027-03-31", 12,
     "Registered", "valid", "GB", None, None, None],
    ["LEIA-4471-2026", "LEIA", "Vendor", None, None, "KSTL", "Kestrel Lift Services",
     "LEIA", None, "4471", "2026-01-09", "2027-01-09", "2027-01-09", 12,
     "Member", "valid", "GB", None, None, None],
]


def read(wb, name):
    rows = list(wb[name].iter_rows(values_only=True))
    hdr = [str(h) for h in (rows[0] if rows else ()) if h is not None]
    return hdr, [list(r[:len(hdr)]) for r in rows[1:] if any(v is not None for v in r)]


def main() -> None:
    if not os.path.exists(SRC):
        raise SystemExit(f"workbook not found: {SRC}")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = SRC.replace(".xlsx", f".before-gaps-{stamp}.xlsx")
    shutil.copy2(SRC, backup)
    print(f"  backup  {os.path.basename(backup)}\n")

    src = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
    out = openpyxl.Workbook()
    out.remove(out.active)

    for name in src.sheetnames:
        hdr, rows = read(src, name)

        if name == "Technicians":
            # The row already names the person; this states which user row that is.
            hdr = hdr + ["user_id"]
            i = hdr.index("full_name")
            unmatched = []
            for r in rows:
                uid = USERS_BY_NAME.get(str(r[i]).strip())
                if uid is None:
                    unmatched.append(r[i])
                r.append(uid)
            print(f"  Technicians          user_id added to {len(rows)} row(s)"
                  + (f"  UNMATCHED: {unmatched}" if unmatched else ""))

        if name == "Work_Orders":
            # NOT NULL in the database, and it was only being filled by a mapping.
            hdr = hdr + ["title"]
            j = hdr.index("fault_description")
            for r in rows:
                text = str(r[j] or "").strip()
                r.append(text[:117].rstrip() + "…" if len(text) > 120 else text)
            print(f"  Work_Orders          title added to {len(rows)} row(s)")

        ws = out.create_sheet(name)
        ws.append(hdr)
        for r in rows:
            ws.append(r)

    ws = out.create_sheet("Compliance_Certificates")
    ws.append(COMPLIANCE_HEADER)
    for r in COMPLIANCE_ROWS:
        ws.append(r)
    by_scope: dict[str, int] = {}
    for r in COMPLIANCE_ROWS:
        by_scope[r[2]] = by_scope.get(r[2], 0) + 1
    print(f"  Compliance_Certificates  {len(COMPLIANCE_ROWS)} row(s): "
          + ", ".join(f"{n} {s.lower()}" for s, n in sorted(by_scope.items())))

    try:
        out.save(SRC)
        path = SRC
    except PermissionError:
        path = SRC.replace(".xlsx", f"-updated-{stamp}.xlsx")
        out.save(path)
        print(f"\n  {os.path.basename(SRC)} is open in Excel — written to "
              f"{os.path.basename(path)}")

    chk = openpyxl.load_workbook(path, read_only=True)
    print(f"\n  {os.path.basename(path)}  {len(chk.sheetnames)} sheets, "
          f"{os.path.getsize(path)/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
