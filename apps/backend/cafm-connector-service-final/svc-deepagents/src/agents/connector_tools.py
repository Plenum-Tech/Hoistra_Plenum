"""Phase 6 — orchestrator tools for source connector registry."""
from __future__ import annotations

from langchain_core.tools import tool

from ..integrations.source_connectors import list_source_connectors, test_source_connector


@tool
async def list_available_source_connectors() -> dict:
    """List pluggable ingestion sources (file, Fiix, Azure Blob, Tencent COS) and whether each is configured."""
    return {"connectors": list_source_connectors()}


@tool
async def test_source_connector_connection(source_type: str) -> dict:
    """Test connectivity for a registered source connector (fiix, file_upload, azure_blob, tencent_cos)."""
    return await test_source_connector(source_type)
