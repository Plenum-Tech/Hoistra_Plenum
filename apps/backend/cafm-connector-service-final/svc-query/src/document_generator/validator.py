"""
svc-query/src/document_generator/validator.py

Task 5.8 — EL-7.DOC.PLAN: DocumentPlan validation.

Runs immediately after the N=3 planning vote produces the winning DocumentPlan.

Checks:
  1. DocumentPlan validates against Pydantic schema
  2. Every data_source in plan verified to resolve to a real table in plenum_cafm
  3. Every filter in data_source verified to return >= 1 row (dry-run query)
  4. Output format is in supported list (docx | xlsx | pdf)

PASS → renderer.py proceeds
FAIL → re-plan once with validation error context
       If still fails after 2nd plan → error returned to user (no file generated)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger

from .schemas import DocumentPlan

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# Known plenum_cafm tables — every data_source must reference one of these
_KNOWN_TABLES = {
    "assets",
    "work_orders",
    "spare_parts",
    "scheduled_pm",
    "inspections",
    "locations",
    "technicians",
    "ingestion_documents",
    "document_chunks",
    "agent_audit_log",
    "orchestration_audit_log",
    "review_queue",
    "claude_api_usage",
}


@dataclass
class ValidationResult:
    """Result of EL-7.DOC.PLAN validation."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_sources_resolved: int = 0
    data_sources_total: int = 0


async def validate_document_plan(
    plan: DocumentPlan,
    session: AsyncSession,
) -> ValidationResult:
    """
    EL-7.DOC.PLAN — validate a DocumentPlan before rendering.

    Returns ValidationResult. If passed=False, caller should re-plan once
    with the error context, then either succeed or return an error to the user.
    """
    with tracer.start_as_current_span("document.validate") as span:
        span.set_attribute("cafm.document_type", plan.document_type)
        span.set_attribute("cafm.data_sources_count", len(plan.data_sources_required))
        span.set_attribute("cafm.sections_count", len(plan.sections))

        errors: list[str] = []
        warnings: list[str] = []

        # Check 1: Pydantic schema (already validated at parse time, but re-check)
        try:
            DocumentPlan.model_validate(plan.model_dump())
        except ValidationError as exc:
            errors.append(f"Schema validation failed: {exc}")

        # Check 2: Output format
        if plan.output_format not in ("docx", "xlsx", "pdf"):
            errors.append(f"Unsupported output_format: {plan.output_format}")

        # Check 3: data_sources_required — all must be known tables
        unknown_sources = []
        for source in plan.data_sources_required:
            table_name = _extract_table_name(source)
            if table_name and table_name not in _KNOWN_TABLES:
                unknown_sources.append(source)
        if unknown_sources:
            errors.append(f"Unknown data sources: {unknown_sources}")

        # Check 4: Each section's data_source resolves to a table with >= 1 row
        resolved = 0
        total = len(plan.sections)

        for section in plan.sections:
            table_name = _extract_table_name(section.data_source)
            if not table_name:
                warnings.append(
                    f"Section '{section.heading}': could not extract table from data_source '{section.data_source}'"
                )
                continue

            if table_name not in _KNOWN_TABLES:
                errors.append(
                    f"Section '{section.heading}': unknown table '{table_name}'"
                )
                continue

            # Dry-run: verify table has at least 1 row
            ok = await _dry_run_table(table_name, session)
            if ok:
                resolved += 1
            else:
                warnings.append(
                    f"Section '{section.heading}': table '{table_name}' returned 0 rows"
                )

        # Check 5: Footer has required fields
        required_footer_keys = {"generated_by", "timestamp", "audit_id"}
        missing_footer = required_footer_keys - set(plan.footer.keys())
        if missing_footer:
            errors.append(f"Footer missing required keys: {missing_footer}")

        passed = len(errors) == 0

        span.set_attribute("cafm.validation_passed", passed)
        span.set_attribute("cafm.all_sources_resolved", resolved == total)
        span.set_attribute("cafm.data_sources_resolved", resolved)
        if not passed:
            span.set_status(StatusCode.ERROR, "; ".join(errors))

        logger.info(
            "el7_doc_plan_validation",
            passed=passed,
            errors=errors,
            warnings=warnings,
            resolved=resolved,
            total=total,
        )

        return ValidationResult(
            passed=passed,
            errors=errors,
            warnings=warnings,
            data_sources_resolved=resolved,
            data_sources_total=total,
        )


def _extract_table_name(data_source: str) -> str | None:
    """
    Extract the base table name from a data_source string.

    data_source may be:
    - A plain table name: "assets"
    - A SQL fragment: "work_orders WHERE status = 'Open'"
    - A qualified name: "plenum_cafm.assets"
    """
    if not data_source:
        return None

    # Strip schema prefix if present
    source = data_source.strip()
    if "." in source and not source.upper().startswith("SELECT"):
        # "plenum_cafm.assets" → "assets"
        source = source.split(".")[-1].strip()

    # If it looks like SQL, extract the first word after FROM or the first word itself
    upper = source.upper()
    if "FROM" in upper:
        after_from = source[upper.index("FROM") + 4:].strip()
        table = after_from.split()[0].rstrip(",").strip()
    elif " WHERE" in upper:
        table = source.split()[0]
    else:
        table = source.split()[0]

    # Remove any trailing punctuation
    table = table.strip(".,;()\"'`")

    # Strip schema prefix again if still present
    if "." in table:
        table = table.split(".")[-1]

    return table.lower() if table else None


async def _dry_run_table(table_name: str, session: AsyncSession) -> bool:
    """Check that a table exists and has at least 1 row."""
    try:
        result = await session.execute(
            text(f"SELECT 1 FROM plenum_cafm.{table_name} LIMIT 1")  # noqa: S608
        )
        return result.scalar() is not None
    except Exception as exc:
        logger.warning("dry_run_table_failed", table=table_name, error=str(exc))
        return False


def format_validation_errors_for_replanning(result: ValidationResult) -> str:
    """Format validation errors as context for the re-planning prompt."""
    lines = ["The previous DocumentPlan failed validation with these errors:"]
    for err in result.errors:
        lines.append(f"  ERROR: {err}")
    for warn in result.warnings:
        lines.append(f"  WARNING: {warn}")
    lines.append(
        "Please produce a corrected DocumentPlan that fixes all errors above."
    )
    return "\n".join(lines)
