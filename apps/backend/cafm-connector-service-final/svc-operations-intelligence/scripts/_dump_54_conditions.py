import json
from pathlib import Path

p = Path(
    r"C:/CMMS/new/FINAL-PLENUM-CAFM-tier2/apps/backend/cafm-connector-service-final"
    r"/svc-operations-intelligence/src/reference/uk_compliance_pack_v1_1.json"
)
data = json.loads(p.read_text(encoding="utf-8"))
types = data.get("certificate_types") or []
out = Path(
    r"C:/CMMS/new/FINAL-PLENUM-CAFM-tier2/apps/backend/cafm-connector-service-final"
    r"/svc-operations-intelligence/src/reference/uk_pack_54_document_conditions.md"
)

lines: list[str] = []
lines.append("# UK Compliance Pack v1.1 — 54 document generation conditions")
lines.append("")
lines.append(f"Total types: **{len(types)}**")
b = sum(1 for t in types if t.get("certificate_scope") == "Building")
v = sum(1 for t in types if t.get("certificate_scope") == "Vendor")
lines.append(f"- Building: **{b}**")
lines.append(f"- Vendor: **{v}**")
lines.append("")
lines.append("## How Single Door routes a file to Feature A")
lines.append("")
lines.append("A document triggers Compliance Engine when **any** of these match:")
lines.append("")
lines.append("1. **Filename** contains type tokens (e.g. `FRA`, `Fire_Risk`, `EICR`, `LOLER`, `Gas_Safe`)")
lines.append("2. **Chat message** contains compliance keywords (`certificate`, `compliance`, `fire risk`, …)")
lines.append("3. **Document body text** contains those tokens (Word/PDF is peeked)")
lines.append("")
lines.append("Platform always expects these canonical fields for extract/upsert:")
lines.append("`certificate_number`, `issue_date`, `expiry_date` (Review date → expiry for FRA/L8).")
lines.append("")
lines.append("Suggested filename pattern:")
lines.append("`NN_<TypeCode_or_Name>_<SiteOrVendor>.docx`")
lines.append("Example: `01_Fire_Risk_Assessment_FRA_Kingsgate.docx`")
lines.append("")

for i, t in enumerate(types, 1):
    schema = t.get("key_fields_schema") or {}
    req = schema.get("required") or []
    opt = schema.get("optional") or []
    lines.append("---")
    lines.append("")
    lines.append(
        f"### {i}. `{t.get('certificate_type_code')}` — {t.get('certificate_type_name')} "
        f"({t.get('certificate_scope')})"
    )
    lines.append("")
    lines.append(f"- **Trade:** {t.get('trade_category') or '—'}")
    lines.append(f"- **Regulation:** {t.get('regulation_reference') or '—'}")
    lines.append(f"- **Frequency:** {t.get('frequency_text') or t.get('frequency_months') or '—'}")
    lines.append(f"- **Issuing body:** {t.get('issuing_body') or '—'}")
    lines.append(
        f"- **Required contractor accreditation:** "
        f"{t.get('required_contractor_accreditation') or '—'}"
    )
    lines.append(f"- **Verification URL:** {t.get('verification_url') or '—'}")
    lines.append("")
    lines.append("**Required fields (extract must populate):**")
    for f in req:
        lines.append(f"- `{f}`")
    if not req:
        lines.append("- _(none listed)_")
    lines.append("")
    lines.append("**Optional / schedule fields (include in Word table when possible):**")
    for f in opt:
        lines.append(f"- `{f}`")
    if not opt:
        lines.append("- _(none listed)_")
    lines.append("")
    src = schema.get("source_key_fields") or ""
    if src:
        lines.append(f"**Word pack source columns:** {src}")
        lines.append("")
    # Filename hints
    code = str(t.get("certificate_type_code") or "")
    name = str(t.get("certificate_type_name") or "")
    hint = code.replace("_", " ")
    lines.append(
        f"**Suggested filename:** `{i:02d}_{code}_{{SiteOrVendor}}.docx` "
        f"(or include `{hint}` / `{name.split('(')[0].strip()}` in the name)"
    )
    lines.append("")

out.write_text("\n".join(lines), encoding="utf-8")

json_out = Path(
    r"C:/CMMS/new/FINAL-PLENUM-CAFM-tier2/apps/backend/cafm-connector-service-final"
    r"/svc-operations-intelligence/src/reference/uk_pack_54_document_conditions.json"
)
rows = []
for i, t in enumerate(types, 1):
    schema = t.get("key_fields_schema") or {}
    rows.append(
        {
            "n": i,
            "scope": t.get("certificate_scope"),
            "code": t.get("certificate_type_code"),
            "name": t.get("certificate_type_name"),
            "trade": t.get("trade_category"),
            "required": schema.get("required") or [],
            "optional": schema.get("optional") or [],
            "source_key_fields": schema.get("source_key_fields") or "",
            "frequency": t.get("frequency_text") or t.get("frequency_months"),
            "regulation": t.get("regulation_reference") or "",
            "issuing_body": t.get("issuing_body") or "",
            "required_contractor_accreditation": t.get(
                "required_contractor_accreditation"
            )
            or "",
            "suggested_filename": f"{i:02d}_{t.get('certificate_type_code')}_SiteOrVendor.docx",
        }
    )
json_out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(f"Wrote {out}")
print(f"Wrote {json_out}")
print(f"types={len(types)} building={b} vendor={v}")
for r in rows:
    req = ",".join(r["required"])
    print(f"{r['n']:02d} [{r['scope']}] {r['code']} | req={req}")
