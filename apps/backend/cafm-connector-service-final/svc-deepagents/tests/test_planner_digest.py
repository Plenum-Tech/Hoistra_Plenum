"""A dependent step gets a digest of the earlier result, and the catalogue tells grade from band."""
from __future__ import annotations

from src.agents import planner


def test_rows_become_one_line_each_with_identifiers_and_figures():
    out = {"ok": True, "count": 61, "total": 61, "method": "rule:x",
           "assets": [{"asset_id": "e883", "asset_code": "B-301-BOILER-01", "asset_name": "Boiler 1", "band": "threat",
                       "explanation": "long prose " * 50, "reasons": ["a", "b"], "anomaly_annual_cost": 24938.5, "section_eui": 262.0},
                      {"asset_id": "43d5", "asset_code": "B-301-BOILER-02", "asset_name": "Boiler 2", "band": "watch", "explanation": "x"}]}
    d = planner._digest(out)
    assert d.startswith('{"count": 61, "total": 61, "method": "rule:x"}') and "2 rows:" in d
    assert "- asset_code=B-301-BOILER-01; asset_name=Boiler 1; band=threat; explanation=long prose" in d
    assert "asset_id" not in d and "reasons" not in d and "…" in d        # ids dropped, prose clipped, lists skipped
    # strings and scalar dicts pass through as text; long row sets are capped
    assert planner._digest("plain") == "plain"
    many = {"records": [{"wo_code": f"WO-{i}", "status": "Open"} for i in range(80)]}
    d = planner._digest(many)
    assert d.count("\n- ") == planner.DIGEST_ROWS and d.endswith("…20 more rows")


def test_the_catalogue_tells_condition_grade_from_energy_band():
    assert "CONDITION GRADE" in planner.PLAN_PROMPT and "never the grade" in planner.PLAN_PROMPT
    from src.agents.energy_intelligence_agent import list_asset_conditions
    first = (list_asset_conditions.description or "").strip().splitlines()[0]
    assert "NOT the 1-5 condition grade" in first and "condition_score" in first
