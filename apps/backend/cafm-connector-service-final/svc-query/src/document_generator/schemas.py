"""
svc-query/src/document_generator/schemas.py

DocumentPlan + DocumentSection Pydantic models shared across planner,
validator, renderer, and filler.

Source of truth for document structure — see CLAUDE.md §14.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SUPPORTED_SECTION_TYPES = Literal[
    "summary_table",
    "schedule_grid",
    "task_checklist",
    "parts_table",
    "findings_list",
    "kpi_summary",
    "signature_block",
    "free_text_header",
]

SUPPORTED_DOCUMENT_TYPES = Literal[
    "pm_schedule",
    "wo_report",
    "wo_package",
    "parts_reorder",
    "inspection_template",
    "asset_health_summary",
    "maintenance_calendar",
    "inspection_report",
    "custom",
]

SUPPORTED_OUTPUT_FORMATS = Literal["docx", "xlsx", "pdf"]


class DocumentSection(BaseModel):
    """One section within a DocumentPlan."""

    type: SUPPORTED_SECTION_TYPES
    heading: str
    data_source: str                  # SQL WHERE clause or table name
    columns: list[str] | None = None
    highlight_rule: str | None = None
    sort_by: str | None = None
    limit: int | None = None


class DocumentPlan(BaseModel):
    """
    Complete plan for a generated document.

    Claude produces this JSON; the deterministic renderer executes it.
    No values are invented — every value must trace to a real DB row.
    """

    document_type: SUPPORTED_DOCUMENT_TYPES
    title: str
    generated_for: str
    output_format: SUPPORTED_OUTPUT_FORMATS
    sections: list[DocumentSection] = Field(min_length=1)
    footer: dict                      # must include: generated_by, timestamp, audit_id
    data_sources_required: list[str]  # table names that must resolve


class PlanningRunResult(BaseModel):
    """Result of one N=3 planning run."""

    run_number: int
    plan: DocumentPlan | None = None
    raw_response: str = ""
    valid: bool = False
    failure_reason: str = ""
