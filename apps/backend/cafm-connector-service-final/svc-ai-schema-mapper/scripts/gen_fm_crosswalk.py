"""Generator: docs/FM_Ontology_Reference_v1.0.docx + plenum_cafm schema
-> src/matchers/fm_crosswalk.py.

The COMPLETE crosswalk: every per-platform COLUMN alias in the FM Ontology Reference
mapped to the REAL plenum_cafm column on the entity's destination table. Where a docx
field name differs from the real column it's translated (TRANSLATE); otherwise an exact
normalized name match is used. Primary-key rows are skipped (table-relative identity is
handled by the deterministic identity strategy). Within-table collisions are dropped so
an alias never maps ambiguously.

  FM_COLUMN_ALIASES_BY_TABLE : {real_table: {platform_alias_norm: real_column}}

Run:  python scripts/gen_fm_crosswalk.py
"""
import os
import re
import sys

import docx

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "..", "src", "matchers", "fm_crosswalk.py")
sys.path.insert(0, os.path.join(_HERE, "..", "src", "matchers"))
import plenum_cafm_schema as SCHEMA  # noqa: E402


def _find_docx() -> str:
    d = _HERE
    for _ in range(9):
        cand = os.path.join(d, "docs", "FM_Ontology_Reference_v1.0.docx")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    raise FileNotFoundError("docs/FM_Ontology_Reference_v1.0.docx not found")


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


STOP = {
    "id", "name", "code", "description", "status", "type", "category", "priority",
    "reference", "notes", "date", "value", "number", "no", "title", "summary", "na",
    "n_a", "result", "address", "city", "country", "email", "phone", "mobile", "level",
    "version", "author", "currency", "unit", "trade", "severity",
    "maximo", "sap", "planon", "archibus", "eptura", "tririga", "cobie", "brick",
    "facilio", "accruent", "famis", "maintainx", "ibm", "class", "order", "use", "scope",
}

ENTITY_CANON = {
    "site": "site", "building": "building", "space": "space", "asset": "asset",
    "assettype": "asset_type", "workorder": "work_order", "resource": "resource",
    "vendor": "vendor", "contract": "contract",
    "compliancecertificate": "compliance_certificate", "ppmschedule": "ppm_schedule",
    "meter": "meter", "document": "document",
}

# FM entity -> destination plenum_cafm table(s).
ENTITY_TABLES = {
    "site": ["sites"], "building": ["locations"], "space": ["locations"],
    "asset": ["assets"], "asset_type": ["asset_categories"], "work_order": ["work_orders"],
    "resource": ["technicians"], "vendor": ["vendors"], "contract": ["vendor_contracts"],
    "compliance_certificate": ["certificates"], "ppm_schedule": ["maintenance_plans", "ppm_schedules"],
    "document": ["files"], "meter": [],
}

# (entity, docx_field) -> real column, ONLY where the names differ. Exact-name matches
# against the real table need no entry. None = explicitly skip (no real target).
TRANSLATE = {
    ("site", "gross_area_sqm"): "gfa_sqm", ("site", "created_date"): "created_at",
    ("building", "building_name"): "name", ("building", "building_type"): "type",
    ("building", "created_date"): "created_at",
    ("space", "space_name"): "name", ("space", "space_type"): "type",
    ("space", "parent_space_id"): "parent_location_id", ("space", "level_number"): "level",
    ("space", "created_date"): "created_at",
    ("asset", "asset_tag"): "barcode", ("asset", "description"): "notes",
    ("asset", "install_date"): "installation_date",
    ("asset", "warranty_expiry_date"): "warranty_expiry",
    ("asset", "created_date"): "created_at", ("asset", "modified_date"): "updated_at",
    ("asset", "space_id"): "location_id", ("asset", "asset_type_id"): "asset_type",
    ("asset_type", "asset_type_name"): "category_name", ("asset_type", "asset_type_code"): None,
    ("work_order", "workorder_ref"): "work_order_id",
    ("work_order", "fault_description"): "description",
    ("work_order", "resolution_notes"): "solution",
    ("work_order", "raised_date"): "created_at",
    ("work_order", "required_by_date"): "scheduled_date",
    ("work_order", "completion_date"): "completed_at",
    ("work_order", "labour_hours"): "actual_hours",
    ("work_order", "parts_cost"): "cost_parts_aed",
    ("work_order", "vendor_id"): "assigned_vendor",
    ("work_order", "resource_id"): "assigned_technician",
    ("work_order", "space_id"): "location_id",
    ("work_order", "created_date"): "created_at", ("work_order", "modified_date"): "updated_at",
    ("resource", "status"): "availability_status", ("resource", "created_date"): "created_at",
    ("vendor", "address_line_1"): "address", ("vendor", "postcode"): "postal_code",
    ("vendor", "primary_contact_email"): "email", ("vendor", "primary_contact_phone"): "phone",
    ("vendor", "created_date"): "created_at",
    ("contract", "start_date"): "contract_start", ("contract", "end_date"): "contract_end",
    ("contract", "annual_value"): "contract_value", ("contract", "created_date"): "created_at",
    ("compliance_certificate", "issue_date"): "uploaded_at",
    ("ppm_schedule", "schedule_name"): "description", ("ppm_schedule", "created_date"): "created_at",
    ("document", "document_name"): "name", ("document", "file_type"): "mime_type",
    ("document", "related_entity_type"): "entity_type",
    ("document", "related_entity_id"): "entity_id",
    ("document", "storage_path"): "blob_url", ("document", "created_date"): "uploaded_at",
}


def clean_tokens(cell: str):
    out = []
    if not cell:
        return out
    cell = re.sub(r"\([^)]*\)", " ", cell)
    for piece in re.split(r"[,/]", cell):
        piece = piece.split("+")[0].strip(" .—-")
        if not piece or " " in piece:
            continue
        low = piece.lower()
        if any(w in low for w in ("not standard", "custom", "derived", "where ", "segment",
                                  "ontology", "native", "domain", "graph", "no standard",
                                  "implied", "sheet", "field", "attribute", "module")):
            continue
        n = _norm(piece)
        if not n or n in STOP or len(n) < 2 or n.isdigit():
            continue
        out.append(n)
    return out


def entity_from_header(text: str):
    m = re.search(r"\b(E\d+)\b", text)  # core entities only (E1..E13 carry aliases)
    if not m:
        return None
    tail = re.sub(r"—\s*Core", "", text[m.end():]).strip()
    return ENTITY_CANON.get(_norm(tail).replace("_", ""))


def resolve_column(entity, docx_field, table):
    key = (entity, docx_field)
    if key in TRANSLATE:
        return TRANSLATE[key]  # may be None (explicit skip)
    real = {_norm(c): c for c in SCHEMA.TABLES.get(table, [])}
    return real.get(docx_field)  # exact normalized name match, else None


def main():
    d = docx.Document(_find_docx())
    by_table = {}            # table -> {alias: real_col}
    conflicts = {}           # table -> {alias dropped due to collision}
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
        if head[0].lower().startswith("column name") and cur_entity:
            for dest in ENTITY_TABLES.get(cur_entity, []):
                proposed = by_table.setdefault(dest, {})
                seen_conflict = conflicts.setdefault(dest, set())
                for r in rows[1:]:
                    if len(r) < 6 or r[2].strip().upper() == "PK":  # skip PK rows
                        continue
                    fld = _norm(r[0])
                    col = resolve_column(cur_entity, fld, dest)
                    if not col or _norm(col) not in {_norm(c) for c in SCHEMA.TABLES.get(dest, [])}:
                        continue
                    real_col = next(c for c in SCHEMA.TABLES[dest] if _norm(c) == _norm(col))
                    tokens = set(clean_tokens(r[5]))
                    tokens.add(fld)  # the docx field name itself
                    for tok in tokens:
                        if tok in seen_conflict:
                            continue
                        if tok in {_norm(c) for c in SCHEMA.TABLES[dest]}:
                            continue  # never shadow a real column (exact match owns it)
                        if tok in proposed and proposed[tok] != real_col:
                            del proposed[tok]      # ambiguous -> drop
                            seen_conflict.add(tok)
                        else:
                            proposed[tok] = real_col
            continue

    by_table = {t: dict(sorted(m.items())) for t, m in by_table.items() if m}

    with open(OUT, "w", encoding="utf-8") as f:
        f.write('"""AUTO-GENERATED from docs/FM_Ontology_Reference_v1.0.docx + plenum_cafm schema.\n\n')
        f.write("Complete FM crosswalk: per-platform CMMS/CAFM/EAM column aliases mapped to the\n")
        f.write("REAL plenum_cafm column on each entity's destination table. Consumed by the\n")
        f.write("deterministic mapper's table-scoped alias strategy. Do not edit by hand.\n")
        f.write("Regenerate with scripts/gen_fm_crosswalk.py.\n\"\"\"\n\n")
        f.write("# real_table -> {platform alias (normalized) -> real column}\n")
        f.write("FM_COLUMN_ALIASES_BY_TABLE = {\n")
        for t in sorted(by_table):
            f.write(f"    {t!r}: {by_table[t]!r},\n")
        f.write("}\n\n")
        # canonical FM entity -> primary real plenum_cafm table (for table routing)
        ent_to_table = {e: tabs[0] for e, tabs in ENTITY_TABLES.items()
                        if tabs and tabs[0] in SCHEMA.TABLES}
        f.write("# canonical FM entity -> primary real plenum_cafm table\n")
        f.write("FM_ENTITY_TO_TABLE = {\n")
        for e in sorted(ent_to_table):
            f.write(f"    {e!r}: {ent_to_table[e]!r},\n")
        f.write("}\n")

    print("tables:", len(by_table), " aliases:", sum(len(m) for m in by_table.values()))
    for t in sorted(by_table):
        print(f"  {t}: {len(by_table[t])}")
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
