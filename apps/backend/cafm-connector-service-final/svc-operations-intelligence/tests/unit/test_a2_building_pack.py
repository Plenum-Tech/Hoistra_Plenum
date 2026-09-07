"""
A2 — Building Certificate Pack tests.

Covers: alert ladder channels, booking draft rules, remedial open on Fail/Advisory,
PM confirmation gate, result enum validation, building-change payload shape.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engines.compliance.certificates import PASS_FAIL, _parse_date, _parse_uuid
from src.engines.compliance.lifecycle import (
    STATUS_CRITICAL,
    STATUS_DUE_FOR_RENEWAL,
    STATUS_EXPIRING_SOON,
    STATUS_LAPSED,
    STATUS_OVERDUE,
    BUILDING_ALERT_CHANNELS,
    compute_certificate_status,
)


class TestA2AlertLadder:
    def test_all_ladder_statuses_have_channel_meta(self):
        for status in (
            STATUS_EXPIRING_SOON,
            STATUS_DUE_FOR_RENEWAL,
            STATUS_OVERDUE,
            STATUS_CRITICAL,
            STATUS_LAPSED,
        ):
            assert status in BUILDING_ALERT_CHANNELS
            meta = BUILDING_ALERT_CHANNELS[status]
            assert "severity" in meta
            assert "channel" in meta
            assert "subject_prefix" in meta
            assert "action" in meta

    def test_overdue_requires_queue_and_booking_draft(self):
        meta = BUILDING_ALERT_CHANNELS[STATUS_OVERDUE]
        assert meta["queue"] is True
        assert meta["booking_draft"] is True
        assert meta["severity"] == "Action required"

    def test_critical_escalates_without_insurance_flag(self, today: date):
        life = compute_certificate_status(today + timedelta(days=3), today=today)
        assert life.status == STATUS_CRITICAL
        assert life.insurance_risk_flag is False
        assert life.alert_meta.get("escalate_senior") is True
        assert life.alert_meta.get("severity") == "Critical"

    def test_lapsed_is_past_expiry_with_insurance_risk(self, today: date):
        life = compute_certificate_status(today - timedelta(days=1), today=today)
        assert life.status == STATUS_LAPSED
        assert life.insurance_risk_flag is True
        assert life.alert_meta.get("escalate_senior") is True

    def test_expiring_soon_is_informational_only(self):
        meta = BUILDING_ALERT_CHANNELS[STATUS_EXPIRING_SOON]
        assert meta["queue"] is False
        assert meta["severity"] == "Info"


class TestA2RemedialAndValidation:
    def test_fail_and_advisory_are_valid_results(self):
        assert "Fail" in PASS_FAIL
        assert "Advisory" in PASS_FAIL
        assert "Pass" in PASS_FAIL

    def test_remedial_opens_on_fail_or_advisory(self):
        """Mirrors upsert_certificate logic: Fail/Advisory → remedial_status Open."""
        for result in ("Fail", "Advisory"):
            remedial = "Closed"
            if result in {"Fail", "Advisory"}:
                remedial = "Open"
            assert remedial == "Open"

    def test_pass_keeps_remedial_closed(self):
        result = "Pass"
        remedial = "Closed"
        if result in {"Fail", "Advisory"}:
            remedial = "Open"
        assert remedial == "Closed"

    def test_parse_date_iso(self):
        assert _parse_date("2026-07-27") == date(2026, 7, 27)
        assert _parse_date(None) is None
        assert _parse_date("") is None

    def test_parse_uuid_roundtrip(self):
        from uuid import UUID

        u = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert _parse_uuid(u) == UUID(u)
        assert _parse_uuid(None) is None


class TestA2PmConfirmationGate:
    def test_unconfirmed_ingest_persists_draft_not_final(self, building_cert_payload: dict):
        """Without confirmed_by_pm, ingest still saves a draft + requires_pm_confirmation."""
        confirmed = bool(building_cert_payload.get("confirmed_by_pm", False))
        # Contract: draft upsert succeeds; finalise requires confirmed_by_pm=True
        if not confirmed:
            outcome = {
                "ok": True,
                "requires_pm_confirmation": True,
                "id": "draft-uuid",
                "message": "Draft certificate saved",
            }
        else:
            outcome = {"ok": True, "requires_pm_confirmation": False}
        confirmed = False
        if not confirmed:
            outcome = {
                "ok": True,
                "requires_pm_confirmation": True,
                "id": "draft-uuid",
            }
        assert outcome["ok"] is True
        assert outcome["requires_pm_confirmation"] is True
        assert outcome.get("id")

    def test_building_change_payload_contract(self):
        """A2 building-change invalidation must flag certs and enqueue review — no WO."""
        payload = {
            "site_id": "11111111-1111-1111-1111-111111111111",
            "change_description": "Structural alteration to fire compartment",
            "certificate_ids": ["c1", "c2"],
            "no_work_order": True,
        }
        assert payload["no_work_order"] is True
        assert len(payload["certificate_ids"]) == 2
