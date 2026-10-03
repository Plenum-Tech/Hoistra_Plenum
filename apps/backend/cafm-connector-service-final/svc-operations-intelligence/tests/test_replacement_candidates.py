"""Parts to reorder and assets at end of life: the pure rules, and a signed-in-only route."""
from __future__ import annotations

from datetime import date

from src.engines import replacement_candidates as rc

TODAY = date(2026, 10, 4)


def test_design_life_used_is_straight_line_and_capped():
    assert rc.design_life_used_pct(date(2016, 10, 4), 20, TODAY) == 50.0
    assert rc.design_life_used_pct(date(2000, 1, 1), 10, TODAY) == 200.0      # long past design life, capped
    assert rc.design_life_used_pct(None, 20, TODAY) is None
    assert rc.design_life_used_pct(date(2016, 1, 1), 0, TODAY) is None


def test_repeat_failure_verdict_matches_the_cost_saving_read():
    # CHILLER-101 style: three reactive jobs costing 60% of a replacement is a replace-or-fix case
    out = rc.classify_repeat(3, 84_000.0, 140_000.0)
    assert out == {"cost_to_replacement_ratio": 0.6, "verdict": "replace or fix the cause"}
    assert rc.classify_repeat(4, 5_000.0, 140_000.0)["verdict"] == "repeat failure"
    assert rc.classify_repeat(2, 5_000.0, None) == {"cost_to_replacement_ratio": None, "verdict": "watch"}


def test_the_grade_and_window_are_the_product_definitions():
    assert rc.EOL_GRADE == 4 and rc.REPEAT_JOBS == 3 and rc.REPLACE_RATIO == 0.5 and rc.DEFAULT_DAYS == 365
    assert "no purchase order is on file" in rc.ANSWER_RULES and "parts first" in rc.ANSWER_RULES


def test_the_route_needs_a_signed_in_caller():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes.replacement_candidates import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/api/assets/replacement-candidates").status_code in (401, 403)
