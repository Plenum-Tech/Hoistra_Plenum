"""The migration summary report must never be lost to one missing number.

Run ee83c4b4 (30 Sep 2026) ended Output Generation with "PDF report generation failed:
unsupported format string passed to NoneType.__format__": a confirmed hierarchy carried no
data_match_rate, the f-string `{rate:.1%}` raised, and the whole report was dropped
(migration_report_url stayed ""). The report has to say "n/a" for a number it does not have.
"""

from src.export.report_generator import generate_pdf_report


def _report(**overrides) -> str:
    kwargs = dict(
        migration_id="ee83c4b4-1695-461d-8cb9-df1b30d1c4a4",
        cmms_name="Custom",
        tier1_count=179,
        tier2_auto_count=0,
        tier2_human_count=1,
        tier2_unmappable=[],
        overall_confidence=0.98,
        data_quality_warnings=[],
        tier1_mappings=[{"tier": "T1_exact"}],
        tier2_auto_mappings=[],
        tier2_human_decisions=[],
        confirmed_hierarchies=[
            {"source_table": "assets", "source_column": "site_id", "target_table": "sites",
             "target_column": "site_id", "relationship_type": "CONTAINMENT", "data_match_rate": 0.97},
        ],
        hierarchy_cycles=[],
    )
    kwargs.update(overrides)
    return generate_pdf_report(**kwargs).decode("utf-8")


def test_a_hierarchy_without_a_match_rate_still_appears_in_the_report():
    text = _report(confirmed_hierarchies=[
        {"source_table": "assets", "source_column": "site_id", "target_table": "sites",
         "target_column": "site_id", "relationship_type": "CONTAINMENT"},  # customer-confirmed, no rate
        {"source_table": "work_orders", "source_column": "asset_code", "target_table": "assets",
         "target_column": "asset_code", "relationship_type": "REFERENCE", "data_match_rate": 0.91},
    ])
    assert "assets.site_id → sites.site_id (CONTAINMENT) [n/a]" in text
    assert "work_orders.asset_code → assets.asset_code (REFERENCE) [91.0%]" in text


def test_a_missing_overall_confidence_reads_as_not_available():
    text = _report(overall_confidence=None)
    assert "Overall Confidence: n/a" in text


def test_a_present_overall_confidence_keeps_its_percentage():
    assert "Overall Confidence: 98.0%" in _report()
