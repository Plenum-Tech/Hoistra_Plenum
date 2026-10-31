from src.agents.homework_router import classify


def test_router_selects_one_clear_domain():
    assert classify("Show the 2026-10 energy EUI for hw1-building-a1")[0] == "energy"
    assert classify("Show the vendor SLA scorecard for October 2026")[0] == "contract_performance"
    assert classify("Which certificates expire this month?")[0] == "compliance"
    assert classify("Import this inspection report")[0] == "ingestion"
    assert classify("Show work-order status for hw1-building-a1")[0] == "reads"


def test_router_asks_before_cross_domain_request():
    route, domains = classify("Show the vendor score and whether it is blocked for expiry")

    assert route is None
    assert set(domains) == {"compliance", "contract_performance"}


def test_router_clarifies_vague_request_instead_of_guessing():
    route, domains = classify("Help me with my data")

    assert route is None
    assert domains == []
