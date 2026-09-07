"""Unit tests for deterministic per-vendor certificate aggregation."""
from __future__ import annotations

import pytest

from src.engines.compliance import certificates


@pytest.mark.asyncio
async def test_more_than_two_excludes_vendor_holding_exactly_two(monkeypatch):
    async def fake_list_certificates(*_args, **_kwargs):
        return [
            {"id": "a1", "vendor_id": "a", "vendor_name": "Alpha FM"},
            {"id": "a2", "vendor_id": "a", "vendor_name": "Alpha FM"},
            {"id": "b1", "vendor_id": "b", "vendor_name": "Beta FM"},
            {"id": "b2", "vendor_id": "b", "vendor_name": "Beta FM"},
            {"id": "b3", "vendor_id": "b", "vendor_name": "Beta FM"},
        ]

    monkeypatch.setattr(certificates, "list_certificates", fake_list_certificates)

    result = await certificates.list_vendors_by_certificate_count(
        object(),
        min_count=2,
        comparison="gt",
    )

    assert result["count"] == 1
    assert result["vendors"] == [
        {
            "vendor_id": "b",
            "vendor_name": "Beta FM",
            "certificate_count": 3,
            "certificates": [
                {
                    "id": "b1",
                    "certificate_type_name": None,
                    "certificate_number": None,
                    "status": None,
                    "issue_date": None,
                    "expiry_date": None,
                    "days_to_expiry": None,
                    "risk_badge": None,
                    "draft": False,
                    "block_reason": None,
                    "vendor_name": "Beta FM",
                },
                {
                    "id": "b2",
                    "certificate_type_name": None,
                    "certificate_number": None,
                    "status": None,
                    "issue_date": None,
                    "expiry_date": None,
                    "days_to_expiry": None,
                    "risk_badge": None,
                    "draft": False,
                    "block_reason": None,
                    "vendor_name": "Beta FM",
                },
                {
                    "id": "b3",
                    "certificate_type_name": None,
                    "certificate_number": None,
                    "status": None,
                    "issue_date": None,
                    "expiry_date": None,
                    "days_to_expiry": None,
                    "risk_badge": None,
                    "draft": False,
                    "block_reason": None,
                    "vendor_name": "Beta FM",
                },
            ],
        }
    ]
