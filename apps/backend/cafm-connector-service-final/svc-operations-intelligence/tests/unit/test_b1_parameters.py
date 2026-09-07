"""Unit tests — Feature B1 contract parameter extraction + criticality."""
from src.engines.contract_performance.parameters import (
    CRITICALITY_SLA_WEIGHT,
    DEFAULT_LABEL,
    merge_extraction_with_defaults,
    propose_asset_criticality,
)


class TestB1Defaults:
    def test_silent_contract_uses_defaults(self):
        merged, defaults_used, sources = merge_extraction_with_defaults({})
        assert merged["sla_response_p1_hours"] == 1
        assert merged["sla_completion_p3_hours"] == 72
        assert any(DEFAULT_LABEL in d for d in defaults_used)
        assert sources["sla_response_p1_hours"] == "default"
        assert "L1" in merged["task_criticality_json"]

    def test_contract_values_override_defaults(self):
        merged, defaults_used, sources = merge_extraction_with_defaults(
            {"sla_response_p1_hours": 0.5, "labour_day_rate": 400}
        )
        assert merged["sla_response_p1_hours"] == 0.5
        assert sources["sla_response_p1_hours"] == "contract"
        assert merged["labour_day_rate"] == 400
        assert sources["labour_day_rate"] == "contract"
        # Other fields still defaulted
        assert sources["sla_response_p2_hours"] == "default"


class TestB1Criticality:
    def test_life_safety_proposes_l1(self):
        level, _ = propose_asset_criticality(function_type="Fire alarm panel")
        assert level == "L1"

    def test_plant_proposes_l2(self):
        level, _ = propose_asset_criticality(function_type="AHU rooftop")
        assert level == "L2"

    def test_routine_proposes_l3(self):
        level, _ = propose_asset_criticality(function_type="Decoration")
        assert level == "L3"

    def test_l1_weighted_3x_vs_l3(self):
        assert CRITICALITY_SLA_WEIGHT["L1"] / CRITICALITY_SLA_WEIGHT["L3"] == 9.0
        assert CRITICALITY_SLA_WEIGHT["L1"] == 3.0
        assert CRITICALITY_SLA_WEIGHT["L3"] == 1.0 / 3.0


class TestB1ExtractHeuristic:
    def test_heuristic_extracts_rates(self):
        from src.engines.contract_performance.extract import _heuristic_contract_extract

        text = (
            "Contract FM-2026-01. P1 response within 2 hours. "
            "Labour day rate £420. Overtime 630. Call-out 175. Net 30."
        )
        result = _heuristic_contract_extract(text)
        ex = result["extracted"]
        assert ex.get("sla_response_p1_hours") == 2.0
        assert ex.get("labour_day_rate") == 420.0
        assert "net" in (ex.get("payment_terms") or "").lower()

    def test_heuristic_extracts_signed_date_iso(self):
        from src.engines.contract_performance.extract import _heuristic_contract_extract

        text = "Agreement dated 2026-01-15. P1 response: 1 hour."
        result = _heuristic_contract_extract(text)
        assert result["extracted"].get("signed_date") == "2026-01-15"

    def test_heuristic_extracts_signed_date_natural(self):
        from src.engines.contract_performance.extract import _heuristic_contract_extract

        text = "This contract was signed on 15 January 2026 between..."
        result = _heuristic_contract_extract(text)
        assert result["extracted"].get("signed_date") == "2026-01-15"


class TestB1OverlappingContracts:
    """FR-035 — overlapping contracts selection via signed_date."""

    def _make_row(self, signed_date, confirmed_at):
        """Build a minimal ContractSlaParameters-like namespace."""
        from datetime import datetime, timezone
        from types import SimpleNamespace

        return SimpleNamespace(
            id="uuid-" + str(signed_date),
            organization_id=None,
            vendor_id=None,
            contract_id=None,
            document_id=None,
            contract_ref=None,
            signed_date=signed_date,
            status="confirmed",
            sla_response_p1_hours=None,
            sla_response_p2_hours=None,
            sla_response_p3_hours=None,
            sla_response_p4_hours=None,
            sla_completion_p1_hours=None,
            sla_completion_p2_hours=None,
            sla_completion_p3_hours=None,
            sla_completion_p4_hours=None,
            labour_day_rate=None,
            labour_hour_rate=None,
            overtime_rate=None,
            call_out_rate=None,
            parts_pricing_json={},
            payment_terms=None,
            kpi_clauses_json={},
            ppm_obligations_json={},
            task_criticality_json={},
            defaults_used=[],
            overrides_log=[],
            field_sources={},
            confirmed_at=confirmed_at or datetime.now(timezone.utc),
        )

    def test_params_to_dict_includes_signed_date(self):
        from datetime import date

        from src.engines.contract_performance.parameters import params_to_dict

        row = self._make_row(date(2026, 1, 15), None)
        d = params_to_dict(row)
        assert d["signed_date"] == "2026-01-15"

    def test_params_to_dict_signed_date_none(self):
        from src.engines.contract_performance.parameters import params_to_dict

        row = self._make_row(None, None)
        d = params_to_dict(row)
        assert d["signed_date"] is None
