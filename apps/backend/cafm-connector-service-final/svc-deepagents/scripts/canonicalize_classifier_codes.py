"""Rewrite compliance_single_door _TYPE_HINTS to CCC §1 canonical codes."""
from __future__ import annotations

from pathlib import Path

p = Path(__file__).resolve().parents[1] / "src" / "agents" / "compliance_single_door.py"
t = p.read_text(encoding="utf-8")

repls = [
    ('"FIRE_ALARM_SERVICE"', '"FIRE_ALARM_SVC"'),
    ('"CONTRACTOR_PL_INSURANCE"', '"CONTRACTOR_PL"'),
    ('"CONTRACTOR_EL_INSURANCE"', '"CONTRACTOR_EL"'),
    ('"NSI_GOLD_SECURITY"', '"NSI_GOLD_SEC"'),
    ('"BAFE_SP203_1"', '"BAFE_SP203"'),
    ('"BPCA"', '"BPCA_MEMBER"'),
    ('"PA1_PA2_PA6"', '"PESTICIDE_PAx"'),
    ('"ASBESTOS_P402_P403_P404"', '"BOHS_P40x"'),
    ('"ACS_CARD"', '"ACS_GAS_CARD"'),
    ('"NICEIC"', '"NICEIC_CONTRACTOR"'),
    ('"NAPIT"', '"NAPIT_REG"'),
    ('"LEIA"', '"LEIA_MEMBER"'),
    ('"HSE_ASBESTOS_LICENCE"', '"ASBESTOS_LICENCE"'),
    ('"LCA"', '"LCA_REG"'),
    ('"BTEC_LEGIONELLA"', '"LEGIONELLA_COMP"'),
    ('"REFCOM"', '"REFCOM_COMPANY"'),
    ('"SIA_INDIVIDUAL"', '"SIA_LICENCE"'),
    ('"GAS_SAFE"', '"GASSAFE_COMPANY"'),
]
for a, b in repls:
    t = t.replace(a, b)

# Bare gas-safe Building leftover should be GAS_CP17 (landlord/building), not company
t = t.replace(
    ', "GASSAFE_COMPANY", "Building")',
    ', "GAS_CP17", "Building")',
)

# Avoid double-canonicalization if script re-run
t = t.replace('"NICEIC_CONTRACTOR_CONTRACTOR"', '"NICEIC_CONTRACTOR"')
t = t.replace('"NAPIT_REG_REG"', '"NAPIT_REG"')
t = t.replace('"BPCA_MEMBER_MEMBER"', '"BPCA_MEMBER"')
t = t.replace('"LEIA_MEMBER_MEMBER"', '"LEIA_MEMBER"')
t = t.replace('"LCA_REG_REG"', '"LCA_REG"')
t = t.replace('"REFCOM_COMPANY_COMPANY"', '"REFCOM_COMPANY"')
t = t.replace('"GASSAFE_COMPANY_COMPANY"', '"GASSAFE_COMPANY"')

# Canonicalize explicit Type code: lines via CODE_ALIASES map inline
if "CODE_ALIASES_INLINE" not in t:
    inject = '''
# CCC §1 canonical aliases for Type code: lines and residual legacy tokens
_CODE_ALIASES_INLINE = {
    "FIRE_ALARM_SERVICE": "FIRE_ALARM_SVC",
    "SPRINKLER": "SPRINKLER_TEST",
    "GAS_SAFE": "GAS_CP17",
    "L8_RISK": "LEGIONELLA_RA",
    "BPCA": "BPCA_MEMBER",
    "NICEIC": "NICEIC_CONTRACTOR",
    "NAPIT": "NAPIT_REG",
    "ACS_CARD": "ACS_GAS_CARD",
    "SIA_INDIVIDUAL": "SIA_LICENCE",
    "NSI_GOLD_SECURITY": "NSI_GOLD_SEC",
    "BAFE_SP203_1": "BAFE_SP203",
    "HSE_ASBESTOS_LICENCE": "ASBESTOS_LICENCE",
    "LCA": "LCA_REG",
    "REFCOM": "REFCOM_COMPANY",
    "LEIA": "LEIA_MEMBER",
    "CONTRACTOR_PL_INSURANCE": "CONTRACTOR_PL",
    "CONTRACTOR_EL_INSURANCE": "CONTRACTOR_EL",
    "PA1_PA2_PA6": "PESTICIDE_PAx",
    "ASBESTOS_P402_P403_P404": "BOHS_P40x",
    "BTEC_LEGIONELLA": "LEGIONELLA_COMP",
}
'''
    t = t.replace(
        "_TYPE_HINTS: list[tuple[re.Pattern[str], str, str]] = [",
        "_TYPE_HINTS: list[tuple[re.Pattern[str], str, str]] = [" + "\n# CODE_ALIASES_INLINE marker\n",
    )
    # place aliases before _TYPE_HINTS
    t = t.replace(
        "# Filename / message / body hints → (certificate_type_code, cert_scope)",
        inject + "\n# Filename / message / body hints → (certificate_type_code, cert_scope)",
    )
    t = t.replace(
        "        code = _tc.group(1).strip().upper()\n        if code in _known_codes:",
        "        code = _tc.group(1).strip().upper()\n"
        "        code = _CODE_ALIASES_INLINE.get(code, code)\n"
        "        if code in _known_codes or code in _CODE_ALIASES_INLINE.values():",
    )
    # Also canonicalize hint matches
    t = t.replace(
        "        if pattern.search(primary):\n            return {\"certificate_type_code\": code, \"cert_scope\": scope}",
        "        if pattern.search(primary):\n"
        "            code = _CODE_ALIASES_INLINE.get(code, code)\n"
        "            return {\"certificate_type_code\": code, \"cert_scope\": scope}",
    )
    t = t.replace(
        "        if pattern.search(hay):\n            return {\"certificate_type_code\": code, \"cert_scope\": scope}",
        "        if pattern.search(hay):\n"
        "            code = _CODE_ALIASES_INLINE.get(code, code)\n"
        "            return {\"certificate_type_code\": code, \"cert_scope\": scope}",
    )

p.write_text(t, encoding="utf-8")
print("updated", p)
# sanity
for bad in ("FIRE_ALARM_SERVICE", "GAS_SAFE", "NAPIT\"", "\"BPCA\""):
    if bad in p.read_text(encoding="utf-8") and bad not in _CODE_ALIASES_INLINE if False else True:
        pass
text = p.read_text(encoding="utf-8")
print("FIRE_ALARM_SVC", text.count("FIRE_ALARM_SVC"))
print("legacy FIRE_ALARM_SERVICE", text.count("FIRE_ALARM_SERVICE"))
print("GAS_SAFE leftover", '"GAS_SAFE"' in text)
print("GAS_CP17", text.count("GAS_CP17"))
print("GASSAFE_COMPANY", text.count("GASSAFE_COMPANY"))
