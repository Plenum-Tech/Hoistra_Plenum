from src.agents.homework_contract_agent import explicit_contract_request
from src.agents.homework_energy_agent import explicit_energy_request


def test_clear_complete_energy_question_has_a_typed_fallback():
    assert explicit_energy_request("Calculate EUI for hw1-building-a1 between 2026-10-01 and 2026-10-31") == (
        "energy_eui",{"building_id":"hw1-building-a1","period_start":"2026-10-01",
                      "period_end":"2026-10-31"})


def test_energy_fallback_requires_building_and_period():
    assert explicit_energy_request("What is our energy EUI?") is None
    assert explicit_energy_request("Calculate EUI for hw1-building-a1 in October") is None


def test_clear_complete_scorecard_question_has_idempotent_report_request():
    result=explicit_contract_request("Generate a vendor scorecard for hw1-building-a1-vendor-0 for October 2026")
    assert result is not None
    name,args=result
    assert name=="contract_vendor_performance"
    assert (args["period_start"],args["period_end"])==("2026-10-01","2026-10-31")
    assert args["building_ids"]==["hw1-building-a1"]
    assert args["idempotency_key"].startswith("provider-fallback-")


def test_ambiguous_or_incomplete_contract_question_does_not_fallback():
    assert explicit_contract_request("Compare hw1-building-a1-vendor-0 and hw1-building-a2-vendor-0 in October 2026") is None
    assert explicit_contract_request("Score vendor hw1-building-a1-vendor-0") is None
