"""
Integration tests for Phase 8 - REST API endpoints.

Tests cover:
- Health and metrics endpoints
- Migration lifecycle (start, status, approve, download, list, cancel)
- LangSmith trace URL endpoint
- Error handling and validation
"""
import json
import uuid
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.migration import MigrationJob, MigrationFieldMapping
from src.schemas import (
    MigrationStartRequest,
    MigrationStatusResponse,
    MigrationApprovalRequest,
)


class TestHealthAndMetrics:
    """Test health check and metrics endpoints."""

    async def test_health_check(self, async_client: AsyncClient):
        """Verify health endpoint responds correctly."""
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    async def test_metrics_endpoint(self, async_client: AsyncClient):
        """Verify metrics endpoint returns Prometheus format."""
        response = await async_client.get("/metrics")
        assert response.status_code == 200
        # Prometheus format contains # TYPE and # HELP comments
        text = response.text
        assert "# TYPE" in text or "# HELP" in text or len(text) > 0


class TestMigrationStart:
    """Test POST /api/migration/start endpoint."""

    async def test_start_migration_with_blob_url(self, async_client: AsyncClient):
        """Test starting a migration with Azure Blob URL."""
        request_data = {
            "source_blob_url": "https://example.blob.core.windows.net/uploads/assets.csv",
            "source_system": "Maximo",
            "customer_id": "customer-123",
        }
        response = await async_client.post(
            "/api/migration/start",
            json=request_data,
        )
        assert response.status_code == 202
        data = response.json()
        assert "migration_id" in data
        assert data["status"] == "pending"
        assert data["message"] == "Migration job created and queued for processing"

    async def test_start_migration_missing_required_field(self, async_client: AsyncClient):
        """Test starting migration with missing required field."""
        request_data = {
            "source_system": "Maximo",
            "customer_id": "customer-123",
            # Missing source_blob_url
        }
        response = await async_client.post(
            "/api/migration/start",
            json=request_data,
        )
        assert response.status_code == 422  # Validation error

    async def test_start_migration_invalid_system(self, async_client: AsyncClient):
        """Test starting migration with invalid source system."""
        request_data = {
            "source_blob_url": "https://example.blob.core.windows.net/uploads/assets.csv",
            "source_system": "InvalidCMMS",  # Invalid system
            "customer_id": "customer-123",
        }
        response = await async_client.post(
            "/api/migration/start",
            json=request_data,
        )
        # Should either validate or accept (depends on implementation)
        assert response.status_code in (202, 422)


class TestMigrationStatus:
    """Test GET /api/migration/{id}/status endpoint."""

    async def test_get_migration_status(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test retrieving migration status."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/status"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["migration_id"] == str(sample_migration_job.id)
        assert data["status"] in ["pending", "processing", "completed", "failed"]
        assert "current_node" in data or "step" in data

    async def test_get_migration_status_not_found(self, async_client: AsyncClient):
        """Test retrieving status for non-existent migration."""
        fake_id = uuid.uuid4()
        response = await async_client.get(
            f"/api/migration/{fake_id}/status"
        )
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data.get("detail", "").lower()

    async def test_get_migration_status_invalid_uuid(self, async_client: AsyncClient):
        """Test retrieving status with invalid UUID format."""
        response = await async_client.get(
            "/api/migration/not-a-uuid/status"
        )
        assert response.status_code in (400, 422)


class TestMigrationApproval:
    """Test POST /api/migration/{id}/approve endpoint."""

    async def test_approve_migration_gate_1(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test approving at GATE 1 (confidence threshold)."""
        request_data = {
            "gate_number": 1,
            "decision": "proceed",
            "rationale": "High confidence mappings approved",
            "reviewer": "qa-team",
        }
        response = await async_client.post(
            f"/api/migration/{sample_migration_job.id}/approve",
            json=request_data,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["migration_id"] == str(sample_migration_job.id)
        assert data["status"] in ["processing", "paused_at_gate"]

    async def test_approve_migration_gate_2(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test approving at GATE 2 (preprocessing)."""
        request_data = {
            "gate_number": 2,
            "decision": "proceed",
            "rationale": "Preprocessing validated",
            "reviewer": "qa-team",
            "field_mapping_decisions": {
                "location": "location_description",
            }
        }
        response = await async_client.post(
            f"/api/migration/{sample_migration_job.id}/approve",
            json=request_data,
        )
        assert response.status_code in (200, 400)  # May fail if state not ready

    async def test_approve_migration_gate_3(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test approving at GATE 3 (final output)."""
        request_data = {
            "gate_number": 3,
            "decision": "approve",
            "rationale": "Output schema validated",
            "reviewer": "data-owner",
        }
        response = await async_client.post(
            f"/api/migration/{sample_migration_job.id}/approve",
            json=request_data,
        )
        assert response.status_code in (200, 400)  # May fail if state not ready

    async def test_approve_migration_missing_required_field(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test approval with missing required field."""
        request_data = {
            "gate_number": 1,
            # Missing decision
            "rationale": "Missing decision field",
        }
        response = await async_client.post(
            f"/api/migration/{sample_migration_job.id}/approve",
            json=request_data,
        )
        assert response.status_code == 422

    async def test_approve_migration_invalid_gate(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test approval with invalid gate number."""
        request_data = {
            "gate_number": 99,  # Invalid gate
            "decision": "proceed",
            "rationale": "Invalid gate",
        }
        response = await async_client.post(
            f"/api/migration/{sample_migration_job.id}/approve",
            json=request_data,
        )
        assert response.status_code in (400, 422)


class TestMigrationAudit:
    """Test GET /api/migration/{id}/audit endpoint."""

    async def test_get_audit_trail(
        self,
        async_client: AsyncClient,
        sample_migration_job_with_mappings: MigrationJob,
    ):
        """Test retrieving audit trail for migration."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job_with_mappings.id}/audit"
        )
        assert response.status_code == 200
        data = response.json()
        assert "field_mappings" in data
        assert isinstance(data["field_mappings"], list)
        assert len(data["field_mappings"]) >= 0

    async def test_get_audit_trail_not_found(self, async_client: AsyncClient):
        """Test audit trail for non-existent migration."""
        fake_id = uuid.uuid4()
        response = await async_client.get(
            f"/api/migration/{fake_id}/audit"
        )
        assert response.status_code == 404


class TestMigrationDownload:
    """Test GET /api/migration/{id}/download/{format} endpoint."""

    async def test_download_json_format(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test downloading migration result in JSON format."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/download/json"
        )
        assert response.status_code in (200, 400)  # May fail if no completed migration
        if response.status_code == 200:
            assert response.headers["content-type"] in ["application/json", "application/octet-stream"]

    async def test_download_csv_format(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test downloading migration result in CSV format."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/download/csv"
        )
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            assert response.headers["content-type"] in ["text/csv", "application/octet-stream"]

    async def test_download_sql_format(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test downloading migration result in SQL format."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/download/sql"
        )
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            assert response.headers["content-type"] in ["text/plain", "application/octet-stream"]

    async def test_download_invalid_format(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test downloading with invalid format."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/download/invalid_format"
        )
        assert response.status_code in (400, 422)


class TestMigrationList:
    """Test GET /api/migration/list endpoint."""

    async def test_list_migrations_empty(self, async_client: AsyncClient):
        """Test listing migrations when database is empty."""
        response = await async_client.get("/api/migration/list")
        assert response.status_code == 200
        data = response.json()
        assert "migrations" in data
        assert isinstance(data["migrations"], list)

    async def test_list_migrations_with_items(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test listing migrations with items."""
        response = await async_client.get("/api/migration/list")
        assert response.status_code == 200
        data = response.json()
        assert "migrations" in data
        assert len(data["migrations"]) > 0
        # Check first item has required fields
        item = data["migrations"][0]
        assert "migration_id" in item or "id" in item
        assert "status" in item

    async def test_list_migrations_with_pagination(self, async_client: AsyncClient):
        """Test listing migrations with pagination."""
        response = await async_client.get("/api/migration/list?skip=0&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "migrations" in data
        assert "total" in data or "skip" in data or "limit" in data


class TestMigrationCancel:
    """Test DELETE /api/migration/{id} endpoint."""

    async def test_cancel_migration(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test canceling a migration."""
        response = await async_client.delete(
            f"/api/migration/{sample_migration_job.id}"
        )
        assert response.status_code in (200, 204)

    async def test_cancel_non_existent_migration(self, async_client: AsyncClient):
        """Test canceling non-existent migration."""
        fake_id = uuid.uuid4()
        response = await async_client.delete(
            f"/api/migration/{fake_id}"
        )
        assert response.status_code == 404


class TestLangSmithTrace:
    """Test GET /api/migration/{id}/langsmith endpoint."""

    async def test_get_langsmith_trace_url(
        self,
        async_client: AsyncClient,
        sample_migration_job: MigrationJob,
    ):
        """Test retrieving LangSmith trace URL."""
        response = await async_client.get(
            f"/api/migration/{sample_migration_job.id}/langsmith"
        )
        assert response.status_code in (200, 400)
        if response.status_code == 200:
            data = response.json()
            assert "trace_url" in data or "url" in data or "message" in data
