"""Exporting everything one company holds — and nothing that belongs to anyone else.

An administrator asks for their data and gets a zip of CSVs, one per table, with a manifest
saying what is in it and what is not. The hard part is not the zip. It is deciding, for each
of 145 tables, whether this company's rows can be identified at all — because three kinds of
table must never reach a customer's downloads folder:

  **Credentials.** ``auth_sessions``, ``auth_otp_codes``, ``auth_role_changes`` and
  ``approval_action_tokens`` hold live session tokens and one-time codes. An export
  containing them hands whoever opens the zip a way in.

  **The platform's own work.** ``regulation_packs``, ``prompt_templates``, the nine
  ``udr_*`` ontology tables, the compliance verification registers. Real data, but not this
  company's records, and shipping it under the heading "your data" misdescribes both.

  **Anything unscopable.** A table with no path to an organization cannot be filtered, and a
  table exported without a filter exports every tenant in the database. These are excluded
  **by name, with a reason** — never exported hopefully, and never dropped silently either.

Scoping is resolved four ways, in this order:

  ``root``      ``organizations`` itself, which has ``id`` rather than ``organization_id``.
  ``direct``    the table carries ``organization_id`` — or ``org_id``, which 13 tables use
                and reading only the longer name quietly loses (saved spaces, the activity
                log, duty holders, manufacturers).
  ``building``  no org column but a ``building_id``: scoped to the org's buildings.
  ``parent``    a named foreign key into a table that is itself already scoped.

**Why buildings are reached backwards.** ``buildings`` has no org column, and the path that
should serve — ``buildings.site_id`` → ``sites.organization_id`` — cannot be joined:
``sites.organization_id`` is an **integer** while ``organizations.id`` and every other
organization id in the schema is a **uuid**. Rather than change production schema to fix it,
a building is treated as this org's when an org-scoped row points at it. That is derivable
today and needs no migration. Its one cost is stated in the manifest: a building with nothing
at all filed against it is unreachable and will not appear.

``parent`` resolution **iterates to a fixed point**, because some parents are themselves
indirect: ``receipt_line_items`` → ``receipts`` → ``purchase_orders``, and
``rca_grouping_actions`` → ``rca_groupings`` → ``work_orders``. A single pass leaves those
unscoped, which would drop real records out of an export claiming to be complete.

Every identifier used in SQL comes from the introspected schema or the literals here. The
org id is always a bound parameter. Nothing in this module writes.
"""
from __future__ import annotations

import re
from typing import NamedTuple

#: Identifiers are only ever formatted into SQL after matching this.
_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")

#: The two spellings of the organization column, in preference order.
_ORG_COLUMNS = ("organization_id", "org_id")


class Rule(NamedTuple):
    """How one table's rows are narrowed to this organization."""

    table: str
    kind: str          # root | direct | building | parent
    where: str         # a WHERE clause binding :org, never empty
    parent: str = ""   # for kind == "parent", the table it hangs off
    column: str = ""   # the column that carried the scope


#: table -> (its own foreign key, the table that key points at). The parent must itself end
#: up scoped; a test enforces that, because selecting from an unscoped parent is an
#: unfiltered export wearing a subquery.
_PARENTS: dict[str, tuple[str, str]] = {
    # Everything hanging off an asset.
    "asset_documents": ("asset_id", "assets"),
    "asset_warranties": ("asset_id", "assets"),
    "asset_offline_log": ("asset_id", "assets"),
    "equipment": ("asset_id", "assets"),
    "inspections": ("asset_id", "assets"),
    "maintenance_history": ("asset_id", "assets"),
    # Work order children.
    "work_order_assets": ("work_order_id", "work_orders"),
    "work_order_attachments": ("work_order_id", "work_orders"),
    "work_order_comments": ("work_order_id", "work_orders"),
    "work_order_history": ("work_order_id", "work_orders"),
    "work_order_parts": ("work_order_id", "work_orders"),
    "work_order_resources": ("work_order_id", "work_orders"),
    "work_order_tasks": ("work_order_id", "work_orders"),
    "work_order_users": ("work_order_id", "work_orders"),
    "misc_costs": ("work_order_id", "work_orders"),
    # Root-cause analysis: groupings hang off work orders, their joins off the groupings.
    "rca_groupings": ("work_order_id", "work_orders"),
    "rca_grouping_actions": ("rca_grouping_id", "rca_groupings"),
    "rca_grouping_causes": ("rca_grouping_id", "rca_groupings"),
    # Purchasing: receipts are two hops from an org, their line items three.
    "receipts": ("purchase_order_id", "purchase_orders"),
    "receipt_line_items": ("receipt_id", "receipts"),
    "purchase_order_line_items": ("purchase_order_id", "purchase_orders"),
    "inventory_transactions": ("part_id", "spare_parts"),
    "bom_group_parts": ("bom_group_id", "bom_groups"),
    # Maintenance planning.
    "schedule_triggers": ("maintenance_plan_id", "maintenance_plans"),
    "scheduled_maintenance_assets": ("maintenance_plan_id", "maintenance_plans"),
    "scheduled_maintenance_parts": ("maintenance_plan_id", "maintenance_plans"),
    "scheduled_maintenance_users": ("maintenance_plan_id", "maintenance_plans"),
    "scheduled_tasks": ("maintenance_plan_id", "maintenance_plans"),
    # People.
    "technician_skills": ("technician_id", "technicians"),
    "user_certifications": ("user_id", "users"),
    "user_roles": ("user_id", "users"),
    "vendor_contacts": ("vendor_id", "vendors"),
    "document_generation_log": ("user_id", "users"),
    # The ingested file behind a document. documents.document_id IS ingestion_documents.id —
    # the same uuid identifies both halves — so the file record is reachable from the graph
    # record, and everything doc-rag wrote about it follows from there.
    "ingestion_documents": ("id", "documents"),
    "corrections_log": ("ingestion_id", "ingestion_documents"),
    "ingestion_audit_log": ("ingestion_id", "ingestion_documents"),
    "review_queue": ("ingestion_id", "ingestion_documents"),
    # ── Tables live on production but absent from db/01_schema.sql ───────────
    # The checked-in schema is 145 tables; the deployed database is 225. Everything below
    # exists only in the deployed one, so it is mapped from the live information_schema
    # rather than from the file, and left unmapped it would fall out of the export with the
    # customer's work-order approvals, stock history and asset moves inside it.
    "asset_events": ("asset_id", "assets"),
    "asset_businesses": ("asset_id", "assets"),
    "asset_users": ("asset_id", "assets"),
    "certificates": ("asset_id", "assets"),
    "meter": ("asset_id", "assets"),
    "wo_assets": ("asset_id", "assets"),
    "move_assets": ("move_id", "moves"),
    "move_back_assets": ("move_back_id", "move_backs"),
    "wo_approval_requests": ("work_order_id", "work_orders"),
    "wo_approval_suggestions": ("work_order_id", "work_orders"),
    "wo_status_history": ("work_order_id", "work_orders"),
    "wo_journey_logs": ("work_order_id", "work_orders"),
    "work_order_businesses": ("work_order_id", "work_orders"),
    "work_order_task_files": ("work_order_task_id", "work_order_tasks"),
    "purchase_order_logs": ("purchase_order_id", "purchase_orders"),
    "purchase_order_additional_costs": ("purchase_order_id", "purchase_orders"),
    "rfq_line_items": ("rfq_id", "rfqs"),
    "stock_history": ("part_id", "spare_parts"),
    "stock_cycle_counts": ("part_id", "spare_parts"),
    "report_card_runs": ("card_id", "report_cards"),
    "site_users": ("user_id", "users"),
    "wo_approver_routing": ("user_id", "users"),
    "wo_locations": ("location_id", "locations"),
    # On the deployed database document_chunks has only `ingestion_id` — 05_docrag_compat.sql
    # dropped the 01_schema version and recreated it. Where the older shape is live the table
    # carries org_id and resolves as `direct` before this entry is ever consulted.
    "document_chunks": ("ingestion_id", "ingestion_documents"),
    # Navigator state and the UDR scripts this company had built.
    "activity_log_action": ("entry_id", "activity_log_entry"),
    "activity_log_refinement": ("entry_id", "activity_log_entry"),
    "activity_log_step": ("entry_id", "activity_log_entry"),
    "saved_space_item": ("space_id", "saved_space"),
    "udr_mapping_decision": ("udr_script_id", "udr_script"),
    "udr_test_result": ("udr_script_id", "udr_script"),
}

#: Primary key of a parent, where it is not `id`. Read from the schema's own naming.
_PARENT_KEYS: dict[str, str] = {
    "assets": "id",
    "work_orders": "id",
    "documents": "document_id",
    "buildings": "building_id",
}

#: Never exported, and why — the reason is printed in the manifest, so it has to explain
#: itself to someone wondering where their data went.
_EXCLUDED: dict[str, str] = {
    # Credentials. The single most important line in this file.
    "auth_sessions": "Live session credentials — never exported.",
    "auth_otp_codes": "Live one-time credential codes — never exported.",
    "auth_role_changes": "Credential and privilege history — never exported.",
    "approval_action_tokens": "Live single-use action credentials — never exported.",
    # The platform's own records, not this company's.
    "permissions": "Platform reference data: the permission catalogue, identical for every company.",
    "role_permissions": "Platform reference data: how permissions map to roles, platform-wide.",
    "regulation_packs": "Platform reference data: the regulation packs Hoistra maintains.",
    "country_certificate_packs": "Platform reference data: certificate rules per country.",
    "prompt_templates": "Platform reference data: extraction prompts Hoistra maintains.",
    "prompt_ab_tests": "Platform reference data: prompt experiments run across all companies.",
    "compliance_verification_sources": "Platform reference data: the public registers used for verification.",
    "compliance_verification_register_rows": "Platform reference data: rows scraped from public registers.",
    "udr_canonical_table": "Platform reference data: the canonical data model, platform-wide.",
    "udr_canonical_column": "Platform reference data: the canonical data model, platform-wide.",
    "udr_ontology_chunk": "Platform reference data: the canonical ontology, platform-wide.",
    "udr_synonym": "Platform reference data: the canonical synonym list, platform-wide.",
    # Hoistra's running costs and internal model telemetry.
    "claude_api_usage": "Hoistra's model usage and cost telemetry, not your operational records.",
    "claude_budget_config": "Hoistra's internal budget configuration, not your operational records.",
    "query_audit_log": "Hoistra's internal query telemetry, not your operational records.",
    "agent_audit_log": "Hoistra's internal agent determinism telemetry, not your operational records.",
    "orchestration_audit_log": "Hoistra's internal orchestration telemetry, not your operational records.",
    # ── Deployed-only platform tables (not in db/01_schema.sql) ─────────────
    "alembic_version": "Database migration bookkeeping, not operational data.",
    "countries": "Platform reference data: the country list, identical for every company.",
    "currencies": "Platform reference data: the currency list, identical for every company.",
    "canonical_registry": "Platform reference data: the canonical field registry, platform-wide.",
    "udr_table_catalog": "Platform reference data: the canonical table catalogue, platform-wide.",
    "fiix_schema_cache": "Platform reference data: a cache of the Fiix CMMS schema.",
    "known_issues": "Hoistra's internal issue list, not your operational records.",
    "agent_activity_log": "Hoistra's internal agent telemetry, not your operational records.",
    "wo_chat_sessions": "Hoistra's internal assistant session log, not your operational records.",
    "connectors": (
        "Integration connection configuration, which can carry credentials for your other "
        "systems — never exported."
    ),
    "field_maps": "Integration field mapping configuration, tied to a connector rather than to your records.",
    "uploaded_files": "Integration staging area, tied to a connector rather than to your records.",
    "import_jobs": "Integration run history, tied to a connector rather than to your records.",
    "import_errors": "Integration run errors, tied to a connector rather than to your records.",
    "migration_field_mappings": "A migration run's internal working state, not your operational records.",
    "migration_hierarchy": "A migration run's internal working state, not your operational records.",
    "schema_mapping_field_mappings": "A schema-mapping run's internal working state, not your operational records.",
    # The job rows these three hang off. They carry organization_id, so without naming them
    # here they resolve as `direct` and the export ships the LangGraph state their children
    # were excluded for — inconsistent, and expensive: migration_jobs is 31MB of
    # node_state_json/pending_gate_payload across 829 rows, and reading it took 110 seconds
    # of the export's 222, all of it before the first row arrived.
    "migration_jobs": (
        "A migration run's internal working state (LangGraph node state and gate payloads), "
        "not your operational records — the records a migration produced are exported as "
        "themselves."
    ),
    "schema_mapping_jobs": (
        "A schema-mapping run's internal working state (LangGraph node state and gate "
        "payloads), not your operational records."
    ),
    "wo_approval_rules": (
        "No organization link exists on this table, so approval rules cannot be told apart "
        "by company — exporting it would include every customer's rules."
    ),
    "wo_approval_thresholds": (
        "No organization link exists on this table, so approval thresholds cannot be told "
        "apart by company — exporting it would include every customer's thresholds."
    ),
    # No path to an organization exists in this schema today.
    "portfolios": (
        "No organization link exists on this table, so its rows cannot be told apart by "
        "company — exporting it would include every customer's portfolios."
    ),
}


def _org_column(columns: set[str]) -> str | None:
    return next((c for c in _ORG_COLUMNS if c in columns), None)


def _buildings_clause(shape: dict[str, set[str]], column: str = "building_id") -> str:
    """The org's buildings, derived from the org-scoped rows that point at them.

    Built from every directly-scoped table that also carries a ``building_id`` rather than a
    hardcoded list, so a new org-scoped table with buildings widens the net by existing.
    """
    sources: list[str] = []
    for table in sorted(shape):
        cols = shape[table]
        org = _org_column(cols)
        if not org or "building_id" not in cols or not _SAFE.match(table):
            continue
        sources.append(
            f"SELECT building_id FROM plenum_cafm.{table} "
            f"WHERE {org}::text = :org AND building_id IS NOT NULL"
        )
    if not sources:
        return ""
    return f"{column}::text IN (SELECT DISTINCT building_id::text FROM (" \
           + " UNION ALL ".join(sources) + ") AS org_buildings)"


def plan_export(shape: dict[str, set[str]]) -> tuple[dict[str, Rule], dict[str, str]]:
    """Decide, for every table in ``shape``, how it is scoped — or why it is excluded.

    ``shape`` maps table name to its column names, as read from ``information_schema``.
    Returns ``(included, excluded)``: a Rule per exported table, and a readable reason per
    table that is not. Every table in ``shape`` appears in exactly one of them.
    """
    included: dict[str, Rule] = {}
    excluded: dict[str, str] = {}

    for table in sorted(shape):
        if not _SAFE.match(table):
            excluded[table] = "Table name is not a plain identifier, so it is not exported."
            continue
        if table in _EXCLUDED:
            excluded[table] = _EXCLUDED[table]
            continue

        cols = shape[table]

        # The company's own row. `organizations` is keyed by `id`, so neither of the org
        # column names matches and it would otherwise fall through to unscopable.
        if table == "organizations":
            included[table] = Rule(table, "root", "id::text = :org", column="id")
            continue

        org = _org_column(cols)
        if org:
            included[table] = Rule(table, "direct", f"{org}::text = :org", column=org)
            continue

        if "building_id" in cols:
            clause = _buildings_clause(shape)
            if clause:
                included[table] = Rule(table, "building", clause, column="building_id")
                continue

    # Parents resolve in passes: receipt_line_items needs receipts, which needs
    # purchase_orders. Repeat until nothing new resolves rather than assuming an order.
    pending = {t: _PARENTS[t] for t in shape if t in _PARENTS and t not in included
               and t not in excluded}
    while True:
        progressed = False
        for table, (fk, parent) in list(pending.items()):
            if parent not in included:
                continue
            if not (_SAFE.match(fk) and _SAFE.match(parent)):
                continue
            if fk not in shape.get(table, set()):
                continue
            key = _PARENT_KEYS.get(parent, "id")
            if key not in shape.get(parent, set()):
                key = next((k for k in (f"{parent[:-1]}_id", "id") if k in shape.get(parent, set())), None)
                if key is None:
                    continue
            included[table] = Rule(
                table, "parent",
                f"{fk}::text IN (SELECT {key}::text FROM plenum_cafm.{parent} "
                f"WHERE {included[parent].where})",
                parent=parent, column=fk,
            )
            del pending[table]
            progressed = True
        if not progressed:
            break

    # Whatever is left has no path to an organization. Said plainly, because the reason
    # lands in the manifest and "missing" and "never yours" are different findings.
    for table in shape:
        if table in included or table in excluded:
            continue
        excluded[table] = (
            "No organization link and no scoped parent found for this table, so its rows "
            "cannot be told apart by company — exporting it would include other customers."
        )
    return included, excluded
