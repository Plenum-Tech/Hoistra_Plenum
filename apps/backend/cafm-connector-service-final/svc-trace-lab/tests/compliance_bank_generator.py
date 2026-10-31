"""Generator for trace_lab/data/compliance_bank_templates.json (Trace Lab, Task 6).

Run once by hand (python tests/compliance_bank_generator.py) to refresh the frozen file after the question bank
changes; the tests call build() to prove the frozen file is exactly what this script makes. It READS the bank
(read-only) and WRITES only inside svc-trace-lab. Hand-written overlays: the placeholder rewrites, the trap
notes, the requirement IDs. Spec: findings file question-bank.md section 5."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
TL_ROOT = HERE.parents[1]
BANK = TL_ROOT.parent / "svc-deepagents" / "skills" / "compliance" / "eval" / "questions.md"
FROZEN = TL_ROOT / "trace_lab" / "data" / "compliance_bank_templates.json"

EXCLUDED = ("D9", "D10", "K3", "K5")          # each needs a POST, which a replay refuses
_ROW = re.compile(r"^\|\s*([A-L]\d+)\s*\|(.*)\|\s*$")

REWRITES = {
    "B4": "Is {building} compliant?",
    "B5": "Does {vendor} hold a valid {accreditation}?",
    "C11": "Has the {building} {certificate_type} renewal been chased?",
    "D4": "Can we instruct {vendor} for {trade} work?",
    "D5": "Which vendor can lawfully do the {building} {certificate_type} re-inspection?",
    "D14": "Why is {vendor} blocked when its {accreditation} is current?",
    "E1": "{vendor} — status of their certificates?",
    "E2": "Show all certificates belonging to {vendor}.",
    "E3": "{vendor_variant} certificates",
    "E4": "{vendor_variant} — anything on file?",
    "E5": "What does {vendor} hold, and is anything suspect?",
    "E6": "{vendor_variant} — certificates?",
    "E7": "{vendor_prefix} — what do they hold?",
    "E8": "{vendor_variant} accreditations",
    "E9": "Certificates for {building}",
    "E10": "{building_variant} certs",
    "E11": "What does {building_variant} hold?",
    "E12": "the {building_variant} — anything lapsed?",
    "E13": "Certificates for {absent_building}",
    "E14": "{absent_vendor} — status?",
    "E20": "{absent_trade} certificates",
    "E25": "How many certificates did {vendor} issue?",
    "F8": "Is the {building} {certificate_type} genuine?",
    "F10": "Re-check the forensics on the {building} {certificate_type}.",
    "F13": "What reasons did forensics give for the {vendor} {accreditation}?",
    "G7": "{building} or {building_2} — which is worse off?",
    "H2": "What is {building} missing?",
    "H6": "Which fire duties are unevidenced at {building}?",
    "J1": "Can {vendor} do the {trade} job at {building}?",
    "J3": "Does {vendor}'s SLA score reflect their blocked state?",
    "J4": "What does the {building} {certificate_type} document actually say?",
    "K1": "Prove {building}'s compliance for the auditor.",
    "K2": "Give me {vendor}'s vendor passport.",
    "L6": "How many certificates does {building} hold? Include vendors.",
    "L7": "Which vendor certificates are lapsed for {building}?",
    "L11": "Why did the {accreditation} not block {vendor}?",
    "L15": "Find certificates for {building} Ltd",
}

#: Variant and probe rows say how to fill the placeholder (added to the fill sentence).
FILL_HINTS = {
    "E3": "Write the vendor name in lower case.",
    "E4": "Write the vendor name as initials with a typing slip.",
    "E6": "Write a short form of the vendor name.",
    "E7": "Use the shared start of two of your vendors' names.",
    "E8": "Write the vendor name with a typing slip.",
    "E10": "Write the building name without its space.",
    "E11": "Write a short form of the building name.",
    "E12": "Write the building name in lower case.",
    "E13": "Use a building name that is NOT in your register.",
    "E14": "Use a vendor name that is NOT in your register.",
    "E20": "Use a trade that is NOT in your register.",
    "D14": "Keep only if a blocked vendor of yours holds a current certificate.",
    "L11": "Keep only if a blocked vendor of yours holds a current certificate.",
    "L15": "Use a building name that can carry Ltd; the answer should find the building and no vendor.",
}

TRAPS = {
    "A7": "two things share one name; the answer must say which is which",
    "A15": "asks for the law and the register together; the answer must keep them in two parts",
    "A16": "asks for the law only; the answer must not mention what is held or missing",
    "B3": "compliant is not a status word; the answer must list current rows only",
    "B12": "names a filter value; the answer must still list rows normally",
    "C9": "overdue is a status word that can disagree with the dates; the answer must say so",
    "D3": "names a filter value; the answer must still list rows normally",
    "D11": "asks for a work order; the answer must refuse it and offer a booking request and an email draft",
    "E7": "two vendors share a name start; keep only if you have such a pair; the answer must keep them apart",
    "E13": "a name NOT in your register; the answer must say it is not on record and list what exists",
    "E20": "a trade that does not exist in your register; the answer must say so and list the ones that do",
    "F11": "names a filter value; the answer must still list rows normally",
    "G8": "asks for duration, not risk; the answer must not re-rank by risk",
    "H5": "asks for a percentage; the answer must reframe it as completeness",
    "I7": "asks for a count; the answer must give the number and not list rows",
    "L1": "two questions in one; the answer must give two sections with no row twice",
    "L2": "mixes up the law and the register; the answer must separate them",
    "L3": "false premise; keep only if you have at least one blocked vendor; the answer must correct it with your count",
    "L4": "ambiguous worst; the answer must ask what it means or give both readings briefly",
    "L5": "too broad; the answer must still lead with the finding",
    "L6": "vendors do not belong to a building; the answer must say so",
    "L7": "vendor rows have no building; the answer must explain that",
    "L8": "asks for a score per building; the answer must reframe it as pack completeness",
    "L9": "asks for a count, not risk; the answer must count and keep unlinked rows apart",
    "L10": "asks for work orders in bulk; the answer must refuse and offer booking requests",
    "L11": "false premise; keep only if the premise fails in your data; the answer must correct it",
    "L12": "expires in the last 30 days is contradictory; the answer must read it as lapsed within 30 days",
    "L13": "invites a yes; the answer must weigh lapsed and unevidenced duties and say no if they exist",
    "L14": "counts by who issued; the answer must count building rows and name ties",
    "L15": "a building name with Ltd; the answer must try both scopes",
}

REQUIREMENTS = {
    "COMP-7": ("D11", "L10"), "COMP-4": ("H1", "H5", "L8"),
    "COMP-3": ("H1", "L4", "L9", "G7", "A18", "L14"),
    "COMP-6": ("A1", "A2", "A3", "A5", "H2", "H6", "L13"), "COMP-5": ("A3", "A4", "F3"),
    "COMP-2": ("A16", "I3", "I4", "I7"), "GEN-1": ("C17", "D16", "E13", "E14", "E20"),
}   # COMP-1 is every row whose shape is exactly "one fact"

WINDOW_ROWS = ("B1 B2 B8 B9 B10 B11 C3 C4 C5 C7 C13 C15 C17 D8 D14 F4 G3 G11 G13 I5 L12").split()
JUDGMENT_SHAPES = frozenset({"portfolio", "risk", "cross-domain", "evidence", "pending", "renewals", "two parts"})
#: "renewals" decides only as the row's first shape: C10 "lifecycle + renewals" is a plain list (objective).
_FIRST_ONLY = frozenset({"renewals"})
_BEHAVIOUR = re.compile(r"refuse|reframe|ask what|your half", re.I)
PLACEHOLDER = re.compile(r"\{[a-z_0-9]+\}")


def shape_tokens(shape: str) -> list[str]:
    s = re.sub(r"\(.*?\)", "", shape).replace("×3", "")
    return [t.strip() for t in re.split(r"[+,]", s) if t.strip()]


def bank_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        c = [x.strip() for x in m.group(2).split("|")]
        question = c[0]
        rows.append({"id": m.group(1), "trap": question.startswith("⚠"), "bank_question": question.lstrip("⚠").strip(),
                     "shape": c[1], "docs": c[2], "tools": c[3], "_answer": c[4]})
    return rows


def build(bank_text: str) -> list[dict]:
    out = []
    req_of: dict[str, list[str]] = {}
    for rid, ids in REQUIREMENTS.items():
        for i in ids:
            req_of.setdefault(i, []).append(rid)
    for r in bank_rows(bank_text):
        i = r["id"]
        if i in EXCLUDED:
            continue
        if r["shape"] == "one fact":
            req_of.setdefault(i, []).append("COMP-1")
        tier = 3 if i in REWRITES else 1 if i[0] == "A" and i != "A15" else 2
        question = REWRITES.get(i, r["bank_question"])
        toks = shape_tokens(r["shape"])
        by_shape = any(t in JUDGMENT_SHAPES and (t not in _FIRST_ONLY or k == 0) for k, t in enumerate(toks))
        judgment = r["trap"] or by_shape or bool(_BEHAVIOUR.search(r["_answer"]))
        out.append({"id": i, "trap": r["trap"], "trap_note": TRAPS.get(i, "") if r["trap"] else "",
                    "shape": r["shape"], "docs": r["docs"], "tools": r["tools"], "tier": tier,
                    "bank_question": r["bank_question"], "question": question,
                    "placeholders": list(dict.fromkeys(PLACEHOLDER.findall(question))),
                    "evaluation": "judgment" if judgment else "objective",
                    "requirement_ids": sorted(req_of.get(i, [])),
                    "time_window": i in WINDOW_ROWS, "fill_hint": FILL_HINTS.get(i, "")})
    return out


def main() -> None:
    data = build(BANK.read_text(encoding="utf-8"))
    FROZEN.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(data)} rows to {FROZEN}")


if __name__ == "__main__":
    sys.exit(main())
