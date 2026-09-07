"""
BE1-15 Integration tests — Work Order CRUD + status machine.

Covers: create, get, list, filter, approve, prepare, status transitions,
close, and the full pending→preparing→prepared→active→completed→closed
lifecycle through the real FastAPI routes backed by SQLite in-memory.
"""
import pytest
from httpx import AsyncClient


_BASE_PAYLOAD = {
    "source": "manual",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Compressor making unusual noise",
    "priority": "high",
    "request_type": "repair",
    "requester_name": "John Smith",
    "requester_email": "john.smith@example.com",
    "requester_phone": "+971501234567",
}


# ── helpers ───────────────────────────────────────────────────────────────────

async def _create(client: AsyncClient, overrides: dict | None = None) -> dict:
    payload = {**_BASE_PAYLOAD, **(overrides or {})}
    resp = await client.post("/api/work-orders/", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreate:
    async def test_status_is_pending_approval(self, http_client: AsyncClient):
        data = await _create(http_client)
        assert data["status"] == "pending_approval"

    async def test_work_order_id_has_wo_prefix(self, http_client: AsyncClient):
        data = await _create(http_client)
        assert data["work_order_id"].startswith("WO-")

    async def test_fields_are_persisted(self, http_client: AsyncClient):
        data = await _create(http_client)
        assert data["asset"] == "AHU-001"
        assert data["priority"] == "high"
        assert data["source"] == "manual"

    async def test_missing_required_field_returns_422(self, http_client: AsyncClient):
        payload = {k: v for k, v in _BASE_PAYLOAD.items() if k != "asset"}
        resp = await http_client.post("/api/work-orders/", json=payload)
        assert resp.status_code == 422

    async def test_blank_asset_returns_422(self, http_client: AsyncClient):
        resp = await http_client.post("/api/work-orders/", json={**_BASE_PAYLOAD, "asset": "  "})
        assert resp.status_code == 422

    async def test_invalid_priority_returns_422(self, http_client: AsyncClient):
        resp = await http_client.post("/api/work-orders/", json={**_BASE_PAYLOAD, "priority": "extreme"})
        assert resp.status_code == 422

    async def test_invalid_source_returns_422(self, http_client: AsyncClient):
        resp = await http_client.post("/api/work-orders/", json={**_BASE_PAYLOAD, "source": "fax"})
        assert resp.status_code == 422

    async def test_invalid_email_returns_422(self, http_client: AsyncClient):
        resp = await http_client.post(
            "/api/work-orders/", json={**_BASE_PAYLOAD, "requester_email": "not-an-email"}
        )
        assert resp.status_code == 422

    async def test_error_response_has_errors_key(self, http_client: AsyncClient):
        resp = await http_client.post("/api/work-orders/", json={**_BASE_PAYLOAD, "priority": "bad"})
        assert resp.status_code == 422
        assert "errors" in resp.json()


# ── Get / List ────────────────────────────────────────────────────────────────

class TestGet:
    async def test_get_by_id_returns_200(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.get(f"/api/work-orders/{wo_id}")
        assert resp.status_code == 200
        assert resp.json()["work_order_id"] == wo_id

    async def test_unknown_id_returns_404(self, http_client: AsyncClient):
        resp = await http_client.get("/api/work-orders/WO-DOES-NOT-EXIST")
        assert resp.status_code == 404

    async def test_404_error_code(self, http_client: AsyncClient):
        resp = await http_client.get("/api/work-orders/WO-DOES-NOT-EXIST")
        assert resp.json()["errors"][0]["code"] == "work_order_not_found"


class TestList:
    async def test_returns_list(self, http_client: AsyncClient):
        await _create(http_client)
        resp = await http_client.get("/api/work-orders/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_filter_by_status(self, http_client: AsyncClient):
        await _create(http_client)
        resp = await http_client.get("/api/work-orders/?status=pending_approval")
        assert resp.status_code == 200
        for wo in resp.json():
            assert wo["status"] == "pending_approval"

    async def test_filter_by_priority(self, http_client: AsyncClient):
        await _create(http_client, {"priority": "urgent"})
        resp = await http_client.get("/api/work-orders/?priority=urgent")
        assert resp.status_code == 200
        assert all(wo["priority"] == "urgent" for wo in resp.json())

    async def test_filter_active_endpoint(self, http_client: AsyncClient):
        resp = await http_client.get("/api/work-orders/filter/active")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_filter_pending_endpoint(self, http_client: AsyncClient):
        await _create(http_client)
        resp = await http_client.get("/api/work-orders/filter/pending-approval")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


# ── Approve ───────────────────────────────────────────────────────────────────

class TestApprove:
    async def test_approve_transitions_to_preparing(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.post(f"/api/work-orders/{wo_id}/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "preparing"

    async def test_approve_stamps_approved_at(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.post(f"/api/work-orders/{wo_id}/approve")
        assert resp.json()["approved_at"] is not None

    async def test_double_approve_returns_409(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")
        resp = await http_client.post(f"/api/work-orders/{wo_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["errors"][0]["code"] == "approval_not_pending"

    async def test_approve_unknown_id_returns_404(self, http_client: AsyncClient):
        resp = await http_client.post("/api/work-orders/WO-GHOST/approve")
        assert resp.status_code == 404


# ── Status transitions ────────────────────────────────────────────────────────

class TestStatusTransitions:
    async def test_preparing_to_prepared(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")  # → preparing
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "prepared"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "prepared"

    async def test_prepared_to_active(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")
        await http_client.patch(f"/api/work-orders/{wo_id}/status", json={"new_status": "prepared"})
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "active"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_active_to_completed(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")
        await http_client.patch(f"/api/work-orders/{wo_id}/status", json={"new_status": "prepared"})
        await http_client.patch(f"/api/work-orders/{wo_id}/status", json={"new_status": "active"})
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "completed"}
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    async def test_invalid_skip_to_active_returns_422(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "active"}
        )
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["code"] == "invalid_status_transition"

    async def test_closed_is_terminal(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/close")
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "preparing"}
        )
        assert resp.status_code == 422

    async def test_invalid_status_value_returns_422(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "flying"}
        )
        assert resp.status_code == 422


# ── Close ─────────────────────────────────────────────────────────────────────

class TestClose:
    async def test_close_from_pending(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.post(f"/api/work-orders/{wo_id}/close")
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_close_already_closed_returns_409(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/close")
        resp = await http_client.post(f"/api/work-orders/{wo_id}/close")
        assert resp.status_code == 409
        assert resp.json()["errors"][0]["code"] == "work_order_closed"


# ── Prepare endpoint ──────────────────────────────────────────────────────────

class TestPrepareEndpoint:
    async def test_prepare_transitions_to_prepared(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")
        resp = await http_client.post(
            f"/api/work-orders/{wo_id}/prepare",
            json={"vendor": "TechServ LLC", "scheduled_date": "2026-05-10"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "prepared"

    async def test_prepare_persists_vendor(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")
        resp = await http_client.post(
            f"/api/work-orders/{wo_id}/prepare",
            json={"vendor": "HVAC Pro Ltd", "estimated_duration": 3.0},
        )
        data = resp.json()
        assert data["vendor"] == "HVAC Pro Ltd"
        assert data["prepared_at"] is not None


# ── Update (PATCH) ────────────────────────────────────────────────────────────

class TestUpdate:
    async def test_patch_mutable_fields(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}",
            json={"vendor": "Acme Services", "scheduled_date": "2026-06-01"},
        )
        assert resp.status_code == 200
        assert resp.json()["vendor"] == "Acme Services"

    async def test_patch_invalid_duration_returns_422(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        resp = await http_client.patch(
            f"/api/work-orders/{wo_id}", json={"estimated_duration": -5.0}
        )
        assert resp.status_code == 422


# ── Full lifecycle ────────────────────────────────────────────────────────────

class TestFullLifecycle:
    async def test_pending_to_closed(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]

        await http_client.post(f"/api/work-orders/{wo_id}/approve")

        await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "prepared"}
        )
        await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "active"}
        )
        await http_client.patch(
            f"/api/work-orders/{wo_id}/status", json={"new_status": "completed"}
        )

        resp = await http_client.post(f"/api/work-orders/{wo_id}/close")
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_get_reflects_latest_status(self, http_client: AsyncClient):
        wo_id = (await _create(http_client))["work_order_id"]
        await http_client.post(f"/api/work-orders/{wo_id}/approve")

        resp = await http_client.get(f"/api/work-orders/{wo_id}")
        assert resp.json()["status"] == "preparing"

    async def test_health_endpoint(self, http_client: AsyncClient):
        resp = await http_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
