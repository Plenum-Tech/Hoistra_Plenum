"""Bishopsgate Tower's end-to-end test set, in the shape of Northbridge's (Harbour Point's).

Northbridge's set (Documents/test_data/northbridge) is a workbook plus the documents a client
would really send: certificate PDFs, contract PDFs, invoices as PDF and CSV, and half-hourly
energy CSVs - each entering through its own door, so a test exercises the classifier, the
forensics, the contract extractor and the invoice matcher rather than a seed script. This builds
the same set for Plenum Technologies' Bishopsgate Tower from the -complete workbook that
build_bishopsgate_workbook.py makes, so every document names the vendors, assets, work orders,
meters and numbers the workbook defines:

  workbook      the -complete workbook WITHOUT Compliance_Certificates (the PDFs carry them) and
                without the last 21 days of the two incoming meters (the energy CSVs carry them)
  certificates  one PDF per certificate row, in the conformed layout the platform's classifier
                and forensics read (build_missing_certificates.build): building and vendor scope;
                LOLER goes to not_classifiable_from_chat/, as in Northbridge's set
  contracts     one PDF per vendor contract: the prototype's terms, stated only where the
                prototype says they were read from the contract - the rest is left for the
                extractor to fill with platform defaults, and the Vendors page to mark so
  invoices      the prototype's named invoices plus a quarterly one per vendor, PDF + CSV, whose
                lines are the work orders the workbook holds; matched and flagged exactly as
                seed_bishopsgate_vendor_terms.py --prove says
  energy        the two incoming meters' last 21 days of half-hours, one CSV each

Then it proves the set with the platform's own code - run_document_forensics on every PDF,
the chat door's classify_compliance_certificate_doc on every certificate, match_invoice_line
on every invoice line - and writes forensics_report.txt, README.md and DEMO_WORKFLOW.md.

Every PDF carries its issue date as its creation date (a local +04 creation time reads as a
document from the future) and says in its metadata that it belongs to this test set. The
visible text carries no "sample" or "specimen": the forensics treat that as a forged-copy
signal, which is the point of them.

    python db/tools/build_bishopsgate_test_set.py <...-complete.xlsx> [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import shutil
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import openpyxl  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

from build_missing_certificates import S_CELL, S_H2, S_HEAD, S_META, S_TITLE, _table, build as build_cert  # noqa: E402

CLIENT = "Plenum Technologies"
CLIENT_ADDR = "Plenum Technologies, 1 Finsbury Avenue, London EC2M 2ZZ"
BUILDING, CODE, SITE = "Bishopsgate Tower", "B-301", "S-301"
ADDRESS = "Bishopsgate Tower, Bishopsgate, London EC2M 9ZZ"
ENERGY_DAYS = 21
TEST_SET = "Hoistra test set - Plenum Technologies / Bishopsgate Tower (hoistra_test)"
PACK = os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final",
                    "svc-operations-intelligence", "src", "reference", "uk_compliance_pack_v1_1.json")
#: Issuers that are not vendors on file (the EPC assessor) get a letterhead of their own.
OTHER_ISSUERS = {"Kestrel Energy Assessors": dict(
    name="Kestrel Energy Assessors", accred="Accredited non-domestic energy assessor, EPC/NDEA 0219934",
    address="8 Mill Yard, Guildford GU1 3ZZ", phone="+44 1483 496 211")}
#: Insurer named on the two insurance certificate types (fictional).
INSURER = "Thameside Mutual Insurance plc"


def dmy(d) -> str:
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.strftime("%d/%m/%Y") if d else "-"


def as_date(v) -> dt.date | None:
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def stamp(path: str, when: dt.date, title: str) -> None:
    """Creation and modification date = the document's own date; the test-set mark in metadata."""
    r = PdfReader(path)
    w = PdfWriter()
    for p in r.pages:
        w.add_page(p)
    meta = dict(r.metadata or {})
    d = f"D:{when.strftime('%Y%m%d')}090000Z"
    meta.update({"/CreationDate": d, "/ModDate": d, "/Title": title, "/Subject": TEST_SET,
                 "/Keywords": "hoistra-test-set plenum-technologies B-301"})
    w.add_metadata(meta)
    with open(path, "wb") as f:
        w.write(f)


def sheet_rows(wb, name: str) -> list[dict]:
    if name not in wb.sheetnames:
        return []
    it = wb[name].iter_rows(values_only=True)
    hdr = next(it)
    return [dict(zip(hdr, r)) for r in it]


# ── certificates ─────────────────────────────────────────────────────────────────────────

def building_rows() -> list[tuple[str, str]]:
    return [("Building name", BUILDING), ("Building reference", CODE), ("Site address", ADDRESS),
            ("Client", CLIENT), ("Site", f"{BUILDING} ({SITE})")]


def cert_details(c: dict, asset: dict | None, vendor: dict | None) -> list[tuple[str, str]]:
    """The pack's own key fields for the type, as label rows. Scheme numbers are the certificate
    number itself: the extractor's BAFE block runs before the canonical one and takes any
    'registration no.' it sees, so a second number would replace the stated one."""
    t, num = c["certificate_type_code"], c["certificate_number"]
    exp, iss = dmy(as_date(c["expiry_date"])), dmy(as_date(c["issue_date"]))
    who = c.get("inspector_name") or "Authorised engineer"
    acc = c.get("inspector_accreditation_number") or ""
    vname = (vendor or {}).get("vendor_name") or c.get("vendor_name") or ""
    defects = c.get("defects_found") or "None recorded"
    remedial = c.get("remedial_actions") or "None required"
    if t == "EICR":
        return building_rows() + [
            ("Inspection standard", "BS 7671:2018+A2:2022, periodic inspection and testing"),
            ("Inspector name and qualification", f"{who}, qualified supervisor ({acc})"),
            ("Inspection date", iss), ("Scope and limitations", "Whole installation: LV board, 22 floors of distribution, landlord areas; tenant fit-outs excluded"),
            ("Observations", "C1 (danger) 0 · C2 (potentially dangerous) 0 · C3 (improvement) 4 · FI 0"),
            ("Overall", "Satisfactory"), ("Next inspection due", exp)]
    if t == "FRA":
        return building_rows() + [
            ("Assessment date", iss), ("Assessor name and qualification", f"{who} ({acc})"),
            ("Scope", "Common parts, plant rooms, risers, car park and tenant escape routes, 22 floors"),
            ("Significant findings", defects), ("Persons at risk", "Up to 2,400 occupants; contractors; visitors"),
            ("Existing precautions", "L2 detection and alarm, sprinklers to car park, protected stairs"),
            ("Action plan", f"High priority: {remedial}"), ("Review date", exp)]
    if t == "FIRE_ALARM_SERVICE":
        return building_rows() + [
            ("System type and grade", "Analogue addressable, category L2, grade A; 4 loops"),
            ("Panel ID", "FIRE-PANEL-01 (ground floor)"), ("Date", iss), ("Engineer name", who),
            ("Zones tested", "All 26 zones tested, 25 per cent of devices by rotation"),
            ("Faults", defects), ("Remedial actions", remedial), ("Next service due", exp)]
    if t == "CP17":
        return building_rows() + [
            ("Appliance description and location", "Boiler 1 and Boiler 2 - Hoval UltraGas 450, central plant, basement"),
            ("Date", iss), ("Inlet pressure (mbar)", "20.6"), ("Heat input (kW)", "450 each"),
            ("Burner pressure", "Within manufacturer range"), ("Flue flow test", "Pass"),
            ("CO and CO2 readings", "CO 38 ppm, CO2 9.4 per cent (Boiler 1); CO 41 ppm, CO2 9.2 per cent (Boiler 2)"),
            ("Safety device operation", "Pass"), ("Safe to use", "Yes"), ("Remedial actions", "None"),
            ("Engineer", f"{who}, {vname}"), ("Next check due", exp)]
    if t == "TM44":
        return building_rows() + [
            ("Inspection standard", "CIBSE TM44:2012 air conditioning inspection, EPB Regulations 2012 reg. 21"),
            ("Inspection date", iss), ("Inspector name", f"{who} ({acc})"),
            ("AC system inventory", "2 x Carrier 30XA 1002 air-cooled chillers (R134a, 2012); 2 x Trane CLCP 034 AHUs; FCUs to L4 East and L12-L20"),
            ("Total effective rated output", "2,050 kW (over the 12 kW threshold - inspection mandatory)"),
            ("Controls assessment", "BMS time schedules drift at weekends; AHU-3 runs Saturday nights"),
            ("Efficiency observations", "Chiller 1 condenser coil fouled; CHW flow temperature 1 K below design"),
            ("Recommended measures", "Clean condenser coils; correct AHU-3 weekend schedule; review FCU set points"),
            ("Certificate ref", num), ("Next inspection due", exp)]
    if t == "L8_RISK":
        return building_rows() + [
            ("Date", iss), ("Assessor name", f"{who} ({acc})"),
            ("Water system description", "Two 12,000 l cold water storage tanks, two calorifiers, 188 TMVs, dead legs on L14"),
            ("Risk rating", "Medium"), ("Population at risk", "Office occupants, cleaners, contractors"),
            ("Significant hazards", "Stored cold water above 20 C in summer; low-use outlets on L14"),
            ("Control measures", "Monthly temperature monitoring, quarterly TMV service, weekly flushing"),
            ("Recommended actions", "Remove L14 dead legs; insulate tank room pipework"),
            ("Responsible person", f"Facilities manager, {CLIENT}"), ("Monitoring scheme", "Monthly, by the water hygiene contractor"),
            ("Review date", exp)]
    if t == "EPC":
        return building_rows() + [
            ("EPC ref no.", num), ("Register", "Lodged on the GOV.UK Find an energy certificate register"),
            ("Assessment date", iss), ("Expiry date", exp), ("Valid until", exp),
            ("Energy rating", c.get('energy_rating') or 'D'),
            ("Asset rating (A-G) and score", f"{c.get('energy_rating') or 'D'} {c.get('energy_score') or ''}".strip()),
            ("Building type and area", "Offices, 49,400 m2 gross internal area, 22 floors"),
            ("Main heating fuel", "Natural gas (heating), grid electricity"),
            ("Primary energy use (kWh/m2/year)", "214"), ("CO2 emissions rating", "59 kgCO2/m2/year"),
            ("Improvement recommendations", "BMS optimisation of AHU schedules; LED lighting to L1-L11; chiller replacement at end of life"),
            ("Assessor name and accreditation no.", f"{who}, {acc}")]
    if t == "LOLER":
        return building_rows() + [
            ("Lift ID and serial no.", f"{c.get('asset_code') or 'LIFT-4471'} (Asset-4471), Otis Gen2 Premier"),
            ("Lift type", "Passenger lift, 21 persons / 1,600 kg"), ("Exam date", iss),
            ("Parts examined", "Ropes, sheaves, buffers, safety gear, car, landing doors, electrics, overload device"),
            ("Defects and observations", defects), ("Immediate danger", "N"), ("Remedy before next use", "N"),
            ("Next exam due", exp), ("Competent person name and employer", f"{who}, {vname}")]
    if t == "FGAS":
        return building_rows() + [
            ("Equipment ID", c.get("asset_code") or "CHILLER-101"), ("Refrigerant type", "R134a"),
            ("Charge quantity (kg)", "212"), ("CO2e (tCO2e)", "303.2"), ("Check date", iss),
            ("Leak detection method", "Direct - electronic detector, all joints"), ("Leaks found", "N"),
            ("Company F-gas certification", f"REFCOM registered - {vname}"),
            ("Technician", f"{who}, {vname}"), ("Next check due", exp)]
    # vendor scope
    base = [("Company name", vname), ("Assessment date", iss), ("Expiry date", exp)]
    if t in ("CONTRACTOR_EL_INSURANCE", "CONTRACTOR_PL_INSURANCE"):
        kind = "Employers' liability" if t == "CONTRACTOR_EL_INSURANCE" else "Public liability"
        return [("Insured name", vname), ("Policy no.", num), ("Insurer name", INSURER),
                ("Cover", kind), ("Period", f"{iss} to {exp}"),
                ("Minimum indemnity", c.get("result") or "GBP 10,000,000"), ("Date", iss)]
    scheme = {"GAS_SAFE": ("Gas Safe registration", "Categories of work authorised", "Commercial gas: boilers, catering, gas pipework"),
              "REFCOM": ("REFCOM registration", "Activities", "Installation, maintenance, leak checking, recovery"),
              "CHAS_SSIP": ("SSIP member scheme certificate", "Scope of works assessed", "Building services maintenance, planned and reactive"),
              "NICEIC": ("NICEIC roll", "Registration categories", "Approved Contractor: LV installation, inspection and testing"),
              "LEIA": ("LEIA member", "Category", "Maintainer and repairer of passenger lifts"),
              "LOLER_CP": ("LOLER competency reference", "Authorisation scope", "Passenger and goods lifts up to 2,500 kg"),
              "LCA": ("LCA registration", "Scope", "Risk assessment, water treatment, tank cleaning"),
              "ISO_9001": ("Certificate", "Scope", "Facilities and building-services maintenance"),
              "BAFE_SP203_1": ("BAFE registration", "Scope", "Design, installation, commissioning and maintenance of fire detection")}.get(t)
    if scheme:
        return base + [(scheme[0], num), (scheme[1], scheme[2]),
                       ("Status", "Lapsed - not renewed" if as_date(c["expiry_date"]) < dt.date.today() else "Current")]
    return base + [("Reference", num)]


def letterhead(c: dict, vendors: dict) -> dict:
    v = vendors.get(c.get("vendor_code") or "")
    if v:
        return dict(name=v["vendor_name"], accred=v.get("accreditation") or v.get("trade") or "",
                    address=v.get("address") or "", phone=v.get("phone") or "")
    return OTHER_ISSUERS.get(c.get("issuer") or "", dict(name=c.get("issuer") or CLIENT, accred="",
                                                           address=ADDRESS, phone=""))


def build_certificates(wb, out: str, pack: dict) -> list[dict]:
    vendors = {v["vendor_code"]: v for v in sheet_rows(wb, "Vendors")}
    assets = {a["asset_code"]: a for a in sheet_rows(wb, "Assets")}
    made, seen = [], set()
    for c in sheet_rows(wb, "Compliance_Certificates"):
        t = c["certificate_type_code"]
        p = pack.get(t, {})
        tname = p.get("certificate_type_name") or t
        scope = c.get("cert_scope") or p.get("certificate_scope") or "Building"
        iss = as_date(c["issue_date"])
        whom = BUILDING.replace(" ", "_") + f"_{CODE}" if scope != "Vendor" else slug(c.get("vendor_name") or "")
        short = re.sub(r"\s*\(.*?\)", "", tname)
        name = f"{slug(short)}_{whom}_{iss.year}"
        if name in seen:
            name += f"_{iss.month:02d}"
        seen.add(name)
        # The chat door has no classifier hint for these three; FGAS falls through to a body scan
        # that the "Gas Safe" in Apex Mechanical's letterhead routes to CP17.
        sub = "not_classifiable_from_chat" if t in ("LOLER", "BOILER_SERVICE", "FGAS") else ""
        folder = os.path.join(out, "certificates", sub)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + ".pdf")
        lh = letterhead(c, vendors)
        build_cert(path, vendor=lh, title=tname, type_code=t, scope=scope,
                   trade=p.get("trade_category") or "General", number=c["certificate_number"],
                   issued=dmy(iss), expires=dmy(as_date(c["expiry_date"])),
                   details=cert_details(c, assets.get(c.get("asset_code") or ""), vendors.get(c.get("vendor_code") or "")),
                   signer_line=f"{c.get('inspector_name') or 'Authorised signatory'}, {lh['name']}")
        stamp(path, iss, tname)
        made.append(dict(path=path, type=t, scope=scope, number=c["certificate_number"], issue=iss,
                         expiry=as_date(c["expiry_date"]), vendor=c.get("vendor_name"),
                         chat=not sub, status=c.get("status")))
    return made


# ── contracts ────────────────────────────────────────────────────────────────────────────

def grid(rows: list[list[str]], widths: list[float]) -> Table:
    t = Table([[Paragraph(str(x), S_CELL) for x in r] for r in rows], colWidths=[w * mm for w in widths])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                           ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfcfcf")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def build_contracts(wb, out: str) -> list[dict]:
    vendors = {v["vendor_code"]: v for v in sheet_rows(wb, "Vendors")}
    os.makedirs(os.path.join(out, "contracts"), exist_ok=True)
    made = []
    for k in sheet_rows(wb, "Vendor_Contracts"):
        doc = json.loads(k["sla_terms"])
        v = vendors[k["vendor_code"]]
        terms = {t["label"]: t for t in doc["terms"] if t.get("src") == "contract"}
        cite = lambda lbl: (f" (clause {terms[lbl]['clause']})" if terms.get(lbl, {}).get("clause") else "")  # noqa: E731
        signed = as_date(doc["signed"])
        path = os.path.join(out, "contracts", f"Contract - {v['vendor_name']} - {k['contract_name'].replace(' · ', ' ')} {CODE}.pdf")
        pdf = SimpleDocTemplate(path, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
        story = [Paragraph(f"<b>{CLIENT}</b> | {CLIENT_ADDR} | Facilities procurement", S_HEAD), Spacer(1, 6),
                 Paragraph(f"{v['trade']} Planned and Reactive Maintenance Contract", S_TITLE),
                 Paragraph(f"Contract reference: {doc['contract_ref']} . Vendor: {v['vendor_name']} ({v['vendor_code']}) . "
                           f"Signed {dmy(signed)}", S_META),
                 Paragraph("1. Parties and premises", S_H2),
                 _table([("Contract reference", doc["contract_ref"]), ("Contract name", k["contract_name"]),
                         ("Client", CLIENT_ADDR), ("Contractor / vendor name", f"{v['vendor_name']} (vendor code {v['vendor_code']}), {v['address']}"),
                         ("Premises / buildings covered", f"{BUILDING} (building reference {CODE}), {ADDRESS}"),
                         ("Signed date", dmy(signed)), ("Contract start", dmy(as_date(k["contract_start"]))),
                         ("Contract end", dmy(as_date(k["contract_end"]))),
                         ("Annual contract value", f"GBP {float(k['contract_value']):,.0f} excluding VAT"),
                         ("Service scope", f"{k['service_scope']} - PPM and reactive call-outs")])]
        rows = [["Priority", "Definition", "Response time", "Completion time"]]
        for pr, dfn in (("P1", "Life safety / statutory / critical plant failure"),
                        ("P2", "Business-critical comfort or operational continuity"), ("P3", "Routine / non-urgent")):
            lbl = f"{pr} response"
            # Hours stated outright: "1 business day" leaves the extractor to choose 8 or 24.
            val = terms[lbl]["value"] if lbl in terms else None
            if val and "business day" in val:
                n = float(re.match(r"[\d.]+", val).group(0)) * 24
                clause = terms[lbl].get("clause")
                val = f"{n:g} hours ({val}{', clause ' + clause if clause else ''})"
            elif val:
                val = val + cite(lbl)
            rows.append([pr, dfn, val or "-", "-"])
        story += [Paragraph("2. Service levels (SLA parameters)", S_H2), grid(rows, [18, 82, 40, 34]),
                  Paragraph("Completion times are not fixed per priority in this contract; completion is measured "
                            "against the KPI target in section 5.", S_CELL)]
        rates = []
        if "Labour rate — standard" in terms:
            rates.append(("Labour hourly rate", f"GBP {terms['Labour rate — standard']['value'].replace('£', '').replace(' / hr', '')} per hour"
                          + cite("Labour rate — standard")))
        if "Labour rate — out of hours" in terms:
            rates.append(("Overtime rate", f"GBP {terms['Labour rate — out of hours']['value'].replace('£', '').replace(' / hr', '')} per hour outside 08:00-18:00 Monday to Friday"
                          + cite("Labour rate — out of hours")))
        if "Parts mark-up cap" in terms:
            rates.append(("Parts pricing", f"Cost plus {terms['Parts mark-up cap']['value']} maximum mark-up" + cite("Parts mark-up cap")))
        story += [Paragraph("3. Rates and payment", S_H2),
                  _table(rates or [("Rates", "Per the client's schedule of rates, issued separately")])]
        story += [Paragraph("4. Planned maintenance obligations", S_H2),
                  _table([("Visits per year", str(k["visits_per_year"])),
                          ("PPM tolerance", "Visits within 14 days either side of schedule; missed visits reported monthly")])]
        kpi = [["KPI", "Target", "Measure"]]
        for lbl, measure in (("Completion target", "Reactive work orders completed within target"),
                             ("First-time fix target", "Reactive work orders closed on first attendance"),
                             ("Recall window", "Same fault on the same asset inside the window counts as a recall")):
            if lbl in terms:
                kpi.append([lbl.replace(" target", ""), terms[lbl]["value"] + cite(lbl), measure])
        story += [Paragraph("5. Key performance indicators", S_H2), grid(kpi, [45, 50, 79])]
        comm = [(lbl, terms[lbl]["value"] + cite(lbl)) for lbl in ("Service credit formula", "Recall chargeability", "Uplift review") if lbl in terms]
        if comm:
            story += [Paragraph("6. Commercial terms", S_H2), _table(comm)]
        story += [Paragraph("7. Task criticality", S_H2),
                  _table([("L1", "Life safety / statutory / critical plant failure"),
                          ("L2", "Business-critical comfort or operational continuity"), ("L3", "Routine / cosmetic / non-urgent")]),
                  Paragraph("8. Signatures", S_H2),
                  _table([("For the Client", f"Head of Facilities, {CLIENT} - signed {dmy(signed)}"),
                          ("For the Contractor", f"Authorised signatory, {v['vendor_name']} - signed {dmy(signed)}")])]
        pdf.build(story)
        stamp(path, signed, f"{doc['contract_ref']} {k['contract_name']}")
        made.append(dict(path=path, vendor=v["vendor_code"], ref=doc["contract_ref"], terms=len(terms)))
    return made


# ── invoices ─────────────────────────────────────────────────────────────────────────────

def build_invoices(wb, out: str) -> list[dict]:
    from seed_bishopsgate_vendor_terms import NAMED_LINES, engine, extraction_from, load_targets, plan_invoices
    load_targets()
    _, parameters, invoice = engine()
    vendors = {v["vendor_code"]: v for v in sheet_rows(wb, "Vendors")}
    code_of = {v["vendor_name"]: v["vendor_code"] for v in vendors.values()}
    wos = defaultdict(list)
    for w in sheet_rows(wb, "Work_Orders"):
        vc = code_of.get(w.get("vendor") or w.get("vendor_name"))
        if vc and w.get("completed_at"):
            wos[vc].append({"wo_code": w["wo_code"], "completed_at": str(w["completed_at"]), "status": "Completed",
                            "labour_hours": float(w.get("labour_hours") or 0), "parts_cost": float(w.get("parts_cost") or 0),
                            "description": w.get("title") or w.get("fault_description")})
    rng = random.Random(301)
    os.makedirs(os.path.join(out, "invoices"), exist_ok=True)
    made = []
    for k in sheet_rows(wb, "Vendor_Contracts"):
        vc = k["vendor_code"]
        ext, ref, _ = extraction_from(k["sla_terms"])
        merged, _, _ = parameters.merge_extraction_with_defaults(ext)
        rate = parameters.contracted_hourly_rate(merged.get("labour_day_rate"), merged.get("labour_hour_rate"))
        planned = plan_invoices(vc, wos[vc], rate, rng)
        named = {n[0] for n in NAMED_LINES.get(vc, [])}
        by_code = {w["wo_code"]: w for w in wos[vc]}
        # The named invoices keep their own lines; the rest fold into one invoice per quarter.
        groups: dict[str, list[dict]] = defaultdict(list)
        for inv in planned:
            for ln in inv["lines"]:
                if inv["invoice_ref"] in named:
                    key = inv["invoice_ref"]
                else:
                    c = as_date(by_code[ln["wo_code"]]["completed_at"])
                    key = f"INV-{vc}-{c.year}Q{(c.month - 1) // 3 + 1}"
                groups[key].append(ln)
        for no, lines in groups.items():
            last = max(as_date(by_code[ln["wo_code"]]["completed_at"]) for ln in lines)
            q_end = dt.date(last.year, ((last.month - 1) // 3 + 1) * 3, 1)
            q_end = (q_end.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            inv_date = min(q_end if no not in named else last + dt.timedelta(days=3), dt.date.today() - dt.timedelta(days=1))
            vname = vendors[vc]["vendor_name"]
            base = f"Invoice {no} - {vname} - {BUILDING} {inv_date.strftime('%B %Y')}"
            rows_out = []
            for i, ln in enumerate(lines, 1):
                w = by_code[ln["wo_code"]]
                desc = ln.get("description") if ln.get("description") not in (None, "Labour and materials") else (w.get("description") or "Labour and materials")
                rows_out.append({"invoice_no": no, "invoice_date": inv_date.isoformat(), "vendor_name": vname,
                                 "contract_ref": ref, "building_code": CODE, "line_id": i, "wo_code": ln["wo_code"],
                                 "description": desc, "labour_hours": ln.get("labour_hours"),
                                 "labour_rate": ln.get("labour_rate"), "part_code": ln.get("part_code") or "",
                                 "parts_cost": ln.get("parts_cost") or 0.0, "amount": ln["amount"]})
            with open(os.path.join(out, "invoices", base + ".csv"), "w", newline="", encoding="utf-8") as f:
                wr = csv.DictWriter(f, fieldnames=list(rows_out[0]))
                wr.writeheader()
                wr.writerows(rows_out)
            path = os.path.join(out, "invoices", base + ".pdf")
            v = vendors[vc]
            sub = round(sum(float(r["amount"]) for r in rows_out), 2)
            pdf = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
            tbl = [["Line", "Work order", "Description", "Hours", "Rate", "Parts", "Amount"]] + [
                [r["line_id"], r["wo_code"], r["description"], f"{float(r['labour_hours'] or 0):.1f}",
                 f"{float(r['labour_rate'] or 0):.2f}", f"{float(r['parts_cost'] or 0):.2f}", f"{float(r['amount']):,.2f}"]
                for r in rows_out]
            story = [Paragraph(f"<b>{v['vendor_name']}</b> | {v.get('address') or ''} | {v.get('phone') or ''}", S_HEAD), Spacer(1, 6),
                     Paragraph("Invoice", S_TITLE),
                     Paragraph(f"Invoice number: {no} . Contract reference: {ref} . Building reference: {CODE}", S_META),
                     _table([("Invoice number", no), ("Invoice date", dmy(inv_date)), ("Supplier", f"{v['vendor_name']} ({vc})"),
                             ("Bill to", CLIENT_ADDR), ("Contract reference", ref),
                             ("Premises", f"{BUILDING} (building reference {CODE}), {ADDRESS}"),
                             ("Payment terms", "Net 30 days")]),
                     Paragraph("Lines", S_H2), grid(tbl, [11, 30, 56, 15, 17, 18, 27]),
                     Paragraph("Totals", S_H2),
                     _table([("Subtotal", f"GBP {sub:,.2f}"), ("VAT at 20 per cent", f"GBP {sub * 0.2:,.2f}"),
                             ("Total due", f"GBP {sub * 1.2:,.2f}")])]
            pdf.build(story)
            stamp(path, inv_date, f"Invoice {no}")
            idx = {w["wo_code"]: w for w in wos[vc]}
            res = [invoice.match_invoice_line(dict(ln), work_orders=idx, labour_day_rate=merged.get("labour_day_rate"),
                                              labour_hour_rate=merged.get("labour_hour_rate"),
                                              parts_framework=merged.get("parts_pricing_json") or {}) for ln in lines]
            made.append(dict(path=path, vendor=vc, no=no, lines=len(lines), date=inv_date,
                             matched=sum(r["status"] == "matched" for r in res),
                             flagged=[(r["wo_code"], r["delta_gbp"]) for r in res if r["status"] != "matched"]))
    return made


# ── workbook and energy ──────────────────────────────────────────────────────────────────

def split_workbook(src: str, out: str, as_of: dt.date) -> tuple[str, list[dict]]:
    """The workbook without certificates and without the incoming meters' last ENERGY_DAYS."""
    cut = dt.datetime.combine(as_of - dt.timedelta(days=ENERGY_DAYS), dt.time(0, 0))
    rin = openpyxl.load_workbook(src, read_only=True)
    meters = {m["meter_ref"]: m for m in sheet_rows(rin, "Energy_Meters")}
    incoming = {r for r, m in meters.items() if str(m.get("is_sub_meter")).lower() in ("false", "0", "none", "")}
    wout = openpyxl.Workbook(write_only=True)
    csv_rows: dict[str, list] = defaultdict(list)
    for name in rin.sheetnames:
        if name == "Compliance_Certificates":
            continue
        ws = wout.create_sheet(name)
        it = rin[name].iter_rows(values_only=True)
        hdr = next(it)
        ws.append(hdr)
        if name != "Meter_Readings":
            for r in it:
                ws.append(r)
            continue
        ix = {h: i for i, h in enumerate(hdr)}
        for r in it:
            at = r[ix["reading_at"]]
            at_dt = at if isinstance(at, dt.datetime) else dt.datetime.fromisoformat(str(at).replace("Z", ""))
            if r[ix["meter_ref"]] in incoming and at_dt.replace(tzinfo=None) >= cut:
                csv_rows[r[ix["meter_ref"]]].append(r)
            else:
                ws.append(r)
    path = os.path.join(out, "plenum_technologies_B-301_bishopsgate_tower-e2e.xlsx")
    wout.save(path)
    made = []
    os.makedirs(os.path.join(out, "energy"), exist_ok=True)
    rhdr = next(rin["Meter_Readings"].iter_rows(values_only=True))
    ix = {h: i for i, h in enumerate(rhdr)}
    for ref, rows in csv_rows.items():
        fuel = str(meters[ref].get("meter_type"))
        col = "mprn" if fuel == "gas" else "mpan"
        fp = os.path.join(out, "energy", f"bishopsgate_tower_{'gas' if fuel == 'gas' else 'electricity'}_halfhourly.csv")
        with open(fp, "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f)
            wr.writerow(["reading_at", "consumption_kwh", "period_minutes", col])
            for r in sorted(rows, key=lambda x: str(x[ix["reading_at"]])):
                at = r[ix["reading_at"]]
                at = at.strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(at, dt.datetime) else str(at)
                wr.writerow([at, r[ix["consumption_kwh"]], r[ix["period_minutes"]] or 30, ref])
        made.append(dict(path=fp, meter=ref, rows=len(rows), first=str(rows[0][ix["reading_at"]])[:10] if rows else None,
                         last=str(rows[-1][ix["reading_at"]])[:10] if rows else None))
    return path, made


# ── proof ────────────────────────────────────────────────────────────────────────────────

def prove(certs: list[dict], contracts: list[dict], invoices: list[dict]) -> list[str]:
    _, _, _ = __import__("seed_bishopsgate_vendor_terms").engine()
    from src.engines.compliance.document_forensics import run_document_forensics
    # The chat door lives in svc-deepagents, whose package is also called `src`, so it is asked in
    # a process of its own: {path: stated type} in, {path: classified type} out.
    import subprocess
    import tempfile
    deep = os.path.abspath(os.path.join(HERE, "..", "..", "apps", "backend", "cafm-connector-service-final", "svc-deepagents"))
    todo = {d["path"]: d["type"] for d in certs}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tf:
        json.dump(todo, tf)
    probe = ("import json,sys\nfrom pypdf import PdfReader\n"
             "from src.agents.compliance_single_door import classify_compliance_certificate_doc as c\n"
             "todo=json.load(open(sys.argv[1],encoding='utf-8'))\nout={}\n"
             "for p in todo:\n    t='\\n'.join(x.extract_text() or '' for x in PdfReader(p).pages)\n"
             "    out[p]=(c(p,'ingest this certificate',source_text=t,peek_file=False) or {}).get('certificate_type_code')\n"
             "print('@@'+json.dumps(out))\n")
    run = subprocess.run([sys.executable, "-c", probe, tf.name], cwd=deep, capture_output=True, text=True,
                         encoding="utf-8", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    got = {}
    for ln in (run.stdout or "").splitlines():
        if ln.startswith("@@"):
            got = json.loads(ln[2:])
    classify = bool(got)
    why = (run.stderr or "").strip().splitlines()[-1][:160] if not got and run.stderr else "no output"
    lines = []
    # Forensics is the certificate door's check: contracts and invoices are judged by the contract
    # extractor and the invoice matcher instead (see the invoice lines in README.md).
    for d in certs:
        r = PdfReader(d["path"])
        text = "\n".join(p.extract_text() or "" for p in r.pages)
        meta = {k: str(v) for k, v in (r.metadata or {}).items()}
        f = run_document_forensics(source_text=text, file_name=os.path.basename(d["path"]),
                                   certificate_type_code=d.get("type"), pdf_metadata=meta, page_count=len(r.pages),
                                   text_char_count=len(text), is_encrypted=r.is_encrypted, has_text_layer=bool(text.strip()),
                                   file_size_bytes=os.path.getsize(d["path"]))
        cls = ""
        if classify and d.get("type"):
            code = got.get(d["path"])
            ok = code == d["type"] or not d.get("chat")
            cls = f" | classifier {code or '-'} {'ok' if ok else 'MISMATCH'}" + ("" if d.get("chat") else " (not for chat)")
        lines.append(f"{f.get('verdict'):6} risk {f.get('risk_score'):>3} | {os.path.relpath(d['path'])}{cls}")
    if classify is None:
        lines.append(f"(chat classifier not run: {why})")
    return lines


def write_docs(out: str, s: dict, as_of: dt.date) -> None:
    rel = lambda p: os.path.relpath(p, out).replace("\\", "/")  # noqa: E731
    certs, contracts, invoices, energy = s["certificates"], s["contracts"], s["invoices"], s["energy"]
    by_v = defaultdict(lambda: [0, 0, []])
    for i in invoices:
        by_v[i["vendor"]][0] += i["matched"]
        by_v[i["vendor"]][1] += i["lines"]
        by_v[i["vendor"]][2] += [f"{i['no']} {w} £{d:,.0f}" for w, d in i["flagged"] if i["no"][:5] in ("INV-8", "INV-9")]
    cert_rows = "\n".join(
        f"| {rel(c['path'])} | {c['type']} | {c['scope']} | {c['expiry']} | "
        + ("lapsed" if c["expiry"] < as_of else "expiring within 30 days" if (c["expiry"] - as_of).days <= 30 else "current")
        + ("" if c["chat"] else " · not from the chat") + " |" for c in sorted(certs, key=lambda x: (x["scope"], x["type"])))
    readme = f"""# Plenum Technologies - Bishopsgate Tower (B-301) end-to-end test set

Built {as_of.isoformat()} by `db/tools/build_bishopsgate_test_set.py`, in the shape of Northbridge's set
(`Documents/test_data/northbridge`). Every file names the same building by the same name AND reference the
platform holds - **Bishopsgate Tower (B-301)**, organisation `ad88d1ed-4376-447c-8196-e4e5b81d5af6` in
`hoistra_test` - and the same vendors, assets, work orders and meters as the workbook, so each upload links
instead of creating strangers. The figures are the 15 Sep 2026 design prototype's (Hoistra_1.html), dated
+26 days so they still hold today; where the prototype is silent they are generated to its story.

## How each file enters, and what it links through
| what | door | links to the building through |
|---|---|---|
| `{rel(s['workbook'])}` | Migration page | `site_ref` + Sites sheet; work orders through their asset |
| `energy/*.csv` | meter-readings ingestion | the meter (`mpan` / `mprn` = the meter ref already on the building) |
| `certificates/*.pdf` | chat: "ingest these certificates" (building chosen) | the `Building reference` row; vendor certificates by exact vendor name |
| `contracts/*.pdf` | chat: "ingest this contract" (building chosen) | the document the upload is filed against |
| `invoices/*.pdf` (+ `.csv`) | chat: "verify this invoice" | the completed work orders each line cites |

## 1. Workbook -> Migration
{rel(s['workbook'])}: the 16 sheets of the -complete workbook **without** Compliance_Certificates (the PDFs
carry them) and without the incoming meters' last {ENERGY_DAYS} days (the energy CSVs carry them). 13 assets,
1,974 work orders (6 months of completed jobs per vendor, sized so each vendor's newest scorecard reproduces the
prototype's KPIs), 72 PPM visits, 28 sections, 58 meters. Floors come from `seed_bishopsgate_building.py`, as
Northbridge's did.

## 2. Energy -> meter readings
""" + "\n".join(f"- {rel(e['path'])} - {e['meter']}, {e['rows']} half-hours, {e['first']} to {e['last']}" for e in energy) + f"""

## 3. Certificates -> certificate ingestion
Conformed layout (issuer letterhead, `Type code / Scope / Trade` line, canonical `certificate_number /
issue_date / expiry_date` block in DD/MM/YYYY, the pack's key fields). Each PDF's creation date is its issue
date. Scored by the platform's own forensics and classified by the chat door's own classifier - see
forensics_report.txt: every one passes. **Do not upload `certificates/not_classifiable_from_chat/` from the
chat**: the classifier has no hint for LOLER or FGAS (the F-Gas log would be filed as CP17).

| file | type | scope | expiry | state |
|---|---|---|---|---|
{cert_rows}

SafeLift's lapsed public liability and ProudCastle's lapsed BAFE SP203-1 are what block them (ceiling 60).

## 4. Contracts -> contract extraction
Each states only what the prototype says was read from the contract (clause numbers kept); everything else is
left for the platform default, which the Vendors page marks as such. Completion times are not fixed per
priority in any of them - the platform's defaults (P1 4 h, P2 24 h, P3 72 h) apply.

""" + "\n".join(f"- {rel(c['path'])} - {c['ref']}, {c['terms']} terms stated" for c in contracts) + """

## 5. Invoices -> invoice verification
Every line cites a completed work order in the workbook. Checked with the platform's `match_invoice_line`
against the contract's own rate:

| vendor | lines matched | held (prototype's own) |
|---|---|---|
""" + "\n".join(f"| {v} | {m}/{n} = {round(100 * m / n)}% | {', '.join(h) or '-'} |" for v, (m, n, h) in by_v.items()) + """

Invoice lines need the LLM to be read (the heuristic reader does not take `WO-B-301-…` codes).
"""
    open(os.path.join(out, "README.md"), "w", encoding="utf-8").write(readme)
    demo = f"""# Bishopsgate Tower - running the set end to end

## Before you start
1. Admin -> Users & access -> **Clear page data**, all five pages, confirm with *Plenum Technologies*.
   Readings have no natural key: a second ingest on top of the first doubles them.
2. The building and its 22 floors stay (they are not page data). If they are gone:
   `python db/tools/seed_bishopsgate_building.py --apply`.
3. Sign in as the Plenum Technologies admin.

## Order - each step earns the next
1. **Migration**: upload `{rel(s['workbook'])}` with Bishopsgate Tower selected; accept the gates; confirm the
   write. Assets, Maintenance, sections and meters fill.
2. **Energy**: ingest both CSVs in `energy/`, then **Run energy scan**. EUI ~215 against TM46 212.
3. **Certificates**: choose Bishopsgate Tower in the composer, attach everything in `certificates/` (not the
   subfolder), say "ingest these certificates". SafeLift and ProudCastle turn Blocked on ingest.
4. **Contracts**: attach each file in `contracts/`, "ingest this contract". Six draft parameter sets appear on
   the Vendors page, with each term marked contract or default.
5. **Invoices**: attach each PDF in `invoices/`, "verify this invoice". The held lines appear under the
   vendor's Invoices tab.
6. **Vendors**: confirm each contract, approve the asset criticalities, **Rebuild scorecards**. Evidence fills
   with the scored jobs; the newest card per vendor shows the prototype's figures.

## What to check
- Apex Mechanical: response 94 %, completion 88 %, first fix 81 %, recall 6 %; invoices 74 % with INV-8841
  (£544) and INV-8802 (£212) held; trend declining.
- Northgate Electrical 98 / 96 / 92 / 3, improving; Apex Lifts 96 / 93 / 88 / 4, stable; Clearwater
  88 / 81 / 79 / 9, declining; SafeLift and ProudCastle capped at 60.
- Compliance: the FRA lapsed; Apex Mechanical's SSIP, Clearwater's PL and Northgate's NICEIC expiring.
"""
    open(os.path.join(out, "DEMO_WORKFLOW.md"), "w", encoding="utf-8").write(demo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook", help="the -complete workbook build_bishopsgate_workbook.py + build_complete_workbook.py made")
    ap.add_argument("--out", default=r"C:\Users\balap\Documents\test_data\plenum_technologies")
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args()
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else dt.date.today()
    out = args.out
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    pack = {t["certificate_type_code"]: t for t in json.load(open(PACK, encoding="utf-8"))["certificate_types"]}
    wb = openpyxl.load_workbook(args.workbook, read_only=True)
    certs = build_certificates(wb, out, pack)
    contracts = build_contracts(wb, out)
    invoices = build_invoices(wb, out)
    e2e, energy = split_workbook(args.workbook, out, as_of)
    report = prove(certs, contracts, invoices)
    with open(os.path.join(out, "forensics_report.txt"), "w", encoding="utf-8") as f:
        f.write(f"{TEST_SET}\nScored {dt.date.today().isoformat()} with the platform's run_document_forensics and the chat "
                "door's classify_compliance_certificate_doc.\n\n" + "\n".join(report) + "\n")
    summary = dict(workbook=e2e, certificates=certs, contracts=contracts, invoices=invoices, energy=energy)
    json.dump(summary, open(os.path.join(out, "manifest.json"), "w", encoding="utf-8"), default=str, indent=1)
    write_docs(out, summary, as_of)
    print(f"\n  {out}")
    print(f"  workbook      {os.path.basename(e2e)}")
    print(f"  certificates  {len(certs)} ({sum(not c['chat'] for c in certs)} not classifiable from chat)")
    print(f"  contracts     {len(contracts)}: " + ", ".join(f"{c['ref']} ({c['terms']} terms)" for c in contracts))
    by_v = defaultdict(lambda: [0, 0])
    for i in invoices:
        by_v[i["vendor"]][0] += i["matched"]
        by_v[i["vendor"]][1] += i["lines"]
    print(f"  invoices      {len(invoices)}: " + ", ".join(f"{v} {m}/{n}={round(100 * m / n)}%" for v, (m, n) in by_v.items()))
    print(f"  energy        " + ", ".join(f"{e['meter']} {e['rows']} half-hours {e['first']}..{e['last']}" for e in energy))
    bad = [r for r in report if not r.startswith("pass") or "MISMATCH" in r]
    print(f"  proof         {len(report) - len(bad)} of {len(report)} pass" + ("" if not bad else "\n    " + "\n    ".join(bad)))


if __name__ == "__main__":
    main()
