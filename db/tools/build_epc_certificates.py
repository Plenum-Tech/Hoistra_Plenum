"""Rebuild the two synthetic EPC PDFs so the document and the register row are the same certificate.

The EPC PDFs carried certificate_number EPC-B-101-2024-0416 and a separate made-up RRN, band C at
62, expiring 2034. The workbook's Compliance_Certificates row for the same building carried the
RRN 0660-5580-7384-4815-3898, band C at 74, expiring 2027. Same building, same band, two
different certificates as far as anything keyed on the number could tell — so ingesting both
produced two EPC rows for Harbour Point, and "EPCs on file" would read 2 / 2 for one building.

For an EPC the RRN IS the certificate number: it is the key the GOV.UK register is queried by
and the number printed on the certificate. So the PDF now carries the RRN in both places, and
every figure a reader could compare — band, score, dates, assessor, accreditation — is read
from close_workbook_gaps.BY_BUILDING rather than typed again here. One record, two renderings.

Overwrites the two files in place under their existing names; the set stays at 29 documents.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_missing_certificates import BUILDINGS, CLIENT, OUT, build  # noqa: E402
from close_workbook_gaps import BY_BUILDING, COMPLIANCE_HEADER  # noqa: E402

ASSESSOR = dict(
    name="Elmwood Energy Assessors",
    accred="Accredited non-domestic energy assessor scheme | lodged on the GOV.UK Find an energy certificate register | NDEA 0221847",
    address="3 Chapel Court, Bristol BS1 5DN", phone="+44 117 496 0142", signer="R. Callaghan",
)

#: What a non-domestic EPC states beyond the band. Plausible for each building and consistent
#: with the band: a C sits mid-table, a B is a well-performing block with communal plant only.
DETAIL = {
    "B-101": dict(fuel="Natural gas (heating), grid electricity",
                  primary="214 kWh/m2/year", co2="58 kgCO2/m2/year",
                  cost="GBP 186,400 per year",
                  recs="LED lighting to Levels 7-12; BMS optimisation of AHU schedules; roof insulation upgrade"),
    "B-102": dict(fuel="Natural gas (communal boilers), grid electricity",
                  primary="128 kWh/m2/year", co2="31 kgCO2/m2/year",
                  cost="GBP 41,900 per year (communal areas)",
                  recs="Boiler sequencing controls; PIR lighting to car park and cores; loft insulation top-up"),
}


def _dmy(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def epc_row(code: str) -> dict:
    """The workbook's EPC row for this building, as a dict keyed by the sheet header."""
    for r in BY_BUILDING[code]:
        rec = dict(zip(COMPLIANCE_HEADER, r))
        if rec["certificate_type_code"] == "EPC":
            return rec
    raise KeyError(f"no EPC row for {code} in close_workbook_gaps.BY_BUILDING")


def main() -> None:
    made = []
    for code, b in BUILDINGS.items():
        e, d = epc_row(code), DETAIL[code]
        rrn = e["certificate_number"]
        fn = f"EPC_Energy_Performance_Certificate_{b['name'].replace(' ', '_')}_{code}.pdf"
        target = os.path.join(OUT, fn)
        # A viewer holding the old file open makes the overwrite fail on Windows. Writing the
        # new one beside it under -rebuilt is better than losing the build, and the report says
        # which name it went to so the stale file is not mistaken for the rebuilt one.
        try:
            with open(target, "ab"):
                pass
        except PermissionError:
            fn = fn[:-4] + "-rebuilt.pdf"
            target = os.path.join(OUT, fn)
            print(f"  {b['name']}: original is open elsewhere - writing {fn}")
        build(target,
              vendor=ASSESSOR,
              title="Energy Performance Certificate (EPC) - Non-Domestic Building",
              type_code="EPC", scope="Building", trade="Energy",
              number=rrn, issued=_dmy(e["issue_date"]), expires=_dmy(e["expiry_date"]),
              details=[
                  ("Building name", b["name"]), ("Building reference", code),
                  ("Property address", b["address"]), ("Client", CLIENT),
                  ("Site", f"{b['name']} ({code})"),
                  # The RRN twice on purpose: it is the certificate number AND the register key.
                  ("EPC reference number (RRN)", rrn),
                  ("Certificate number", rrn),
                  ("Assessment date", _dmy(e["issue_date"])),
                  ("Valid until", _dmy(e["expiry_date"])),
                  ("Energy rating", f"Band {e['energy_rating']}"),
                  ("Asset rating (A-G) and score", f"{e['energy_rating']} ({e['energy_score']})"),
                  ("Building type / area", f"{b['use']}, {b['floors']} floors / {b['gia']}"),
                  ("Main heating fuel", d["fuel"]),
                  ("Primary energy use", d["primary"]),
                  ("CO2 emissions rating", d["co2"]),
                  ("Typical energy cost", d["cost"]),
                  ("Improvement recommendations", d["recs"]),
                  ("Assessor name", e["inspector_name"]),
                  ("Inspector accreditation number", e["inspector_accreditation_number"]),
                  ("Lodged on the register", "Yes - retrievable by RRN at find-energy-certificate.service.gov.uk"),
              ],
              signer_line=f"{e['inspector_name']} (accredited non-domestic energy assessor, "
                          f"{e['inspector_accreditation_number']})")
        made.append((fn, rrn, e["energy_rating"], e["energy_score"], e["expiry_date"]))

    print(f"  {len(made)} EPC PDFs rebuilt in {OUT}\n")
    for fn, rrn, band, score, exp in made:
        print(f"    {fn}")
        print(f"       RRN {rrn}   band {band} ({score})   expires {exp}")


if __name__ == "__main__":
    main()
