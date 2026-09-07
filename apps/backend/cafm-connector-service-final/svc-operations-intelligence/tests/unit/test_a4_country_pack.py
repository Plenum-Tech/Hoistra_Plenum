"""
A4 — Country Certificate Pack architecture tests.

Covers: UK pack JSON structure, building/vendor scopes, trade categories,
verification registers, key_fields_schema, pack versioning metadata.
"""
from __future__ import annotations

from src.engines.compliance.country_pack import load_pack_json, pack_to_dict


class TestA4UkPackStructure:
    def test_pack_metadata(self):
        pack = load_pack_json()
        assert pack["pack_id"] == "UK-COMPLIANCE-v1.1"
        assert pack["country_code"] == "UK"
        assert pack["pack_version"] == "1.1"
        assert "certificate_types" in pack

    def test_at_least_47_types(self):
        pack = load_pack_json()
        assert len(pack["certificate_types"]) >= 47

    def test_docx_authoritative_counts_54(self):
        """Word pack v1.1 = 54 cards (27 Building + 27 Vendor)."""
        pack = load_pack_json()
        types = pack["certificate_types"]
        building = [t for t in types if t["certificate_scope"] == "Building"]
        vendor = [t for t in types if t["certificate_scope"] == "Vendor"]
        assert len(building) == 27
        assert len(vendor) == 27
        assert len(types) == 54
        counts = pack.get("type_counts") or {}
        assert counts.get("total") == 54
        assert counts.get("building") == 27
        assert counts.get("vendor") == 27

    def test_docx_combined_operative_modules(self):
        """P402/P403/P404 and PA1/PA2/PA6 are single CountryPack cards; modules on ResourceSkill."""
        pack = load_pack_json()
        codes = {t["certificate_type_code"] for t in pack["certificate_types"]}
        assert "ASBESTOS_P402_P403_P404" in codes
        assert "PA1_PA2_PA6" in codes
        assert "CONTRACTOR_EL_INSURANCE" in codes
        assert "CONTRACTOR_PL_INSURANCE" in codes
        assert "EL_PL_INSURANCE" not in codes
        assert "P402" not in codes
        assert "PA1" not in codes

    def test_building_and_vendor_scopes_present(self):
        pack = load_pack_json()
        scopes = {t["certificate_scope"] for t in pack["certificate_types"]}
        assert scopes == {"Building", "Vendor"}

    def test_building_includes_core_statutory_types(self):
        pack = load_pack_json()
        codes = {
            t["certificate_type_code"]
            for t in pack["certificate_types"]
            if t["certificate_scope"] == "Building"
        }
        for required in ("FRA", "EICR", "CP17", "LOLER", "L8_RISK", "FGAS", "EPC"):
            assert required in codes, f"missing building type {required}"

    def test_vendor_includes_core_accreditations(self):
        pack = load_pack_json()
        codes = {
            t["certificate_type_code"]
            for t in pack["certificate_types"]
            if t["certificate_scope"] == "Vendor"
        }
        for required in ("GAS_SAFE", "NICEIC", "BAFE_SP203_1", "REFCOM", "CHAS_SSIP", "SIA_ACS"):
            assert required in codes, f"missing vendor type {required}"

    def test_unique_type_codes_within_pack(self):
        pack = load_pack_json()
        codes = [t["certificate_type_code"] for t in pack["certificate_types"]]
        assert len(codes) == len(set(codes))

    def test_key_fields_schema_on_every_type(self):
        pack = load_pack_json()
        for t in pack["certificate_types"]:
            schema = t.get("key_fields_schema") or {}
            assert "required" in schema
            assert "certificate_number" in schema["required"]
            assert "expiry_date" in schema["required"]


class TestA4VerificationRegisters:
    def test_thirteen_uk_registers_documented(self):
        pack = load_pack_json()
        regs = pack.get("verification_registers") or {}
        # PRD lists 13 UK registers
        assert len(regs) >= 13
        for name in (
            "Gas Safe",
            "NICEIC",
            "NAPIT",
            "BAFE",
            "NSI",
            "HSE Asbestos",
            "UKAS",
            "LCA",
            "REFCOM",
            "SSIP",
            "SIA ACS",
            "SIA licence",
            "BPCA",
        ):
            assert name in regs
            assert regs[name].startswith("http")

    def test_types_with_verification_url_are_https(self):
        pack = load_pack_json()
        for t in pack["certificate_types"]:
            url = t.get("verification_url")
            if url:
                assert url.startswith("https://"), t["certificate_type_code"]


class TestA4PackToDict:
    def test_pack_to_dict_shape(self, mock_pack_row):
        d = pack_to_dict(mock_pack_row)
        assert d["certificate_type_code"] == "GAS_SAFE"
        assert d["country_code"] == "UK"
        assert d["verification_url"].startswith("https://")
        assert "id" in d
