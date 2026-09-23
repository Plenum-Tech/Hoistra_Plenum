"""Close the gaps an audit found, in whichever Northbridge workbook is handed to it.

Audited 23 Sep 2026 against hoistra_test. The files' internal integrity was already perfect —
every asset_code, vendor_name, contract_name and meter_ref used anywhere in them was defined in
them. What they lacked was columns the DATABASE requires and a file cannot infer.

  Technicians.user_id      technicians.user_id is NOT NULL AND carries a real foreign key to
                           plenum_cafm.users, so both rows were rejected on every run. A made-up
                           uuid would fail the constraint, so each technician is pointed at an
                           actual Northbridge login. Where the technician's own name matches a
                           user it is that user; where it does not, the technician keeps its name
                           and holds a separate login, which is what the table's own shape allows
                           — full_name and user_id are different columns for this reason.

  Compliance_Certificates  No certificates sheet at all, so Compliance was fed entirely by rows
                           that happened to survive a clear. Added per building, covering all
                           three shapes a certificate takes, because a set that is all one shape
                           proves one join: about the BUILDING, about an ASSET, and about the
                           VENDOR whose accreditation lets them hold the contract.

  compliance -> contract   compliance_certificates has no contract_id column, so this link is not
                           invented as one. It runs through the vendor: the sheet carries
                           vendor_code and vendor_name, vendor_id resolves from those, and
                           vendor_contracts keys on the same vendor.

  Work_Orders.title        Not a gap that failed, but work_orders.title is NOT NULL and was being
                           satisfied by a mapping rather than by the file. One mapper change away
                           from every work order failing at once.

Every other sheet is copied through unchanged, including the meter readings.

    python db/tools/close_workbook_gaps.py                     # all three workbooks
    python db/tools/close_workbook_gaps.py <path.xlsx> ...     # only these
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys

import openpyxl

DOWNLOADS = os.environ.get("NORTHBRIDGE_DIR", r"C:\Users\balap\Downloads")
DEFAULT_FILES = [
    "northbridge_B-101_harbour_point.xlsx",
    "northbridge_B-102_ashgrove_court.xlsx",
    "northbridge_cmms_export.xlsx",
]

#: plenum_cafm.users in hoistra_test. technicians.user_id is a real FK, so these must be real.
#: Two match the technician by name; two do not, and hold a login belonging to someone else on
#: the team, which the table allows and a test dataset needs in order to have four technicians.
USER_FOR_ENGINEER = {
    "E-101": ("25f74478-4000-5c8c-a65e-64f65662e5b8", "tech.mech@northbridge-estates"),
    "E-102": ("ff17dcfd-ee29-5dac-a88f-dc36370f72df", "tech.elec@northbridge-estates"),
    "E-103": ("63db4f7f-9bd5-55c5-8592-eb2dc4e704ed", "fm@northbridge-estates"),
    "E-104": ("ac9b228f-6bd9-53a7-95a3-3511c652a635", "inspector@northbridge-estates"),
}

COMPLIANCE_HEADER = [
    "certificate_number", "certificate_type_code", "cert_scope", "building_code", "asset_code",
    "vendor_code", "vendor_name", "issuer", "inspector_name",
    "inspector_accreditation_number", "issue_date", "expiry_date", "next_due_date",
    "inspection_frequency_months", "result", "status", "country_code", "defects_found",
    "remedial_actions", "remedial_status",
]


def _c(num, typ, scope, bldg, asset, vcode, vname, issuer, inspector, accred,
       issued, expires, months, result, defects=None, remedial=None, rstatus=None):
    return [num, typ, scope, bldg, asset, vcode, vname, issuer, inspector, accred,
            issued, expires, expires, months, result, "valid", "GB", defects, remedial, rstatus]


#: Certificates about a building or an asset in it, keyed by the building they belong to.
BY_BUILDING: dict[str, list[list]] = {
    "B-101": [
        _c("EICR-B-101-2026-0917", "EICR", "Building", "B-101", None, "BRLT",
           "Brightline Electrical", "Brightline Electrical", "Owen Fletcher", "NICEIC 021874",
           "2026-09-17", "2031-09-17", 60, "Satisfactory"),
        # The full twenty-digit RRN, not a shortened one. It is the key the GOV.UK register is
        # queried by — energy_verification deep-links to /energy-certificate/<RRN> — so a
        # truncated number looks like a certificate and verifies as nothing.
        _c("0660-5580-7384-4815-3898", "EPC", "Building", "B-101", None, None, None,
           "Elmwood Energy Assessors", "Ruth Callaghan", "EPC/NDEA 0221847",
           "2017-05-18", "2027-05-18", 120, "C"),
        _c("FAS-B-101-202603", "FIRE_ALARM_SERVICE", "Building", "B-101", None, "BRLT",
           "Brightline Electrical", "Brightline Electrical", "Owen Fletcher",
           "BAFE SP203-1 5581", "2026-03-02", "2026-09-02", 6, "Satisfactory",
           "Zone 4 sounder below 65dB at the far end of the Level 9 corridor.",
           "Additional sounder proposed, quotation with client.", "open"),
        _c("CP17-B-101-2026-0812", "CP17", "Building", "B-101", None, "MERI",
           "Meridian Mechanical Ltd", "Meridian Mechanical Ltd", "Sam Whitlock",
           "Gas Safe 559120", "2026-08-12", "2027-08-12", 12, "Pass"),
        _c("LOLER-B-101-LIFT-01-2026", "LOLER", "Asset", "B-101", "B-101-LIFT-01", "KSTL",
           "Kestrel Lift Services", "Kestrel Lift Services", "Marta Kowalczyk", "LEIA 4471",
           "2026-04-14", "2026-10-14", 6, "Satisfactory"),
        _c("PSSR-B-101-CHILLER-01-2026", "PSSR", "Asset", "B-101", "B-101-CHILLER-01", "MERI",
           "Meridian Mechanical Ltd", "Meridian Mechanical Ltd", "Sam Whitlock",
           "Gas Safe 559120", "2026-02-20", "2028-02-20", 24, "Satisfactory"),
    ],
    "B-102": [
        _c("EICR-B-102-2026-0304", "EICR", "Building", "B-102", None, "BRLT",
           "Brightline Electrical", "Brightline Electrical", "Owen Fletcher", "NICEIC 021874",
           "2026-03-04", "2031-03-04", 60, "Satisfactory"),
        _c("0421-4815-1433-2575-9823", "EPC", "Building", "B-102", None, None, None,
           "Elmwood Energy Assessors", "Ruth Callaghan", "EPC/NDEA 0221847",
           "2024-03-24", "2034-03-24", 120, "B"),
        _c("CP17-B-102-2026-0619", "CP17", "Building", "B-102", None, "MERI",
           "Meridian Mechanical Ltd", "Meridian Mechanical Ltd", "Sam Whitlock",
           "Gas Safe 559120", "2026-06-19", "2027-06-19", 12, "Pass"),
        _c("EML-B-102-2026-0731", "EMERGENCY_LIGHTING", "Asset", "B-102", "B-102-EML-01",
           "BRLT", "Brightline Electrical", "Brightline Electrical", "Owen Fletcher",
           "NICEIC 021874", "2026-07-31", "2027-07-31", 12, "Satisfactory"),
        _c("FAS-B-102-2026-0812", "FIRE_ALARM_SERVICE", "Asset", "B-102",
           "B-102-FIRE-PANEL-01", "GRDF", "Guardian Fire Systems", "Guardian Fire Systems",
           "Nadia Haddad", "BAFE SP203-1 7742", "2026-08-12", "2027-02-12", 6, "Satisfactory"),
        _c("LOLER-B-102-LIFT-01-2026", "LOLER", "Asset", "B-102", "B-102-LIFT-01", "KSTL",
           "Kestrel Lift Services", "Kestrel Lift Services", "Marta Kowalczyk", "LEIA 4471",
           "2026-05-06", "2026-11-06", 6, "Satisfactory"),
        _c("LOLER-B-102-LIFT-02-2026", "LOLER", "Asset", "B-102", "B-102-LIFT-02", "KSTL",
           "Kestrel Lift Services", "Kestrel Lift Services", "Marta Kowalczyk", "LEIA 4471",
           "2026-05-06", "2026-11-06", 6, "Satisfactory",
           "Door restrictor on car B showing wear beyond tolerance.",
           "Restrictor replacement scheduled with the next service visit.", "open"),
    ],
}

#: Accreditations. They belong to the vendor, not to a building, so they go in every workbook
#: whose Vendors sheet names that vendor — the certificate is what lets them hold the contract.
BY_VENDOR: dict[str, list] = {
    "BRLT": _c("NICEIC-021874-2026", "NICEIC", "Vendor", None, None, "BRLT",
               "Brightline Electrical", "NICEIC", None, "021874",
               "2026-02-14", "2027-02-14", 12, "Approved Contractor"),
    "MERI": _c("GS-REG-559120", "GAS_SAFE", "Vendor", None, None, "MERI",
               "Meridian Mechanical Ltd", "Gas Safe Register", None, "559120",
               "2026-03-31", "2027-03-31", 12, "Registered"),
    "KSTL": _c("LEIA-4471-2026", "LEIA", "Vendor", None, None, "KSTL",
               "Kestrel Lift Services", "LEIA", None, "4471",
               "2026-01-09", "2027-01-09", 12, "Member"),
    "GRDF": _c("BAFE-5581-2026", "BAFE_SP203_1", "Vendor", None, None, "GRDF",
               "Guardian Fire Systems", "BAFE", None, "SP203-1 7742",
               "2026-03-03", "2029-03-03", 36, "Certificated"),
}


def read(wb, name):
    rows = list(wb[name].iter_rows(values_only=True))
    hdr = [str(h) for h in (rows[0] if rows else ()) if h is not None]
    return hdr, [list(r[:len(hdr)]) for r in rows[1:] if any(v is not None for v in r)]


def close_gaps(path: str) -> None:
    if not os.path.exists(path):
        print(f"  {os.path.basename(path)}: not found"); return
    src = openpyxl.load_workbook(path, read_only=True, data_only=True)

    buildings = {str(r[0]).strip() for _h, rows in [read(src, "Buildings")] for r in rows} \
        if "Buildings" in src.sheetnames else set()
    vendors = set()
    if "Vendors" in src.sheetnames:
        h, rows = read(src, "Vendors")
        i = h.index("vendor_code")
        vendors = {str(r[i]).strip() for r in rows if r[i]}

    print(f"\n  {os.path.basename(path)}")
    print(f"    buildings {sorted(buildings) or '—'}   vendors {sorted(vendors) or '—'}")

    out = openpyxl.Workbook()
    out.remove(out.active)
    for name in src.sheetnames:
        if name == "Compliance_Certificates":
            continue                      # rebuilt below, so re-running does not stack rows
        hdr, rows = read(src, name)

        if name == "Technicians" and "user_id" not in hdr:
            hdr = hdr + ["user_id"]
            i = hdr.index("engineer_id")
            unknown = []
            for r in rows:
                pair = USER_FOR_ENGINEER.get(str(r[i]).strip())
                r.append(pair[0] if pair else None)
                if not pair:
                    unknown.append(r[i])
            print(f"    Technicians            user_id on {len(rows) - len(unknown)}"
                  f" of {len(rows)}" + (f"  NO USER FOR {unknown}" if unknown else ""))

        if name == "Work_Orders" and "title" not in hdr:
            hdr = hdr + ["title"]
            j = hdr.index("fault_description")
            for r in rows:
                t = str(r[j] or "").strip()
                r.append(t[:117].rstrip() + "…" if len(t) > 120 else t)
            print(f"    Work_Orders            title on {len(rows)} row(s)")

        ws = out.create_sheet(name)
        ws.append(hdr)
        for r in rows:
            ws.append(r)

    certs = [r for b in sorted(buildings) for r in BY_BUILDING.get(b, [])]
    certs += [BY_VENDOR[v] for v in sorted(vendors) if v in BY_VENDOR]
    if certs:
        ws = out.create_sheet("Compliance_Certificates")
        ws.append(COMPLIANCE_HEADER)
        for r in certs:
            ws.append(r)
        shape: dict[str, int] = {}
        for r in certs:
            shape[r[2]] = shape.get(r[2], 0) + 1
        print(f"    Compliance_Certificates {len(certs)} row(s): "
              + ", ".join(f"{n} {s.lower()}" for s, n in sorted(shape.items())))

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.replace(".xlsx", f".before-gaps-{stamp}.xlsx"))
    try:
        out.save(path)
        saved = path
    except PermissionError:
        saved = path.replace(".xlsx", f"-updated-{stamp}.xlsx")
        out.save(saved)
        print(f"    open in Excel — written to {os.path.basename(saved)}")
    chk = openpyxl.load_workbook(saved, read_only=True)
    print(f"    -> {os.path.basename(saved)}  {len(chk.sheetnames)} sheets, "
          f"{os.path.getsize(saved)/1024/1024:.1f} MB")


def main() -> None:
    files = sys.argv[1:] or [os.path.join(DOWNLOADS, f) for f in DEFAULT_FILES]
    for f in files:
        close_gaps(f)


if __name__ == "__main__":
    main()
