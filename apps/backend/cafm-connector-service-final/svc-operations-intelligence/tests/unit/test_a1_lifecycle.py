"""
A1 — Certificate Lifecycle Infrastructure tests.

Covers: status ladder thresholds, days_to_expiry, alert meta, insurance risk,
configurable thresholds, Not on record.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.engines.compliance.lifecycle import (
    DEFAULT_THRESHOLDS,
    STATUS_CRITICAL,
    STATUS_CURRENT,
    STATUS_DUE_FOR_RENEWAL,
    STATUS_EXPIRING_SOON,
    STATUS_LAPSED,
    STATUS_NOT_ON_RECORD,
    STATUS_OVERDUE,
    compute_certificate_status,
    days_until,
)


class TestA1StatusLadder:
    def test_current_gt_90(self, today: date):
        r = compute_certificate_status(today + timedelta(days=91), today=today)
        assert r.status == STATUS_CURRENT
        assert r.days_to_expiry == 91
        assert r.insurance_risk_flag is False

    def test_expiring_soon_61_to_90(self, today: date):
        for d in (61, 75, 90):
            r = compute_certificate_status(today + timedelta(days=d), today=today)
            assert r.status == STATUS_EXPIRING_SOON, f"days={d}"
            assert r.alert_meta.get("channel") == "email"
            assert r.alert_meta.get("queue") is False

    def test_due_for_renewal_31_to_60(self, today: date):
        for d in (31, 45, 60):
            r = compute_certificate_status(today + timedelta(days=d), today=today)
            assert r.status == STATUS_DUE_FOR_RENEWAL, f"days={d}"
            assert r.alert_meta.get("booking_prompt") is True
            assert r.alert_meta.get("platform_notification") is True

    def test_overdue_8_to_30(self, today: date):
        for d in (8, 15, 30):
            r = compute_certificate_status(today + timedelta(days=d), today=today)
            assert r.status == STATUS_OVERDUE, f"days={d}"
            assert r.alert_meta.get("queue") is True
            assert r.alert_meta.get("booking_draft") is True

    def test_critical_0_to_7(self, today: date):
        for d in (7, 3, 0):
            r = compute_certificate_status(today + timedelta(days=d), today=today)
            assert r.status == STATUS_CRITICAL, f"days={d}"
            assert r.insurance_risk_flag is False
            assert r.alert_meta.get("escalate_senior") is True
            assert r.alert_meta.get("severity") == "Critical"

    def test_lapsed_past_expiry_only(self, today: date):
        for d in (-1, -30):
            r = compute_certificate_status(today + timedelta(days=d), today=today)
            assert r.status == STATUS_LAPSED, f"days={d}"
            assert r.insurance_risk_flag is True
            assert r.alert_meta.get("escalate_senior") is True


class TestA1Helpers:
    def test_days_until_none(self):
        assert days_until(None) is None

    def test_not_on_record_when_no_expiry(self):
        r = compute_certificate_status(None)
        assert r.status == STATUS_NOT_ON_RECORD
        assert r.days_to_expiry is None

    def test_custom_thresholds_override(self, today: date):
        # Widen critical window to ≤14 days
        th = {
            **DEFAULT_THRESHOLDS,
            "critical_max": 14,
            "overdue_min": 15,
            "overdue_max": 30,
        }
        r = compute_certificate_status(today + timedelta(days=10), today=today, thresholds=th)
        assert r.status == STATUS_CRITICAL

    def test_legacy_lapsed_lte_maps_to_critical(self, today: date):
        # Old pack rows only stored lapsed_lte (no critical_max)
        th = {"lapsed_lte": 14, "overdue_min": 15, "overdue_max": 30}
        r = compute_certificate_status(today + timedelta(days=10), today=today, thresholds=th)
        assert r.status == STATUS_CRITICAL

    def test_boundary_day_90_is_expiring_soon(self, today: date):
        assert (
            compute_certificate_status(today + timedelta(days=90), today=today).status
            == STATUS_EXPIRING_SOON
        )

    def test_boundary_day_91_is_current(self, today: date):
        assert (
            compute_certificate_status(today + timedelta(days=91), today=today).status
            == STATUS_CURRENT
        )
