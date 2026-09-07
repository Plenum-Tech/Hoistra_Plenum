"""Generator: docs/FM_Ontology_Reference_v1.0.docx -> src/matchers/fm_ontology_data.py.

Parses the FM Ontology Reference (13 core + 8 elastic entities, each with per-platform
column + table aliases for IBM Maximo, SAP EAM, Planon, Archibus, TRIRIGA, COBie, BRICK,
Facilio, Accruent, MaintainX) and emits two normalized alias dicts:

  DOCX_FIELD_ALIASES : alias (normalized) -> canonical UDR field
  DOCX_TABLE_ALIASES : source table/sheet name -> canonical FM entity

fm_ontology.py merges these on top of its curated core (curated entries win), so the
deterministic alias step resolves the full CMMS/CAFM/EAM vocabulary with no LLM call.

Run:  python scripts/gen_fm_ontology_data.py
Requires: python-docx (pip install python-docx).
"""
import os
import re

import docx

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_docx() -> str:
    d = _HERE
    for _ in range(9):
        cand = os.path.join(d, "docs", "FM_Ontology_Reference_v1.0.docx")
        if os.path.exists(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise FileNotFoundError("docs/FM_Ontology_Reference_v1.0.docx not found")


DOCX = _find_docx()
OUT = os.path.join(_HERE, "..", "src", "matchers", "fm_ontology_data.py")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


# Generic tokens that must NEVER become an alias (would mis-map everything), plus the
# platform names themselves and a few words that are ambiguous across entities.
STOP = {
    "id", "name", "code", "description", "status", "type", "category", "priority",
    "reference", "notes", "date", "value", "number", "no", "title", "summary", "na",
    "n_a", "result", "address", "city", "country", "email", "phone", "mobile", "level",
    "version", "author", "currency", "unit", "trade", "severity", "make", "model",
    "serial", "manufacturer",
    "maximo", "sap", "planon", "archibus", "eptura", "tririga", "cobie", "brick",
    "facilio", "accruent", "famis", "maintainx", "ibm",
    "class", "order", "use", "owner", "scope", "height", "capacity",
}

# docx canonical column name -> codebase canonical field (only fields with a real target
# in matchers.cmms_aliases.CANONICAL_FIELDS).
FIELD_MAP = {
    # asset_id (PK) and asset_tag (barcode) are handled by the curated 'id' / 'barcode'
    # clusters in fm_ontology.py; the docx asset_code row owns the operational code.
    "asset_code": "asset_code",
    "asset_name": "asset_name",
    "manufacturer": "make",
    "model_number": "model",
    "serial_number": "serial",
    "asset_type_id": "asset_type", "asset_type_code": "asset_type", "asset_type_name": "asset_type",
    "uniclass_code": "asset_type", "omniclass_code": "asset_type", "brick_class": "asset_type",
    "category_level_1": "category", "category_level_2": "category", "category_level_3": "category",
    "workorder_id": "wo_code", "workorder_ref": "wo_code",
    "workorder_type": "wo_type",
    "title": "wo_description", "fault_description": "wo_description",
    "raised_date": "created_date",
    "required_by_date": "due_date",
    "completion_date": "last_completion_date",
    "ppm_schedule_id": "sm_code",
    "frequency_type": "trigger_type",
    "frequency_value": "schedule_interval", "frequency_unit": "schedule_interval",
    "last_completed_date": "last_completion_date",
    "first_name": "user_full_name", "last_name": "user_full_name",
    "inspector_name": "inspector_name",
    "issue_date": "inspection_date",
    "part_code": "part_code", "part_number": "part_code",
    "manufacturer_part_number": "part_code",
    "part_name": "part_description", "part_description": "part_description",
    "unit_of_measure": "unit_of_measure",
    "reorder_level": "minimum_allowed_stock",
    "stock_quantity": "stock_on_hand",
    "supplier": "supplier",
    "incident_type": "finding_type",
}
# (entity, docx_field) -> canonical — fields that mean different things per entity.
FIELD_MAP_BY_ENTITY = {
    ("asset", "status"): "asset_status",
    ("work_order", "status"): "wo_status",
    ("work_order", "priority"): "wo_priority",
    ("work_order", "resource_id"): "assigned_to",
    ("work_order", "vendor_id"): "supplier",
    ("ppm_schedule", "vendor_id"): "supplier",
    ("incident", "severity"): "risk_level",
    ("vendor", "vendor_name"): "supplier",
    ("vendor", "vendor_code"): "supplier",
    ("vendor", "vendor_id"): "supplier",
}

ENTITY_CANON = {
    "site": "site", "building": "building", "space": "space", "asset": "asset",
    "assettype": "asset_type", "workorder": "work_order", "resource": "resource",
    "vendor": "vendor", "contract": "contract",
    "compliancecertificate": "compliance_certificate", "ppmschedule": "ppm_schedule",
    "meter": "meter", "document": "document", "workorderlabour": "work_order_labour",
    "workorderpart": "work_order_part", "sparepart": "spare_part",
    "meterreading": "meter_reading", "resourceskill": "resource_skill",
    "incident": "incident", "tenant": "tenant", "servicechargebudget": "service_charge_budget",
}


def clean_tokens(cell: str):
    """Pull specific alias codes out of a cell, dropping platform names / prose notes."""
    out = []
    if not cell:
        return out
    cell = re.sub(r"\([^)]*\)", " ", cell)  # strip "(Maximo)" etc. from the WHOLE cell first
    for piece in re.split(r"[,/]", cell):
        piece = piece.split("+")[0].strip(" .—-")
        if not piece:
            continue
        low = piece.lower()
        if any(w in low for w in ("not standard", "custom", "derived", "where ", "segment",
                                  "ontology", "native", "domain", "graph", "no standard",
                                  "implied", "sheet", "field", "attribute", "level", " or ",
                                  "enrichment", "sourced", "module")):
            continue
        if " " in piece:  # codes are single tokens; phrases are annotations
            continue
        n = _norm(piece)
        if not n or n in STOP or len(n) < 2 or n.isdigit():
            continue
        out.append(n)
    return out


def entity_from_header(text: str):
    m = re.search(r"\b(E\d+|EX\d+)\b", text)
    if not m:
        return None
    tail = re.sub(r"—\s*(Core|Elastic)(\s*\([^)]*\))?", "", text[m.end():]).strip()
    return ENTITY_CANON.get(_norm(tail).replace("_", ""))


def main():
    d = docx.Document(DOCX)
    field_aliases, table_aliases = {}, {}
    cur_entity = None
    for tbl in d.tables:
        rows = [[c.text.strip() for c in r.cells] for r in tbl.rows]
        if not rows:
            continue
        head = rows[0]
        if len(tbl.columns) == 1 and len(rows) == 1:
            e = entity_from_header(head[0])
            if e:
                cur_entity = e
            continue
        if head[0].lower().startswith("column name"):
            for r in rows[1:]:
                if len(r) < 6:
                    continue
                fld = _norm(r[0])
                canon = FIELD_MAP_BY_ENTITY.get((cur_entity, fld)) or FIELD_MAP.get(fld)
                if not canon:
                    continue
                for tok in clean_tokens(r[5]):
                    field_aliases.setdefault(tok, canon)
                field_aliases.setdefault(fld, canon)
            continue
        if head[0].lower() == "system" and cur_entity:
            for r in rows[1:]:
                if len(r) >= 2:
                    for tok in clean_tokens(r[1]):
                        table_aliases.setdefault(tok, cur_entity)
            continue
        if head[0].lower().startswith("canonical entity"):
            for r in rows[1:]:
                ent = ENTITY_CANON.get(_norm(r[0]).replace("_", ""))
                if not ent:
                    continue
                for cell in r[1:]:
                    for tok in clean_tokens(cell):
                        table_aliases.setdefault(tok, ent)
            continue

    with open(OUT, "w", encoding="utf-8") as f:
        f.write('"""AUTO-GENERATED from docs/FM_Ontology_Reference_v1.0.docx — do not edit by hand.\n\n')
        f.write("Platform-specific CMMS/CAFM/EAM column + table aliases (IBM Maximo, SAP EAM,\n")
        f.write("Planon, Archibus, TRIRIGA, COBie, BRICK, Facilio, Accruent, MaintainX). Merged\n")
        f.write("into fm_ontology.FM_FIELD_ALIASES / the table index (curated core wins).\n")
        f.write("Regenerate with scripts/gen_fm_ontology_data.py.\n\"\"\"\n\n")
        f.write("# alias (normalized) -> canonical UDR field\n")
        f.write("DOCX_FIELD_ALIASES = {\n")
        for k in sorted(field_aliases):
            f.write(f"    {k!r}: {field_aliases[k]!r},\n")
        f.write("}\n\n")
        f.write("# source table/sheet name (normalized) -> canonical FM entity\n")
        f.write("DOCX_TABLE_ALIASES = {\n")
        for k in sorted(table_aliases):
            f.write(f"    {k!r}: {table_aliases[k]!r},\n")
        f.write("}\n")
    print(f"field aliases: {len(field_aliases)}  table aliases: {len(table_aliases)}")
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
