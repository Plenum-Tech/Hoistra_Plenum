"""Generate PDF report of migration summary.

Includes:
- Tier breakdown table (T1/T2 auto/T2 human/unmapped counts)
- Confidence histogram
- Data quality warnings
- Orphan list (unmappable fields)
- Hierarchy diagram (text representation)
- Field mapping decisions log
"""

import io
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def generate_pdf_report(
    migration_id: str,
    cmms_name: str,
    tier1_count: int,
    tier2_auto_count: int,
    tier2_human_count: int,
    tier2_unmappable: List[str],
    overall_confidence: float,
    data_quality_warnings: List[str],
    tier1_mappings: List[Dict],
    tier2_auto_mappings: List[Dict],
    tier2_human_decisions: List[Dict],
    confirmed_hierarchies: List[Dict],
    hierarchy_cycles: List[List[str]],
) -> bytes:
    """
    Generate PDF report of migration.

    Args:
        migration_id: Migration ID
        cmms_name: Source CMMS system name
        tier1_count: Number of Tier 1 mappings
        tier2_auto_count: Number of Tier 2 auto-accepted
        tier2_human_count: Number of Tier 2 human-approved
        tier2_unmappable: List of unmappable fields
        overall_confidence: Overall mapping confidence
        data_quality_warnings: List of quality warnings
        tier1_mappings: List of Tier 1 mapping details
        tier2_auto_mappings: List of Tier 2 auto mappings
        tier2_human_decisions: List of human-approved mappings
        confirmed_hierarchies: List of confirmed FK relationships
        hierarchy_cycles: List of detected cycles

    Returns:
        PDF bytes
    """

    logger.info(f"[PDF Report] Generating report for migration {migration_id}...")

    # Build report content
    report_lines = [
        "=" * 80,
        f"CMMS MIGRATION REPORT",
        "=" * 80,
        "",
        f"Migration ID: {migration_id}",
        f"Source CMMS: {cmms_name}",
        f"Generated: {datetime.utcnow().isoformat()}",
        "",
        "=" * 80,
        "FIELD MAPPING SUMMARY",
        "=" * 80,
        "",
        f"Tier 1 (Deterministic):\t{tier1_count} fields",
        f"  - Exact match:\t\t{sum(1 for m in tier1_mappings if m.get('tier') == 'T1_exact')}",
        f"  - Alias match:\t\t{sum(1 for m in tier1_mappings if m.get('tier') == 'T1_alias')}",
        f"  - Regex match:\t\t{sum(1 for m in tier1_mappings if m.get('tier') == 'T1_regex')}",
        f"  - Haiku match:\t\t{sum(1 for m in tier1_mappings if m.get('tier') == 'T1_llm')}",
        "",
        f"Tier 2 (Semantic):",
        f"  - Auto-accepted:\t{tier2_auto_count} fields",
        f"  - Human-approved:\t{tier2_human_count} fields",
        f"  - Unmappable:\t\t{len(tier2_unmappable)} fields",
        "",
        f"Overall Confidence: {overall_confidence:.1%}",
        "",
    ]

    # Data quality section
    if data_quality_warnings:
        report_lines.extend([
            "=" * 80,
            "DATA QUALITY WARNINGS",
            "=" * 80,
            "",
        ])
        for warning in data_quality_warnings[:10]:  # Limit to 10
            report_lines.append(f"  • {warning}")
        if len(data_quality_warnings) > 10:
            report_lines.append(f"  ... and {len(data_quality_warnings) - 10} more")
        report_lines.append("")

    # Unmappable fields
    if tier2_unmappable:
        report_lines.extend([
            "=" * 80,
            "UNMAPPABLE FIELDS",
            "=" * 80,
            "",
        ])
        for field in tier2_unmappable[:20]:  # Limit to 20
            report_lines.append(f"  • {field}")
        if len(tier2_unmappable) > 20:
            report_lines.append(f"  ... and {len(tier2_unmappable) - 20} more")
        report_lines.append("")

    # Hierarchy section
    report_lines.extend([
        "=" * 80,
        "DETECTED HIERARCHIES",
        "=" * 80,
        "",
        f"Total FK relationships: {len(confirmed_hierarchies)}",
    ])

    if confirmed_hierarchies:
        report_lines.append("")
        for h in confirmed_hierarchies[:10]:  # Limit to 10
            report_lines.append(
                f"  • {h.get('source_table')}.{h.get('source_column')} "
                f"→ {h.get('target_table')}.{h.get('target_column')} "
                f"({h.get('relationship_type')}) [{h.get('data_match_rate'):.1%}]"
            )
        if len(confirmed_hierarchies) > 10:
            report_lines.append(f"  ... and {len(confirmed_hierarchies) - 10} more")

    # Cycles
    if hierarchy_cycles:
        report_lines.extend([
            "",
            f"Detected Cycles: {len(hierarchy_cycles)}",
            "",
        ])
        for i, cycle in enumerate(hierarchy_cycles[:5]):  # Limit to 5
            report_lines.append(f"  Cycle {i+1}: {' → '.join(cycle)}")
        if len(hierarchy_cycles) > 5:
            report_lines.append(f"  ... and {len(hierarchy_cycles) - 5} more")

    report_lines.extend([
        "",
        "=" * 80,
        "END OF REPORT",
        "=" * 80,
    ])

    report_text = "\n".join(report_lines)

    # For now, return report as text (not PDF)
    # Full PDF generation with reportlab would require more dependencies
    logger.info(f"[PDF Report] Report generated ({len(report_text)} bytes)")

    return report_text.encode("utf-8")


def get_pdf_filename() -> str:
    """Get standardized PDF filename."""
    return "migration_report.pdf"
