"""
BE1-15 Unit tests — Pydantic schema validation.

Validates all Literal enums, EmailStr, blank-field guards,
and duration constraints without touching the database or network.
"""
import pytest
from pydantic import ValidationError

from src.api.schemas.work_order import WorkOrderCreate, WorkOrderUpdate, StatusUpdate


_VALID = {
    "source": "manual",
    "asset": "AHU-001",
    "location": "Level 3 Plant Room",
    "issue_description": "Unusual noise from compressor",
    "requester_name": "Alice Chen",
    "requester_email": "alice.chen@example.com",
}


# ── WorkOrderCreate ───────────────────────────────────────────────────────────

class TestWorkOrderCreate:
    def test_valid_payload_passes(self):
        wo = WorkOrderCreate(**_VALID)
        assert wo.asset == "AHU-001"

    def test_defaults_applied(self):
        wo = WorkOrderCreate(**_VALID)
        assert wo.priority == "medium"
        assert wo.request_type == "repair"

    def test_phone_is_optional(self):
        wo = WorkOrderCreate(**_VALID)
        assert wo.requester_phone is None

    # ── blank-field guard ─────────────────────────────────────────────────────

    def test_blank_asset_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "asset": "   "})

    def test_empty_asset_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "asset": ""})

    def test_blank_location_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "location": "  "})

    def test_blank_description_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "issue_description": "\t"})

    def test_blank_requester_name_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "requester_name": ""})

    def test_whitespace_stripped_from_fields(self):
        wo = WorkOrderCreate(**{**_VALID, "asset": "  AHU-001  ", "location": " L3 "})
        assert wo.asset == "AHU-001"
        assert wo.location == "L3"

    # ── email ─────────────────────────────────────────────────────────────────

    def test_invalid_email_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "requester_email": "not-an-email"})

    def test_email_without_domain_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "requester_email": "alice@"})

    # ── priority literal ──────────────────────────────────────────────────────

    def test_all_valid_priorities(self):
        for p in ("low", "medium", "high", "urgent", "critical"):
            wo = WorkOrderCreate(**{**_VALID, "priority": p})
            assert wo.priority == p

    def test_invalid_priority_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "priority": "extreme"})

    def test_priority_case_sensitive(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "priority": "High"})

    # ── source literal ────────────────────────────────────────────────────────

    def test_all_valid_sources(self):
        for s in ("email", "ppm", "manual", "tenant", "internal", "remediation"):
            wo = WorkOrderCreate(**{**_VALID, "source": s})
            assert wo.source == s

    def test_invalid_source_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "source": "fax"})

    # ── request_type literal ──────────────────────────────────────────────────

    def test_all_valid_request_types(self):
        for rt in ("repair", "maintenance", "inspection", "installation"):
            wo = WorkOrderCreate(**{**_VALID, "request_type": rt})
            assert wo.request_type == rt

    def test_invalid_request_type_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderCreate(**{**_VALID, "request_type": "demolition"})


# ── WorkOrderUpdate ───────────────────────────────────────────────────────────

class TestWorkOrderUpdate:
    def test_empty_update_is_valid(self):
        update = WorkOrderUpdate()
        assert update.vendor is None
        assert update.estimated_duration is None

    def test_all_fields_optional(self):
        update = WorkOrderUpdate(vendor="TechServ", scheduled_date="2026-05-10")
        assert update.vendor == "TechServ"
        assert update.scheduled_date == "2026-05-10"

    def test_positive_duration_passes(self):
        update = WorkOrderUpdate(estimated_duration=4.5)
        assert update.estimated_duration == 4.5

    def test_zero_duration_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderUpdate(estimated_duration=0.0)

    def test_negative_duration_raises(self):
        with pytest.raises(ValidationError):
            WorkOrderUpdate(estimated_duration=-1.0)

    def test_very_small_positive_duration_passes(self):
        update = WorkOrderUpdate(estimated_duration=0.1)
        assert update.estimated_duration == 0.1


# ── StatusUpdate ──────────────────────────────────────────────────────────────

class TestStatusUpdate:
    def test_all_valid_statuses(self):
        for s in ("pending_approval", "preparing", "prepared", "active", "completed", "closed"):
            su = StatusUpdate(new_status=s)
            assert su.new_status == s

    def test_notes_is_optional(self):
        su = StatusUpdate(new_status="preparing")
        assert su.notes is None

    def test_notes_accepted(self):
        su = StatusUpdate(new_status="closed", notes="Resolved by vendor")
        assert su.notes == "Resolved by vendor"

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            StatusUpdate(new_status="flying")

    def test_empty_status_raises(self):
        with pytest.raises(ValidationError):
            StatusUpdate(new_status="")
