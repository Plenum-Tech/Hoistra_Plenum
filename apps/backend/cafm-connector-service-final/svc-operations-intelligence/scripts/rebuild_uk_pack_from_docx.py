"""
Rebuild uk_compliance_pack_v1_1.json from the authoritative Word pack:

  C:\\Users\\balap\\Documents\\UK_Compliance_Certification_Pack_v1.1.docx

Word v1.1 = 54 certificate cards (27 Building + 27 Vendor).
Previous JSON had 57 by expanding P402/P403/P404 and PA1/PA2/PA6 into separate
CountryPack rows and merging contractor EL+PL insurance into one row.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from docx import Document

DOCX = Path(r"C:\Users\balap\Documents\UK_Compliance_Certification_Pack_v1.1.docx")
OUT = Path(__file__).resolve().parents[1] / "src" / "reference" / "uk_compliance_pack_v1_1.json"

VERIFY = {
    "Gas Safe": "https://www.gassaferegister.co.uk/find-an-engineer/",
    "NICEIC": "https://www.niceic.com/find-a-contractor",
    "NAPIT": "https://www.napit.org.uk/find-an-installer",
    "BAFE": "https://www.bafe.org.uk/find-a-bafe-registered-company/",
    "NSI": "https://www.nsi.org.uk/find-a-company/",
    "HSE Asbestos": "https://www.hse.gov.uk/asbestos/licensing/",
    "UKAS": "https://www.ukas.com/find-an-organisation/",
    "LCA": "https://www.legionellacontrol.org.uk/members/",
    "REFCOM": "https://www.refcom.org.uk/find-a-company/",
    "SSIP": "https://ssip.org.uk/members/",
    "SIA ACS": "https://www.sia.homeoffice.gov.uk/Pages/acs-rosta.aspx",
    "SIA licence": "https://www.sia.homeoffice.gov.uk/Pages/licensing-check.aspx",
    "BPCA": "https://bpca.org.uk/find-a-pest-controller",
    "CHAS": "https://www.chas.co.uk/",
    "LEIA": "https://www.leia.co.uk/",
}

# Title substring → (code, trade_category, verification_key, required_accreditation)
CODE_MAP: list[tuple[str, str, str, str | None, str | None]] = [
    # Building
    ("Fire Risk Assessment", "FRA", "Fire", None, "BAFE SP205"),
    ("Fire Alarm", "FIRE_ALARM_SERVICE", "Fire", "BAFE", "BAFE SP203-1"),
    ("Emergency Lighting", "EMERGENCY_LIGHTING", "Fire", None, None),
    ("Sprinkler", "SPRINKLER", "Fire", None, None),
    ("Fire Door", "FIRE_DOOR", "Fire", None, None),
    ("Electrical Installation Condition Report", "EICR", "Electrical", "NICEIC", "NICEIC"),
    ("Electrical Installation Certificate", "EIC", "Electrical", "NICEIC", "NICEIC"),
    ("Portable Appliance Testing", "PAT", "Electrical", None, None),
    ("Gas Safety Certificate", "CP17", "Gas", "Gas Safe", "Gas Safe"),
    ("Boiler Service", "BOILER_SERVICE", "Gas", "Gas Safe", "Gas Safe"),
    ("Legionella Risk Assessment", "L8_RISK", "Water/Legionella", "LCA", "LCA"),
    ("Legionella Monitoring", "LEGIONELLA_MONITORING", "Water/Legionella", None, None),
    ("Cold Water Storage Tank", "COLD_WATER_TANK", "Water/Legionella", None, None),
    ("Written Scheme of Examination", "WSE", "Pressure Systems", None, None),
    ("LOLER Thorough Examination", "LOLER", "LOLER", None, "LEEA"),
    ("Asbestos Management Survey", "ASBESTOS_SURVEY", "Asbestos", "UKAS", "UKAS"),
    ("Asbestos Register", "ASBESTOS_REGISTER", "Asbestos", None, None),
    ("Energy Performance Certificate", "EPC", "Energy", None, None),
    ("Air Conditioning Inspection", "TM44", "Energy", None, None),
    ("Display Energy Certificate", "DEC", "Energy", None, None),
    ("ESOS Assessment", "ESOS", "Energy", None, None),
    ("F-Gas Leak Check", "FGAS", "F-Gas", "REFCOM", "REFCOM"),
    ("Health & Safety Policy", "HS_POLICY", "H&S", None, None),
    ("General Risk Assessment", "HS_RISK_ASSESSMENT", "H&S", None, None),
    ("Employers' Liability Insurance Certificate", "EL_INSURANCE", "H&S", None, None),
    ("Higher-Risk Building", "HRB_REGISTRATION", "Building Safety Act", None, None),
    ("Building Safety Case", "SAFETY_CASE_REPORT", "Building Safety Act", None, None),
    # Vendor
    ("Gas Safe Registration Certificate", "GAS_SAFE", "Gas", "Gas Safe", None),
    ("ACS Individual Competency Card", "ACS_CARD", "Gas", "Gas Safe", None),
    ("NICEIC Approved Contractor", "NICEIC", "Electrical", "NICEIC", None),
    ("NAPIT Registration", "NAPIT", "Electrical", "NAPIT", None),
    ("City & Guilds 2382", "CG_2382", "Electrical", None, None),
    ("BAFE SP203-1", "BAFE_SP203_1", "Fire", "BAFE", None),
    ("NSI Gold Certificate — Fire", "NSI_GOLD_FIRE", "Fire", "NSI", None),
    ("NSI Gold Certificate - Fire", "NSI_GOLD_FIRE", "Fire", "NSI", None),
    ("BAFE SP101", "BAFE_SP101", "Fire", "BAFE", None),
    ("LEIA Membership", "LEIA", "Lifts", "LEIA", None),
    ("LOLER Competent Person", "LOLER_CP", "Lifts", None, None),
    ("HSE Asbestos Removal Licence", "HSE_ASBESTOS_LICENCE", "Asbestos", "HSE Asbestos", None),
    ("UKAS Accreditation Certificate", "UKAS_ASBESTOS", "Asbestos", "UKAS", None),
    ("Individual Asbestos Competency", "ASBESTOS_P402_P403_P404", "Asbestos", None, None),
    ("Legionella Control Association", "LCA", "Water", "LCA", None),
    ("BTEC/BOHS Legionella", "BTEC_LEGIONELLA", "Water", None, None),
    ("F-Gas Company Certificate", "REFCOM", "HVAC/F-Gas", "REFCOM", None),
    ("City & Guilds 2079", "CG_2079", "HVAC/F-Gas", None, None),
    ("CHAS Premium", "CHAS_SSIP", "General", "SSIP", None),
    ("ISO 9001", "ISO_9001", "General", "UKAS", None),
    ("ISO 14001", "ISO_14001", "General", "UKAS", None),
    ("Contractors' Employers' Liability", "CONTRACTOR_EL_INSURANCE", "General", None, None),
    ("Contractors' Public Liability", "CONTRACTOR_PL_INSURANCE", "General", None, None),
    ("SIA Approved Contractor Scheme", "SIA_ACS", "Security", "SIA ACS", None),
    ("Individual SIA Licence", "SIA_INDIVIDUAL", "Security", "SIA licence", None),
    ("NSI Gold Certificate — Security", "NSI_GOLD_SECURITY", "Security", "NSI", None),
    ("NSI Gold Certificate - Security", "NSI_GOLD_SECURITY", "Security", "NSI", None),
    ("BPCA Corporate Membership", "BPCA", "Pest Control", "BPCA", None),
    ("Individual Pesticide Application", "PA1_PA2_PA6", "Pest Control", None, None),
]


def cell_full_text(cell) -> str:
    parts: list[str] = []
    for p in cell.paragraphs:
        t = p.text.strip()
        if t:
            parts.append(t)
    for nested in cell.tables:
        for row in nested.rows:
            for c in row.cells:
                ct = cell_full_text(c)
                if ct:
                    parts.append(ct)
    return "\n".join(parts)


def field_after(body: str, label: str) -> str | None:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == label.lower():
            vals: list[str] = []
            for j in range(i + 1, len(lines)):
                nxt = lines[j].strip()
                if not nxt:
                    continue
                if nxt in {
                    "Regulation",
                    "Responsible party",
                    "Frequency",
                    "Issuing body",
                    "Key fields",
                    "SAMPLE FORMAT / OFFICIAL TEMPLATE",
                } or nxt.startswith("SAMPLE FORMAT"):
                    break
                if nxt.lower() in {
                    "regulation",
                    "responsible party",
                    "frequency",
                    "issuing body",
                    "key fields",
                }:
                    break
                vals.append(nxt)
            return " ".join(vals).strip() if vals else None
    return None


def parse_frequency_months(freq: str | None) -> int | None:
    if not freq:
        return None
    f = freq.lower()
    if "5-year" in f or "5 year" in f:
        return 60
    if "4-year" in f or "4 year" in f or "48" in f:
        return 48
    if "3-year" in f or "3 year" in f or "36" in f:
        return 36
    if "10-year" in f or "120" in f:
        return 120
    if "quarterly" in f or "6-month" in f or "6 month" in f:
        return 6
    if "monthly" in f:
        return 1
    if "annual" in f or "yearly" in f or "12" in f:
        return 12
    if "no fixed" in f or "living document" in f or "on completion" in f:
        return None
    m = re.search(r"(\d+)\s*year", f)
    if m:
        return int(m.group(1)) * 12
    return None


def key_fields_schema(key_fields: str | None) -> dict:
    required = ["certificate_number", "issue_date", "expiry_date"]
    optional: list[str] = []
    if key_fields:
        bits = [b.strip() for b in re.split(r"[|/;]", key_fields) if b.strip()]
        # Keep as documentation list under optional labels
        optional = bits[:24]
    return {"required": required, "optional": optional, "source_key_fields": key_fields}


def match_code(title: str) -> tuple[str, str, str | None, str | None]:
    # Prefer longer / more specific matches
    for needle, code, trade, vkey, req in sorted(CODE_MAP, key=lambda x: -len(x[0])):
        if needle.lower() in title.lower():
            # Avoid EL_INSURANCE matching Contractors' Employers' Liability
            if code == "EL_INSURANCE" and "contractor" in title.lower():
                continue
            return code, trade, vkey, req
    slug = re.sub(r"[^A-Z0-9]+", "_", title.upper())[:60].strip("_")
    return slug, "General", None, None


def extract_cards(doc: Document) -> list[dict]:
    cards: list[dict] = []
    scope = None
    trade_section = None
    for table in doc.tables:
        t0 = table.rows[0].cells[0].text.strip()
        if "SECTION 1" in t0.upper():
            scope = "Building"
            continue
        if "SECTION 2" in t0.upper():
            scope = "Vendor"
            continue
        if len(table.rows) == 1:
            trade_section = t0
            continue
        if not scope or not t0:
            continue
        body = cell_full_text(table.rows[1].cells[0]) if len(table.rows) > 1 else ""
        code, trade, vkey, req = match_code(t0)
        # Prefer section trade category when present
        if trade_section and scope == "Vendor":
            # keep mapped trade (cleaner)
            pass
        regulation = field_after(body, "Regulation")
        issuing = field_after(body, "Issuing body")
        frequency = field_after(body, "Frequency")
        keys = field_after(body, "Key fields")
        cards.append(
            {
                "pack_id": "UK-COMPLIANCE-v1.1",
                "country_code": "UK",
                "pack_version": "1.1",
                "certificate_type_code": code,
                "certificate_type_name": t0,
                "certificate_scope": scope,
                "trade_category": trade,
                "regulation_reference": regulation,
                "regulation_url": None,
                "frequency_months": parse_frequency_months(frequency),
                "issuing_body": issuing,
                "required_contractor_accreditation": req,
                "verification_url": VERIFY.get(vkey) if vkey else None,
                "key_fields_schema": key_fields_schema(keys),
                "source_document": "UK_Compliance_Certification_Pack_v1.1.docx",
                "docx_trade_section": trade_section,
                "frequency_text": frequency,
            }
        )
    return cards


def main() -> None:
    doc = Document(str(DOCX))
    cards = extract_cards(doc)
    codes = [c["certificate_type_code"] for c in cards]
    if len(codes) != len(set(codes)):
        dupes = [c for c in codes if codes.count(c) > 1]
        raise SystemExit(f"Duplicate codes: {sorted(set(dupes))}")

    building = sum(1 for c in cards if c["certificate_scope"] == "Building")
    vendor = sum(1 for c in cards if c["certificate_scope"] == "Vendor")
    assert building == 27 and vendor == 27 and len(cards) == 54, (
        f"expected 27+27=54 got {building}+{vendor}={len(cards)}"
    )

    out = {
        "pack_id": "UK-COMPLIANCE-v1.1",
        "country_code": "UK",
        "pack_version": "1.1",
        "description": (
            "UK Compliance Certification Pack v1.1 — rebuilt from authoritative "
            "UK_Compliance_Certification_Pack_v1.1.docx (54 certificate cards)."
        ),
        "source_docx": str(DOCX),
        "type_counts": {
            "building": 27,
            "vendor": 27,
            "total": 54,
            "note": (
                "Authoritative Word pack v1.1 has 54 cards (27 Building + 27 Vendor). "
                "Earlier platform JSON used 57 by splitting P402/P403/P404 and PA1/PA2/PA6 "
                "into separate CountryPack rows and merging contractor EL+PL insurance. "
                "Operative modules P402/P403/P404 and PA1/PA2/PA6 remain on ResourceSkill."
            ),
        },
        "verification_registers": VERIFY,
        "certificate_types": cards,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(cards)} types ({building}B + {vendor}V) -> {OUT}")


if __name__ == "__main__":
    main()
