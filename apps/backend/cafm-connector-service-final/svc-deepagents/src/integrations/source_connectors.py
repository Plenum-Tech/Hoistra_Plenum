"""
Phase 6 — Pluggable source connector registry (cloud + CMMS file/API).

Orchestrator tools call this layer; drivers are configured via env, not hardcoded in prompts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

from ..config import settings

log = structlog.get_logger(__name__)


@dataclass
class ConnectorDescriptor:
    source_type: str
    display_name: str
    ingestion_strategy: str  # file_upload | live_api | object_storage
    configured: bool
    notes: str = ""


class SourceConnector(ABC):
    source_type: str = "generic"

    @abstractmethod
    def describe(self) -> ConnectorDescriptor:
        ...

    @abstractmethod
    async def test_connection(self) -> dict[str, Any]:
        ...


class FiixLiveConnector(SourceConnector):
    source_type = "fiix"

    def describe(self) -> ConnectorDescriptor:
        return ConnectorDescriptor(
            source_type="fiix",
            display_name="Fiix CMMS (live API)",
            ingestion_strategy="live_api",
            configured=True,
            notes="Use start_fiix_ingestion tool; credentials on schema-mapper.",
        )

    async def test_connection(self) -> dict[str, Any]:
        from ..agents.fiix_agent import test_fiix_connection

        return await test_fiix_connection.ainvoke({})


class FileUploadConnector(SourceConnector):
    source_type = "file_upload"

    def describe(self) -> ConnectorDescriptor:
        return ConnectorDescriptor(
            source_type="file_upload",
            display_name="CSV / Excel / PDF / images",
            ingestion_strategy="file_upload",
            configured=True,
            notes="Single-door /api/workflow/run-stateful-with-files",
        )

    async def test_connection(self) -> dict[str, Any]:
        return {"status": "ok", "message": "File upload path always available"}


class AzureBlobConnector(SourceConnector):
    source_type = "azure_blob"

    def describe(self) -> ConnectorDescriptor:
        configured = bool(
            getattr(settings, "azure_storage_connection_string", "")
            or getattr(settings, "azure_storage_account", "")
        )
        return ConnectorDescriptor(
            source_type="azure_blob",
            display_name="Azure Blob Storage",
            ingestion_strategy="object_storage",
            configured=configured,
            notes="Set AZURE_STORAGE_CONNECTION_STRING or account/key in svc-deepagents env.",
        )

    async def test_connection(self) -> dict[str, Any]:
        if not self.describe().configured:
            return {
                "status": "not_configured",
                "message": "Azure Blob connector not configured in environment.",
            }
        return {"status": "ok", "message": "Azure Blob credentials present (ingest via future driver)."}


class TencentCosConnector(SourceConnector):
    source_type = "tencent_cos"

    def describe(self) -> ConnectorDescriptor:
        configured = bool(getattr(settings, "tencent_cos_secret_id", ""))
        return ConnectorDescriptor(
            source_type="tencent_cos",
            display_name="Tencent Cloud COS",
            ingestion_strategy="object_storage",
            configured=configured,
            notes="Set TENCENT_COS_SECRET_ID / TENCENT_COS_SECRET_KEY when enabling object ingest.",
        )

    async def test_connection(self) -> dict[str, Any]:
        if not self.describe().configured:
            return {
                "status": "not_configured",
                "message": "Tencent COS connector not configured.",
            }
        return {"status": "ok", "message": "Tencent COS credentials present (ingest via future driver)."}


_REGISTRY: dict[str, SourceConnector] = {
    "fiix": FiixLiveConnector(),
    "file_upload": FileUploadConnector(),
    "azure_blob": AzureBlobConnector(),
    "tencent_cos": TencentCosConnector(),
}


def list_source_connectors() -> list[dict[str, Any]]:
    return [
        {
            "source_type": c.source_type,
            "display_name": c.describe().display_name,
            "ingestion_strategy": c.describe().ingestion_strategy,
            "configured": c.describe().configured,
            "notes": c.describe().notes,
        }
        for c in _REGISTRY.values()
    ]


async def test_source_connector(source_type: str) -> dict[str, Any]:
    conn = _REGISTRY.get(source_type)
    if not conn:
        return {"error": f"Unknown source_type: {source_type}", "available": list(_REGISTRY)}
    return await conn.test_connection()
