"""Unit tests — Single Door Feature B auto-classification."""
from src.agents.contract_performance_single_door import classify_contract_performance_doc


def test_contract_filename():
    assert classify_contract_performance_doc("/tmp/FM_Framework_Agreement_2026.pdf") == "contract"
    assert classify_contract_performance_doc("/tmp/vendor_sla_contract.docx") == "contract"
    assert classify_contract_performance_doc("/tmp/PO-44521.pdf") == "contract"


def test_invoice_filename():
    assert classify_contract_performance_doc("/tmp/Invoice_Mar2026.pdf") == "invoice"
    assert classify_contract_performance_doc("/tmp/vendor_bill_final.xlsx") == "invoice"


def test_user_message_overrides_generic_name():
    assert (
        classify_contract_performance_doc(
            "/tmp/scan001.pdf",
            "Please extract SLA from this FM contract",
        )
        == "contract"
    )
    assert (
        classify_contract_performance_doc(
            "/tmp/upload.pdf",
            "Verify this invoice against work orders",
        )
        == "invoice"
    )


def test_generic_doc_not_routed():
    assert classify_contract_performance_doc("/tmp/site_inspection_report.pdf") is None
    assert classify_contract_performance_doc("/tmp/assets.csv") is None
