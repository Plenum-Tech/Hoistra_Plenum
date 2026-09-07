"""Unit tests — A1 lifecycle status ladder."""
from datetime import date, timedelta

from src.engines.compliance.lifecycle import (
    STATUS_CRITICAL,
    STATUS_CURRENT,
    STATUS_DUE_FOR_RENEWAL,
    STATUS_EXPIRING_SOON,
    STATUS_LAPSED,
    STATUS_OVERDUE,
    compute_certificate_status,
    vendor_risk_level,
)


def test_status_ladder():
    today = date(2026, 7, 27)
    assert compute_certificate_status(today + timedelta(days=120), today=today).status == STATUS_CURRENT
    assert compute_certificate_status(today + timedelta(days=75), today=today).status == STATUS_EXPIRING_SOON
    assert compute_certificate_status(today + timedelta(days=45), today=today).status == STATUS_DUE_FOR_RENEWAL
    assert compute_certificate_status(today + timedelta(days=15), today=today).status == STATUS_OVERDUE
    assert compute_certificate_status(today + timedelta(days=5), today=today).status == STATUS_CRITICAL
    assert compute_certificate_status(today - timedelta(days=1), today=today).status == STATUS_LAPSED


def test_lapsed_sets_insurance_risk():
    today = date(2026, 7, 27)
    life = compute_certificate_status(today - timedelta(days=2), today=today)
    assert life.status == STATUS_LAPSED
    assert life.insurance_risk_flag is True
    assert life.alert_meta.get("queue") is True


def test_critical_does_not_set_insurance_risk():
    today = date(2026, 7, 27)
    life = compute_certificate_status(today + timedelta(days=3), today=today)
    assert life.status == STATUS_CRITICAL
    assert life.insurance_risk_flag is False
    assert life.alert_meta.get("escalate_senior") is True


def test_vendor_risk_levels():
    assert vendor_risk_level(120) == "Clear"
    assert vendor_risk_level(60) == "Medium Risk"
    assert vendor_risk_level(20) == "High Risk"
    assert vendor_risk_level(0) == "Lapsed"
    assert vendor_risk_level(-3) == "Lapsed"


def test_uk_pack_json_loads():
    from src.engines.compliance.country_pack import load_pack_json

    pack = load_pack_json()
    assert pack["country_code"] == "UK"
    assert pack["pack_version"] == "1.1"
    assert len(pack["certificate_types"]) >= 47
    scopes = {t["certificate_scope"] for t in pack["certificate_types"]}
    assert scopes == {"Building", "Vendor"}
