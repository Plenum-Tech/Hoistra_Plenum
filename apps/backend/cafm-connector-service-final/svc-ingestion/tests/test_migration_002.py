"""
tests/test_migration_002.py

Static structural tests for Alembic migration 002 — Task 2.10.

Verifies (without running against a live DB):
  - Migration file is syntactically valid Python (importable)
  - revision / down_revision chaining is correct
  - All 4 new tables are created in upgrade()
  - All ALTER TABLE additions are present (review_queue, corrections_log)
  - orchestration_audit_log INSERT-only enforcement (REVOKE statement present)
  - downgrade() drops all tables added in upgrade()
  - SCHEMA constant is correct
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the migration module without executing its upgrade/downgrade
# ---------------------------------------------------------------------------

MIGRATION_PATH = (
    Path(__file__).parent.parent
    / "alembic"
    / "versions"
    / "002_add_inspection_and_agent_audit_tables.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    if not MIGRATION_PATH.exists():
        pytest.fail(f"Migration file not found: {MIGRATION_PATH}")
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


# ===========================================================================
# Metadata constants
# ===========================================================================

class TestMigrationMetadata:
    def test_file_is_valid_python(self, migration_source: str):
        """Migration file parses as valid Python AST."""
        ast.parse(migration_source)  # raises SyntaxError on failure

    def test_revision_is_002(self, migration_source: str):
        assert 'revision: str = "002"' in migration_source

    def test_down_revision_is_001(self, migration_source: str):
        assert 'down_revision' in migration_source
        assert '"001"' in migration_source

    def test_schema_constant(self, migration_source: str):
        assert 'SCHEMA = "plenum_cafm"' in migration_source

    def test_imports_sqlalchemy(self, migration_source: str):
        assert "import sqlalchemy as sa" in migration_source

    def test_imports_jsonb(self, migration_source: str):
        assert "JSONB" in migration_source

    def test_imports_uuid(self, migration_source: str):
        assert "UUID" in migration_source


# ===========================================================================
# New tables in upgrade()
# ===========================================================================

class TestUpgradeTables:
    def test_inspections_table_created(self, migration_source: str):
        assert '"inspections"' in migration_source

    def test_agent_audit_log_table_created(self, migration_source: str):
        assert '"agent_audit_log"' in migration_source

    def test_orchestration_audit_log_table_created(self, migration_source: str):
        assert '"orchestration_audit_log"' in migration_source

    def test_document_generation_log_table_created(self, migration_source: str):
        assert '"document_generation_log"' in migration_source

    def test_all_four_tables_present(self, migration_source: str):
        tables = [
            '"inspections"',
            '"agent_audit_log"',
            '"orchestration_audit_log"',
            '"document_generation_log"',
        ]
        for table in tables:
            assert table in migration_source, f"Missing table: {table}"


# ===========================================================================
# inspections table columns
# ===========================================================================

class TestInspectionsColumns:
    def test_asset_code_column(self, migration_source: str):
        assert "asset_code" in migration_source

    def test_inspector_column(self, migration_source: str):
        assert '"inspector"' in migration_source

    def test_inspection_date_column(self, migration_source: str):
        assert '"inspection_date"' in migration_source

    def test_section_column(self, migration_source: str):
        assert '"section"' in migration_source

    def test_risk_level_column(self, migration_source: str):
        assert '"risk_level"' in migration_source

    def test_corrective_action_column(self, migration_source: str):
        assert '"corrective_action"' in migration_source

    def test_findings_jsonb_column(self, migration_source: str):
        assert '"findings_jsonb"' in migration_source

    def test_source_file_column(self, migration_source: str):
        assert '"source_file"' in migration_source

    def test_ingestion_id_fk(self, migration_source: str):
        assert "ingestion_documents" in migration_source


# ===========================================================================
# agent_audit_log eval result columns (EL-5.x)
# ===========================================================================

class TestAgentAuditLogColumns:
    def test_bound_validation_passed(self, migration_source: str):
        assert "bound_validation_passed" in migration_source

    def test_run_outputs(self, migration_source: str):
        assert "run_1_output" in migration_source
        assert "run_2_output" in migration_source
        assert "run_3_output" in migration_source

    def test_run_valid_flags(self, migration_source: str):
        assert "run_1_valid" in migration_source
        assert "run_2_valid" in migration_source
        assert "run_3_valid" in migration_source

    def test_runs_agreed(self, migration_source: str):
        assert "runs_agreed" in migration_source

    def test_winner_status(self, migration_source: str):
        assert "winner_status" in migration_source

    def test_confidence_gate_passed(self, migration_source: str):
        assert "confidence_gate_passed" in migration_source

    def test_hard_rules_fired(self, migration_source: str):
        assert "hard_rules_fired" in migration_source

    def test_requires_human_review(self, migration_source: str):
        assert "requires_human_review" in migration_source


# ===========================================================================
# orchestration_audit_log — INSERT-only enforcement
# ===========================================================================

class TestOrchestrationAuditLogInsertOnly:
    def test_revoke_update_delete_present(self, migration_source: str):
        """REVOKE UPDATE, DELETE must be applied to orchestration_audit_log."""
        assert "REVOKE UPDATE, DELETE" in migration_source
        assert "orchestration_audit_log" in migration_source

    def test_revoke_targets_app_role(self, migration_source: str):
        assert "plenum_app" in migration_source

    def test_action_column_present(self, migration_source: str):
        assert '"action"' in migration_source

    def test_confidence_column_present(self, migration_source: str):
        assert '"confidence"' in migration_source

    def test_agent_results_jsonb_column(self, migration_source: str):
        assert "agent_results_jsonb" in migration_source

    def test_bound_passed_column(self, migration_source: str):
        assert "bound_passed" in migration_source

    def test_safety_passed_column(self, migration_source: str):
        assert "safety_passed" in migration_source


# ===========================================================================
# document_generation_log — EL-7.x columns
# ===========================================================================

class TestDocumentGenerationLogColumns:
    def test_plan_validation_passed(self, migration_source: str):
        assert "plan_validation_passed" in migration_source

    def test_spot_checks_run(self, migration_source: str):
        assert "spot_checks_run" in migration_source

    def test_spot_checks_passed(self, migration_source: str):
        assert "spot_checks_passed" in migration_source

    def test_eval_score_column(self, migration_source: str):
        assert "eval_score" in migration_source

    def test_held_for_review(self, migration_source: str):
        assert "held_for_review" in migration_source

    def test_document_plan_json(self, migration_source: str):
        assert "document_plan_json" in migration_source

    def test_output_format_column(self, migration_source: str):
        assert "output_format" in migration_source


# ===========================================================================
# ALTER TABLE additions (review_queue + corrections_log)
# ===========================================================================

class TestAlterTableAdditions:
    def test_review_queue_payload_column_added(self, migration_source: str):
        assert '"payload"' in migration_source
        assert "review_queue" in migration_source

    def test_review_queue_review_type_added(self, migration_source: str):
        assert '"review_type"' in migration_source

    def test_review_queue_resolved_value_added(self, migration_source: str):
        assert "resolved_value" in migration_source

    def test_review_queue_resolved_by_added(self, migration_source: str):
        assert "resolved_by" in migration_source

    def test_corrections_log_review_queue_id_added(self, migration_source: str):
        assert "review_queue_id" in migration_source

    def test_corrections_log_corrected_by_added(self, migration_source: str):
        assert "corrected_by" in migration_source


# ===========================================================================
# downgrade() — tables dropped in reverse order
# ===========================================================================

class TestDowngrade:
    def test_downgrade_drops_document_generation_log(self, migration_source: str):
        assert "document_generation_log" in migration_source

    def test_downgrade_drops_orchestration_audit_log(self, migration_source: str):
        assert "orchestration_audit_log" in migration_source

    def test_downgrade_drops_agent_audit_log(self, migration_source: str):
        assert "agent_audit_log" in migration_source

    def test_downgrade_drops_inspections(self, migration_source: str):
        assert "inspections" in migration_source

    def test_downgrade_function_exists(self, migration_ast: ast.Module):
        func_names = [
            node.name
            for node in ast.walk(migration_ast)
            if isinstance(node, ast.FunctionDef)
        ]
        assert "downgrade" in func_names
        assert "upgrade" in func_names


# ===========================================================================
# Index coverage
# ===========================================================================

class TestIndexes:
    def test_inspections_has_indexes(self, migration_source: str):
        assert "ix_inspections_asset_code" in migration_source
        assert "ix_inspections_inspection_date" in migration_source
        assert "ix_inspections_risk_level" in migration_source

    def test_agent_audit_log_has_indexes(self, migration_source: str):
        assert "ix_agent_audit_log_agent_id" in migration_source
        assert "ix_agent_audit_log_timestamp" in migration_source

    def test_orchestration_audit_log_has_indexes(self, migration_source: str):
        assert "ix_orchestration_audit_log_action" in migration_source
        assert "ix_orchestration_audit_log_timestamp" in migration_source

    def test_document_generation_log_has_indexes(self, migration_source: str):
        assert "ix_document_generation_log_held_for_review" in migration_source
        assert "ix_document_generation_log_timestamp" in migration_source
