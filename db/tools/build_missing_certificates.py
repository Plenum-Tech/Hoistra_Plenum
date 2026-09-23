"""Build the synthetic certificates the Northbridge test set was missing, in its existing shape.

The set at Documents/test_data/northbridge/certificates already covers eight document types per
building and four vendor accreditations. Sixteen real third-party PDFs arrived on 23 Sep 2026 as
candidates for more — an HSE asbestos licence, BAFE SP101/SP105, BPCA membership, LEIA, two ISO
9001s, an employers' liability policy, two GOV.UK EPCs, TM44 reports and BS 5839-1 inspections.
Those cannot be edited to name our buildings: a genuine certificate with a property written into
it asserts that property was inspected, licensed or insured when it was not, and after ingest it
is indistinguishable from the real thing.

So the types they represent are built here from scratch instead, in the same shape as the twenty
already on disk: a vendor letterhead, the type/scope/trade line the classifier keys on, a
"canonical fields" block the extractor reads first, a details table and a signature block. Every
one names Northbridge Estates Ltd as the client and a building the workbooks define, so the
ingest has a building to land on and a vendor to link to. Nothing here reproduces any real
company's document.

Type codes and frequencies are the UK compliance pack's own:

    TM44                  60 months   air-conditioning inspection, EPBD Article 15
    ASBESTOS_SURVEY       12 months   management survey to HSG264, per building
    FIRE_ALARM_SERVICE     6 months   BS 5839-1 Section 6 periodic inspection
    HSE_ASBESTOS_LICENCE  36 months   the contractor's licence — vendor scope
    BPCA                  12 months   pest control membership — vendor scope
    EMPLOYERS_LIABILITY   12 months   the insurance that gates engaging a vendor

Nine documents: three types for each of two buildings, three vendor-scoped once.
"""
from __future__ import annotations

import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = os.environ.get(
    "NORTHBRIDGE_CERT_DIR",
    r"C:\Users\balap\Documents\test_data\northbridge\certificates")

CLIENT = "Northbridge Estates Ltd"

BUILDINGS = {
    "B-101": dict(name="Harbour Point",
                  address="Harbour Point, 1 Northbridge Wharf, London E14 9GE",
                  use="Commercial offices", floors=12, gia="14,200 m2"),
    "B-102": dict(name="Ashgrove Court",
                  address="Ashgrove Court, 22 Hulme Street, Manchester M15 4FN",
                  use="Residential apartments", floors=9, gia="8,600 m2"),
}

#: Vendors. The first four are the workbooks' own and match on vendor_name; the last two are new
#: trades the existing set had no vendor for, so a new accreditation had someone to belong to.
VENDORS = {
    "GRDF": dict(name="Guardian Fire Systems", accred="BAFE SP203-1 registered, certificate 7742",
                 address="Unit 12, Trafford Park Road, Manchester M17 1HA", phone="+44 161 496 0187",
                 signer="N. Haddad"),
    "ELMW": dict(name="Elmwood Energy Assessors", accred="Accredited air-conditioning energy assessor, NDEA 0221847",
                 address="3 Chapel Court, Bristol BS1 5DN", phone="+44 117 496 0142",
                 signer="R. Callaghan"),
    "TAML": dict(name="Thames Asbestos Management Ltd", accred="HSE licensed asbestos contractor, licence 012588",
                 address="Unit 7, Barking Riverside, London IG11 0AL", phone="+44 20 7946 0293",
                 signer="D. Achterberg"),
    "CPSL": dict(name="Capital Pest Solutions Ltd", accred="BPCA member 4419",
                 address="19 Bermondsey Street, London SE1 3UW", phone="+44 20 7946 0358",
                 signer="J. Mbeki"),
}

ss = getSampleStyleSheet()
S_HEAD = ParagraphStyle("head", parent=ss["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
S_TITLE = ParagraphStyle("title", parent=ss["Heading1"], fontSize=17, leading=21, spaceAfter=4)
S_META = ParagraphStyle("meta", parent=ss["Normal"], fontSize=9.5, leading=13, spaceAfter=8)
S_H2 = ParagraphStyle("h2", parent=ss["Heading3"], fontSize=11, leading=14, spaceBefore=8, spaceAfter=4)
S_MONO = ParagraphStyle("mono", parent=ss["Code"], fontSize=9, leading=12, leftIndent=18)
S_CELL = ParagraphStyle("cell", parent=ss["Normal"], fontSize=9.5, leading=12.5)


def _table(rows: list[tuple[str, str]]) -> Table:
    data = [[Paragraph(k, S_CELL), Paragraph(str(v), S_CELL)] for k, v in rows]
    t = Table(data, colWidths=[62 * mm, 112 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfcfcf")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build(path: str, *, vendor: dict, title: str, type_code: str, scope: str, trade: str,
          number: str, issued: str, expires: str, details: list[tuple[str, str]],
          signer_line: str) -> None:
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm)
    story = [
        Paragraph(f"<b>{vendor['name']}</b> | {vendor['accred']} | {vendor['address']} | "
                  f"{vendor['phone']}", S_HEAD),
        Spacer(1, 6),
        Paragraph(title, S_TITLE),
        Paragraph(f"Type code: {type_code} . Scope: {scope} . Trade: {trade}", S_META),
        Paragraph("Compliance Engine - canonical fields", S_H2),
        Paragraph(f"certificate_number: {number}", S_MONO),
        Paragraph(f"issue_date: {issued}", S_MONO),
        Paragraph(f"expiry_date: {expires}", S_MONO),
        Paragraph("Certificate details", S_H2),
        _table(details),
        Paragraph("Signatures", S_H2),
        _table([("Signed", signer_line), ("Date", issued)]),
    ]
    doc.build(story)


def building_rows(code: str) -> list[tuple[str, str]]:
    b = BUILDINGS[code]
    return [("Building name", b["name"]), ("Building reference", code),
            ("Site address", b["address"]), ("Client", CLIENT),
            ("Site", f"{b['name']} ({code})")]


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    made: list[str] = []

    def out(name: str) -> str:
        p = os.path.join(OUT, name)
        made.append(name)
        return p

    # ── TM44 air-conditioning inspection, per building ─────────────────────────────────
    tm44 = {
        "B-101": dict(issued="14/05/2026", expires="14/05/2031", capacity="1,180 kW",
                      systems="2 x Carrier 30XA-452 air-cooled chillers (roof plant), 2 x Trane CLCP 034 AHUs, 14 fan coil zones",
                      rec="Chiller 2 standby setpoint 1.5 K below Chiller 1 - equalise; replace AHU 1 filters at G4 rather than F7 on the return side; review out-of-hours schedule for Levels 7-12",
                      ref="TM44-B-101-2026-0514"),
        "B-102": dict(issued="02/06/2026", expires="02/06/2031", capacity="86 kW",
                      systems="Split systems to concierge, plant room and 3 communal lounges; server cabinet cooling",
                      rec="Two condensers on the west elevation exceed 12 kW combined and are on the register; clean coils annually; fit a 7-day timer to the lounge units",
                      ref="TM44-B-102-2026-0602"),
    }
    for code, t in tm44.items():
        b = BUILDINGS[code]
        build(out(f"TM44_Air_Conditioning_Inspection_Report_{b['name'].replace(' ', '_')}_{code}_2026.pdf"),
              vendor=VENDORS["ELMW"], title="TM44 Air Conditioning Inspection Report",
              type_code="TM44", scope="Building", trade="Energy",
              number=t["ref"], issued=t["issued"], expires=t["expires"],
              details=building_rows(code) + [
                  ("Inspection standard", "CIBSE TM44:2012, Energy Performance of Buildings (England and Wales) Regulations 2012 reg. 21"),
                  ("Total effective rated output", f"{t['capacity']} (over the 12 kW threshold - inspection mandatory)"),
                  ("Systems inspected", t["systems"]),
                  ("Building use", f"{b['use']}, {b['floors']} floors, {b['gia']} GIA"),
                  ("Refrigerant and F-gas", "All systems on the F-gas register; no leaks recorded at inspection"),
                  ("Recommendations", t["rec"]),
                  ("Lodged on the register", "Yes - report reference lodged with the accreditation scheme"),
                  ("Assessor accreditation number", "NDEA 0221847"),
                  ("Next inspection due", t["expires"]),
              ],
              signer_line="R. Callaghan (accredited air-conditioning energy assessor, NDEA 0221847)")

    # ── Asbestos management survey, per building ───────────────────────────────────────
    asb = {
        "B-101": dict(issued="09/04/2026", expires="09/04/2027", ref="ASB-MS-B-101-2026-0409",
                      built="2016 - post-2000 construction; asbestos-containing materials not expected",
                      found="None. 214 locations inspected; no ACMs identified. Register holds a nil return.",
                      action="Re-inspect annually; manage under the site asbestos management plan"),
        "B-102": dict(issued="21/03/2026", expires="21/03/2027", ref="ASB-MS-B-102-2026-0321",
                      built="1974 - pre-2000 construction; presumed ACMs until surveyed",
                      found="3 items: textured coating to plant room ceiling (chrysotile, good condition, low risk); "
                            "gasket to boiler 1 flue (chrysotile, sealed); floor tile adhesive, ground floor store (chrysotile, encapsulated).",
                      action="Label and manage in situ; re-inspect at 12 months; remove textured coating before any plant room works"),
    }
    for code, a in asb.items():
        b = BUILDINGS[code]
        build(out(f"Asbestos_Management_Survey_{b['name'].replace(' ', '_')}_{code}_2026.pdf"),
              vendor=VENDORS["TAML"], title="Asbestos Management Survey Report",
              type_code="ASBESTOS_SURVEY", scope="Building", trade="Asbestos",
              number=a["ref"], issued=a["issued"], expires=a["expires"],
              details=building_rows(code) + [
                  ("Survey type", "Management survey to HSG264 (Control of Asbestos Regulations 2012, reg. 4 duty to manage)"),
                  ("Duty holder", CLIENT),
                  ("Construction", a["built"]),
                  ("Asbestos-containing materials found", a["found"]),
                  ("Recommended action", a["action"]),
                  ("Surveyor accreditation number", "BOHS P402 / UKAS 4471"),
                  ("Re-inspection due", a["expires"]),
              ],
              signer_line="D. Achterberg (BOHS P402 surveyor, Thames Asbestos Management Ltd)")

    # ── BS 5839-1 periodic inspection, per building ────────────────────────────────────
    fire = {
        # Expired 02/09/2026 on purpose, matching the workbook's FAS-B-101-202603: the Compliance
        # page needs one lapsed item so it reports something rather than nine green rows.
        "B-101": dict(issued="02/03/2026", expires="02/09/2026", ref="BS5839-B-101-2026-0302",
                      system="Analogue addressable, category L2, 3 loops, 412 devices, 2 panels networked",
                      result="Satisfactory with 1 observation",
                      obs="Zone 4 sounder below 65 dB(A) at the far end of the Level 9 corridor; additional sounder proposed"),
        "B-102": dict(issued="12/08/2026", expires="12/02/2027", ref="BS5839-B-102-2026-0812",
                      system="Analogue addressable, category L1 (residential common parts) with LD2 to flats, 1 loop, 186 devices",
                      result="Satisfactory",
                      obs="None. Weekly test log complete; false alarm rate 2 in 12 months, within BS 5839-1 guidance"),
    }
    for code, f in fire.items():
        b = BUILDINGS[code]
        build(out(f"BS5839-1_Fire_Alarm_System_Inspection_{b['name'].replace(' ', '_')}_{code}_2026.pdf"),
              vendor=VENDORS["GRDF"], title="Fire Alarm System Periodic Inspection and Servicing Certificate",
              type_code="FIRE_ALARM_SERVICE", scope="Building", trade="Fire",
              number=f["ref"], issued=f["issued"], expires=f["expires"],
              details=building_rows(code) + [
                  ("Standard", "BS 5839-1:2017 Section 6 - periodic inspection and test"),
                  ("System description", f["system"]),
                  ("Tests carried out", "Panel function, all zones, 100% device test over the 12-month cycle, sounder levels, standby battery capacity, remote signalling to ARC"),
                  ("Result", f["result"]),
                  ("Observations and defects", f["obs"]),
                  ("Engineer accreditation number", "BAFE SP203-1 7742"),
                  ("Next inspection due", f["expires"]),
              ],
              signer_line="N. Haddad (BAFE SP203-1 registered engineer, Guardian Fire Systems)")

    # ── Vendor-scoped, once each ───────────────────────────────────────────────────────
    v = VENDORS["TAML"]
    build(out("HSE_Asbestos_Licence_Thames_Asbestos_Management_2024-27.pdf"),
          vendor=v, title="Licence to Undertake Work with Asbestos",
          type_code="HSE_ASBESTOS_LICENCE", scope="Vendor", trade="Asbestos",
          number="HSE-ASB-012588", issued="01/06/2024", expires="31/05/2027",
          details=[("Licence holder", v["name"]), ("Registered address", v["address"]),
                   ("Licence number", "012588"),
                   ("Granted under", "Control of Asbestos Regulations 2012, regulation 8"),
                   ("Scope", "Licensable work with asbestos including removal of asbestos insulation, coating and insulating board"),
                   ("Conditions", "Notification to HSE 14 days before licensable work (ASB5); named supervisor on every site"),
                   ("Client engaging under this licence", CLIENT),
                   ("Valid from / to", "01/06/2024 to 31/05/2027 (3 years)")],
          signer_line="Issued by the licensing authority; held by D. Achterberg, Thames Asbestos Management Ltd")

    v = VENDORS["CPSL"]
    build(out("BPCA_Membership_Certificate_Capital_Pest_Solutions_2026.pdf"),
          vendor=v, title="Certificate of Membership",
          type_code="BPCA", scope="Vendor", trade="Pest control",
          number="BPCA-4419-2026", issued="01/01/2026", expires="31/12/2026",
          details=[("Member company", v["name"]), ("Registered address", v["address"]),
                   ("Membership number", "4419"), ("Membership grade", "Full servicing member"),
                   ("Standard", "BS EN 16636:2015 pest management services - audited"),
                   ("Technicians", "6 - all holding RSPH Level 2 in Pest Management"),
                   ("Client engaging this member", CLIENT),
                   ("Membership year", "1 January 2026 to 31 December 2026")],
          signer_line="J. Mbeki (Managing Director, Capital Pest Solutions Ltd)")

    v = VENDORS["GRDF"]
    build(out("Employers_Liability_Insurance_Certificate_Guardian_Fire_Systems_2026-27.pdf"),
          vendor=v, title="Certificate of Employers' Liability Insurance",
          type_code="EMPLOYERS_LIABILITY", scope="Vendor", trade="Fire",
          number="ELI-GRDF-2026-88041", issued="01/04/2026", expires="31/03/2027",
          details=[("Policyholder", v["name"]), ("Registered address", v["address"]),
                   ("Policy number", "ELI/2026/88041"),
                   ("Issued under", "Employers' Liability (Compulsory Insurance) Act 1969, regulation 5"),
                   ("Limit of indemnity", "GBP 10,000,000 any one occurrence"),
                   ("Also held", "Public liability GBP 5,000,000 (policy PL/2026/88042); professional indemnity GBP 2,000,000"),
                   ("Client requiring this cover", CLIENT),
                   ("Period of insurance", "1 April 2026 to 31 March 2027")],
          signer_line="Authorised signatory for the insurer; held by N. Haddad, Guardian Fire Systems")

    print(f"  {len(made)} documents written to {OUT}\n")
    for m in made:
        print(f"    {os.path.getsize(os.path.join(OUT, m)) / 1024:5.1f} KB  {m}")


if __name__ == "__main__":
    main()
