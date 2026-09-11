"""An invoice keyed on what its file was called.

The building graph listed Riverside Court's invoices as

    d0effe16-7bf6-4c31-a3c1-47d5611b5966_Invoice-HAL-B006-0042-Riverside-Court  £3695.00
    80042cd0-6888-4b04-996e-6a1f0b5412e0_Invoice-HAL-B006-0042-Riverside-Court  £3695.00

The single-door ingest set invoice_ref to Path(file_path).stem — the saved filename, session
id and all — while the document itself prints, on two lines as PDF headers do:

    Invoice no.
    HAL-B006-0042

Those two rows are one invoice uploaded twice: same vendor, same total, same twelve lines.
They became two because their only key was a filename that differs on every upload.

So: read the number the document prints, and let it identify the invoice.
"""
from __future__ import annotations

import pytest

from src.engines.contract_performance.extract import invoice_number_in

#: The header block of the invoice that prompted this, as PyMuPDF extracts it.
REAL = (
    "SPECIMEN\nVAT INVOICE\nHalden Building Services\n"
    "Mechanical & electrical maintenance · Manchester, UK\n"
    "Vendor record HALD · plenum_cafm.vendors\n"
    "Invoice no.\nHAL-B006-0042\nInvoice date\n10.09.2026\nDue date\n10.10.2026\n"
    "Contract\nCT-HALD-2026\nCurrency\nGBP\n"
)


def test_the_number_the_real_document_prints():
    assert invoice_number_in(REAL) == "HAL-B006-0042"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Invoice no.\nHAL-B006-0042", "HAL-B006-0042"),
        ("Invoice Number: INV-2026-001", "INV-2026-001"),
        ("Tax Invoice #ABC-99", "ABC-99"),
        ("INVOICE NO 12345", "12345"),
        ("invoice ref: 2026/0042", "2026/0042"),
        ("Invoice No.: 000123", "000123"),
    ],
)
def test_the_layouts_suppliers_actually_use(text: str, expected: str):
    assert invoice_number_in(text) == expected


def test_a_bare_invoice_heading_is_not_a_number():
    # "VAT INVOICE" sits above the supplier's name on most layouts. Matching it without a
    # label word would key the whole register on "Halden".
    assert invoice_number_in("VAT INVOICE\nHalden Building Services") is None
    assert invoice_number_in("INVOICE\nAcme Facilities Ltd") is None


def test_the_invoice_date_is_not_the_invoice_number():
    assert invoice_number_in("Invoice date\n10.09.2026") is None


def test_prose_about_a_missing_number_is_not_a_number():
    # Without the digit rule this reads the number as "is".
    assert invoice_number_in("Invoice number is not shown on this document") is None


@pytest.mark.parametrize("text", ["", None, "   ", "no invoice here", "Statement of account"])
def test_a_document_with_no_number_says_so(text):
    # None is an answer. The caller falls back to a label rather than inventing a key.
    assert invoice_number_in(text) is None


def test_the_first_number_wins_when_a_document_repeats_itself():
    # Continuation pages reprint the header. The same value either way, but the rule is
    # stated so a change to finditer order is a visible decision.
    assert invoice_number_in(REAL + "\nInvoice no.\nHAL-B006-0042") == "HAL-B006-0042"


def test_trailing_punctuation_is_not_part_of_the_number():
    assert invoice_number_in("Invoice no: HAL-B006-0042.") == "HAL-B006-0042"


def test_a_number_is_never_the_filename():
    # The value the bug used. A filename is not printed on the document and must not be
    # recovered from one by accident.
    stem = "d0effe16-7bf6-4c31-a3c1-47d5611b5966_Invoice-HAL-B006-0042-Riverside-Court"
    assert invoice_number_in(REAL) != stem


def test_it_reads_a_whole_document_not_just_the_head():
    # Some layouts print the number in a footer block under the line items.
    body = "\n".join(f"{i:02}. WO-B-006-{i} Annual service 385.00" for i in range(1, 40))
    assert invoice_number_in(body + "\nInvoice No. HAL-B006-0042") == "HAL-B006-0042"
