"""Generate per-format forgery YAML checklists for all CCC catalogue codes."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "engines" / "compliance" / "forgery_prompts"

SPECS: dict[str, tuple[list[str], list[str], list[str], str]] = {
    "FRA": (
        ["fire risk assessment", "fra"],
        ["ifsm", "ife", "fpa", "bafe"],
        ["assessor", "premises", "review"],
        "Fire risk assessment",
    ),
    "FIRE_ALARM_SVC": (
        ["fire alarm", "bs 5839"],
        ["bafe", "nsi", "ssaib"],
        ["certificate", "service", "system"],
        "Fire alarm service",
    ),
    "EMERGENCY_LIGHTING": (
        ["emergency lighting", "bs 5266"],
        ["niceic", "napit"],
        ["duration", "test", "certificate"],
        "Emergency lighting",
    ),
    "SPRINKLER_TEST": (
        ["sprinkler", "suppression"],
        ["bafe", "lps"],
        ["annual", "test", "certificate"],
        "Sprinkler/suppression",
    ),
    "FIRE_DOOR": (
        ["fire door"],
        ["fdis", "bwf", "certifire", "trada"],
        ["inspection", "door", "rating"],
        "Fire door inspection",
    ),
    "EIC": (
        ["electrical installation certificate", "eic"],
        ["niceic", "napit", "eca"],
        ["bs 7671", "circuit", "certificate"],
        "Electrical installation cert",
    ),
    "PAT": (
        ["portable appliance", "pat"],
        ["city & guilds", "iet"],
        ["appliance", "pass", "test"],
        "PAT register/self-held",
    ),
    "BOILER_SVC": (
        ["boiler", "service"],
        ["gas safe"],
        ["appliance", "service", "date"],
        "Boiler service record",
    ),
    "LEGIONELLA_RA": (
        ["legionella", "risk assessment", "acop l8"],
        ["lca"],
        ["assessor", "scheme", "review"],
        "Legionella RA",
    ),
    "LEGIONELLA_LOG": (
        ["legionella", "monitoring", "log"],
        [],
        ["temperature", "outlet", "date"],
        "Legionella monitoring log",
    ),
    "CWST_INSPECTION": (
        ["cold water", "storage tank", "cwst"],
        ["lca"],
        ["inspection", "tank", "hygiene"],
        "CWST inspection",
    ),
    "WSE_PRESSURE": (
        ["written scheme", "pressure"],
        ["allianz", "lloyd", "bureau veritas"],
        ["examination", "vessel", "competent"],
        "WSE / pressure systems",
    ),
    "LOLER_LIFT": (
        ["loler", "thorough examination", "lift"],
        ["allianz", "bureau veritas", "lloyd"],
        ["examination", "lift", "report"],
        "LOLER lift exam",
    ),
    "ASBESTOS_SURVEY": (
        ["asbestos", "survey", "hsg264"],
        ["ukas", "p402"],
        ["survey", "asbestos", "premises"],
        "Asbestos management survey",
    ),
    "ASBESTOS_REGISTER": (
        ["asbestos register"],
        [],
        ["location", "asbestos", "register"],
        "Asbestos living register",
    ),
    "ESOS": (
        ["esos", "energy savings"],
        ["environment agency", "cibse", "companies house"],
        ["assessment", "phase", "notification"],
        "ESOS evidence",
    ),
    "FGAS_LEAK": (
        ["f-gas", "leak check", "fluorinated"],
        ["refcom", "acrib"],
        ["charge", "leak", "certificate"],
        "F-Gas leak check",
    ),
    "HS_POLICY": (
        ["health and safety policy", "h&s policy"],
        [],
        ["policy", "statement", "signed"],
        "H&S policy statement",
    ),
    "RISK_REGISTER": (
        ["risk assessment", "risk register"],
        [],
        ["hazard", "control", "risk"],
        "General risk register",
    ),
    "HRB_REGISTRATION": (
        ["higher-risk building", "hrb", "building safety"],
        ["building safety regulator", "hse"],
        ["registration", "building", "accountable"],
        "HRB registration",
    ),
    "SAFETY_CASE": (
        ["safety case", "building safety"],
        ["building safety regulator", "hse"],
        ["safety case", "report", "building"],
        "Building safety case",
    ),
    "ACS_GAS_CARD": (
        ["acs", "competency", "gas"],
        ["gas safe", "eusr", "cata"],
        ["engineer", "card", "expiry"],
        "ACS gas card",
    ),
    "NAPIT_REG": (
        ["napit"],
        ["napit"],
        ["membership", "registration", "electrician"],
        "NAPIT registration",
    ),
    "CG_2382": (
        ["2382", "18th edition", "bs 7671"],
        ["city & guilds", "eal"],
        ["qualification", "certificate", "candidate"],
        "C&G 2382",
    ),
    "BAFE_SP203": (
        ["bafe", "sp203"],
        ["bafe"],
        ["registration", "company", "fire"],
        "BAFE SP203-1",
    ),
    "NSI_GOLD_FIRE": (
        ["nsi", "gold"],
        ["nsi"],
        ["approved", "company", "fire"],
        "NSI Gold fire",
    ),
    "BAFE_SP101": (
        ["bafe", "sp101", "gaseous"],
        ["bafe", "lps"],
        ["registration", "suppression"],
        "BAFE SP101",
    ),
    "LEIA_MEMBER": (
        ["leia"],
        ["leia"],
        ["member", "membership", "lift"],
        "LEIA membership",
    ),
    "LOLER_CP": (
        ["loler", "competent person"],
        ["allianz", "bureau veritas", "hse"],
        ["competent", "inspection", "body"],
        "LOLER competent person",
    ),
    "ASBESTOS_LICENCE": (
        ["asbestos", "licence", "licensed"],
        ["hse"],
        ["licence number", "expiry", "contractor"],
        "HSE asbestos licence",
    ),
    "UKAS_ASBESTOS": (
        ["ukas", "accreditation", "asbestos"],
        ["ukas"],
        ["accreditation", "number", "organisation"],
        "UKAS asbestos",
    ),
    "BOHS_P40x": (
        ["p402", "p403", "p404", "bohs"],
        ["bohs", "rsph"],
        ["competency", "certificate", "candidate"],
        "BOHS P40x",
    ),
    "LCA_REG": (
        ["lca", "legionella control"],
        ["lca"],
        ["member", "registration", "company"],
        "LCA registration",
    ),
    "LEGIONELLA_COMP": (
        ["legionella", "competency"],
        ["bohs", "wmsoc", "btec"],
        ["certificate", "qualification"],
        "Legionella competency",
    ),
    "REFCOM_COMPANY": (
        ["refcom", "f-gas"],
        ["refcom"],
        ["company", "certificate", "registration"],
        "REFCOM company",
    ),
    "CG_2079": (
        ["2079", "f-gas", "technician"],
        ["city & guilds", "acrib", "refcom"],
        ["technician", "certificate", "qualification"],
        "C&G 2079",
    ),
    "CHAS_SSIP": (
        ["chas", "ssip"],
        ["chas", "ssip", "constructionline"],
        ["member", "accreditation", "expiry"],
        "CHAS/SSIP",
    ),
    "ISO_9001": (
        ["iso 9001", "quality"],
        ["ukas"],
        ["certificate", "accredited", "expiry"],
        "ISO 9001",
    ),
    "ISO_14001": (
        ["iso 14001", "environmental"],
        ["ukas"],
        ["certificate", "accredited", "expiry"],
        "ISO 14001",
    ),
    "SIA_ACS": (
        ["sia", "approved contractor", "acs"],
        ["sia"],
        ["acs", "company", "approval"],
        "SIA ACS",
    ),
    "SIA_LICENCE": (
        ["sia", "licence", "license"],
        ["sia"],
        ["licence number", "expiry", "licence"],
        "SIA individual licence",
    ),
    "NSI_GOLD_SEC": (
        ["nsi", "nacoss", "security"],
        ["nsi"],
        ["gold", "approved", "security"],
        "NSI Gold security",
    ),
    "BPCA_MEMBER": (
        ["bpca", "pest"],
        ["bpca"],
        ["membership", "member", "number"],
        "BPCA membership",
    ),
    "PESTICIDE_PAx": (
        ["pa1", "pa2", "pa6", "pesticide"],
        ["basis", "lantra", "city & guilds"],
        ["certificate", "qualification", "expiry"],
        "Pesticide PAx",
    ),
}

# Do not overwrite already-specialized high-volume files
SKIP_IF_EXISTS = {
    "EPC",
    "DEC",
    "TM44",
    "GAS_CP17",
    "GASSAFE_COMPANY",
    "EICR",
    "NICEIC_CONTRACTOR",
    "EL_INSURANCE",
    "CONTRACTOR_EL",
    "CONTRACTOR_PL",
    "_default",
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    n = 0
    for code, (tok, body, hints, notes) in SPECS.items():
        path = ROOT / f"{code}.yaml"
        if path.exists() and code in SKIP_IF_EXISTS:
            continue
        lines = ["required_tokens:"]
        lines.extend(f"  - {t}" for t in tok)
        lines.append("issuing_body_tokens:")
        lines.extend(f"  - {t}" for t in body)
        lines.append("required_field_hints:")
        lines.extend(f"  - {t}" for t in hints)
        lines.append(f"notes: {notes}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        n += 1
    total = sorted(p.stem for p in ROOT.glob("*.yaml"))
    print(f"wrote={n} total={len(total)}")
    print(",".join(total))


if __name__ == "__main__":
    main()
