"""Unit tests for deterministic compliance answers (no LangChain)."""
from src.agents.compliance_answer import (
    build_deterministic_compliance_answer,
    compliance_answer_looks_wrong,
)


def test_lapsed_building_includes_detail():
    tools = [
        {
            "tool": "list_building_certificates",
            "output": {
                "ok": True,
                "count": 2,
                "certificates": [
                    {
                        "certificate_type_name": "Fire Risk Assessment (FRA)",
                        "certificate_number": "FRA-26198",
                        "status": "Lapsed",
                        "expiry_date": "2026-07-07",
                        "days_to_expiry": -29,
                        "trade_category": "Fire",
                        "inspector_name": "James Okafor",
                    },
                    {
                        "certificate_type_name": "Emergency Lighting Test Certificate",
                        "certificate_number": "EL-29404",
                        "status": "Lapsed",
                        "expiry_date": "2026-07-31",
                        "days_to_expiry": -5,
                        "trade_category": "Fire",
                    },
                ],
            },
        },
    ]
    ans = build_deterministic_compliance_answer(
        "which building certificates are lapsed", tools
    )
    assert ans is not None
    assert "**2**" in ans
    assert "FRA-26198" in ans
    assert "James Okafor" in ans
    assert "Emergency Lighting" in ans
    assert "overdue" in ans.lower() or "Expiry" in ans
    assert "What to do next" in ans
    assert "Draft a renewal email for FRA-26198" in ans
    assert "booking" in ans.lower()


def test_blocked_vendor_includes_name_and_accreditation():
    tools = [
        {
            "tool": "list_vendor_accreditations",
            "output": {
                "certificates": [
                    {
                        "vendor_name": "PestGuard Environmental Ltd",
                        "certificate_type_name": "BPCA Corporate Membership Certificate",
                        "certificate_number": "BPCA-29596",
                        "status": "Lapsed",
                        "risk_badge": "Blocked",
                        "vendor_block_state": "Blocked",
                        "trade_category": "Pest",
                        "expiry_date": "2026-06-01",
                        "days_to_expiry": -60,
                        "block_reason": "Lapsed BPCA accreditation",
                    }
                ]
            },
        }
    ]
    ans = build_deterministic_compliance_answer("Which vendors are blocked?", tools)
    assert ans is not None
    assert "PestGuard" in ans
    assert "BPCA" in ans
    assert "BPCA-29596" in ans
    assert "Blocked" in ans
    assert "What to do next" in ans
    assert "Draft a renewal email for BPCA-29596" in ans
    assert "Do not assign" in ans or "do not assign" in ans.lower()


def test_zero_list_is_honest():
    tools = [
        {
            "tool": "list_building_certificates",
            "output": {"count": 0, "certificates": []},
        }
    ]
    ans = build_deterministic_compliance_answer(
        "which building certificates are lapsed", tools
    )
    assert ans is not None
    assert "**0**" in ans


def test_detects_wrong_llm_zero():
    tools = [
        {
            "tool": "list_building_certificates",
            "output": {
                "certificates": [{"certificate_type_name": "FRA", "status": "Lapsed"}],
            },
        }
    ]
    assert compliance_answer_looks_wrong(
        "There are 0 expired building certificates.", tools
    )


def test_named_vendor_status_excludes_unrelated_building_rows():
    tools = [
        {
            "tool": "list_building_certificates",
            "input": {},
            "output": {
                "certificates": [
                    {
                        "vendor_name": "Vital Energy",
                        "certificate_type_name": "Air Conditioning Inspection Report (TM44)",
                        "certificate_number": "8835-5966",
                        "status": "Current",
                    }
                ]
            },
        },
        {
            "tool": "list_vendor_accreditations",
            "input": {"vendor_name": "AIB Solutions Limited"},
            "output": {
                "certificates": [
                    {
                        "vendor_name": "AIB Solutions Limited",
                        "certificate_type_name": "HSE Asbestos Removal Licence",
                        "certificate_number": "052305041",
                        "status": "Current",
                        "expiry_date": "2026-12-30",
                        "days_to_expiry": 134,
                        "draft": True,
                    }
                ]
            },
        },
    ]

    answer = build_deterministic_compliance_answer(
        "AIB Solutions Limited - what is the compliance status of its certificates?",
        tools,
    )

    assert answer is not None
    assert "Compliance status — AIB Solutions Limited" in answer
    assert "HSE Asbestos Removal Licence" in answer
    assert "052305041" in answer
    assert "draft awaiting PM confirmation" in answer
    assert "Vital Energy" not in answer
    assert "Air Conditioning Inspection Report" not in answer


def test_vendor_more_than_two_uses_strict_grouped_count():
    tools = [
        {
            "tool": "list_vendor_accreditations",
            "output": {
                "certificates": [
                    {
                        "id": "k1",
                        "vendor_name": "Kurt J. Lesker Company",
                        "certificate_number": "023248",
                    },
                    {
                        "id": "k2",
                        "vendor_name": "Kurt J. Lesker Company",
                        "certificate_number": "EMS 602407",
                    },
                    {
                        "id": "a1",
                        "vendor_name": "AIB Solutions Limited",
                        "certificate_number": "052305041",
                    },
                ]
            },
        }
    ]

    answer = build_deterministic_compliance_answer(
        "Which vendors hold more than 2 certificates?",
        tools,
    )

    assert answer == "**0 vendors** hold more than 2 accreditation certificates."


def test_vendor_count_aggregate_lists_only_qualified_vendors():
    tools = [
        {
            "tool": "list_vendors_by_certificate_count",
            "input": {"min_count": 2, "comparison": "gt"},
            "output": {
                "ok": True,
                "count": 1,
                "vendors": [
                    {
                        "vendor_name": "Example FM Limited",
                        "certificate_count": 3,
                        "certificates": [
                            {
                                "certificate_type_name": "ISO 9001",
                                "certificate_number": "QMS-001",
                                "status": "Current",
                                "expiry_date": "2027-01-01",
                                "days_to_expiry": 136,
                            },
                            {
                                "certificate_type_name": "ISO 14001",
                                "certificate_number": "EMS-002",
                                "status": "Lapsed",
                                "expiry_date": "2026-01-01",
                                "days_to_expiry": -229,
                            },
                            {
                                "certificate_type_name": "ISO 45001",
                                "certificate_number": "OHS-003",
                                "status": "Current",
                                "expiry_date": "2027-04-01",
                                "days_to_expiry": 226,
                            },
                        ],
                    }
                ],
            },
        }
    ]

    answer = build_deterministic_compliance_answer(
        "Which vendors hold more than 2 certificates?",
        tools,
    )

    assert answer is not None
    assert "Example FM Limited" in answer
    assert "3 certificates" in answer
    assert "ISO 9001" in answer
    assert "Current" in answer
    assert "expires 2027-01-01" in answer
    assert 'Draft a renewal email for EMS-002' in answer


def test_renewal_draft_answer_is_concise_and_does_not_repeat_email_body():
    tools = [
        {
            "tool": "draft_certificate_renewal",
            "input": {"certificate_number": "023248"},
            "output": {
                "ok": True,
                "email_drafts": [
                    {
                        "certificate_id": "cert-1",
                        "queue_item_id": "queue-1",
                        "subject": "[URGENT] Accreditation renewal required",
                        "body": "Dear vendor, full email body",
                    }
                ],
            },
        }
    ]

    answer = build_deterministic_compliance_answer(
        "Draft a renewal email for 023248",
        tools,
    )

    assert answer is not None
    assert "Renewal email draft created for **023248**" in answer
    assert "no email has been sent" in answer
    assert "Dear vendor" not in answer
