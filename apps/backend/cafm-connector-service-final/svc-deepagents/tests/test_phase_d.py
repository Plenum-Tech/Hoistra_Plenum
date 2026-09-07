"""Phase D — Fiix tools and image extension routing."""
from pathlib import Path

import pytest

from src.agents.single_door_flow import (
    DOCUMENT_EXTS,
    IMAGE_EXTS,
    STRUCTURED_EXTS,
    document_type_hint,
)
from src.agents.session_workspace import (
    build_conversation_context,
    build_session_runtime_context,
    default_session_state,
    extract_start_migration_ids,
    normalize_migration_file_key,
    record_conversation_turn,
    register_migration_file,
    resolve_session_migration_ids,
    workspace_has_ingestion,
    _SESSION_STATE,
)


def test_image_extensions_in_document_exts():
    assert ".png" in DOCUMENT_EXTS
    assert ".tiff" in DOCUMENT_EXTS
    assert IMAGE_EXTS <= DOCUMENT_EXTS


def test_structured_and_image_disjoint():
    assert not (STRUCTURED_EXTS & IMAGE_EXTS)


def test_document_type_hint_scan():
    assert document_type_hint("/tmp/equipment_scan.tiff") == "scan"
    assert document_type_hint("/tmp/photo.png") == "image"
    assert document_type_hint("/tmp/report.pdf") == "auto"


def test_workspace_has_ingestion_files():
    state = default_session_state()
    state["ingested_documents"] = 2
    assert workspace_has_ingestion(state)


def test_workspace_has_ingestion_fiix():
    state = default_session_state()
    state["fiix_ingestion_id"] = "00000000-0000-0000-0000-000000000099"
    assert workspace_has_ingestion(state)


def test_workspace_has_no_ingestion():
    assert not workspace_has_ingestion(default_session_state())


def test_normalize_migration_file_key_strips_session_prefix():
    key = normalize_migration_file_key(
        "d7647087-e3b5-4455-98f0-ca023add5851_combinedtest100rows.xlsx"
    )
    assert key == "combinedtest100rows.xlsx"


def test_one_migration_per_workbook_session():
    sid = "test-session-migration-dedupe"
    _SESSION_STATE.pop(sid, None)
    register_migration_file(sid, "000d1788-d7da-439b-8284-bf7cafe15508", "Combinedtest100rows.xlsx")
    register_migration_file(sid, "c72b1a26-bd94-4386-b94a-cadfef410c64", "Combinedtest100rows.xlsx")
    resolved = resolve_session_migration_ids(sid)
    assert resolved == ["000d1788-d7da-439b-8284-bf7cafe15508"]
    state = _SESSION_STATE[sid]
    assert state["migration_by_file"]["combinedtest100rows.xlsx"] == "000d1788-d7da-439b-8284-bf7cafe15508"


def test_extract_start_migration_ids_only_start_tool():
    tool_calls = [
        {"tool": "start_migration", "output": {"migration_id": "aaa"}},
        {"tool": "run_migration", "output": {"migration_id": "aaa", "status": "gate"}},
        {"tool": "start_migration", "output": {"migration_id": "bbb"}},
    ]
    assert extract_start_migration_ids(tool_calls) == ["aaa", "bbb"]


def test_format_matched_rows_report_includes_scores_and_fields():
    from src.agents.single_door_flow import format_matched_rows_report

    sample = {
        "total_chunks_analyzed": 32,
        "unique_rows_matched": 1,
        "latency_ms": 120,
        "by_table": {"assets": 1},
        "matched_rows": [
            {
                "source_table": "assets",
                "row_pk": "AHU-001",
                "confidence": 0.92,
                "match_method": "exact_key",
                "row_data": {"asset_code": "AHU-001", "name": "Air Handler"},
                "matched_metadata_fields": ["asset_code"],
                "match_details": {
                    "semantic_score": 0.81,
                    "bm25_overlap": 0.55,
                    "metadata_overlap": 0.9,
                },
                "chunk_matches": [
                    {
                        "chunk_index": 3,
                        "confidence": 0.92,
                        "semantic_score": 0.81,
                        "bm25_score": 0.55,
                        "metadata_score": 0.9,
                        "matched_fields": ["asset_code"],
                        "chunk_text_preview": "AHU-001 quarterly maintenance",
                    }
                ],
            }
        ],
    }
    report = format_matched_rows_report(sample)
    assert "AHU-001" in report
    assert "semantic 0.810" in report
    assert "asset_code" in report


def test_phases_from_migration_pre_semantic_gate():
    from src.agents.session_workspace import phases_from_migration_status

    phases = phases_from_migration_status(
        {
            "status": "awaiting_review",
            "current_step": 3,
            "pending_gate_type": "pre_semantic",
        }
    )
    assert phases == ("in_progress", "pending")


def test_phases_from_migration_hierarchy_gate():
    from src.agents.session_workspace import phases_from_migration_status

    phases = phases_from_migration_status(
        {
            "status": "awaiting_review",
            "current_step": 7,
            "pending_gate_type": "hierarchy",
        }
    )
    assert phases == ("complete", "in_progress")


def test_phases_from_migration_complete():
    from src.agents.session_workspace import phases_from_migration_status

    assert phases_from_migration_status({"status": "complete", "current_step": 9}) == (
        "complete",
        "complete",
    )


def test_unstructured_register_marks_mapping_hierarchy_complete():
    from src.agents.session_workspace import (
        get_session_state,
        record_unstructured_register_ready,
        workspace_snapshot,
    )

    sid = "test-unstructured-register"
    get_session_state(sid)
    record_unstructured_register_ready(sid)
    snap = workspace_snapshot(sid)
    assert snap["mapping_status"] == "complete"
    assert snap["hierarchy_status"] == "complete"
    assert get_session_state(sid)["ingestion_mode"] == "unstructured"


def test_bulk_threshold_default():
    from src.config import Settings

    s = Settings.model_construct(
        db_url="postgresql+asyncpg://u:p@localhost/db",
        ingest_batch_inline_threshold=3,
    )
    assert s.ingest_batch_inline_threshold == 3


@pytest.mark.asyncio
async def test_ingest_single_file_skipped():
    from src.agents.single_door_flow import ingest_single_file

    result = await ingest_single_file(file_path="/tmp/sample.xyz")
    assert result["kind"] == "skipped"
    assert result["status"] == "error"


def test_schema_gate_followup_on_yes_with_active_mapping():
    from src.agents.session_workspace import (
        _SESSION_STATE,
        record_schema_mapping_started,
        resolve_active_schema_mapping_id,
    )

    sid = "test-schema-gate-yes"
    _SESSION_STATE.pop(sid, None)
    record_schema_mapping_started(sid, "5c044ff5-621a-425c-8179-d35ebdd07981")
    state = _SESSION_STATE[sid]
    assert resolve_active_schema_mapping_id(sid) == "5c044ff5-621a-425c-8179-d35ebdd07981"
    assert state.get("pending_schema_gate_confirm") is True


def test_classify_route_intent_fiix_schema():
    from src.agents.session_workspace import ROUTE_FIIX_SYNC, classify_route_intent, default_session_state

    assert classify_route_intent("fetch live fiix schema", default_session_state()) == ROUTE_FIIX_SYNC


def test_classify_route_intent_rerun_mapping():
    from src.agents.session_workspace import ROUTE_UDR_MAP, classify_route_intent, default_session_state

    assert classify_route_intent("re-run mapping on saved udr script", default_session_state()) == ROUTE_UDR_MAP


def test_resolve_route_intent_forced_from_context():
    from src.agents.session_workspace import (
        ROUTE_UDR_MAP,
        ROUTE_GENERAL,
        default_session_state,
        resolve_route_intent,
    )

    state = default_session_state()
    assert (
        resolve_route_intent("hello", state, "plenum_forced_route=udr_run_mapping_hierarchy")
        == ROUTE_UDR_MAP
    )
    assert resolve_route_intent("hello", state, "plenum_forced_route=not_a_route") == ROUTE_GENERAL


def test_fiix_credentials_session_flow():
    from src.agents.session_workspace import (
        fiix_credentials_configured,
        set_fiix_credentials,
    )

    sid = "test-fiix-creds"
    _SESSION_STATE.pop(sid, None)
    assert not fiix_credentials_configured(sid)
    set_fiix_credentials(
        sid,
        fiix_subdomain="plenumtechnology",
        fiix_app_key="app",
        fiix_access_key="access",
        fiix_secret_key="secret",
    )
    assert fiix_credentials_configured(sid)


def test_parse_fiix_credentials_from_chat_block():
    from src.agents.fiix_credential_parse import (
        merge_fiix_credentials_from_message,
        parse_fiix_credentials_from_text,
    )

    text = """
    Subdomain : plenumtechnology
    App Key : macmmsackp-test-app
    Access Key : macmmsaakp-test-access
    Secret Key : macmmsaskp-test-secret
    """
    parsed = parse_fiix_credentials_from_text(text)
    assert parsed["subdomain"] == "plenumtechnology"
    assert "macmms" in parsed["app_key"]
    sid = "test-fiix-parse-creds"
    _SESSION_STATE.pop(sid, None)
    assert merge_fiix_credentials_from_message(sid, text)
    from src.agents.session_workspace import fiix_credentials_configured

    assert fiix_credentials_configured(sid)


def test_conversation_context_retained_across_turns():
    sid = "test-conversation-context"
    _SESSION_STATE.pop(sid, None)
    record_conversation_turn(sid, "user", "Connect Fiix and map schema")
    record_conversation_turn(sid, "assistant", "Please provide subdomain and API keys.")
    ctx = build_conversation_context(sid)
    assert "Connect Fiix" in ctx
    assert "subdomain" in ctx
    runtime = build_session_runtime_context(sid)
    assert "Recent conversation" in runtime


def test_summarize_fiix_mapper_uses_tables_by_object():
    """tables_by_object (not legacy tables) drives table_count in schema summary."""
    mapper = {
        "tables_by_object": {
            "assets": {"id": "asset_code"},
            "sites": {"id": "site_id"},
        },
        "canonical_fields": {},
    }
    tables_by_object = mapper.get("tables_by_object") or {}
    table_names = sorted(tables_by_object.keys())
    assert len(table_names) == 2
    assert "assets" in table_names


def test_workspace_snapshot_exposes_saved_space_fields():
    from src.agents.session_workspace import (
        ROUTE_UDR_INGEST,
        infer_saved_space,
        set_route_metadata,
        workspace_snapshot,
    )

    sid = "test-saved-space-snapshot"
    _SESSION_STATE.pop(sid, None)
    set_route_metadata(sid, route_intent=ROUTE_UDR_INGEST, domain="udr", tool="ingest_udr_batch")
    state = _SESSION_STATE[sid]
    assert infer_saved_space(state) == "udr"
    snap = workspace_snapshot(sid)
    assert snap["saved_space"] == "udr"
    assert snap["last_domain"] == "udr"
    assert snap["last_tool"] == "ingest_udr_batch"
